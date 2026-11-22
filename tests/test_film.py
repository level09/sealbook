"""The case film in the app reads the site's one copy: the storyboard rows, the case
each story belongs to, and story starts that are caption starts in the film."""

import json
import re
import unittest
from pathlib import Path

from sealbook.guide import film

ROOT = Path(__file__).resolve().parents[1]


class BoardTest(unittest.TestCase):
    def test_the_four_case_studies_are_its_chapters(self):
        studies = {
            json.loads(p.read_text(encoding="utf-8"))["investigation"]
            for p in (ROOT / "acceptance/cases").glob("*/case.json")
        }
        chapters = film.chapters()
        self.assertEqual({c["case"] for c in chapters}, studies)
        self.assertEqual(len(chapters), 4)
        starts = [c["at"] for c in film.board()]
        self.assertEqual(starts, sorted(starts))
        for c in chapters:
            self.assertGreater(c["length"], 0, c)
            self.assertIs(film.for_case(c["case"]), c)
        for other in ("case-demo-02", "never-there", "", "case-"):
            self.assertIsNone(film.for_case(other), other)

    def test_labels_and_starts_come_from_the_board_file(self):
        text = (ROOT / "site/film_board.tsv").read_text(encoding="utf-8")
        for row in film.board():
            self.assertIn(f"{row['at']:g}\t", text)
            self.assertIn(f"\t{row['label']}", text)

    def test_every_start_is_a_caption_start(self):
        vtt = (ROOT / "site/media/case_film.vtt").read_text(encoding="utf-8")
        cues = {
            int(h) * 3600 + int(m) * 60 + float(s)
            for h, m, s in re.findall(r"^(\d+):(\d+):([\d.]+) -->", vtt, re.M)
        }
        for row in film.board():
            self.assertIn(row["at"], cues, row["label"])

    def test_urls_name_the_site_files(self):
        src = film.urls()
        self.assertEqual(set(src), {"mp4", "jpg", "vtt"})
        for ext, url in src.items():
            self.assertRegex(url, rf"^/film/case_film\.{ext}\?v=[0-9a-f]{{8}}$")
            self.assertTrue((film.SITE / "media" / f"case_film.{ext}").is_file())


if __name__ == "__main__":
    unittest.main()
