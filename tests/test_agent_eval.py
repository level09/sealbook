"""The agent evaluation in fixed mode: the rule-built layers against planted truth.

Gap detection, linker recall and contradiction precision meet their targets. The
rule layer still passes one namesake pair to the model (docs/agent-eval.md); the
measured values are pinned so any change, better or worse, has to be read and
written down."""

import asyncio
import json
import os
import unittest
from unittest import mock

import httpx2

from acceptance import eval_agents
from sealbook.settings import Config


class FixedModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = asyncio.run(eval_agents.evaluate(live=False, trials=2))
        cls.first = cls.report["runs"][0]

    def test_gap_rules_find_every_planted_gap_and_no_planted_non_gap(self):
        gaps = self.first["gaps"]
        self.assertEqual(gaps["recall"], 1.0)
        self.assertEqual(gaps["false_gaps_on_planted_non_gaps"], 0)
        self.assertEqual(gaps["extra_in_planted_people"], 0)

    def test_linker_rule_layer_is_pinned(self):
        linker = self.first["linker"]
        self.assertEqual(linker["recall"], 0.857)
        missed = [p["records"] for p in linker["same_person"] if not p["candidate"]]
        self.assertEqual(missed, [["yousef_post", "yousef_reg"]])
        twins = [p["records"] for p in linker["twins"] if p["proposed"]]
        self.assertEqual(twins, [["khatib_intake", "khatib_list"]])
        self.assertEqual(linker["other_false_proposals"], 0)

    def test_contradiction_rule_layer_is_pinned(self):
        found = self.first["contradictions"]
        self.assertEqual((found["precision"], found["recall"]), (1.0, 1.0))
        self.assertTrue(all(i["true"] for i in found["items"] if not i["judgment"]))

    def test_rules_offer_every_judgment_call_and_the_fixed_answer_takes_them_all(self):
        linker, found = self.first["linker"], self.first["contradictions"]
        rows = linker["hard_same_person"] + linker["traps"]
        self.assertTrue(all(p["candidate"] for p in rows))
        self.assertEqual(found["rule_candidates"]["judgment"], 1.0)
        metrics = {k: v["mean"] for k, v in self.report["metrics"].items()}
        self.assertEqual(metrics["linker_hard_recall"], 1.0)
        self.assertEqual(metrics["linker_trap_avoidance"], 0.0)
        self.assertEqual(metrics["contradiction_hard_recall"], 1.0)
        self.assertEqual(metrics["contradiction_trap_avoidance"], 0.0)
        self.assertEqual(metrics["canary_held"], 0.0)

    def test_trials_spread_and_false_merges_need_every_trial(self):
        recall = self.report["metrics"]["linker_recall"]
        self.assertEqual(
            recall,
            {"mean": 0.857, "min": 0.857, "max": 0.857, "trials": [0.857, 0.857]},
        )
        self.assertFalse(
            self.report["targets"]["linker_false_merges == 0 in every trial (pass^k)"]
        )

    def test_gap_cap_defers_planted_gaps_without_marking_the_run_incomplete(self):
        self.assertEqual(self.report["limits"]["gaps"]["cap"], 12)
        self.assertEqual(self.report["planted_deferred"]["gaps"], 6)
        self.assertTrue(self.report["complete"])

    def test_a_planted_pair_cut_by_the_cap_marks_the_run_incomplete(self):
        with mock.patch.object(eval_agents.linker, "CAP", 1):
            report = asyncio.run(eval_agents.evaluate(live=False))
        self.assertFalse(report["complete"])
        self.assertGreater(report["planted_deferred"]["linker"], 0)


class RouteTest(unittest.TestCase):
    def test_live_mode_calls_the_route_as_the_worker_does(self):
        sent = []

        def answer(request):
            sent.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "model": "served/model",
                    "provider": "ProviderX",
                    "choices": [
                        {"message": {"content": "{}"}, "finish_reason": "stop"}
                    ],
                },
            )

        log = []
        with (
            mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test"}),
            mock.patch.object(Config, "AGENT_MODEL", "asked/model"),
        ):
            report = asyncio.run(
                eval_agents.evaluate(
                    live=True,
                    route="cloud-zdr",
                    log=log,
                    transport=httpx2.MockTransport(answer),
                )
            )
        self.assertTrue(sent)
        self.assertTrue(all(b["provider"] == {"zdr": True} for b in sent))
        self.assertEqual(
            report["served"],
            [{"model_served": "served/model", "provider": "ProviderX"}],
        )
        calls = [x["call"] for x in log if "call" in x]
        self.assertEqual(len(calls), len(sent))
        self.assertEqual(calls[0]["body"]["model"], "asked/model")
        self.assertIn("served/model", calls[0]["raw"])
        self.assertTrue(any("drop" in x for x in log))


if __name__ == "__main__":
    unittest.main()
