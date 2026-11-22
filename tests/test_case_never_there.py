"""The case study "The witness who was never there" in fixed mode.

The contradiction hunter's rules, run by acceptance/eval_agents.py on the planted
case, offer the alibi and the two other real disagreements among 152 claims, and
neither the market trap (its times are known) nor any planted non-contradiction.
The numbers are pinned
here and in acceptance/cases/never-there/README.md; change both together."""

import asyncio
import importlib.util
import json
import unittest
from pathlib import Path

from acceptance import eval_agents
from sealbook.maps import geotag

HERE = Path(__file__).parents[1] / "acceptance" / "cases" / "never-there"
CASE = HERE / "case.json"


def make():
    spec = importlib.util.spec_from_file_location("never_there_make", HERE / "make.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NeverThereTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = asyncio.run(eval_agents.evaluate(live=False, path=CASE))
        cls.found = cls.report["runs"][0]["contradictions"]

    def test_case_json_is_what_make_writes(self):
        written = json.dumps(make().case(), ensure_ascii=False, indent=2) + "\n"
        self.assertEqual(CASE.read_text(encoding="utf-8"), written)

    def test_story_and_size(self):
        spec = json.loads(CASE.read_text(encoding="utf-8"))
        self.assertTrue(spec["story"]["fictional"])
        self.assertEqual(spec["investigation"], "case-never-there")
        kinds = [s["doc_type"] for s in spec["sources"].values()]
        self.assertEqual(kinds.count("witness_statement"), 40)
        self.assertEqual((len(spec["sources"]), len(spec["claims"])), (53, 152))

    def test_rules_offer_the_alibi_and_no_planted_non_contradiction(self):
        """By the clock: her own 18:00 to 24:00 in Brindle Cross overlaps 21:52
        and 22:07 on Tanner's Row. Her sister's 23:30 fits a 34 km drive after
        22:07, so it is not a side, and Jo at the market at 09:40 and home from
        18:00 is not raised at all."""
        offered = self.found["rule_candidates"]["candidates"]
        self.assertEqual(len(offered), 3)
        self.assertIn(
            ["mara_mq3.at", "mara_mq4.at", "mara_stmt.at"],
            [c["claims"] for c in offered],
        )
        self.assertFalse(any(c["planted_non_contradiction"] for c in offered))
        self.assertEqual(self.found["rule_candidates"]["judgment"], 0.0)

    def test_fixed_mode_scores(self):
        metrics = {k: v["mean"] for k, v in self.report["metrics"].items()}
        self.assertEqual(metrics["contradiction_precision"], 1.0)
        self.assertEqual(metrics["contradiction_recall"], 1.0)
        # The times are known, so the market trap is never offered.
        self.assertEqual(metrics["contradiction_trap_avoidance"], 1.0)
        self.assertEqual(metrics["linker_false_merges"], 0)
        self.assertEqual(self.report["runs"][0]["linker"]["other_false_proposals"], 0)
        self.assertTrue(self.report["complete"])

    def test_photo_carries_the_location_geotag_reads(self):
        photo = HERE / "files" / "IMG_4471.jpg"
        if not photo.exists():
            self.skipTest("the picture is not made yet")
        lon, lat = make().PHOTO_GPS
        loc = geotag.exif(photo)
        self.assertEqual(
            (round(loc["lon"], 5), round(loc["lat"], 5), loc["precision_m"]),
            (lon, lat, 50),
        )


if __name__ == "__main__":
    unittest.main()
