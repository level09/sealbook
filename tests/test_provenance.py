"""What a claim's proposer is called comes from its run's own provenance: a model
that read the source here, a model's answer replayed from a recording, a demo's
fixed answer, or claims planted from a case file. A model id is detail, never the
first words a person reads."""

import unittest
import uuid

from sealbook.ledger.models import Principal, Run
from sealbook.monitor.data import Context, answered


def run(**fields) -> Run:
    return Run(id=uuid.uuid4(), principal_id=uuid.uuid4(), **fields)


class AnsweredTest(unittest.TestCase):
    def test_a_model_that_ran_here(self):
        got = answered(
            run(
                tool="extract",
                route="cloud-zdr",
                model_requested="anthropic/claude-haiku-5.5",
                model_served="anthropic/claude-haiku-5.5",
                inputs={},
            )
        )
        self.assertEqual(got["how"], "model")
        self.assertEqual(got["label"], "Read by a model")
        self.assertIn("anthropic/claude-haiku-5.5", got["detail"])
        self.assertIn("zero-retention", got["detail"])

    def test_a_recorded_answer_replayed(self):
        got = answered(
            run(
                tool="extract",
                route="cloud-zdr",
                model_requested="anthropic/claude-haiku-5.5",
                model_served="anthropic/claude-haiku-5.5",
                inputs={
                    "answer": "recorded",
                    "recorded_at": "2026-11-21T10:00:00+00:00",
                },
            )
        )
        self.assertEqual(got["how"], "recorded")
        self.assertEqual(got["label"], "Replayed from a recording of 21 November 2026")
        self.assertIn("anthropic/claude-haiku-5.5", got["detail"])
        self.assertIn("no model ran", got["detail"])

    def test_cell_values_from_a_case_file_are_planted_not_ai(self):
        got = answered(
            run(
                tool="extract_sheet",
                route="local",
                model_requested="sealbook-demo-fixed-output",
                model_served="sealbook-demo-fixed-output",
                provider_served="demo",
                inputs={"answer": "case file"},
            )
        )
        self.assertEqual(got["how"], "planted")
        self.assertEqual(got["label"], "Planted from the case file")
        self.assertNotIn("AI", got["label"])

    def test_a_planted_fallback(self):
        got = answered(
            run(
                tool="extract",
                model_requested="planted-from-case.json",
                model_served="planted-from-case.json",
                inputs={},
            )
        )
        self.assertEqual(got["how"], "planted")

    def test_a_fixed_demo_answer_names_no_model(self):
        for fields in (
            {
                "route": "local",
                "model_requested": "qwen3-vl-4b",
                "model_served": "qwen3-vl-4b-sy-demo",
                "inputs": {"answer": "fixed"},
            },
            {
                "route": "cloud-zdr",
                "model_requested": "sealbook-demo-fixed-output",
                "model_served": "sealbook-demo-fixed-output",
                "provider_served": "demo",
                "inputs": {},
            },
        ):
            got = answered(run(tool="extract", **fields))
            self.assertEqual(got["how"], "fixed")
            self.assertEqual(got["label"], "Fixed demo answer")
            self.assertNotIn("qwen", got["label"] + got["detail"])

    def test_local_code_is_the_tool_itself(self):
        got = answered(run(tool="geotag", route=None, inputs={}))
        self.assertEqual(
            got, {"how": "tool", "label": "camera location reading", "detail": None}
        )

    def test_actor_reads_the_provenance(self):
        ctx = Context()
        agent = Principal(id=uuid.uuid4(), kind="agent", name="Extractor")
        r = run(
            tool="extract_sheet",
            route="local",
            model_requested="sealbook-demo-fixed-output",
            model_served="sealbook-demo-fixed-output",
            provider_served="demo",
            inputs={"answer": "case file"},
        )
        ctx.principals[agent.id] = agent
        ctx.runs[r.id] = r
        got = ctx.actor(agent.id, r.id)
        self.assertEqual(got["label"], "Sheet extraction, planted from the case file")
        self.assertEqual(got["how"], "planted")


if __name__ == "__main__":
    unittest.main()
