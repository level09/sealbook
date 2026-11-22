"""A case's Overview API against a throwaway Postgres database migrated to head, with
synthetic rows written straight to the tables."""

import random
import unittest
import uuid
from datetime import UTC, datetime, timedelta

import stk
import stk.extensions as ext
from alembic import command
from quart_security import hash_password
from sqlalchemy import insert, select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import build_alembic_config, throwaway_database_url
from stk.user.models import Role, User

from sealbook.agents import contradictions
from sealbook.app import create_app
from sealbook.cases import conclusions
from sealbook.cases.models import Case
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Principal,
    Run,
    Statement,
    StatementTier,
)
from sealbook.settings import Config

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
X, Y = "case-overview-test-x", "case-overview-test-y"


def hexs(n=64):
    return f"{random.getrandbits(n * 4):0{n}x}"


@unittest.skipUnless(POSTGRES, "the overview needs Postgres")
class CaseOverviewTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        command.upgrade(build_alembic_config("sealbook", url, quiet=True), "heads")
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
            analyst = (
                await s.execute(select(Role).where(Role.name == "analyst"))
            ).scalar_one()
            ids = {}
            for email, roles in (("me@example.com", [analyst]), ("no@example.com", [])):
                user = User(
                    email=email,
                    name=email.split("@")[0],
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                user.roles.extend(roles)
                s.add(user)
                await s.flush()
                ids[email] = user.id
            me, agent = uuid.uuid4(), uuid.uuid4()
            await s.execute(
                insert(Principal),
                [
                    {
                        "id": me,
                        "kind": "human",
                        "name": "me",
                        "user_id": ids["me@example.com"],
                    },
                    {"id": agent, "kind": "agent", "name": "extractor"},
                ],
            )
            s.add_all(
                [
                    Case(slug=X, name="Case X"),
                    Case(slug=Y, name="Case Y"),
                    Case(slug="case-never-there", name="Never there"),
                ]
            )
            self.x, self.y = hexs(), hexs()
            await s.execute(
                insert(Blob),
                [
                    {
                        "sha256": sha,
                        "md5": hexs(32),
                        "size": 10,
                        "mime": "text/plain",
                        "storage_ref": "synthetic",
                        "created_at": now,
                    }
                    for sha in (self.x, self.y)
                ],
            )
            await s.execute(
                insert(Acquisition),
                [
                    {
                        "blob_sha256": sha,
                        "origin": f"synthetic:overview/{case}",
                        "collector_id": agent,
                        "at": now - timedelta(hours=1),
                        "context": {"file": f"{case}.txt", "investigation": case},
                        "sensitivity": "ordinary",
                        "doc_type": "note",
                    }
                    for sha, case in ((self.x, X), (self.y, Y))
                ],
            )
            person, other = f"person:{uuid.uuid4()}", f"person:{uuid.uuid4()}"
            facts = [
                (self.x, Prop.NAME, Tier.ACCEPTED),
                (self.x, Prop.BIRTH_DATE, Tier.UNCONFIRMED),
                (self.x, Prop.FATHER_NAME, Tier.REJECTED),
                (self.x, Prop.SAME_AS, Tier.ACCEPTED),  # identity link, not a fact
                (self.y, Prop.NAME, Tier.CORROBORATED),  # case Y
                # Case Y also has what no person holds: a post's report and a match.
                (self.y, Prop.REPORTS, Tier.ACCEPTED),
                (self.y, Prop.MEDIA_MATCH, Tier.UNCONFIRMED),
            ]
            rows = [
                {
                    "id": uuid.uuid4(),
                    "subject": f"blob:{sha}"
                    if prop in (Prop.REPORTS, Prop.MEDIA_MATCH)
                    else other
                    if sha == self.y
                    else person,
                    "prop": prop,
                    "commitment": hexs(),
                    "span_blob": None if prop == Prop.MEDIA_MATCH else sha,
                    "author_id": agent,
                    "recorded_at": now - timedelta(minutes=30),
                    "hash": hexs(),
                    "evidence": [],
                }
                for sha, prop, _ in facts
            ]
            await s.execute(insert(Statement), rows)
            await s.execute(
                insert(StatementTier),
                [
                    {"statement_id": r["id"], "tier": tier, "reason": "t"}
                    for r, (_, _, tier) in zip(rows, facts, strict=True)
                ],
            )
            await s.execute(
                insert(Payload),
                [
                    {
                        "commitment": r["commitment"],
                        "salt": random.randbytes(32),
                        "value": {"person": str(uuid.uuid4())}
                        if r["prop"] == Prop.SAME_AS
                        else {"text": "synthetic"},
                        "value_key": hexs(),
                        "idem_key": hexs(),
                    }
                    for r in rows
                ],
            )
            # The contradiction hunter ran for me in case X after I left; in Y too.
            await s.execute(
                insert(Run),
                [
                    {
                        "principal_id": agent,
                        "tool": contradictions.TOOL,
                        "inputs": {
                            "investigation": case,
                            "for_user": ids["me@example.com"],
                        },
                        "status": "failed",
                        "error": "synthetic",
                        "created_at": now - timedelta(minutes=5),
                    }
                    for case in (X, Y)
                ],
            )
        self.me = ids["me@example.com"]

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def client(self, email="me@example.com"):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(email, "/desk")
        self.assertEqual(
            (await client.get(f"/_test/login?token={token}")).status_code, 302
        )
        return client

    async def test_overview(self):
        nobody = await self.client("no@example.com")
        for path in ("/cases/overview", f"/api/cases/overview?investigation={X}"):
            self.assertEqual((await nobody.get(path)).status_code, 403, path)
        me = await self.client()
        self.assertEqual((await me.get("/cases/overview")).status_code, 200)
        missing = await me.get("/api/cases/overview?investigation=no-such-case")
        self.assertEqual(missing.status_code, 404)

        r = await me.get(f"/api/cases/overview?investigation={X}")
        body = await r.get_json()
        self.assertEqual(r.status_code, 200, body)
        stands = body["stands"]
        self.assertIsNone(body["film"], "only the four case studies are in the film")
        study = await me.get("/api/cases/overview?investigation=case-never-there")
        film = (await study.get_json())["film"]
        self.assertEqual(film["label"], "The witness who was never there")
        self.assertGreater(film["length"], 0)
        # Everything the case holds by kind, as a conclusion may cite it: the
        # rejected claim and case Y left out, the accepted link counted.
        none = {"accepted": 0, "unconfirmed": 0}
        self.assertEqual(
            stands["items"],
            {
                "claims": {"accepted": 1, "corroborated": 0, "unconfirmed": 1},
                "links": {"accepted": 1, "unconfirmed": 0},
                "matches": none,
                "findings": none,
            },
        )
        async with ext.async_session_factory() as s:
            citeable = await conclusions.citeable(s, X)
        self.assertEqual(
            [len(citeable[k]) for k in ("claims", "links", "findings")], [1, 1, 0]
        )
        # A case with no people still says what it holds.
        r = await me.get(f"/api/cases/overview?investigation={Y}")
        items = (await r.get_json())["stands"]["items"]
        self.assertEqual(
            items["claims"], {"accepted": 1, "corroborated": 1, "unconfirmed": 0}
        )
        self.assertEqual(items["matches"], {"accepted": 0, "unconfirmed": 1})
        self.assertEqual(stands["sources"]["total"], 1)
        self.assertIsNone(stands["anchored"])
        self.assertEqual((stands["disagreements"], stands["findings"]), (0, 0))
        # Since I left (a week ago, no visit or sign-in before): case X's run only.
        agents = body["away"]["agents"]
        self.assertEqual([a["key"] for a in agents], ["contradictions"])
        self.assertTrue(agents[0]["failed"])
        self.assertEqual(agents[0]["href"], "/people")


class SourcesReadTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_read_source_waiting_only_on_search_indexing_is_read(self):
        """Replayed cases leave their embeddings queued: the Overview read "0 of 52
        sources read, 52 in progress" while every source page said it was read."""
        from unittest import mock

        from sealbook.cases import overview

        def step(key, state, **kw):
            return {"key": key, "state": state, "words": key} | kw

        steps = {
            "a": [step("read", "done"), step("indexed", "queued", side=True)],
            "b": [step("read", "queued")],
        }
        db = mock.AsyncMock()
        db.scalars.return_value = ["a", "b"]
        with mock.patch.object(
            overview.batch, "source_steps", mock.AsyncMock(return_value=steps)
        ):
            got = await overview.sources(db, "case-x")
        self.assertEqual((got["read"], got["working"]), (1, 1))
