"""Watchlists (acceptance test 15) against a throwaway Postgres migrated to head."""

import json
import os
import re
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import httpx
import orjson
import stk
import stk.extensions as ext
from alembic import command
from quart_security import hash_password
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)
from stk.user.models import Role, User

from sealbook import blobs
from sealbook.app import create_app
from sealbook.ledger.contracts import Prop
from sealbook.ledger.models import Principal, Run, Statement
from sealbook.ledger.write import record_acquisition, verify_db
from sealbook.monitor.data import mask
from sealbook.runs.worker import work_once
from sealbook.settings import Config
from sealbook.watch.matcher import corroborate, match_run, matcher_agent

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
CASE = json.loads((Path(__file__).parent.parent / "acceptance/case.json").read_text())
DOCS = {d["key"]: d for d in CASE["documents"]}


class CorroborateTest(unittest.TestCase):
    def test_names_alone_never_match(self):
        p1, p2 = CASE["persons"]["P1"], CASE["persons"]["P2"]
        watched = {
            "name": [p1["spellings"][0]],
            "birth_date": ["1991"],
            "father_name": [p1["father_name"]],
        }
        self.assertIsNone(corroborate({"name": p2["spellings"]}, watched))
        self.assertIsNone(
            corroborate({"name": ["سامر"], "birth_date": ["1991"]}, watched)
        )
        hit = corroborate(
            {"name": ["Yusuf Khalid al Ali"], "birth_date": ["1991-03-14"]}, watched
        )
        self.assertEqual(hit["name"], ["Yusuf Khalid al Ali", p1["spellings"][0]])
        self.assertEqual(hit["fields"], [{"prop": "birth_date", "value": "1991-03-14"}])
        # A full date never equals a different year.
        self.assertIsNone(
            corroborate(
                {"name": p2["spellings"], "birth_date": ["1985-11-02"]}, watched
            )
        )


def document(key: str) -> tuple[str, list[dict]]:
    """Synthetic text for a case document and the extraction a model would return:
    one line per person, each expected value quoted in it."""
    lines, items = [], []
    for person in dict.fromkeys(e["person"] for e in DOCS[key]["expect"]):
        facts = [e for e in DOCS[key]["expect"] if e.get("person") == person]
        lines.append(" | ".join(f"{e['prop']}: {e['value']}" for e in facts))
        items += [
            {
                "mention": person,
                "prop": e["prop"],
                "value": {"text": e["value"], "lang": "ar"},
                # Twins share a name: the second person quotes its second occurrence.
                "occurrence": 1
                + sum(
                    line.count(e["value"]) for line in lines[:-1] if e["prop"] == "name"
                ),
                "evidence": ["handwritten_entry"],
            }
            for e in facts
        ]
    return "\n".join(lines), items


