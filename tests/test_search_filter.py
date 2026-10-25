import base64
import unittest

from sqlalchemy import column, table
from sqlalchemy.dialects import postgresql

from sealbook.search import filter as filters


def leaf(field="tier", op="eq", value="accepted"):
    return {"field": field, "op": op, "value": value}


class FilterTest(unittest.TestCase):
    def test_values(self):
        for field, value in [
            ("investigation", "case-1"),
            ("place", "دمشق"),
            ("entity", "person:12345678-1234-1234-1234-123456789abc"),
            ("tier", "accepted"),
            ("prop", "name"),
            ("doc_type", "register"),
            ("origin", "source-1"),
            ("author_kind", "human"),
        ]:
            for op in ("eq", "in") if field != "author_kind" else ("eq",):
                ast = leaf(field, op, [value] if op == "in" else value)
                with self.subTest(ast=ast):
                    self.assertIs(filters.validate(ast), ast)

    def test_invalid_nodes(self):
        cases = [
            (None, "node must be an object"),
            ({}, "expected a logical node or leaf"),
            (leaf("unknown"), "unknown field"),
            (leaf(op="like"), "unknown op"),
            (leaf(value=True), "value must be a string"),
            (leaf(op="in", value=[]), "in-list must not be empty"),
            (leaf(op="in", value="accepted"), "in value must be a list"),
            (leaf(op="in", value=[1]), "value must be a string"),
            (leaf(value="bogus"), "invalid tier"),
            (leaf("prop", value="bogus"), "invalid prop"),
            (leaf("author_kind", value="bogus"), "invalid author_kind"),
            (leaf("author_kind", "in", ["human"]), "unknown op"),
            (leaf("entity", value="person:bad"), "invalid entity"),
            (leaf() | {"extra": 1}, "unknown keys"),
            ({"and": [], "or": []}, "unknown keys"),
            ({"and": "x"}, "and must be a list"),
            ({"or": []}, "or must not be empty"),
            ({"not": []}, "node must be an object"),
            (leaf("time", "between", ["2026-02-30", None]), "invalid ISO date"),
            (leaf("time", "between", ["20260101", None]), "invalid ISO date"),
            (leaf("time", "between", [True, None]), "date must be a string or null"),
            (leaf("time", "between", [None]), "between requires two bounds"),
            (
                leaf("time", "between", ["2026-02-01", "2026-01-01"]),
                "time bounds are reversed",
            ),
        ]
        for ast, reason in cases:
            with self.subTest(ast=ast), self.assertRaisesRegex(ValueError, reason):
                filters.validate(ast)

    def test_limits(self):
        node = leaf()
        for _ in range(5):
            node = {"not": node}
        filters.validate(node)
        with self.assertRaisesRegex(ValueError, "depth exceeds 6"):
            filters.validate({"not": node})
        filters.validate({"and": [leaf()] * 50})
        with self.assertRaisesRegex(ValueError, "more than 50 leaves"):
            filters.validate({"and": [leaf()] * 51})

    def sql(self, ast):
        t = table("e", *(column(n) for n in ("tier", "prop", "time_from", "time_to")))
        return str(
            filters.compile(ast, dict(t.c.items())).compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )

    def test_compile_logic(self):
        self.assertEqual(
            self.sql(
                {
                    "and": [
                        leaf(),
                        {
                            "or": [
                                leaf("prop", "in", ["name", "birth_date"]),
                                {"not": leaf(value="rejected")},
                            ]
                        },
                    ]
                }
            ),
            "e.tier = 'accepted' AND (e.prop IN ('name', 'birth_date') OR e.tier != 'rejected')",
        )

    def test_overlap_open_bounds(self):
        cases = [
            (
                ["2026-01-01", "2026-02-01"],
                "(e.time_to IS NULL OR e.time_to >= '2026-01-01') AND "
                "(e.time_from IS NULL OR e.time_from <= '2026-02-01')",
            ),
            (["2026-01-01", None], "e.time_to IS NULL OR e.time_to >= '2026-01-01'"),
            (
                [None, "2026-02-01"],
                "e.time_from IS NULL OR e.time_from <= '2026-02-01'",
            ),
            ([None, None], "true"),
        ]
        for bounds, sql in cases:
            with self.subTest(bounds=bounds):
                self.assertEqual(self.sql(leaf("time", "between", bounds)), sql)

    def test_encoding(self):
        ast = {"and": [leaf(), leaf("place", value="دمشق")]}
        encoded = filters.encode(ast)
        self.assertNotIn("=", encoded)
        self.assertEqual(filters.decode(encoded), ast)
        self.assertFalse(
            filters.differs(leaf(), {"value": "accepted", "op": "eq", "field": "tier"})
        )
        self.assertTrue(filters.differs(leaf(), leaf(value="rejected")))
        for invalid in (
            "!",
            "a",
            "e30",
            base64.urlsafe_b64encode(b'{"and":[],"and":[]}').decode().rstrip("="),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                filters.decode(invalid)

    def test_compile_and_encode_validate(self):
        for call in (
            lambda: filters.compile(leaf(value="bad"), {}),
            lambda: filters.encode(leaf(value="bad")),
            lambda: filters.differs(leaf(), {}),
        ):
            with self.assertRaises(ValueError):
                call()


if __name__ == "__main__":
    unittest.main()
