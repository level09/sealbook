"""Desk read endpoints against a throwaway Postgres database migrated to head, with
synthetic rows written straight to the tables."""

import random
import unittest
import uuid
from datetime import UTC, datetime, timedelta

from quart_security import hash_password
from sqlalchemy import insert, select
from sqlalchemy.engine import make_url

import stk.extensions as ext
from alembic import command
from stk.agent_login import create_agent_login_token
from stk.app import create_app
from stk.collaboration.models import Assignment, Notification
from stk.desk.data import title
from stk.discovery.models import FindingKey
from stk.ledger.contracts import Prop, Tier
from stk.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Principal,
    Run,
    Statement,
    StatementTier,
)
from stk.migrations import (
    build_alembic_config,
    get_database_url,
    throwaway_database_url,
)
from stk.settings import Config
from stk.user.models import Role, User

POSTGRES = make_url(get_database_url()).get_backend_name() == "postgresql"
X, Y = "case-desk-test-x", "case-desk-test-y"


def hexs(n=64):
    return f"{random.getrandbits(n * 4):0{n}x}"


class TitleTest(unittest.TestCase):
    def test_bare_word_files_take_the_document_kind(self):
        self.assertEqual(
            title(None, "list", "detention_register"), "Detention register"
        )
        self.assertEqual(title(None, "jdeideh_2017.mp4", "video"), "jdeideh_2017.mp4")
        self.assertEqual(
            title("Appeal for Lina", "post", "web_post"), "Appeal for Lina"
        )
        self.assertEqual(title(None, "note", "unknown"), "note")
        self.assertEqual(title(None, None, "unknown"), "Untitled source")


