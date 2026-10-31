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
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from quart_security import hash_password
from sqlalchemy import select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import build_alembic_config, throwaway_database_url
from stk.user.models import Role, User

from sealbook import blobs
from sealbook.app import create_app
from sealbook.collaboration.service import request_review
from sealbook.ledger.contracts import Prop, StatementIn
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

            self.run_sign, self.agent, self.run = sign, agent.id, run.id

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

    async def client(self, email="analyst@example.com"):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(email, "/discover")
        await client.get(f"/_test/login?token={token}")
        page = await (await client.get("/discover")).get_data(as_text=True)
        client.csrf = {
            "X-CSRFToken": re.search(
                r'name="csrf-token" content="([^"]+)"', page
            ).group(1)
        }
        return client

    async def sign(self, client, path, item, passkey=None):
        r = await client.post(path, json={"item": item}, headers=client.csrf)
        body = await r.get_json()
        self.assertEqual(r.status_code, 200, body)
        r = await client.post(
            "/api/review/commit",
            json={
                "item": {
                    "hash": body["hash"],
                    "credential": (passkey or self.passkey).assert_(body["hash"]),
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
        [built] = found["relations"]["findings"]
        assert built["label"] == "Same event" and built["reason"].startswith("2 posts")
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

    async def first_pass(self, i):
        sha, text = self.shas[i], POSTS[i][0]
        async with ext.async_session_factory() as s, s.begin():
            for prop, value, quote in (
                (
                    Prop.TRIAGE,
                    {"content": "incident", "reliability": "some_evidence", "why": "w"},
                    "Tel Ward",
                ),
                (Prop.LABEL, {"label": "Type, Martial, Shelling"}, "Tel Ward"),
            ):
                start = text.index(quote)
                await append(
                    s,
                    StatementIn(
                        subject=f"blob:{sha}",
                        prop=prop,
                        value=value | {"text": quote, "lang": "en"},
                        author_id=self.agent,
                        run_id=self.run,
                        span_blob=sha,
                        span={"start": start, "end": start + len(quote)},
                    ),
                    sign=self.run_sign,
                )
        return sha

    async def test_first_pass_accepts_in_one_gesture(self):
        sha = await self.first_pass(0)
        client = await self.client()
        staged, out = await self.sign(
            client, "/api/review/firstpass/options", {"sha": sha}
        )
        assert [r["summary"] for r in staged["rows"]] == [
            "accept the triage",
            "accept a label",
        ]
        assert len(out["statements"]) == 2
        r = await client.post(
            "/api/review/firstpass/options",
            json={"item": {"sha": sha}},
            headers=client.csrf,
        )
        assert r.status_code == 404

    async def test_a_pass_sent_for_peer_review_is_the_reviewers(self):
        sha = await self.first_pass(1)
        async with ext.async_session_factory() as s, s.begin():
            role = await s.scalar(select(Role).where(Role.name == "analyst"))
            peer = User(
                email="peer@example.com",
                name="peer",
                password="x",
                active=True,
                confirmed_at=datetime.now(),
            )
            peer.roles.append(role)
            s.add(peer)
            await s.flush()
            me = await s.scalar(select(User).where(User.email == "analyst@example.com"))
            triage = await s.scalar(
                select(Statement).where(
                    Statement.prop == Prop.TRIAGE, Statement.subject == f"blob:{sha}"
                )
            )
            await request_review(
                s, triage, me, peer.id, "Peer review of the first pass"
            )
        client = await self.client()
        r = await client.post(
            "/api/review/firstpass/options",
            json={"item": {"sha": sha}},
            headers=client.csrf,
        )
        assert r.status_code == 403

    async def test_an_incident_sent_for_peer_review_is_the_reviewers(self):
        client = await self.client()
        [g] = await self.queue(client)
        _, out = await self.sign(
            client,
            "/api/review/groups/options",
            {"group": g["id"], "kept": self.shas[:2], "title": "Tel Ward market"},
        )
        ref = next(s["subject"] for s in out["statements"] if s["prop"] == "incident")
        url = f"/api/incidents/{ref.removeprefix('incident:')}"
        statement = (await (await client.get(url)).get_json())["statement"]
        peer_key = SoftPasskey()
        async with ext.async_session_factory() as s, s.begin():
            role = await s.scalar(select(Role).where(Role.name == "analyst"))
            peer = User(
                email="peer@example.com",
                name="peer",
                password="x",
                active=True,
                confirmed_at=datetime.now(),
            )
            peer.roles.append(role)
            s.add(peer)
            await s.flush()
            s.add(peer_key.register(peer))
        r = await client.post(
            "/api/collaboration/requests",
            json={
                "item": {"statement": statement, "reviewer": peer.id, "reason": "Peer"}
            },
            headers=client.csrf,
        )
        assert r.status_code == 201, await r.get_json()
        path, item = "/api/review/incidents/peer/options", {"incident": ref}
        r = await client.post(path, json={"item": item}, headers=client.csrf)
        assert r.status_code == 403
        reviewer = await self.client("peer@example.com")
        [asked] = (await (await reviewer.get("/api/todo")).get_json())["asked"]
        assert asked["incident"] == ref.removeprefix("incident:")
        r = await reviewer.post(
            f"/api/collaboration/requests/{asked['request']}/comments",
            json={"item": {"text": "+Type, Martial, Shelling\n*check the date"}},
            headers=reviewer.csrf,
        )
        assert r.status_code < 300
        staged, _ = await self.sign(reviewer, path, item, peer_key)
        assert [r["summary"] for r in staged["rows"]] == [
            "accept the incident: no review needed"
        ]
        assert (await (await reviewer.get("/api/todo")).get_json())["asked"] == []
        found = await (await reviewer.get(url)).get_json()
        assert found["peer_signed"]
        [t] = [t for t in found["threads"] if t["about"] == statement]
        thread = await (
            await reviewer.get(f"/api/collaboration/requests/{t['id']}")
        ).get_json()
        assert thread["comments"][0]["text"].splitlines()[1] == "*check the date"
        r = await reviewer.post(path, json={"item": item}, headers=reviewer.csrf)
        assert r.status_code == 404

    async def test_todo_lists_each_decision_once(self):
        await self.first_pass(0)
        client = await self.client()
        d = await (await client.get(f"/api/todo?investigation={CASE}")).get_json()
        assert [g["posts"] for g in d["groups"]] == [3]
        assert [t["priority"] for t in d["triage"]] == ["high"]
        # Same-event links and first-pass lines are decided with their group and
        # item, so Review's open list leaves them out.
        r = await client.get(f"/api/review/queue?box=open&investigation={CASE}")
        props = {i["prop"] for i in (await r.get_json())["items"]}
        assert not props & {"relation", "triage", "label"}, props
        assert (await client.get("/todo")).status_code == 200

    async def test_an_ambiguous_town_is_placed_by_the_analyst(self):
        async with ext.async_session_factory() as s, s.begin():
            other = Place(
                name_ar="تل ورد",
                name_en="Tel Ward",
                kind="populated",
                geom=point(37, 35),
                geo_source="analyst",
                source="person",
            )
            s.add(other)
            await s.flush()
            s.add(
                PlaceName(
                    place_id=other.id,
                    name="Tel Ward",
                    norm=normalise("Tel Ward"),
                    lang="en",
                    kind="official",
                    source="person",
                )
            )
        client = await self.client()
        r = await client.get(f"/api/discover/groups?investigation={CASE}")
        [g] = (await r.get_json())["items"]
        assert g["summary"]["place"] is None
        d = await (await client.get(f"/api/discover/groups/{g['id']}")).get_json()
        assert len(d["summary"]["choices"]) == 2
        item = {"group": g["id"], "kept": self.shas[:2], "title": "t"}
        r = await client.post(
            "/api/review/groups/options",
            json={"item": item | {"place": str(uuid.uuid4())}},
            headers=client.csrf,
        )
        assert r.status_code == 400
        _, out = await self.sign(
            client, "/api/review/groups/options", item | {"place": str(other.id)}
        )
        props = [s["prop"] for s in out["statements"]]
        assert Prop.OCCURRED_AT in props

    async def test_first_pass_text_is_masked_on_a_sensitive_source(self):
        sha, text = self.shas[2], POSTS[2][0]
        async with ext.async_session_factory() as s, s.begin():
            start = text.index("Tel Ward")
            await append(
                s,
                StatementIn(
                    subject=f"blob:{sha}",
                    prop=Prop.DESCRIPTION,
                    value={
                        "line": "A witness names the market.",
                        "text": "Tel Ward",
                        "lang": "en",
                    },
                    author_id=self.agent,
                    run_id=self.run,
                    span_blob=sha,
                    span={"start": start, "end": start + 8},
                ),
                sign=self.run_sign,
            )
        client = await self.client()
        item = await (await client.get(f"/api/evidence/{sha}/item")).get_json()
        [line] = [c for c in item["claims"] if c["prop"] == "description"]
        assert line["display"] is None
