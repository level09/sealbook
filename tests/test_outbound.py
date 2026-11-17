"""What left this server: the joined log over stored rows, and who sees which rows."""

import tempfile
import unittest
from datetime import UTC, datetime
from unittest import mock

import stk
import stk.extensions as ext
from alembic import command
from quart_security import hash_password
from sqlalchemy import select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import build_alembic_config, throwaway_database_url
from stk.user.models import Role, User

from sealbook import blobs
from sealbook.anchor.models import Anchor, Batch
from sealbook.app import create_app
from sealbook.cases.models import Case
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Principal, Run
from sealbook.ledger.write import append, record_acquisition
from sealbook.settings import Config

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
CASE = "case-outbound-test"
HIDDEN = "case-outbound-unlisted"


@unittest.skipUnless(POSTGRES, "the outbound log needs Postgres")
class OutboundTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        command.upgrade(build_alembic_config("sealbook", url, quiet=True), "heads")
        self.enterContext(
            mock.patch.object(
                Config, "BLOB_DIR", self.enterContext(tempfile.TemporaryDirectory())
            )
        )
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
        now = datetime.now(UTC)
        async with ext.async_session_factory() as s, s.begin():
            roles = {r.name: r for r in await s.scalars(select(Role))}
            roles.setdefault("admin", Role(name="admin"))  # as `stk install` makes it
            for email, names in (
                ("analyst@example.com", ["analyst"]),
                ("admin@example.com", ["admin"]),
                ("no@example.com", []),
            ):
                user = User(
                    email=email,
                    name=email.split("@")[0],
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                user.roles.extend(roles[n] for n in names)
                s.add(user)
            s.add(Case(slug=CASE, name="Outbound test case"))
            agent = Principal(kind="agent", name="extractor")
            person = Principal(kind="human", name="analyst-2")
            s.add_all([agent, person])
            await s.flush()

            async def source(case, title, sensitivity, data):
                sha = (await blobs.put(s, data, "image/png", Config.BLOB_DIR)).sha256
                return await record_acquisition(
                    s,
                    blob_sha256=sha,
                    origin="device:test",
                    collector_id=person.id,
                    at=now,
                    context={"investigation": case, "title": title},
                    sensitivity=sensitivity,
                    doc_type="register",
                )

            roster = await source(CASE, "Synthetic roster", "sensitive", b"one")
            unlisted = await source(HIDDEN, "Unlisted register", "ordinary", b"two")

            def run(acq, tool, route, **kw):
                return Run(
                    principal_id=agent.id,
                    tool=tool,
                    route=route,
                    inputs={"blobs": [acq.blob_sha256], "acquisition": str(acq.id)},
                    created_at=now,
                    **kw,
                )

            s.add_all(
                [
                    run(
                        roster,
                        "ocr",
                        "cloud-zdr",
                        status="done",
                        finished_at=now,
                        endpoint="https://openrouter.ai/api/v1/chat/completions",
                        model_requested="vendor/model-x",
                        model_served="vendor/model-x",
                        provider_served="DeepInfra",
                    ),
                    run(roster, "transcribe", "local", status="done", finished_at=now),
                    run(
                        roster,
                        "extract",
                        "cloud",
                        status="failed",
                        error="RouteError: route cloud not allowed for "
                        "extract/register/sensitive (allowed: local, cloud-zdr)",
                    ),
                    run(roster, "hash", "local", status="done", finished_at=now),
                    # A finished run with no route recorded: it counts as read here.
                    run(roster, "assess", None, status="done", finished_at=now),
                    run(roster, "ocr", "cloud-zdr", status="queued"),
                    run(
                        unlisted, "assess", "cloud-zdr", status="done", finished_at=now
                    ),
                ]
            )
            batch = Batch(first_seq=1, last_seq=4, size=4, root="ab" * 32, at=now)
            s.add(batch)
            await s.flush()
            s.add(
                Anchor(
                    batch_id=batch.id,
                    kind="tsa",
                    target="https://freetsa.org/tsr",
                    receipt=b"r",
                    at=now,
                )
            )
            await append(
                s,
                StatementIn(
                    subject=f"investigation:{CASE}",
                    prop=Prop.ACCESS,
                    value={
                        "action": "research",
                        "provider": "brave",
                        "kind": "web",
                        "query": "synthetic query words",
                        "investigation": CASE,
                        "people": [],
                        "at": now.isoformat(),
                    },
                    author_id=person.id,
                ),
            )

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def client(self, email):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(email, "/outbound")
        await client.get(f"/_test/login?token={token}")
        return client

    async def get(self, client, query=""):
        r = await client.get(f"/api/outbound{query}")
        self.assertEqual(r.status_code, 200, await r.get_data(as_text=True))
        return await r.get_json(), await r.get_data(as_text=True)

    async def test_log_joins_stored_rows_and_respects_case_access(self):
        analyst = await self.client("analyst@example.com")
        self.assertEqual((await analyst.get("/outbound")).status_code, 200)
        body, raw = await self.get(analyst)
        what = [i["what"] for i in body["items"]]
        # Sent, read here, refused, a search and a timestamp; no hash or queued run.
        self.assertEqual(body["total"], 6, what)
        self.assertIn("A scanned page, to read its text", what)
        self.assertIn("Nothing. Read on this server.", what)
        self.assertIn("A web search", what)
        self.assertIn("A timestamp request", what)
        refused = next(i for i in body["items"] if i["what"].startswith("Refused"))
        self.assertEqual(refused["tone"], "warn")
        sent = next(i for i in body["items"] if i["what"].startswith("A scanned"))
        self.assertEqual(sent["to"], "Hosted model that keeps no data")
        self.assertEqual(sent["source"]["title"], "Synthetic roster")
        self.assertTrue(sent["source"]["sensitive"])
        self.assertIn("allows this route", sent["why"])
        search = next(i for i in body["items"] if i["what"] == "A web search")
        self.assertIn("analyst-2", search["why"])
        # No evidence values: neither the search words nor a source outside access.
        self.assertNotIn("synthetic query words", raw)
        self.assertNotIn("Unlisted register", raw)
        self.assertEqual(body["today"]["refused"], 1)
        self.assertNotIn(None, body["today"]["models"])
        self.assertEqual(body["today"]["timestamp"], 1)

        only, _ = await self.get(analyst, "?kind=timestamp")
        self.assertEqual([i["what"] for i in only["items"]], ["A timestamp request"])
        sens, _ = await self.get(analyst, "?sensitive=1")
        self.assertEqual(sens["total"], 4)

        admin = await self.client("admin@example.com")
        everything, raw = await self.get(admin)
        self.assertEqual(everything["total"], 7)
        self.assertIn("Unlisted register", raw)

        nobody = await self.client("no@example.com")
        self.assertEqual((await nobody.get("/api/outbound")).status_code, 403)
        self.assertEqual((await nobody.get("/outbound")).status_code, 403)