@unittest.skipUnless(POSTGRES, "the desk needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class DeskTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(throwaway_database_url(sync=True))
        config = build_alembic_config()
        config.config_file_name = None
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        db_url = (
            make_url(url)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False)
        )

        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = db_url
            TESTING = True
            SESSION_TYPE = None
            STK_ENV = "testing"
            STK_ENABLE_AGENT_LOGIN = True
            STK_AGENT_LOGIN_MAX_TTL_SECONDS = 60

        self.app = create_app(TestConfig)

    async def asyncSetUp(self):
        now = datetime.now(UTC)
        self.now = now
        async with ext.async_session_factory() as s, s.begin():
            analyst = (
                await s.execute(select(Role).where(Role.name == "analyst"))
            ).scalar_one()
            ids = {}
            for email, roles in (
                ("me@example.com", [analyst]),
                ("colleague@example.com", [analyst]),
                ("nobody@example.com", []),
            ):
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
            self.me = ids["me@example.com"]
            me, other, agent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            await s.execute(
                insert(Principal),
                [
                    {"id": me, "kind": "human", "name": "me", "user_id": self.me},
                    {
                        "id": other,
                        "kind": "human",
                        "name": "colleague",
                        "user_id": ids["colleague@example.com"],
                    },
                    {"id": agent, "kind": "agent", "name": "extractor"},
                ],
            )
            self.list_sha, self.photo_sha, self.y_sha = hexs(), hexs(), hexs()
            await s.execute(
                insert(Blob),
                [
                    {
                        "sha256": sha,
                        "md5": hexs(32),
                        "size": 10,
                        "mime": mime,
                        "storage_ref": "synthetic",
                        "created_at": now,
                    }
                    for sha, mime in (
                        (self.list_sha, "image/png"),
                        (self.photo_sha, "image/jpeg"),
                        (self.y_sha, "text/plain"),
                    )
                ],
            )
            await s.execute(
                insert(Acquisition),
                [
                    {
                        "blob_sha256": self.list_sha,
                        "origin": "synthetic:desk/1",
                        "collector_id": other,
                        "at": now - timedelta(hours=5),
                        "context": {"file": "list", "investigation": X},
                        "sensitivity": "sensitive",
                        "doc_type": "detention_register",
                    },
                    {
                        "blob_sha256": self.photo_sha,
                        "origin": "synthetic:desk/2",
                        "collector_id": other,
                        "at": now - timedelta(minutes=30),
                        "context": {"file": "checkpoint.jpg", "investigation": X},
                        "sensitivity": "ordinary",
                        "doc_type": "photo",
                    },
                    {
                        "blob_sha256": self.y_sha,
                        "origin": "synthetic:desk/3",
                        "collector_id": other,
                        "at": now - timedelta(minutes=20),
                        "context": {"file": "elsewhere.txt", "investigation": Y},
                        "sensitivity": "ordinary",
                        "doc_type": "note",
                    },
                ],
            )

            def st(prop, author, at, span=None, subject=None, commitment=None):
                return {
                    "id": uuid.uuid4(),
                    "subject": subject or f"person:{uuid.uuid4()}",
                    "prop": prop,
                    "commitment": commitment or hexs(),
                    "span_blob": span,
                    "author_id": author,
                    "recorded_at": at,
                    "hash": hexs(),
                    "evidence": [],
                }

            ago = lambda h: now - timedelta(hours=h)  # noqa: E731
            claim = st(Prop.NAME, agent, ago(1), self.list_sha)
            request_commit = hexs()
            request = st(
                Prop.REVIEW_REQUEST,
                other,
                ago(0.5),
                subject=f"statement:{claim['id']}",
                commitment=request_commit,
            )
            rows = [
                # The last thing I did before this sign-in: the Desk's "since".
                st(Prop.ACCESS, me, ago(2), subject=f"blob:{self.list_sha}"),
                claim,
                st(Prop.NAME, agent, ago(1), self.list_sha),
                st(Prop.NAME, agent, ago(3), self.list_sha),  # before I left
                st(Prop.NAME, me, ago(1), self.list_sha),  # mine
                st(Prop.NAME, agent, ago(1), self.y_sha),  # other case
                request,
            ]
            await s.execute(insert(Statement), rows)
            await s.execute(
                insert(StatementTier),
                [
                    {"statement_id": r["id"], "tier": Tier.UNCONFIRMED, "reason": "t"}
                    for r in rows
                    if r["prop"] == Prop.NAME
                ],
            )
            await s.execute(
                insert(Payload),
                [
                    {
                        "commitment": r["commitment"],
                        "salt": random.randbytes(32),
                        "value": {"reviewer": self.me, "reason": "check the row"}
                        if r is request
                        else {"text": "synthetic"},
                        "value_key": hexs(),
                        "idem_key": hexs(),
                    }
                    for r in rows
                ],
            )
            run = uuid.uuid4()
            await s.execute(
                insert(Run),
                [
                    {
                        "id": run,
                        "principal_id": agent,
                        "tool": "discover",
                        "inputs": {"investigation": X},
                        "status": "done",
                    }
                ],
            )
            await s.execute(
                insert(FindingKey),
                [
                    {
                        "key": hexs(),
                        "kind": "shared_presence",
                        "investigation": X,
                        "state": "open",
                        "rank": 25,
                        "run_id": run,
                        "members": [str(claim["id"])],
                        "features": {"sources": 2, "days": 3, "overlap": 0.5},
                        "notes": [],
                        "created_at": now,
                    }
                ],
            )
            s.add_all(
                [
                    Notification(
                        user_id=self.me,
                        kind="mention",
                        title="You were mentioned in a review",
                        subtitle="Request x",
                        link=f"/review?request={request['id']}",
                        subject=f"statement:{request['id']}",
                    ),
                    Assignment(
                        investigation=X,
                        subject=f"blob:{self.photo_sha}",
                        assignee_id=self.me,
                        assigned_by=ids["colleague@example.com"],
                    ),
                ]
            )

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

    async def get(self, client, path, **params):
        r = await client.get(f"/api/desk/{path}", query_string=params)
        body = await r.get_json()
        self.assertEqual(r.status_code, 200, body)
        return body

    async def test_access_and_home(self):
        anon = self.app.test_client()
        home = await anon.get("/")
        self.assertTrue(home.headers["Location"].endswith("/login"))
        self.assertNotEqual((await anon.get("/api/desk/needs")).status_code, 200)
        nobody = await self.client("nobody@example.com")
        for path in ("/desk", "/api/desk/changes", "/api/desk/recent"):
            self.assertEqual((await nobody.get(path)).status_code, 403, path)
        me = await self.client()
        self.assertTrue((await me.get("/")).headers["Location"].endswith("/desk"))
        self.assertEqual((await me.get("/desk")).status_code, 200)
        bad = await me.get("/api/desk/needs", query_string={"per_page": 500})
        self.assertEqual(bad.status_code, 400)

    async def test_since_you_left(self):
        body = await self.get(await self.client(), "changes", investigation=X)
        self.assertEqual(body["basis"], "access")
        groups = {(r["who"]["name"], r["what"]): r for r in body["items"]}
        # Two agent claims after I left; the earlier one, mine and case Y's are out.
        names = groups[("extractor", "name")]
        self.assertEqual((names["n"], names["items"]), (2, 1))
        self.assertEqual(names["sources"][0]["title"], "Detention register")
        # A request is placed in the case through the claim it is about.
        self.assertIn(("colleague", "review_request"), groups)
        self.assertEqual(groups[("colleague", "acquisition")]["items"], 1)
        self.assertNotIn("me", {r["who"]["name"] for r in body["items"]})
        self.assertEqual(body["total"], len(body["items"]))

    async def test_needs_you_pages_across_lists(self):
        c = await self.client()
        body = await self.get(c, "needs", investigation=X)
        self.assertEqual(
            [i["kind"] for i in body["items"]], ["review", "mention", "assignment"]
        )
        self.assertEqual((body["total"], body["asks"]), (3, 1))
        review = body["items"][0]
        self.assertEqual((review["status"], review["prop"]), ("asks you", "name"))
        self.assertEqual(body["items"][2]["source"]["title"], "checkpoint.jpg")
        page2 = await self.get(c, "needs", investigation=X, per_page=1, page=2)
        self.assertEqual([i["kind"] for i in page2["items"]], ["mention"])
        other = await self.get(c, "needs", investigation=Y)
        self.assertEqual(other["total"], 0)

    async def test_findings_and_recent(self):
        c = await self.client()
        found = await self.get(c, "findings", investigation=X)
        self.assertEqual((found["total"], found["ran"]), (1, True))
        self.assertEqual(found["items"][0]["features"]["sources"], 2)
        none = await self.get(c, "findings", investigation=Y)
        self.assertEqual((none["total"], none["ran"]), (0, False))
        recent = await self.get(c, "recent", investigation=X, per_page=10)
        rows = {r["sha256"]: r for r in recent["items"]}
        self.assertEqual(set(rows), {self.list_sha, self.photo_sha})
        self.assertEqual(rows[self.list_sha]["title"], "Detention register")
        self.assertEqual(rows[self.photo_sha]["investigation"], X)
        # Acquired after I left is new; before, not.
        self.assertTrue(rows[self.photo_sha]["new"])
        self.assertFalse(rows[self.list_sha]["new"])
        # A sensitive tile is locked until a reveal, so the client never asks for it.
        for r in rows.values():
            self.assertEqual(r["revealed"], not r["sensitive"])
        everything = await self.get(c, "recent")
        self.assertEqual(everything["total"], 3)
