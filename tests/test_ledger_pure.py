import unittest
import uuid
from datetime import datetime

from stk.ledger import chain
from stk.ledger.contracts import (
    EXTRACTED_PROPS,
    GateInput,
    PolicyRow,
    PrincipalKind,
    Prop,
    Tier,
)
from stk.ledger.gate import gate, resolve


class CanonTest(unittest.TestCase):
    def test_refuses_floats_and_naive_datetimes(self):
        with self.assertRaises(TypeError):
            chain.canon({"x": 1.5})
        with self.assertRaises(TypeError):
            chain.canon(datetime(2026, 1, 1))

    def test_key_order_and_script_do_not_change_bytes(self):
        a = chain.canon({"text": "محمد", "lang": "ar"})
        b = chain.canon({"lang": "ar", "text": "محمد"})
        self.assertEqual(a, b)
        self.assertIn("محمد".encode(), a)

    def test_commitment_hides_value_behind_salt(self):
        self.assertNotEqual(
            chain.commitment(b"a" * 32, "x"), chain.commitment(b"b" * 32, "x")
        )


def _chain(n):
    entries, rows, prev = [], {}, chain.GENESIS
    for seq in range(1, n + 1):
        ref = str(uuid.uuid4())
        rh = chain.sha256(ref.encode())
        h = chain.link(prev, seq, "statement", rh)
        entries.append(chain.Entry(seq, "statement", ref, rh, prev, h))
        rows[("statement", ref)] = rh
        prev = h
    return entries, rows


class ChainVerifyTest(unittest.TestCase):
    def test_intact(self):
        entries, rows = _chain(5)
        self.assertEqual(chain.verify(entries, rows), [])

    def test_modified_row(self):
        entries, rows = _chain(5)
        rows[("statement", entries[2].ref)] = "f" * 64
        self.assertIn("seq 3", chain.verify(entries, rows)[0])

    def test_deleted_entry(self):
        entries, rows = _chain(5)
        del entries[2]
        self.assertTrue(chain.verify(entries, rows))

    def test_rewritten_entry_breaks_link(self):
        entries, rows = _chain(5)
        e = entries[1]
        entries[1] = chain.Entry(e.seq, e.kind, e.ref, "0" * 64, e.prev_hash, e.hash)
        rows[("statement", e.ref)] = "0" * 64
        self.assertTrue(chain.verify(entries, rows))


def _gi(**kw):
    base = {
        "prop": Prop.NAME,
        "author_kind": PrincipalKind.AGENT,
        "has_span": True,
        "span_valid": True,
        "attested": False,
        "verdict": None,
        "retracted": False,
        "distinct_origins": 1,
    }
    return GateInput(**(base | kw))


PLAIN = PolicyRow("*", "*", "*")


class GateTest(unittest.TestCase):
    def test_rules(self):
        cases = [
            (_gi(), PLAIN, Tier.UNCONFIRMED, "default"),
            (_gi(prop=Prop.ACCESS, has_span=False), PLAIN, Tier.ACCEPTED, "access_log"),
            (_gi(has_span=False), PLAIN, Tier.REJECTED, "no_span"),
            (_gi(span_valid=False), PLAIN, Tier.REJECTED, "span_not_in_source"),
            (_gi(verdict="accept"), PLAIN, Tier.ACCEPTED, "human_accept"),
            (_gi(verdict="reject"), PLAIN, Tier.REJECTED, "human_reject"),
            (_gi(verdict="accept", retracted=True), PLAIN, Tier.REJECTED, "retracted"),
            (
                _gi(author_kind=PrincipalKind.HUMAN, attested=True),
                PLAIN,
                Tier.ACCEPTED,
                "human_attested",
            ),
            (_gi(author_kind=PrincipalKind.HUMAN), PLAIN, Tier.UNCONFIRMED, "default"),
            (
                _gi(prop=Prop.SHA256, has_span=False),
                PolicyRow("hash", "*", "*", auto_accept=True),
                Tier.ACCEPTED,
                "auto_accept",
            ),
            # Origins corroborate only when a calibration artifact backs the threshold.
            (
                _gi(distinct_origins=3),
                PolicyRow("*", "*", "*", min_origins=2),
                Tier.UNCONFIRMED,
                "default",
            ),
            (
                _gi(distinct_origins=3),
                PolicyRow(
                    "*", "*", "*", min_origins=2, calibration={"error_rate": "0.02"}
                ),
                Tier.CORROBORATED,
                "origins",
            ),
        ]
        for gi, policy, tier, reason in cases:
            with self.subTest(gi=gi, policy=policy):
                self.assertEqual(gate(gi, policy), (tier, reason))

    def test_resolve_prefers_specific(self):
        rows = [
            PolicyRow("*", "*", "sensitive", routes=("local",)),
            PolicyRow("*", "*", "ordinary", routes=("local", "cloud")),
            PolicyRow("hash", "*", "*", auto_accept=True),
        ]
        self.assertEqual(
            resolve(rows, "ocr", "register", "sensitive").routes, ("local",)
        )
        self.assertTrue(resolve(rows, "hash", "register", "sensitive").auto_accept)
        with self.assertRaises(LookupError):
            resolve(rows[:2], "ocr", "register", "secret")


class PropValuesTest(unittest.TestCase):
    def test_discovery_props_are_not_extracted(self):
        for (
            name
        ) in "incident occurred_at involved supported_by finding located_at".split():
            self.assertNotIn(Prop(name), EXTRACTED_PROPS)


if __name__ == "__main__":
    unittest.main()
