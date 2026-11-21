"""The case study "The name nobody wrote down", scored in fixed mode.

The gap finder must flag the driver's 73-day silence and the missing woman's open
silence, and none of the four planted silences that are not gaps. The numbers are
pinned so any change has to be read and written down in the case's README."""

import asyncio
import json
import unittest
from datetime import date
from pathlib import Path

from acceptance import eval_agents

CASE = (
    Path(__file__).parents[1] / "acceptance" / "cases" / "unwritten-name" / "case.json"
)


class UnwrittenNameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(CASE.read_text(encoding="utf-8"))
        cls.report = asyncio.run(eval_agents.evaluate(live=False, path=CASE))
        cls.first = cls.report["runs"][0]

    def test_story_is_fictional_and_rests_on_the_gap_finder(self):
        story = self.spec["story"]
        self.assertTrue(story["fictional"])
        self.assertIn("gap finder", story["rests_on"])
        self.assertEqual(self.spec["investigation"], "case-unwritten-name")

    def test_gap_finder_flags_both_planted_gaps_and_no_planted_non_gap(self):
        gaps = self.first["gaps"]
        self.assertEqual(gaps["recall"], 1.0)
        self.assertEqual(gaps["false_gaps_on_planted_non_gaps"], 0)
        self.assertEqual(gaps["extra_in_planted_people"], 0)
        self.assertEqual(gaps["detected_elsewhere"], 0)
        self.assertEqual((gaps["detected"], gaps["reported"]), (2, 2))
        self.assertEqual(self.report["planted_deferred"]["gaps"], 0)

    def test_the_driver_silence_is_honestly_over_the_threshold(self):
        aron = next(g for g in self.spec["truth"]["gaps"] if g["record"] == "aron_gate")
        days = (date.fromisoformat(aron["to"]) - date.fromisoformat(aron["from"])).days
        self.assertEqual(days, 73)
        self.assertGreater(days, eval_agents.gaps.GAP_DAYS)

    def test_logs_hold_several_hundred_dated_lines(self):
        seen = [c for c in self.spec["claims"] if c["prop"] == "seen_at"]
        self.assertEqual(len(self.spec["claims"]), 651)
        self.assertGreater(len(seen), 500)

    def test_no_contradiction_or_same_person_is_planted_or_offered(self):
        linker, found = self.first["linker"], self.first["contradictions"]
        self.assertEqual(found["rule_candidates"]["candidates"], [])
        self.assertEqual(found["items"], [])
        self.assertEqual(linker["false_merges"], 0)
        self.assertEqual(linker["other_false_proposals"], 0)


if __name__ == "__main__":
    unittest.main()
