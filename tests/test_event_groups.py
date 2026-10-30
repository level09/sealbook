"""Same-event groups in Discover: open same_event proposals joined into one candidate
incident, accepted or rejected in one signed gesture, against a throwaway Postgres
with three synthetic posts."""

import re
import tempfile
import unittest
import uuid
from datetime import UTC, date, datetime
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
from sealbook.app import create_app
from sealbook.ledger.contracts import Prop, StatementIn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from sealbook.ledger.models import Payload, Principal, Run, Statement, StatementTier
from sealbook.ledger.write import append, record_acquisition, verify_db
from sealbook.places import normalise
from sealbook.places.models import Place, PlaceName, point
from sealbook.settings import Config
from tests.passkey import SoftPasskey

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
CASE = "case-e"
POSTS = [
    ("Shelling hit the market in Tel Ward last night.", "2031-03-02", "ordinary"),
    ("Tel Ward: the market was shelled on 1 March.", "2031-03-02", "ordinary"),
    ("Witness says shells fell on Tel Ward market.", "2031-03-03", "sensitive"),
]


@unittest.skipUnless(POSTGRES, "groups need Postgres (set SQLALCHEMY_DATABASE_URI)")
class EventGroupsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict("stk.websocket._clients", {}, clear=True))
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
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
            SECURITY_WAN_RP_ID = None
            SECURITY_WAN_EXPECTED_ORIGIN = None

        self.app = create_app(TestConfig)
        blob_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(Config, "BLOB_DIR", blob_dir))

    async def asyncSetUp(self):
        self.passkey = SoftPasskey()
        async with ext.async_session_factory() as s, s.begin():
            role = await s.scalar(select(Role).where(Role.name == "analyst"))
            user = User(
                email="analyst@example.com",
                name="analyst",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            user.roles.append(role)
            s.add(user)
            await s.flush()
            s.add(self.passkey.register(user))
            clerk = Principal(kind="human", name="clerk")
            agent = Principal(kind="agent", name="linker")
            place = Place(
                name_ar="تل ورد",
                name_en="Tel Ward",
                kind="populated",
                geom=point(36, 34),
                geo_source="analyst",
                source="person",
            )
            s.add_all([clerk, agent, place])
            await s.flush()
            s.add(
                PlaceName(
                    place_id=place.id,
                    name="Tel Ward",
                    norm=normalise("Tel Ward"),
                    lang="en",
                    kind="official",
                    source="person",
                )
            )
            self.place = place.id
            key = Ed25519PrivateKey.generate()
            run = Run(
                principal_id=agent.id,
                tool="link",
                route="local",
                inputs={"investigation": CASE},
                pubkey=key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
            )
            s.add(run)
            await s.flush()

            def sign(h):
                return {"kind": "ed25519", "sig": key.sign(h.encode()).hex()}

            self.shas, self.reports = [], []
            for text, posted, sensitivity in POSTS:
                sha = (
                    await blobs.put(s, text.encode(), "text/plain", Config.BLOB_DIR)
                ).sha256
                await record_acquisition(
                    s,
                    blob_sha256=sha,
                    origin="web",
                    collector_id=clerk.id,
                    at=datetime.now(UTC),
                    context={
                        "investigation": CASE,
                        "file": f"post-{len(self.shas) + 1}.txt",
                    },
                    sensitivity=sensitivity,
                    doc_type="post",
                )
                await append(
                    s,
                    StatementIn(
                        subject=f"blob:{sha}",
                        prop=Prop.DOCUMENT_DATE,
                        value={"text": posted, "lang": "en"},
                        author_id=agent.id,
                        run_id=run.id,
                        span_blob=sha,
                        span={"start": 0, "end": 1},
                    ),
                    sign=sign,
                )
                st = await append(
                    s,
                    StatementIn(
                        subject=f"blob:{sha}",
                        prop=Prop.REPORTS,
                        value={
                            "text": "Tel Ward",
                            "lang": "en",
                            "kind": "shelling",
                            "town": "Tel Ward",
                        },
                        author_id=agent.id,
                        run_id=run.id,
                        span_blob=sha,
                        span={
                            "start": text.index("Tel Ward"),
                            "end": text.index("Tel Ward") + 8,
                        },
                        valid_from=date(2031, 3, 1),
                        valid_to=date(2031, 3, 1),
                    ),
                    sign=sign,
                )
                self.shas.append(sha)
                self.reports.append(str(st.id))
            self.links = []
            for a, b in ((0, 1), (1, 2)):
                st = await append(
                    s,
                    StatementIn(
                        subject=f"blob:{self.shas[a]}",
                        prop=Prop.RELATION,
                        value={
                            "kind": "same_event",
                            "blob": self.shas[b],
                            "why": f"Both name Tel Ward ({a}, {b}).",
                        },
                        author_id=agent.id,
                        run_id=run.id,
                    ),
                    sign=sign,
                )
                self.links.append(str(st.id))

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def client(self):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token("analyst@example.com", "/discover")
        await client.get(f"/_test/login?token={token}")
        page = await (await client.get("/discover")).get_data(as_text=True)
        client.csrf = {
            "X-CSRFToken": re.search(
                r'name="csrf-token" content="([^"]+)"', page
            ).group(1)
        }
        return client

    async def sign(self, client, path, item):
        r = await client.post(path, json={"item": item}, headers=client.csrf)
        body = await r.get_json()
        self.assertEqual(r.status_code, 200, body)
        r = await client.post(
            "/api/review/commit",
            json={
                "item": {
                    "hash": body["hash"],
                    "credential": self.passkey.assert_(body["hash"]),
                }
            },
            headers=client.csrf,
        )
        self.assertEqual(r.status_code, 200, await r.get_json())
        return body, await r.get_json()

    async def queue(self, client):
        r = await client.get(f"/api/discover/groups?investigation={CASE}")
        return (await r.get_json())["items"]

    async def tiers(self, ids):
        async with ext.async_session_factory() as s:
            rows = await s.execute(
                select(StatementTier.statement_id, StatementTier.tier).where(
                    StatementTier.statement_id.in_([uuid.UUID(i) for i in ids])
                )
            )
            return {str(i): t for i, t in rows}

    async def test_queue_and_detail(self):
        client = await self.client()
        [g] = await self.queue(client)
        assert g["count"] == {"posts": 3, "links": 2}
        assert g["summary"]["town"] == "Tel Ward"
        assert g["summary"]["place"]["en"] == "Tel Ward"
        assert g["summary"]["window"] == ["2031-03-01", "2031-03-01"]
        r = await client.get(f"/api/discover/groups/{g['id']}")
        d = await r.get_json()
        assert r.headers["Cache-Control"] == "no-store"
        by = {p["sha"]: p for p in d["posts"]}
        first = by[self.shas[0]]
        assert [run["t"] for run in first["text"] if run.get("mark")] == ["Tel Ward"]
        # The sensitive post stays closed until its logged reveal.
        assert by[self.shas[2]]["text"] is None
        assert by[self.shas[2]]["reports"][0]["text"] is None
        assert (
            await client.get("/api/discover/groups/0000000000000000")
        ).status_code == 404

    async def test_accept_keeps_two_posts_in_one_gesture(self):
        client = await self.client()
        [g] = await self.queue(client)
        staged, out = await self.sign(
            client,
            "/api/review/groups/options",
            {
                "group": g["id"],
                "kept": self.shas[:2],
                "title": "Shelling of Tel Ward market",
            },
        )
        summaries = [r["summary"] for r in staged["rows"]]
        assert "accept the same-event link" in summaries
        props = [s["prop"] for s in out["statements"]]
        assert props.count(Prop.SUPPORTED_BY) == 2
        assert props.count(Prop.REVIEW) == 1
        assert Prop.OCCURRED_AT in props
        tiers = await self.tiers(self.links)
        assert tiers[self.links[0]] == "accepted"
        assert tiers[self.links[1]] == "unconfirmed"
        # The dropped post's link stays open as its own group.
        [left] = await self.queue(client)
        assert left["count"] == {"posts": 2, "links": 1}
        incident = next(
            s["subject"] for s in out["statements"] if s["prop"] == Prop.INCIDENT
        )
        r = await client.get(f"/api/incidents/{incident.removeprefix('incident:')}")
        found = await r.get_json()
        assert found["title"] == "Shelling of Tel Ward market"
        assert found["places"]
        async with ext.async_session_factory() as s:
            assert await verify_db(s) == []
        # Undo returns the decided link to the queue.
        await self.sign(client, "/api/review/undo", {"gesture": staged["gesture"]})
        [back] = await self.queue(client)
        assert back["count"] == {"posts": 3, "links": 2}

    async def test_reject_rejects_every_link(self):
        client = await self.client()
        [g] = await self.queue(client)
        await self.sign(
            client,
            "/api/review/groups/reject",
            {"group": g["id"], "reason": "two events"},
        )
        assert set((await self.tiers(self.links)).values()) == {"rejected"}
        assert await self.queue(client) == []

    async def test_refuses_bad_keeps(self):
        client = await self.client()
        [g] = await self.queue(client)
        for kept in (
            [self.shas[0]],
            [self.shas[0], self.shas[2]],
            [self.shas[0], "x" * 64],
        ):
            r = await client.post(
                "/api/review/groups/options",
                json={"item": {"group": g["id"], "kept": kept, "title": "t"}},
                headers=client.csrf,
            )
            assert r.status_code == 400, kept
        async with ext.async_session_factory() as s:
            assert not (
                await s.execute(
                    select(Statement.id)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .where(Statement.prop == Prop.INCIDENT)
                )
            ).all()
