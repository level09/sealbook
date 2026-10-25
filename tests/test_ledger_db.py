"""Ledger write path against a throwaway Postgres database migrated to head."""

import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from io import BytesIO
from unittest import mock

from alembic import command
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)

from sealbook import blobs
from sealbook.ledger import queue
from sealbook.ledger.contracts import Prop, Refused, StatementIn, Tier
from sealbook.ledger.models import Policy, Principal, Run, StatementTier
from sealbook.ledger.write import append, record_acquisition, seal_run, verify_db
from sealbook.settings import Config
from tests.passkey import attester

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"


def png(color: str) -> bytes:
    """A real image, so span validation has page sizes to check against."""
    out = BytesIO()
    Image.new("RGB", (64, 64), color).save(out, "PNG")
    return out.getvalue()


@unittest.skipUnless(POSTGRES, "ledger needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class LedgerDbCase(unittest.IsolatedAsyncioTestCase):
    """Setup and helpers shared with test_ledger_integrity; holds no tests."""

    def setUp(self):
        # One database per test: a tamper test must not poison the next test's chain.
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config("sealbook", url, quiet=True)
        command.upgrade(config, "heads")
        self.url = make_url(url).set(drivername="postgresql+asyncpg")
        blob_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(Config, "BLOB_DIR", blob_dir))

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.url)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as s, s.begin():
            self.human, self.passkey = await attester(s)
            self.agent = Principal(kind="agent", name="extractor", on_behalf_of=None)
            s.add(self.agent)
            await s.flush()
            self.key = Ed25519PrivateKey.generate()
            pub = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            self.run = Run(
                principal_id=self.agent.id, tool="extract", route="cloud", pubkey=pub
            )
            s.add(self.run)
            self.sha = (
                await blobs.put(s, png("white"), "image/png", Config.BLOB_DIR)
            ).sha256
            await record_acquisition(
                s,
                blob_sha256=self.sha,
                origin="device:test",
                collector_id=self.human.id,
                at=datetime.now(UTC),
                context={},
                sensitivity="ordinary",
                doc_type="register",
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    def sign(self, h):
        return {"kind": "ed25519", "sig": self.key.sign(h.encode()).hex()}

    def proposal(self, value="محمد", span=True, subject=None):
        return StatementIn(
            subject=subject or f"person:{uuid.uuid4()}",
            prop=Prop.NAME,
            value={"text": value, "lang": "ar"},
            author_id=self.agent.id,
            run_id=self.run.id,
            span_blob=self.sha if span else None,
            span={"page": 1, "bbox": [0, 0, 10, 10]} if span else None,
        )

    async def tier(self, s, sid):
        return (
            await s.execute(
                select(StatementTier.tier).where(StatementTier.statement_id == sid)
            )
        ).scalar()

    async def review(self, s, target, verdict):
        return await append(
            s,
            StatementIn(
                subject=f"statement:{target}",
                prop=Prop.REVIEW,
                value={"verdict": verdict, "reason": "test"},
                author_id=self.human.id,
            ),
            sign=self.passkey.sig,
        )


@unittest.skipUnless(POSTGRES, "ledger needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class LedgerDbTest(LedgerDbCase):
    async def test_proposal_review_and_verify(self):
        async with self.Session() as s, s.begin():
            st = await append(s, self.proposal(), sign=self.sign)
            self.assertEqual(await self.tier(s, st.id), Tier.UNCONFIRMED)
            await self.review(s, st.id, "accept")
            self.assertEqual(await self.tier(s, st.id), Tier.ACCEPTED)
            self.assertEqual(await verify_db(s), [])

    async def test_missing_span_is_rejected(self):
        async with self.Session() as s, s.begin():
            st = await append(s, self.proposal(span=False), sign=self.sign)
            self.assertEqual(await self.tier(s, st.id), Tier.REJECTED)

    async def test_agent_must_sign(self):
        async with self.Session() as s, s.begin():
            with self.assertRaises(Refused):
                await append(s, self.proposal())
            with self.assertRaises(Refused):
                await append(
                    s,
                    self.proposal(),
                    sign=lambda h: {"kind": "ed25519", "sig": "00" * 64},
                )

    async def test_agent_involved_role_refused(self):
        def involved(role):
            return StatementIn(
                subject=f"incident:{uuid.uuid4()}",
                prop=Prop.INVOLVED,
                value={"person": f"person:{uuid.uuid4()}", "role": role},
                author_id=self.agent.id,
                run_id=self.run.id,
            )

        async with self.Session() as s, s.begin():
            with self.assertRaisesRegex(Refused, "unspecified"):
                await append(s, involved("victim"), sign=self.sign)
            await append(s, involved("unspecified"), sign=self.sign)

    async def test_retry_rejected_and_duplicate(self):
        async with self.Session() as s, s.begin():
            p = self.proposal()
            first = await append(s, p, sign=self.sign)
            again = await append(s, p, sign=self.sign)
            self.assertEqual(first.id, again.id)
            other_run = StatementIn(
                **(p.__dict__ | {"run_id": None, "author_id": self.human.id})
            )
            with self.assertRaisesRegex(Refused, "pending"):
                await append(s, other_run)
            await self.review(s, first.id, "reject")
            with self.assertRaisesRegex(Refused, "rejected"):
                await append(s, other_run)

    async def test_retry_in_new_run_returns_existing(self):
        async with self.Session() as s, s.begin():
            first = await append(s, self.proposal(), sign=self.sign)
            run2 = Run(
                principal_id=self.agent.id, tool="extract", pubkey=self.run.pubkey
            )
            s.add(run2)
            await s.flush()
            retry = StatementIn(
                **(
                    self.proposal().__dict__
                    | {"subject": first.subject, "run_id": run2.id}
                )
            )
            self.assertEqual((await append(s, retry, sign=self.sign)).id, first.id)

    async def test_second_origin_corroborates(self):
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
            other = (
                await blobs.put(s, png("black"), "image/png", Config.BLOB_DIR)
            ).sha256
            await record_acquisition(
                s,
                blob_sha256=other,
                origin="channel:elsewhere",
                collector_id=self.human.id,
                at=datetime.now(UTC),
                context={},
                sensitivity="ordinary",
                doc_type="register",
            )
            first = await append(s, self.proposal(), sign=self.sign)
            second = await append(
                s,
                StatementIn(
                    **(
                        self.proposal().__dict__
                        | {"subject": first.subject, "span_blob": other}
                    )
                ),
                sign=self.sign,
            )
            self.assertNotEqual(first.id, second.id)
            self.assertEqual(await self.tier(s, first.id), Tier.CORROBORATED)
            self.assertEqual(await self.tier(s, second.id), Tier.CORROBORATED)

    async def test_direct_edit_blocked_then_caught(self):
        async with self.Session() as s, s.begin():
            st = await append(s, self.proposal(), sign=self.sign)
        async with self.Session() as s:
            with self.assertRaises(DBAPIError):
                async with s.begin():
                    await s.execute(
                        text("update statement set prop = 'father_name' where id = :i"),
                        {"i": st.id},
                    )
        async with self.Session() as s, s.begin():
            # An insider with superuser rights bypasses the trigger; verification catches it.
            await s.execute(text("set local session_replication_role = replica"))
            await s.execute(
                text("update statement set prop = 'father_name' where id = :i"),
                {"i": st.id},
            )
        async with self.Session() as s:
            problems = await verify_db(s)
            self.assertTrue(
                any(str(st.id) in p and "modified" in p for p in problems), problems
            )

    async def test_sealed_run_edit_caught(self):
        async with self.Session() as s, s.begin():
            run = await s.get(Run, self.run.id)
            run.model_served = "model-a"
            await seal_run(s, run, output_hash="a" * 64)
            self.assertEqual(await verify_db(s), [])
            run.model_served = "model-b"
            await s.flush()
            self.assertTrue(any("modified" in p for p in await verify_db(s)))

    async def test_queue_claims_each_run_once(self):
        tool = f"q-{uuid.uuid4().hex}"
        async with self.Session() as s, s.begin():
            s.add(Run(principal_id=self.agent.id, tool=tool))
        async with self.Session() as s, self.Session() as t:
            await s.begin()
            await t.begin()
            a = await queue.claim(s, [tool])
            b = await queue.claim(t, [tool])
            self.assertIsNotNone(a)
            self.assertIsNone(b)
            await s.rollback()
            await t.rollback()


if __name__ == "__main__":
    unittest.main()
