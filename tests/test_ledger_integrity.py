"""Integrity holes closed after review: payload rewrites, unverified signatures,
checks that raced the chain lock, and one source counted as many origins."""

import unittest
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from sealbook.ledger.contracts import Prop, Refused, StatementIn, Tier
from sealbook.ledger.models import Policy
from sealbook.ledger.write import append, commit, prepare, record_acquisition, verify_db
from tests.passkey import SoftPasskey
from tests.test_ledger_db import POSTGRES, LedgerDbCase


@unittest.skipUnless(POSTGRES, "ledger needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class LedgerIntegrityTest(LedgerDbCase):
    async def insider(self, sql, **params):
        async with self.Session() as s, s.begin():
            await s.execute(text("set local session_replication_role = replica"))
            await s.execute(text(sql), params)

    async def test_payload_rewrite_refused_then_caught(self):
        async with self.Session() as s, s.begin():
            st = await append(s, self.proposal(), sign=self.sign)
        async with self.Session() as s:
            with self.assertRaises(DBAPIError):
                async with s.begin():
                    await s.execute(text("update payload set value = '{}'"))
        await self.insider(
            "update payload set value = :v where commitment = :c",
            v='{"text": "forged", "lang": "ar"}',
            c=st.commitment,
        )
        async with self.Session() as s:
            problems = await verify_db(s)
        self.assertIn(
            f"payload {st.commitment} does not match its commitment", problems
        )

    async def test_stored_hash_must_match_row(self):
        async with self.Session() as s, s.begin():
            st = await append(s, self.proposal(), sign=self.sign)
        await self.insider(
            "update statement set hash = :h where id = :i", h="0" * 64, i=st.id
        )
        async with self.Session() as s:
            problems = await verify_db(s)
        self.assertIn(
            f"statement {st.id}: stored hash does not match its row", problems
        )

    async def test_unverified_passkey_never_accepts(self):
        stranger = SoftPasskey()  # a valid assertion, but not the author's passkey
        forged = {}
        async with self.Session() as s, s.begin():
            for name, sign in (
                ("bare", lambda h: {"kind": "webauthn", "challenge": h}),
                ("stranger", stranger.sig),
            ):
                st = await append(s, self.proposal(), sign=self.sign)
                review = await append(
                    s,
                    StatementIn(
                        subject=f"statement:{st.id}",
                        prop=Prop.REVIEW,
                        value={"verdict": "accept", "reason": name},
                        author_id=self.human.id,
                    ),
                    sign=sign,
                )
                self.assertEqual(await self.tier(s, st.id), Tier.UNCONFIRMED)
                forged[name] = review.id
        async with self.Session() as s:
            problems = await verify_db(s)
        self.assertTrue(any(str(forged["bare"]) in p for p in problems), problems)
        self.assertIn(
            f"statement {forged['stranger']}: signature fails: "
            "passkey is not registered to the author",
            problems,
        )

    async def test_commit_rechecks_under_the_lock(self):
        mine = self.proposal()
        theirs = StatementIn(**(mine.__dict__ | {"author_id": self.human.id}))
        # Both prepare before either commits: neither sees the other's value.
        async with self.Session() as a, self.Session() as b:
            await a.begin()
            await b.begin()
            pa = await prepare(a, mine)
            pb = await prepare(b, theirs)
            retry = await prepare(b, mine)
            first = await commit(a, pa, self.sign(pa.hash))
            await a.commit()
            with self.assertRaisesRegex(Refused, "pending"):
                await commit(b, pb)
            await b.rollback()
            await b.begin()
            again = await commit(b, retry, self.sign(retry.hash))
            self.assertEqual(again.id, first.id)
            await b.rollback()

    async def test_one_blob_through_many_channels_is_one_origin(self):
        async with self.Session() as s, s.begin():
            s.add(
                Policy(
                    capability="extract",
                    doc_type="register",
                    sensitivity="ordinary",
                    min_origins=2,
                    calibration={"artifact": "synthetic-test", "error_rate": "0.01"},
                )
            )
            await record_acquisition(
                s,
                blob_sha256=self.sha,
                origin="channel:elsewhere",
                collector_id=self.human.id,
                at=datetime.now(UTC),
                context={},
                sensitivity="ordinary",
                doc_type="register",
            )
            st = await append(s, self.proposal(), sign=self.sign)
            self.assertEqual(await self.tier(s, st.id), Tier.UNCONFIRMED)


if __name__ == "__main__":
    unittest.main()
