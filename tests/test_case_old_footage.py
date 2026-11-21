"""The old-footage case: the app's fingerprints against its planted copies.

The measured values are pinned so any change, better or worse, has to be read and
written down in acceptance/cases/old-footage/README.md."""

import hashlib
import importlib.util
import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from sealbook.settings import Config

HERE = Path(__file__).resolve().parent.parent / "acceptance" / "cases" / "old-footage"
CASE = json.loads((HERE / "case.json").read_text(encoding="utf-8"))
FILES = [HERE / "files" / s["file"] for s in CASE["sources"].values()]


def load_eval():
    spec = importlib.util.spec_from_file_location("old_footage_eval", HERE / "eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
@unittest.skipUnless(all(f.exists() for f in FILES), "needs the case's video files")
class OldFootageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.multiple(
            Config, PDQ_DISTANCE=31, PDQ_QUALITY=50, MEDIA_CANDIDATES=20
        ):
            cls.report = load_eval().score(CASE)

    def test_files_are_the_ones_the_truth_names(self):
        for s in CASE["sources"].values():
            data = (HERE / "files" / s["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), s["sha256"], s["file"])

    def test_every_copy_but_the_muted_short_is_tied_to_the_original(self):
        self.assertEqual(self.report["recall"], [8, 9])
        self.assertEqual(self.report["tied_to_original"], [8, 9])
        self.assertEqual(self.report["misses"], ["copy_short_muted"])
        self.assertEqual(self.report["misses"], sorted(CASE["truth"]["known_misses"]))

    def test_crop_mirror_and_logo_are_found_by_sound_not_picture(self):
        by = {r["copy"]: r["by"] for r in self.report["copies"]}
        self.assertEqual(by["copy_lowrate"], "picture+sound")
        self.assertEqual(by["copy_trim"], "picture+sound")
        for key in ("copy_logo", "copy_crop", "copy_mirror", "copy_vertical"):
            self.assertEqual(by[key], "sound", key)
        self.assertEqual(by["copy_viral"], "sound")
        self.assertEqual(by["copy_viral_muted"], "picture")

    def test_no_strong_match_outside_the_planted_family(self):
        self.assertEqual(self.report["false_strong"], 0)
        # Any one shared sound landmark still writes a weak proposal (README).
        self.assertEqual(len(self.report["false_matches"]), 36)
        self.assertTrue(
            all(f["strength"] == "weak" for f in self.report["false_matches"])
        )


if __name__ == "__main__":
    unittest.main()
