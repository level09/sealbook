"""The agent evaluation in fixed mode: the rule-built layers against planted truth.

Gap detection, linker recall and contradiction precision meet their targets. The
rule layer still passes one namesake pair to the model (docs/agent-eval.md); the
measured values are pinned so any change, better or worse, has to be read and
written down."""

import asyncio
import unittest

from acceptance import eval_agents


class FixedModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = asyncio.run(eval_agents.evaluate(live=False))

    def test_gap_rules_find_every_planted_gap_and_no_planted_non_gap(self):
        gaps = self.report["gaps"]
        self.assertEqual(gaps["recall"], 1.0)
        self.assertEqual(gaps["false_gaps_on_planted_non_gaps"], 0)
        self.assertEqual(gaps["extra_in_planted_people"], 0)

    def test_linker_rule_layer_is_pinned(self):
        linker = self.report["linker"]
        self.assertEqual(linker["recall"], 0.857)
        missed = [p["records"] for p in linker["same_person"] if not p["candidate"]]
        self.assertEqual(missed, [["yousef_post", "yousef_reg"]])
        twins = [p["records"] for p in linker["twins"] if p["proposed"]]
        self.assertEqual(twins, [["khatib_intake", "khatib_list"]])
        self.assertEqual(linker["other_false_proposals"], 0)

    def test_contradiction_rule_layer_is_pinned(self):
        found = self.report["contradictions"]
        self.assertEqual((found["precision"], found["recall"]), (1.0, 1.0))
        self.assertTrue(all(i["true"] for i in found["items"]))


if __name__ == "__main__":
    unittest.main()