@unittest.skipUnless(POSTGRES, "watchlists need Postgres (set SQLALCHEMY_DATABASE_URI)")
class WatchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config("sealbook", url, quiet=True)
        command.upgrade(config, "heads")
        db_url = (
            make_url(url)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False)
        )
        self.enterContext(
            mock.patch.object(
                Config, "BLOB_DIR", self.enterContext(tempfile.TemporaryDirectory())
            )
        )
        self.enterContext(
            mock.patch.dict(
                os.environ, {"ROUTE_LOCAL_BASE_URL": "http://local.test/v1"}
            )
        )
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))

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
            analyst = (
                await s.execute(select(Role).where(Role.name == "analyst"))
            ).scalar_one()
            for email in ("analyst-1@example.com", "analyst-2@example.com"):
                user = User(
                    email=email,
                    name=email.split("@")[0],
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                user.roles.append(analyst)
                s.add(user)
            self.extractor = Principal(kind="agent", name="extractor")
            s.add(self.extractor)

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def ingest(self, key: str) -> dict[str, str]:
        """Queue an extract run for a case document, work the queue dry (extract,
        then the match run it enqueues) and return person key -> mention subject."""
        text, items = document(key)
        async with ext.async_session_factory() as s, s.begin():
            blob = await blobs.put(s, text.encode(), "text/plain", Config.BLOB_DIR)
            acq = await record_acquisition(
                s,
                blob_sha256=blob.sha256,
                origin=DOCS[key]["origin"],
                collector_id=self.extractor.id,
                at=datetime.now(UTC),
                context={"investigation": CASE["investigation"]},
                sensitivity=DOCS[key]["sensitivity"],
                doc_type=DOCS[key]["doc_type"],
            )
            s.add(
                Run(
                    principal_id=self.extractor.id,
                    tool="extract",
                    route="local",
                    model_requested="local/model",
                    inputs={"blobs": [blob.sha256], "acquisition": str(acq.id)},
                )
            )
        body = {
            "model": "local/model",
            "choices": [
                {"message": {"content": orjson.dumps({"statements": items}).decode()}}
            ],
        }
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        while await work_once(ext.async_session_factory, transport=transport):
            pass
        async with ext.async_session_factory() as s:
            runs = (await s.execute(select(Run.tool, Run.status, Run.error))).all()
            self.assertTrue(all(r.status == "done" for r in runs), runs)
            rows = (
                await s.execute(
                    select(Statement.subject)
                    .where(Statement.span_blob == blob.sha256, Statement.prop == "name")
                    .order_by(Statement.span["start"].as_integer())
                )
            ).scalars()
            return dict(
                zip(dict.fromkeys(i["mention"] for i in items), rows, strict=True)
            )

    async def login(self, client, email):
        async with self.app.app_context():
            token = create_agent_login_token(email, "/watch")
        self.assertEqual(
            (await client.get(f"/_test/login?token={token}")).status_code, 302
        )
        page = await (await client.get("/watch")).get_data(as_text=True)
        return {
            "X-CSRFToken": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        }

    async def proposals(self) -> int:
        async with ext.async_session_factory() as s:
            return await s.scalar(
                select(func.count()).where(Statement.prop == Prop.SAME_AS)
            )

    async def test_15_watchlist_match(self):
        register = await self.ingest("register")
        p1 = register["P1"]
        self.assertEqual(await self.proposals(), 0)

        watcher, other = self.app.test_client(), self.app.test_client()
        headers = await self.login(watcher, "analyst-1@example.com")
        await self.login(other, "analyst-2@example.com")
        refused = await watcher.post(
            "/api/watch", json={"item": {"person": "person:nope"}}, headers=headers
        )
        self.assertEqual(refused.status_code, 400)
        for _ in range(2):
            made = await watcher.post(
                "/api/watch", json={"item": {"person": p1}}, headers=headers
            )
            self.assertEqual(made.status_code, 201)

        transfer = await self.ingest("transfer")
        feed = (await (await watcher.get("/api/watch")).get_json())["items"]
        self.assertEqual([w["person"] for w in feed], [p1])
        scoped = await watcher.get("/api/watch?investigation=case-nowhere")
        self.assertEqual((await scoped.get_json())["items"], [])
        [proposal] = feed[0]["proposals"]
        self.assertEqual(proposal["mention"], transfer["P1"])
        self.assertEqual(proposal["tier"], "unconfirmed")
        self.assertEqual(
            [f["prop"] for f in proposal["evidence"]["fields"]],
            ["birth_date", "detention_branch"],
        )
        # Both documents are sensitive: names and fields are masked by default and
        # reveal only through the logged monitor endpoint.
        names = ["يوسف خالد علي", "يوسف خالد العلي"]
        evidence = proposal["evidence"]
        cells = [*feed[0]["names"], *evidence["name"], *evidence["fields"]]
        self.assertTrue(all(c["masked"] for c in cells))
        self.assertEqual(
            [c["display"] for c in evidence["name"]], [mask(n) for n in names]
        )
        page = await (await watcher.get("/watch")).get_data(as_text=True)
        for n in names:
            self.assertNotIn(n, page)
        revealed = await watcher.post(
            "/api/monitor/reveal",
            json={"item": {"statement": evidence["name"][0]["statement"]}},
            headers=headers,
        )
        self.assertEqual((await revealed.get_json())["display"], names[0])
        async with ext.async_session_factory() as s:
            self.assertEqual(
                await s.scalar(
                    select(func.count()).where(
                        Statement.prop == Prop.ACCESS,
                        Statement.subject
                        == f"statement:{evidence['name'][0]['statement']}",
                    )
                ),
                1,
            )
        self.assertIn("/review?statement=", page)

        # The twin P2 shares the name but no field; re-running is idempotent.
        async with ext.async_session_factory() as s, s.begin():
            names = (
                await s.execute(select(Statement).where(Statement.prop == "name"))
            ).scalars()
            names = list(names)
            self.assertIn(register["P2"], {n.subject for n in names})
            acq = (
                await s.execute(select(Run.inputs).where(Run.tool == "match"))
            ).scalar()
            s.add(
                match_run(
                    await matcher_agent(s),
                    acq["acquisition"],
                    acq["blobs"][0],
                    [n.id for n in names],
                )
            )
        while await work_once(ext.async_session_factory):
            pass
        self.assertEqual(await self.proposals(), 1)

        self.assertEqual(
            (await (await other.get("/api/watch")).get_json())["items"], []
        )
        gone = await watcher.delete(f"/api/watch/{p1}", headers=headers)
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(
            (await (await watcher.get("/api/watch")).get_json())["items"], []
        )
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
