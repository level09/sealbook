"""The read-through rules in acceptance/check_sense.py, text in and findings out."""

import unittest

from acceptance.check_sense import (
    broken_value,
    check_page,
    empty_label,
    internal_words,
    load_allow,
    one_plural,
    raw_hex_id,
    raw_score,
    raw_uuid,
    repeated_rows,
    snake_case,
    stray_separator,
)


class RuleTest(unittest.TestCase):
    def test_hex_ids_alone_or_glued(self):
        self.assertTrue(raw_hex_id("Person a464e7a6 was seen"))
        self.assertTrue(raw_hex_id("Request 1fbaac41"))
        self.assertTrue(raw_hex_id("Value hidden · 72b5e86dc74b"))
        self.assertTrue(raw_hex_id("texteb148f8f"))
        self.assertFalse(raw_hex_id("Branch 999 in 2013, decade, facade"))
        self.assertFalse(raw_hex_id("Sha256: 72b5e86dc74b11"))
        self.assertFalse(raw_hex_id("for eb148f8f0000 in", expected=["eb148f8f0000"]))

    def test_uuid(self):
        self.assertTrue(raw_uuid("person 2b7f89f7-5818-45d2-9a4a-08d7db8957f8"))
        self.assertFalse(raw_uuid("2014-02-12"))

    def test_snake_case_but_not_file_names(self):
        self.assertEqual(
            snake_case("Photo, checkpoint_forward"), ["Photo, checkpoint_forward"]
        )
        self.assertTrue(snake_case("event statements_written"))
        self.assertFalse(snake_case("text file, messy_people_v2.csv"))
        self.assertFalse(snake_case("analyst-2@example.com"))

    def test_internal_words(self):
        self.assertTrue(internal_words("read 2 located at values"))
        self.assertTrue(internal_words("extract run (qwen3-vl-4b-sy-demo)"))
        self.assertTrue(internal_words("import by importer"))
        self.assertFalse(internal_words("AI extraction, accepted 26 claims"))

    def test_raw_score(self):
        self.assertTrue(raw_score("cosine distance 0.216"))
        self.assertTrue(raw_score("lead 0.66"))
        self.assertFalse(raw_score("loose lead, 3 sources"))

    def test_one_with_plural(self):
        self.assertTrue(one_plural("19 facts from 1 documents"))
        self.assertTrue(one_plural("overlapping 1 days"))
        self.assertFalse(one_plural("1 fact from 2 documents"))
        self.assertFalse(one_plural("Unnamed person 1\nRelations"))
        self.assertFalse(one_plural("1 witness, 21 days"))

    def test_separators(self):
        self.assertTrue(stray_separator("family . 2 sentences"))
        self.assertTrue(stray_separator("Name · · 2014"))
        self.assertTrue(stray_separator("born ()"))
        self.assertFalse(stray_separator("Damascus, 2014 · 3 sources"))
        self.assertFalse(stray_separator("5,name,place,,\n"), "a sheet row is content")

    def test_empty_label_and_broken_values(self):
        self.assertTrue(empty_label("Born: · Died: 2014"))
        self.assertTrue(empty_label("Born 1979 Died:"))
        self.assertFalse(empty_label("Next:\n2 review requests"))
        for bad in ("undefined", "age null", "NaN days", "[object Object]"):
            self.assertTrue(broken_value(bad), bad)
        self.assertFalse(broken_value("None of these"))

    def test_repeated_rows(self):
        rows = [["Names match"] * 40 + ["Ages differ"], ["Same place list"] * 2]
        self.assertEqual(repeated_rows([rows[0]]), ["Names match (x40)"])
        self.assertEqual(repeated_rows([rows[1]]), [])
        self.assertEqual(repeated_rows([["—", "—", "—", "12", "12", "12"]]), [])

    def test_allowlist_needs_reasons_and_scopes_by_page(self):
        allow = load_allow()
        page = {"page": "/desk @phone", "text": "Items per page:\nRequest 1fbaac41"}
        rules = {f["rule"] for f in check_page(page, allow)}
        self.assertEqual(rules, {"hex-id"})
        page = {"page": "/desk @phone", "text": "Sha256\ndocument\n" + "ab12" * 16}
        self.assertTrue(check_page(page, allow), "the digest allowance is monitor only")


if __name__ == "__main__":
    unittest.main()
