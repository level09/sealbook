"""Review queue with passkey accept (acceptance tests 3, 12 and 16) against a
throwaway Postgres database migrated to head."""

import asyncio
import re
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from unittest import mock

import orjson
import stk
import stk.extensions as ext
from alembic import command
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from quart_security import hash_password
from quart_security.webauthn import bytes_to_base64url
from sqlalchemy import select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)
from stk.user.models import Role, User

from sealbook import blobs
from sealbook.app import app as sealbook_app
from sealbook.app import create_app
from sealbook.ledger.contracts import Prop, Refused, StatementIn
from sealbook.ledger.models import (
    Payload,
    Principal,
    Run,
    RunEvent,
    Statement,
    StatementTier,
)
from sealbook.ledger.write import append, record_acquisition, subject_head
from sealbook.monitor import data
from sealbook.settings import Config
from tests.passkey import SoftPasskey

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
TEXT = "سجل الموقوفين\nالاسم: محمد\nالاسم: يوسف\nالاسم: أحمد\n"


@unittest.skipUnless(POSTGRES, "review needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class ReviewTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict("stk.websocket._clients", {}, clear=True))
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config(sealbook_app, url, quiet=True)
        command.upgrade(config, "heads")
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
        self.key = Ed25519PrivateKey.generate()
        async with ext.async_session_factory() as s, s.begin():
            analyst = (
                await s.execute(select(Role).where(Role.name == "analyst"))
            ).scalar_one()
            user = User(
                email="analyst@example.com",
                name="analyst",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            user.roles.append(analyst)
            s.add(user)
            await s.flush()
            s.add(self.passkey.register(user))
            self.agent = Principal(kind="agent", name="extractor")
            self.viewer = Principal(kind="human", name="viewer")
            s.add_all([self.agent, self.viewer])
            await s.flush()
            self.sha = (
                await blobs.put(s, TEXT.encode(), "text/plain", Config.BLOB_DIR)
            ).sha256
            await record_acquisition(
                s,
                blob_sha256=self.sha,
                origin="device:scanner",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={"investigation": "case-r"},
                sensitivity="sensitive",
                doc_type="register",
            )

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def propose(self, subject, name, span=None, prop=Prop.NAME, based_on=None):
        """An agent proposal in a fresh run, the way every MCP call is one."""
        start = TEXT.index(name)
        async with ext.async_session_factory() as s, s.begin():
            run = Run(
                principal_id=self.agent.id,
                tool="extract",
                inputs={"blobs": [self.sha]},
                pubkey=self.key.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                ),
            )
            s.add(run)
            await s.flush()
            return await append(
                s,
                StatementIn(
                    subject=subject,
                    prop=prop,
                    value={"text": name, "lang": "ar"},
                    author_id=self.agent.id,
                    run_id=run.id,
                    span_blob=self.sha,
                    span=span or {"start": start, "end": start + len(name)},
                    based_on=based_on,
                ),
                sign=lambda h: {
                    "kind": "ed25519",
                    "sig": self.key.sign(h.encode()).hex(),
                },
            )

    async def tier(self, sid):
        async with ext.async_session_factory() as s:
            row = (
                await s.execute(
                    select(StatementTier).where(StatementTier.statement_id == sid)
                )
            ).scalar_one()
            return row.tier, row.reason

    async def client(self, email="analyst@example.com"):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(email, "/monitor")
        self.assertEqual(
            (await client.get(f"/_test/login?token={token}")).status_code, 302
        )
        page = await (await client.get("/case-map")).get_data(as_text=True)
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        client.csrf = {"X-CSRFToken": csrf}
        return client

    async def start(self, client, item):
        r = await client.post(
            "/api/review/options", json={"item": item}, headers=client.csrf
        )
        return r.status_code, await r.get_json()

    async def finish(self, client, digest):
        r = await client.post(
            "/api/review/commit",
            json={"item": {"hash": digest, "credential": self.passkey.assert_(digest)}},
            headers=client.csrf,
        )
        return r.status_code, await r.get_json()

    async def review(self, client, item):
        status, body = await self.start(client, item)
        self.assertEqual(status, 200, body)
        return await self.finish(client, body["hash"])

    async def queue(self, client):
        f = orjson.dumps({"investigation": "case-r"}).decode()
        panels = await (await client.get(f"/api/monitor/panels?filter={f}")).get_json()
        return [c["id"] for c in panels["queue"]], panels["queue"]

    async def test_3_passkey_accept_leaves_queue_and_undo_returns_it(self):
        st = await self.propose(f"person:{uuid.uuid4()}", "محمد")
        client = await self.client()
        ids, cards = await self.queue(client)
        self.assertEqual(ids, [str(st.id)])
        self.assertTrue(all(i["ok"] for i in cards[0]["invariants"]), cards)

        status, body = await self.start(
            client, {"statement": str(st.id), "verdict": "accept", "reason": "seen"}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["options"]["challenge"], _b64(body["hash"]))
        # A different passkey is refused and the prepared review is spent.
        other = SoftPasskey()
        r = await client.post(
            "/api/review/commit",
            json={
                "item": {
                    "hash": body["hash"],
                    "credential": other.assert_(body["hash"]),
                }
            },
            headers=client.csrf,
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual((await self.tier(st.id))[0], "unconfirmed")

        status, done = await self.review(
            client, {"statement": str(st.id), "verdict": "accept", "reason": "seen"}
        )
        self.assertEqual((status, done["tier"]), (200, "accepted"), done)
        self.assertEqual(await self.tier(st.id), ("accepted", "human_accept"))
        self.assertEqual((await self.queue(client))[0], [])
        async with ext.async_session_factory() as s:
            gestures = (
                (
                    await s.execute(
                        select(RunEvent.stage).where(RunEvent.human_gesture.is_(True))
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual(gestures, ["review_accept"])

        status, undone = await self.review(client, {"undo": done["review"]})
        self.assertEqual((status, undone["tier"]), (200, "unconfirmed"), undone)
        self.assertEqual((await self.queue(client))[0], [str(st.id)])

    async def test_12_retry_rejection_and_stale(self):
        subject = f"person:{uuid.uuid4()}"
        first = await self.propose(subject, "محمد")
        self.assertEqual((await self.propose(subject, "محمد")).id, first.id)

        client = await self.client()
        status, done = await self.review(
            client,
            {"statement": str(first.id), "verdict": "reject", "reason": "misread"},
        )
        self.assertEqual((status, done["tier"]), (200, "rejected"))
        with self.assertRaises(Refused) as caught:
            await self.propose(subject, "محمد")
        self.assertIn("rejected by a human", caught.exception.reason)

        pending = await self.propose(subject, "يوسف")
        status, body = await self.start(
            client, {"statement": str(pending.id), "verdict": "accept"}
        )
        self.assertEqual(status, 200)
        # While the passkey prompt is open, another value on the subject is accepted.
        other = await self.propose(subject, "أحمد")
        self.assertEqual(
            (
                await self.review(
                    client, {"statement": str(other.id), "verdict": "accept"}
                )
            )[0],
            200,
        )
        status, refused = await self.finish(client, body["hash"])
        self.assertEqual(status, 409)
        self.assertIn("stale", refused["error"])
        card = dict(zip(*await self.queue(client), strict=True))[str(pending.id)]
        self.assertFalse(card["invariants"][2]["ok"])
        status, refused = await self.start(
            client, {"statement": str(pending.id), "verdict": "accept"}
        )
        self.assertEqual(status, 409)
        # Reject stays allowed on a stale card.
        status, done = await self.review(
            client, {"statement": str(pending.id), "verdict": "reject"}
        )
        self.assertEqual((status, done["tier"]), (200, "rejected"))

    async def test_stale_accept_needs_the_current_head_acknowledged(self):
        subject = f"person:{uuid.uuid4()}"
        stale, sibling = (
            await self.propose(subject, "يوسف"),
            await self.propose(subject, "أحمد"),
        )
        client = await self.client()
        await self.review(client, {"statement": str(sibling.id), "verdict": "accept"})
        card = dict(zip(*await self.queue(client), strict=True))[str(stale.id)]
        head = card["invariants"][2]["acknowledge"]
        item = {"statement": str(stale.id), "verdict": "accept"}
        status, _ = await self.start(client, item | {"acknowledged_head": "0" * 64})
        self.assertEqual(status, 409)
        status, done = await self.review(client, item | {"acknowledged_head": head})
        self.assertEqual((status, done["tier"]), (200, "accepted"), done)
        async with ext.async_session_factory() as s:
            review = await s.get(Statement, uuid.UUID(done["review"]))
            value = (await s.get(Payload, review.commitment)).value
        self.assertEqual(value["acknowledged_head"], head)

    async def test_accept_waits_for_the_chain_lock_then_sees_the_new_head(self):
        subject = f"person:{uuid.uuid4()}"
        pending, sibling = (
            await self.propose(subject, "يوسف"),
            await self.propose(subject, "أحمد"),
        )
        client = await self.client()
        status, body = await self.start(
            client, {"statement": str(pending.id), "verdict": "accept"}
        )
        self.assertEqual(status, 200)
        async with ext.async_session_factory() as s:
            await s.begin()
            # Another analyst's accept holds the lock while this one commits.
            who = (
                await s.execute(select(Principal).where(Principal.user_id.isnot(None)))
            ).scalar()
            await append(
                s,
                StatementIn(
                    subject=f"statement:{sibling.id}",
                    prop=Prop.REVIEW,
                    value={"verdict": "accept", "reason": "", "at": "t"},
                    author_id=who.id,
                ),
                sign=self.passkey.sig,
            )
            finishing = asyncio.create_task(self.finish(client, body["hash"]))
            await asyncio.sleep(0.5)
            self.assertFalse(finishing.done())
            await s.commit()
        status, refused = await finishing
        self.assertEqual(status, 409, refused)
        self.assertIn("stale", refused["error"])

    async def test_an_old_proposal_accepted_late_makes_newer_ones_stale(self):
        subject = f"person:{uuid.uuid4()}"
        old = await self.propose(subject, "محمد", prop=Prop.FATHER_NAME)
        client = await self.client()
        name = await self.propose(subject, "يوسف")
        await self.review(client, {"statement": str(name.id), "verdict": "accept"})
        async with ext.async_session_factory() as s:
            head = await subject_head(s, subject)
        newer = await self.propose(
            subject, "أحمد", prop=Prop.FATHER_NAME, based_on=head
        )
        await self.review(client, {"statement": str(old.id), "verdict": "accept"})
        card = dict(zip(*await self.queue(client), strict=True))[str(newer.id)]
        self.assertFalse(card["invariants"][2]["ok"], card["invariants"])

    async def test_accepting_one_prop_leaves_the_others_acceptable(self):
        subject = f"person:{uuid.uuid4()}"
        name = await self.propose(subject, "محمد")
        father = await self.propose(subject, "يوسف", prop=Prop.FATHER_NAME)
        client = await self.client()
        for st in (name, father):
            status, done = await self.review(
                client, {"statement": str(st.id), "verdict": "accept"}
            )
            self.assertEqual((status, done.get("tier")), (200, "accepted"), done)

    async def test_identity_proposal_is_checked_on_its_basis(self):
        a, b = f"person:{uuid.uuid4()}", f"person:{uuid.uuid4()}"
        basis = [await self.propose(a, "محمد"), await self.propose(b, "يوسف")]
        async with ext.async_session_factory() as s, s.begin():
            run = Run(
                principal_id=self.agent.id,
                tool="mcp",
                inputs={"basis": [f"statement:{x.id}" for x in basis]},
                pubkey=self.key.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                ),
            )
            s.add(run)
            await s.flush()
            same = await append(
                s,
                StatementIn(
                    subject=a,
                    prop=Prop.SAME_AS,
                    value={"person": b.removeprefix("person:")},
                    author_id=self.agent.id,
                    run_id=run.id,
                ),
                sign=lambda h: {
                    "kind": "ed25519",
                    "sig": self.key.sign(h.encode()).hex(),
                },
            )
        async with ext.async_session_factory() as s:
            check = (await data.card(s, same.id))["invariants"][0]
        self.assertEqual((check["name"], check["ok"]), ("basis stands", True), check)
        client = await self.client()
        await self.review(client, {"statement": str(basis[1].id), "verdict": "reject"})
        async with ext.async_session_factory() as s:
            check = (await data.card(s, same.id))["invariants"][0]
        self.assertFalse(check["ok"], check)

    async def test_span_outside_source_never_reaches_the_queue(self):
        st = await self.propose(
            f"person:{uuid.uuid4()}", "محمد", span={"start": 0, "end": 10_000}
        )
        self.assertEqual(await self.tier(st.id), ("rejected", "span_not_in_source"))

    async def colleague(self):
        async with ext.async_session_factory() as s, s.begin():
            role = await s.scalar(select(Role).where(Role.name == "analyst"))
            user = User(
                email="colleague@example.com",
                name="Colleague",
                active=True,
                password=hash_password("TestPassword123!", app=self.app),
            )
            user.roles.append(role)
            s.add(user)
            await s.flush()
            return user.id

    async def test_correction_is_pending_and_reviewer_receives_it(self):
        original = await self.propose(f"person:{uuid.uuid4()}", "محمد")
        reviewer = await self.colleague()
        client = await self.client()
        response = await client.post(
            "/api/collaboration/suggestions",
            headers=client.csrf,
            json={
                "item": {
                    "statement": str(original.id),
                    "value": {"text": "يوسف", "lang": "ar"},
                    "span": {
                        "start": TEXT.index("يوسف"),
                        "end": TEXT.index("يوسف") + 4,
                    },
                    "reviewer": reviewer,
                    "reason": "Read the second entry",
                }
            },
        )
        self.assertEqual(
            response.status_code, 201, await response.get_data(as_text=True)
        )
        result = await response.get_json()
        self.assertEqual(
            (await self.tier(uuid.UUID(result["proposal"])))[0], "unconfirmed"
        )
        async with ext.async_session_factory() as s:
            self.assertIsNone((await s.get(Statement, original.id)).supersedes)
            proposed = await s.get(Statement, uuid.UUID(result["proposal"]))
            self.assertEqual(proposed.supersedes, original.id)
        recipient = await self.client("colleague@example.com")
        inbox = await (await recipient.get("/api/collaboration/requests")).get_json()
        self.assertEqual(inbox["items"][0]["proposal"]["id"], result["proposal"])
        self.assertEqual(inbox["items"][0]["reason"], "Read the second entry")
        self.assertTrue(inbox["items"][0]["can_review"])
        sent = await (
            await client.get(f"/api/collaboration/requests/{result['id']}")
        ).get_json()
        self.assertFalse(sent["can_review"])
        status, _ = await self.start(
            client, {"statement": result["proposal"], "verdict": "accept"}
        )
        self.assertEqual(status, 403)

    async def test_invalid_correction_span_does_not_write_a_proposal(self):
        original = await self.propose(f"person:{uuid.uuid4()}", "محمد")
        client = await self.client()
        reviewer = await self.colleague()
        response = await client.post(
            "/api/collaboration/suggestions",
            headers=client.csrf,
            json={
                "item": {
                    "statement": str(original.id),
                    "value": {"text": "يوسف", "lang": "ar"},
                    "span": {"start": 0, "end": 9999},
                    "reviewer": reviewer,
                    "reason": "Correction",
                }
            },
        )
        self.assertEqual(response.status_code, 400)

    async def test_request_requires_another_active_analyst(self):
        original = await self.propose(f"person:{uuid.uuid4()}", "محمد")
        client = await self.client()
        async with ext.async_session_factory() as s:
            own_id = await s.scalar(
                select(User.id).where(User.email == "analyst@example.com")
            )
        for reviewer in (own_id, 999999):
            response = await client.post(
                "/api/collaboration/requests",
                headers=client.csrf,
                json={
                    "item": {
                        "statement": str(original.id),
                        "reviewer": reviewer,
                        "reason": "Please check",
                    }
                },
            )
            self.assertEqual(response.status_code, 400)

    async def test_colleague_correction_notifies_then_replaces_only_the_current_view(
        self,
    ):
        from sealbook.people import data as people

        subject = f"person:{uuid.uuid4()}"
        original = await self.propose(subject, "محمد")
        reviewer = await self.client()
        status, _ = await self.review(
            reviewer, {"statement": str(original.id), "verdict": "accept"}
        )
        self.assertEqual(status, 200)
        await self.colleague()
        sender = await self.client("colleague@example.com")
        async with ext.async_session_factory() as s:
            reviewer_id = await s.scalar(
                select(User.id).where(User.email == "analyst@example.com")
            )
        async with reviewer.websocket(
            "/ws", headers={"Origin": "http://localhost"}
        ) as ws:
            self.assertEqual((await ws.receive_json())["type"], "connected")
            response = await sender.post(
                "/api/collaboration/suggestions",
                headers=sender.csrf,
                json={
                    "item": {
                        "statement": str(original.id),
                        "value": {"text": "يوسف", "lang": "ar"},
                        "span": {
                            "start": TEXT.index("يوسف"),
                            "end": TEXT.index("يوسف") + 4,
                        },
                        "reviewer": reviewer_id,
                        "reason": "Please check the second entry",
                    }
                },
            )
            self.assertEqual(response.status_code, 201)
            task = await response.get_json()
            notification = await asyncio.wait_for(ws.receive_json(), timeout=3)
            self.assertEqual(notification["to"], f"/inbox?item=request:{task['id']}")
            self.assertNotIn("يوسف", orjson.dumps(notification).decode())
        comment = await sender.post(
            f"/api/collaboration/requests/{task['id']}/comments",
            headers=sender.csrf,
            json={"item": {"text": "The source has two entries."}},
        )
        self.assertEqual(comment.status_code, 201)
        status, accepted = await self.review(
            reviewer, {"statement": task["proposal"], "verdict": "accept"}
        )
        self.assertEqual((status, accepted["tier"]), (200, "accepted"))
        async with ext.async_session_factory() as s:
            current = await people.person(s, subject, "factual")
            self.assertEqual(
                [row["id"] for row in current["facts"]["name"]], [task["proposal"]]
            )
            self.assertIsNotNone(await s.get(Statement, original.id))
        selected = await (
            await reviewer.get(f"/api/collaboration/requests/{task['id']}")
        ).get_json()
        self.assertEqual(selected["comments"][0]["text"], "The source has two entries.")
        # Private discussions must not escape through the general ledger reader.
        self.assertEqual(
            (await reviewer.get(f"/api/monitor/statement/{task['id']}")).status_code,
            404,
        )
        hidden = await reviewer.post(
            "/api/monitor/reveal",
            headers=reviewer.csrf,
            json={"item": {"statement": task["id"]}},
        )
        self.assertEqual(hidden.status_code, 404)
        status, _ = await self.start(sender, {"undo": accepted["review"]})
        self.assertEqual(status, 403)

    async def test_confirmed_same_recording_updates_corroboration_and_undo_restores_it(
        self,
    ):
        from sealbook.ledger.models import Policy
        from sealbook.ledger.write import gate_input

        async with ext.async_session_factory() as s, s.begin():
            s.add(
                Policy(
                    capability="extract",
                    doc_type="register",
                    sensitivity="sensitive",
                    routes=["local"],
                    min_origins=2,
                    calibration={"artifact": "test-only", "error_rate": "0.01"},
                )
            )
            other = await blobs.put(
                s, (TEXT + "\n").encode(), "text/plain", Config.BLOB_DIR
            )
            await record_acquisition(
                s,
                blob_sha256=other.sha256,
                origin="device:other-scanner",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={},
                sensitivity="sensitive",
                doc_type="register",
            )
        subject = f"person:{uuid.uuid4()}"
        first = await self.propose(subject, "محمد")
        original_sha, self.sha = self.sha, other.sha256
        second = await self.propose(subject, "محمد")
        self.sha = original_sha
        self.assertEqual((await self.tier(first.id))[0], "corroborated")
        async with ext.async_session_factory() as s, s.begin():
            run = Run(
                principal_id=self.agent.id,
                tool="fingerprint",
                inputs={"blobs": [original_sha]},
                pubkey=self.key.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                ),
            )
            s.add(run)
            await s.flush()
            match = await append(
                s,
                StatementIn(
                    subject=f"blob:{original_sha}",
                    prop=Prop.MEDIA_MATCH,
                    value={"blob": other.sha256, "picture": None, "sound": None},
                    author_id=self.agent.id,
                    run_id=run.id,
                ),
                sign=lambda h: {
                    "kind": "ed25519",
                    "sig": self.key.sign(h.encode()).hex(),
                },
            )
        client = await self.client()
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", "test/model"))
        status, accepted = await self.review(
            client,
            {
                "statement": str(match.id),
                "verdict": "accept",
                "relationship": "same_recording",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual((await self.tier(first.id))[0], "unconfirmed")
        async with ext.async_session_factory() as s:
            refreshes = list(
                (
                    await s.execute(select(Run).where(Run.tool == "embed_actor"))
                ).scalars()
            )
            self.assertEqual({run.inputs["subject"] for run in refreshes}, {subject})
            self.assertEqual(
                {run.inputs["investigation"] for run in refreshes}, {"", "case-r"}
            )
            self.assertEqual(
                (await gate_input(s, await s.get(Statement, second.id)))[
                    0
                ].distinct_origins,
                1,
            )
        await self.review(client, {"undo": accepted["review"]})
        self.assertEqual((await self.tier(first.id))[0], "corroborated")

    async def test_accepted_correction_removes_old_corroboration_and_undo_restores_it(
        self,
    ):
        from sealbook.ledger.models import Policy

        async with ext.async_session_factory() as s, s.begin():
            s.add(
                Policy(
                    capability="extract",
                    doc_type="register",
                    sensitivity="sensitive",
                    routes=["local"],
                    min_origins=2,
                    calibration={"artifact": "test-only", "error_rate": "0.01"},
                )
            )
            other = await blobs.put(
                s, (TEXT + "\n").encode(), "text/plain", Config.BLOB_DIR
            )
            await record_acquisition(
                s,
                blob_sha256=other.sha256,
                origin="device:second",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={},
                sensitivity="sensitive",
                doc_type="register",
            )
        subject = f"person:{uuid.uuid4()}"
        first = await self.propose(subject, "محمد")
        original_sha, self.sha = self.sha, other.sha256
        second = await self.propose(subject, "محمد")
        self.sha = original_sha
        self.assertEqual((await self.tier(second.id))[0], "corroborated")
        async with ext.async_session_factory() as s, s.begin():
            correction = await append(
                s,
                StatementIn(
                    subject=subject,
                    prop=Prop.NAME,
                    value={"text": "يوسف", "lang": "ar"},
                    author_id=self.viewer.id,
                    span_blob=self.sha,
                    span={"start": TEXT.index("يوسف"), "end": TEXT.index("يوسف") + 4},
                    supersedes=first.id,
                    based_on=await subject_head(s, subject),
                ),
            )
        client = await self.client()
        status, accepted = await self.review(
            client, {"statement": str(correction.id), "verdict": "accept"}
        )
        self.assertEqual(status, 200)
        self.assertEqual((await self.tier(second.id))[0], "unconfirmed")
        await self.review(client, {"undo": accepted["review"]})
        self.assertEqual((await self.tier(second.id))[0], "corroborated")

    async def test_security_and_admin_hubs(self):
        client = await self.client()
        r = await client.get("/security")
        self.assertEqual(r.status_code, 200)
        body = await r.get_data(as_text=True)
        for href in ("/change", "/tf-setup", "/mf-recovery-codes"):
            self.assertIn(href, body)
        self.assertEqual((await client.get("/admin")).status_code, 403)
        async with ext.async_session_factory() as s, s.begin():
            role = Role(name="admin", description="Administrator")
            admin = User(
                email="admin@example.com",
                name="Admin",
                active=True,
                password=hash_password("TestPassword123!", app=self.app),
            )
            admin.roles.append(role)
            s.add(admin)
        r = await (await self.client("admin@example.com")).get("/admin")
        self.assertEqual(r.status_code, 200)
        body = await r.get_data(as_text=True)
        for href in ("/users/", "/roles/", "/activities/"):
            self.assertIn(href, body)

    async def test_nav_counts_open_review_requests(self):
        original = await self.propose(f"person:{uuid.uuid4()}", "محمد")
        reviewer = await self.colleague()
        client = await self.client()
        recipient = await self.client("colleague@example.com")
        counts = lambda c: c.get("/api/nav/counts")  # noqa: E731
        self.assertEqual((await (await counts(recipient)).get_json())["review"], 0)
        response = await client.post(
            "/api/collaboration/requests",
            headers=client.csrf,
            json={
                "item": {
                    "statement": str(original.id),
                    "reviewer": reviewer,
                    "reason": "Please check",
                }
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual((await (await counts(recipient)).get_json())["review"], 1)
        self.assertEqual((await (await counts(client)).get_json())["review"], 0)

    async def test_layout_has_new_nav(self):
        client = await self.client()
        body = await (await client.get("/case-map")).get_data(as_text=True)
        self.assertNotIn("My Account", body)
        self.assertNotIn("NAVIGATION", body)
        self.assertIn("/security", body)

    async def test_collaboration_pages_render_and_media_needs_a_logged_reveal(self):
        client = await self.client()
        for path in ("/review", "/inbox", "/evidence"):
            response = await client.get(path)
            self.assertEqual(response.status_code, 200)
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (16, 16), "red").save(buffer, "PNG")
        async with ext.async_session_factory() as s, s.begin():
            blob = await blobs.put(s, buffer.getvalue(), "image/png", Config.BLOB_DIR)
        denied = await client.get(f"/api/evidence/{blob.sha256}/content")
        self.assertEqual(denied.status_code, 403)
        revealed = await client.post(
            f"/api/evidence/{blob.sha256}/reveal",
            json={"item": {}},
            headers=client.csrf,
        )
        self.assertEqual(revealed.status_code, 200)
        response = await client.get(f"/api/evidence/{blob.sha256}/content")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(await response.get_data(), buffer.getvalue())

    async def test_media_match_statement_page_is_readable_and_carries_both_sources(
        self,
    ):
        from io import BytesIO

        from PIL import Image

        shas = []
        async with ext.async_session_factory() as s, s.begin():
            for colour in ("red", "blue"):
                buffer = BytesIO()
                Image.new("RGB", (16, 16), colour).save(buffer, "PNG")
                blob = await blobs.put(
                    s, buffer.getvalue(), "image/png", Config.BLOB_DIR
                )
                shas.append(blob.sha256)
                await record_acquisition(
                    s,
                    blob_sha256=blob.sha256,
                    origin=f"device:{colour}",
                    collector_id=self.agent.id,
                    at=datetime.now(UTC),
                    context={"file": f"{colour}.png"},
                    sensitivity="sensitive",
                    doc_type="photo",
                )
            run = Run(
                principal_id=self.agent.id,
                tool="fingerprint",
                inputs={"blobs": shas},
                pubkey=self.key.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                ),
            )
            s.add(run)
            await s.flush()
            match = await append(
                s,
                StatementIn(
                    subject=f"blob:{shas[0]}",
                    prop=Prop.MEDIA_MATCH,
                    value={"blob": shas[1], "picture": None, "sound": None},
                    author_id=self.agent.id,
                    run_id=run.id,
                ),
                sign=lambda h: {
                    "kind": "ed25519",
                    "sig": self.key.sign(h.encode()).hex(),
                },
            )
        client = await self.client()
        page = await (await client.get(f"/api/monitor/statement/{match.id}")).get_json()
        self.assertEqual(
            [page["media"][side]["sha256"] for side in ("left", "right")], shas
        )
        self.assertEqual(page["media"]["left"]["mime"], "image/png")
        self.assertNotIn("{", page["display"])

    async def test_fingerprint_rebuild_preserves_a_rejected_pair(self):
        from io import BytesIO

        import numpy as np
        from PIL import Image

        from sealbook.discovery import service
        from sealbook.ledger.models import Acquisition, Blob
        from sealbook.runs.worker import work_once

        image = Image.fromarray(
            np.random.default_rng(7).integers(0, 256, (256, 256, 3), dtype=np.uint8)
        )
        shas = []
        for fmt, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg")):
            buffer = BytesIO()
            image.save(buffer, format=fmt)
            async with ext.async_session_factory() as s, s.begin():
                blob = await blobs.put(s, buffer.getvalue(), mime, Config.BLOB_DIR)
                shas.append(blob.sha256)
                acq = await record_acquisition(
                    s,
                    blob_sha256=blob.sha256,
                    origin=f"device:{fmt}",
                    collector_id=self.agent.id,
                    at=datetime.now(UTC),
                    context={},
                    sensitivity="sensitive",
                    doc_type="photo",
                )
                await service.queue(s, acq, mime)
            await work_once(ext.async_session_factory, tools=["fingerprint"])
        async with ext.async_session_factory() as s:
            match = await s.scalar(
                select(Statement).where(Statement.prop == Prop.MEDIA_MATCH)
            )
        client = await self.client()
        status, _ = await self.review(
            client, {"statement": str(match.id), "verdict": "reject"}
        )
        self.assertEqual(status, 200)
        # Rebuilding the older file searches in the opposite direction.
        async with ext.async_session_factory() as s, s.begin():
            acq = await s.scalar(
                select(Acquisition).where(Acquisition.blob_sha256 == shas[0])
            )
            blob = await s.get(Blob, shas[0])
            ids = await service.queue(s, acq, blob.mime, rebuild=True)
        await work_once(ext.async_session_factory, tools=["fingerprint"])
        async with ext.async_session_factory() as s:
            run = await s.get(Run, uuid.UUID(ids[0]))
            self.assertEqual(run.status, "done", run.error)
            self.assertEqual(
                len(
                    list(
                        (
                            await s.execute(
                                select(Statement).where(
                                    Statement.prop == Prop.MEDIA_MATCH
                                )
                            )
                        ).scalars()
                    )
                ),
                1,
            )
            events = list(
                (
                    await s.execute(select(RunEvent).where(RunEvent.run_id == run.id))
                ).scalars()
            )
            self.assertTrue(
                any(event.drop_reason == "human_reject" for event in events)
            )

    async def test_16_statements_in_use_jump_the_queue(self):
        untouched = await self.propose(f"person:{uuid.uuid4()}", "محمد")
        viewed_person = f"person:{uuid.uuid4()}"
        on_track = await self.propose(viewed_person, "يوسف")
        cited = await self.propose(f"person:{uuid.uuid4()}", "أحمد")
        client = await self.client()
        self.assertEqual(
            (await self.queue(client))[0],
            [str(untouched.id), str(on_track.id), str(cited.id)],
        )
        async with ext.async_session_factory() as s, s.begin():
            for subject, statements in (
                (viewed_person, []),
                (f"brief:{uuid.uuid4()}", [str(cited.id)]),
            ):
                await append(
                    s,
                    StatementIn(
                        subject=subject,
                        prop=Prop.ACCESS,
                        value={
                            "action": "view",
                            "at": datetime.now(UTC).isoformat(),
                            "statements": statements,
                        },
                        author_id=self.viewer.id,
                    ),
                )
        ids, cards = await self.queue(client)
        self.assertEqual(ids, [str(on_track.id), str(cited.id), str(untouched.id)])
        self.assertEqual([c["used"] for c in cards], [True, True, False])


def _b64(digest: str) -> str:
    return bytes_to_base64url(bytes.fromhex(digest))


if __name__ == "__main__":
    unittest.main()
