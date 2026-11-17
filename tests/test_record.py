"""The record status behind the seal chip: counts, witness state per batch and the
reader's last signed decision, hashes and counts only; receipts download by id."""

import unittest
import uuid
from datetime import datetime
from unittest import mock

import stk
import stk.extensions as ext
from alembic import command
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import build_alembic_config, throwaway_database_url
from stk.user.models import User

from sealbook.anchor.models import Anchor
from sealbook.anchor.service import close_batch
from sealbook.app import create_app
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.receipts import OTS_HEAD
from sealbook.ledger.write import append
from sealbook.settings import Config
from tests.passkey import attester

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
REASON = "synthetic reason text"


@unittest.skipUnless(
    POSTGRES, "the record needs Postgres (set SQLALCHEMY_DATABASE_URI)"
)
class RecordTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        command.upgrade(build_alembic_config("sealbook", url, quiet=True), "heads")
        self.enterContext(mock.patch.object(Config, "PARTNER_ROOT_DIRS", []))
        db_url = (
            make_url(url)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False)
        )

        class TestConfig(stk.TestingConfig, Config):
            SQLALCHEMY_DATABASE_URI = db_url
            TESTING = True
            SESSION_TYPE = None
            STK_ENV = "testing"
            STK_ENABLE_AGENT_LOGIN = True
            STK_AGENT_LOGIN_MAX_TTL_SECONDS = 60

        self.app = create_app(TestConfig)

    async def asyncSetUp(self):
        async with ext.async_session_factory() as s, s.begin():
            reviewer, passkey = await attester(s, "reviewer")
            user = await s.get(User, reviewer.user_id)
            user.confirmed_at = datetime.now()
            self.email = user.email
            for _ in range(2):
                claim = await append(
                    s,
                    StatementIn(
                        subject=f"person:{uuid.uuid4()}",
                        prop=Prop.NAME,
                        value={"text": REASON, "lang": "en"},
                        author_id=reviewer.id,
                    ),
                    sign=passkey.sig,
                )
                await append(
                    s,
                    StatementIn(
                        subject=f"statement:{claim.id}",
                        prop=Prop.REVIEW,
                        value={"verdict": "accept", "reason": REASON},
                        author_id=reviewer.id,
                    ),
                    sign=passkey.sig,
                )

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def client(self):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(self.email, "/todo")
        await client.get(f"/_test/login?token={token}")
        return client

    async def status(self, client):
        r = await client.get("/api/record/status")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(REASON, await r.get_data(as_text=True))
        return await r.get_json()

    async def test_signed_out_is_refused(self):
        r = await self.app.test_client().get("/api/record/status")
        self.assertIn(r.status_code, (302, 401))

    async def test_before_any_batch(self):
        p = await self.status(await self.client())
        self.assertEqual(p["batches"], 0)
        self.assertIsNone(p["latest"])
        self.assertEqual(p["recent"], [])
        self.assertGreater(p["entries"], 0)
        self.assertEqual(p["unanchored"], p["entries"])
        self.assertFalse(p["decision"]["anchored"])
        self.assertIsNotNone(p["decision"]["seq"])

    async def test_witnesses_and_receipts(self):
        async with ext.async_session_factory() as s, s.begin():
            batch = await close_batch(s)
            ots = Anchor(
                batch_id=batch.id,
                kind="ots",
                target="https://a.cal.test,https://b.cal.test",
                receipt=OTS_HEAD + b"pending only",
            )
            tsa = Anchor(
                batch_id=batch.id,
                kind="tsa",
                target="https://tsa.test/tsr",
                receipt=b"tsr bytes",
            )
            s.add_all([ots, tsa])
            s.add(
                Anchor(batch_id=batch.id, kind="partner", target="/srv/p", error="down")
            )
            await s.flush()
            root, ots_id, tsa_id = batch.root, ots.id, tsa.id

        me = await self.client()
        p = await self.status(me)
        self.assertEqual((p["batches"], p["unanchored"]), (1, 0))
        latest = p["latest"]
        self.assertEqual(latest["root"], root)
        states = {w["kind"]: (w["state"], w["id"]) for w in latest["witnesses"]}
        self.assertEqual(
            states,
            {
                "ots": ("pending", ots_id),
                "tsa": ("held", tsa_id),
                "partner": ("failed", None),
            },
        )
        ots_row = latest["witnesses"][0]
        self.assertEqual(ots_row["label"], "Receipt held, waiting for Bitcoin")
        self.assertEqual(ots_row["where"], "a.cal.test, b.cal.test")
        self.assertIsNone(latest["witnesses"][2]["where"], "no server path")
        self.assertEqual((latest["held"], latest["failed"]), (2, 1))
        self.assertEqual(p["recent"][0]["held"], 2)
        self.assertTrue(p["decision"]["anchored"])

        r = await me.get(f"/api/record/receipt/{ots_id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(await r.get_data(), OTS_HEAD + b"pending only")
        self.assertIn(f'filename="{root}.ots"', r.headers["Content-Disposition"])
        self.assertEqual((await me.get("/api/record/receipt/999999")).status_code, 404)
