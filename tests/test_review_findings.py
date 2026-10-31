"""Findings in review: the queue, and acceptance, rejection and undo in one signed
gesture, against a throwaway Postgres with synthetic accepted position claims."""

import re
import tempfile
import unittest
import uuid
from datetime import UTC, date, datetime
from io import BytesIO
from unittest import mock

import stk
import stk.extensions as ext
from alembic import command
from PIL import Image
from quart_security import hash_password
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)
from stk.user.models import Role, User

from sealbook import blobs
from sealbook.app import create_app
from sealbook.discovery.models import FindingKey
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Payload, Principal, Run, Statement, StatementTier
from sealbook.ledger.offline_sigs import gesture_digest
from sealbook.ledger.write import append, record_acquisition, verify_db
from sealbook.people.data import erase
from sealbook.places import resolve
from sealbook.places.models import Place, point
from sealbook.review import findings
from sealbook.runs.worker import work_once
from sealbook.settings import Config
from tests.passkey import SoftPasskey, attester

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
JUL13 = (date(2013, 7, 1), date(2013, 7, 31))


@unittest.skipUnless(POSTGRES, "findings need Postgres (set SQLALCHEMY_DATABASE_URI)")
class ReviewFindingsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict("stk.websocket._clients", {}, clear=True))
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
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
            analyst = await s.scalar(select(Role).where(Role.name == "analyst"))
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
            self.user_id = user.id
            # Another person accepted the claims, so the analyst reviews fresh work.
            clerk, self.clerk_key = await attester(s, "clerk")
            agent = Principal(kind="agent", name="discover")
            place = Place(
                name_ar="الفرع 999",
                name_en="Branch 999",
                kind="facility",
                geom=point(36, 33),
                geo_source="analyst",
                source="person",
            )
            s.add_all([agent, place])
            await s.flush()
            self.clerk, self.agent, self.place = clerk.id, agent.id, place.id
            self.people = [f"person:{uuid.uuid4()}" for _ in range(3)]
            self.members = []
            for i, person in enumerate(self.people):
                st = await self.claim(s, person, i)
                self.members.append(str(st.id))
            run = Run(
                principal_id=self.agent,
                tool="discover",
                route="local",
                inputs={"investigation": "case-f"},
            )
            s.add(run)
        assert await work_once(ext.async_session_factory, ["discover"])
        async with ext.async_session_factory() as s:
            keys = (
                await s.execute(select(FindingKey).where(FindingKey.state == "open"))
            ).scalars()
            by_kind = {k.kind: k.key for k in keys}
        self.fid = by_kind["candidate_incident"]
        self.shared = by_kind["shared_presence"]

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def claim(self, s, person, i):
        buf = BytesIO()
        Image.new("RGB", (8, 8), (i + 1, 0, 0)).save(buf, "PNG")
        sha = (await blobs.put(s, buf.getvalue(), "image/png", Config.BLOB_DIR)).sha256
        await record_acquisition(
            s,
            blob_sha256=sha,
            origin=f"device:{i}",
            collector_id=self.clerk,
            at=datetime.now(UTC),
            context={"investigation": "case-f"},
            sensitivity="sensitive",
            doc_type="register",
        )
        st = await append(
            s,
            StatementIn(
                subject=person,
                prop=Prop.DETAINED_AT,
                value={"text": "الفرع 999", "lang": "ar"},
                author_id=self.clerk,
                span_blob=sha,
                span={"page": 1, "bbox": [0, 0, 4, 4]},
                valid_from=JUL13[0],
                valid_to=JUL13[1],
            ),
            sign=self.clerk_key.sig,
        )
        await append(
            s,
            resolve.confirmation(st.id, self.place, "الفرع 999", self.clerk),
            sign=self.clerk_key.sig,
        )
        return st

    async def client(self, email="analyst@example.com"):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(email, "/discover")
        self.assertEqual(
            (await client.get(f"/_test/login?token={token}")).status_code, 302
        )
        page = await (await client.get("/discover")).get_data(as_text=True)
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        client.csrf = {"X-CSRFToken": csrf}
        return client

    async def post(self, client, path, item):
        r = await client.post(path, json={"item": item}, headers=client.csrf)
        return r.status_code, await r.get_json()

    async def sign(self, client, path, item):
        status, body = await self.post(client, path, item)
        self.assertEqual(status, 200, body)
        return await self.post(
            client,
            "/api/review/commit",
            {"hash": body["hash"], "credential": self.passkey.assert_(body["hash"])},
        )

    async def accept(self, client, **item):
        item = {"finding": self.fid, "kept": self.members, "title": "x"} | item
        status, out = await self.sign(client, "/api/review/findings/options", item)
        self.assertEqual(status, 200, out)
        return out

    async def gesture_rows(self, gesture):
        async with ext.async_session_factory() as s:
            return (
                await s.execute(
                    select(Statement, Payload.value, StatementTier.tier)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .where(Payload.value["gesture"].astext == gesture)
                )
            ).all()

    async def current_incidents(self):
        async with ext.async_session_factory() as s:
            return await findings.incidents(s, None)

    async def state(self, key):
        async with ext.async_session_factory() as s:
            return (await s.get(FindingKey, key)).state

    async def analyst(self, s):
        return await s.get(User, self.user_id)

    async def test_queue_reason_band_and_members(self):
        client = await self.client()
        r = await client.get("/api/discover/findings?investigation=case-f")
        items = (await r.get_json())["items"]
        assert r.headers["Cache-Control"] == "no-store"
        by = {i["id"]: i for i in items}
        assert by[self.shared]["reason"] == "3 people held at Branch 999 in July 2013"
        assert by[self.fid]["band"] in ("strong", "moderate", "weak")
        assert {m["statement"] for m in by[self.fid]["members"]} == set(self.members)
        assert "0." not in str(by[self.fid]["band"])
        two = findings.reason(findings.discovery.SHARED, 2, ["Branch 999"], "2013-07")
        assert two == "Both held at Branch 999 in July 2013"
        r = await client.get("/api/discover/findings?investigation=case-other")
        assert (await r.get_json())["items"] == []

    async def test_accept_candidate_writes_one_gesture(self):
        client = await self.client()
        out = await self.accept(
            client, kept=self.members[:-1], title="Branch 999, July 2013"
        )
        rows = await self.gesture_rows(out["gesture"])
        props = [st.prop for st, _, _ in rows]
        assert set(props) == {
            Prop.INCIDENT,
            Prop.OCCURRED_AT,
            Prop.INVOLVED,
            Prop.SUPPORTED_BY,
            Prop.REVIEW,
        }
        assert props.count(Prop.SUPPORTED_BY) == len(self.members) - 1
        assert props.count(Prop.INVOLVED) == len(self.members) - 1
        assert all(
            v["role"] == "unspecified" for st, v, _ in rows if st.prop == "involved"
        )
        assert all(t == "accepted" for _, _, t in rows)
        [occurred] = [st for st, _, _ in rows if st.prop == Prop.OCCURRED_AT]
        assert (occurred.valid_from, occurred.valid_to) == JUL13
        # One assertion signed every row; each verifies on its own.
        sigs = {orjson_key(st.sig) for st, _, _ in rows}
        assert len(sigs) == 1
        hashes = rows[0][0].sig["batch"]
        assert rows[0][0].sig["challenge"] == gesture_digest(hashes)
        assert sorted(hashes) == sorted(st.hash for st, _, _ in rows)
        assert await self.state(self.fid) == "accepted"
        [incident] = await self.current_incidents()
        assert incident["title"] == "Branch 999, July 2013"
        async with ext.async_session_factory() as s:
            assert await verify_db(s) == []
            assert await findings.queue(s, await self.analyst(s), None, self.fid) == []

    async def test_roles_set_at_acceptance(self):
        client = await self.client()
        async with ext.async_session_factory() as s:
            [f] = await findings.queue(s, await self.analyst(s), None, self.fid)
        victim = f["people"][0]
        status, body = await self.post(
            client,
            "/api/review/findings/options",
            {
                "finding": self.fid,
                "kept": self.members,
                "title": "x",
                "roles": {victim: "boss"},
            },
        )
        assert status == 400, body
        # The older word is no longer offered; old statements that carry it stay.
        status, _ = await self.post(
            client,
            "/api/review/findings/options",
            {
                "finding": self.fid,
                "kept": self.members,
                "title": "x",
                "roles": {victim: "victim"},
            },
        )
        assert status == 400
        out = await self.accept(client, roles={victim: "injured_party"})
        roles = {
            v["person"]: v["role"]
            for st, v, _ in await self.gesture_rows(out["gesture"])
            if st.prop == Prop.INVOLVED
        }
        assert roles[victim] == "injured_party"
        assert sorted(set(roles.values())) == ["injured_party", "unspecified"]
        incident = next(
            s for s in out["statements"] if s["subject"].startswith("incident:")
        )["subject"]
        r = await client.get(f"/api/incidents/{incident.removeprefix('incident:')}")
        people = (await r.get_json())["relations"]["people"]
        assert sorted(r for p in people for r in p["roles"]) == [
            "Injured party",
            "Role not set",
            "Role not set",
        ]

    async def test_violation_categories_on_an_incident(self):
        client = await self.client()
        out = await self.accept(client, title="Branch 999, July 2013")
        incident = next(
            s for s in out["statements"] if s["subject"].startswith("incident:")
        )["subject"]
        path = "/api/review/incidents/violation/options"
        status, _ = await self.post(
            client,
            path,
            {"incident": incident, "kind": "claimed", "category": "Not in the list"},
        )
        assert status == 400
        _, staged = await self.post(
            client,
            path,
            {"incident": incident, "kind": "potential", "category": "War Crime"},
        )
        assert staged["rows"][0]["summary"] == "possible violation: War Crime"
        await self.sign(
            client,
            path,
            {
                "incident": incident,
                "kind": "claimed",
                "category": "Torture/Cruel or Inhuman Treatment",
            },
        )
        await self.sign(
            client,
            path,
            {"incident": incident, "kind": "potential", "category": "War Crime"},
        )
        r = await client.get(f"/api/incidents/{incident.removeprefix('incident:')}")
        found = await r.get_json()
        assert found["violations"] == {
            "potential": ["War Crime"],
            "claimed": ["Torture/Cruel or Inhuman Treatment"],
        }
        r = await client.get("/api/incidents?investigation=case-f")
        [row] = (await r.get_json())["items"]
        assert row["category"] == "War Crime"
        assert (await client.get("/incidents")).status_code == 200

    async def test_undo_gesture_retracts_all(self):
        client = await self.client()
        out = await self.accept(client)
        status, undone = await self.sign(
            client, "/api/review/undo", {"gesture": out["gesture"]}
        )
        assert status == 200, undone
        assert await self.current_incidents() == []
        assert all(
            t == "rejected" for _, _, t in await self.gesture_rows(out["gesture"])
        )
        assert await self.state(self.fid) == "open"
        status, body = await self.post(
            client, "/api/review/undo", {"gesture": out["gesture"]}
        )
        assert status == 404, body
        async with ext.async_session_factory() as s:
            assert await verify_db(s) == []

    async def test_second_analyst_cannot_undo_a_gesture(self):
        out = await self.accept(await self.client())
        other = SoftPasskey()
        async with ext.async_session_factory() as s, s.begin():
            role = await s.scalar(select(Role).where(Role.name == "analyst"))
            user = User(
                email="second@example.com",
                name="second",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            user.roles.append(role)
            s.add(user)
            await s.flush()
            s.add(other.register(user))
        second = await self.client("second@example.com")
        for item in (
            {"gesture": out["gesture"]},
            {"gesture": out["gesture"], "statements": [out["statements"][0]["id"]]},
        ):
            status, body = await self.post(second, "/api/review/undo", item)
            assert status == 403, body
        assert all(t == "accepted" for *_, t in await self.gesture_rows(out["gesture"]))
        assert await self.state(self.fid) == "accepted"

    async def test_staged_rows_show_what_is_signed(self):
        client = await self.client()
        status, body = await self.post(
            client,
            "/api/review/findings/options",
            {
                "finding": self.fid,
                "kept": self.members,
                "title": "Branch 999, July 2013",
            },
        )
        assert status == 200, body
        rows = body["rows"]
        assert body["hash"] == gesture_digest([r["hash"] for r in rows])
        assert [r["prop"] for r in rows][:2] == ["incident", "occurred_at"]
        assert rows[0]["summary"] == "title \u201cBranch 999, July 2013\u201d"
        assert rows[1]["summary"] == "Branch 999, 2013-07"
        assert rows[-1]["summary"] == "accept the finding"
        assert len(rows) == 3 + 2 * len(self.members)

    async def test_undo_one_statement(self):
        client = await self.client()
        out = await self.accept(client)
        rows = await self.gesture_rows(out["gesture"])
        [one] = [st for st, _, _ in rows if st.prop == Prop.INVOLVED][:1]
        status, body = await self.sign(
            client,
            "/api/review/undo",
            {"gesture": out["gesture"], "statements": [str(one.id)]},
        )
        assert status == 200, body
        tiers = {st.id: t for st, _, t in await self.gesture_rows(out["gesture"])}
        assert tiers[one.id] == "rejected"
        assert sum(t == "accepted" for t in tiers.values()) == len(tiers) - 1
        assert await self.state(self.fid) == "accepted"

    async def test_reject_stores_key_and_reason(self):
        client = await self.client()
        status, out = await self.sign(
            client,
            "/api/review/findings/reject",
            {"finding": self.fid, "reason": "different wing"},
        )
        assert status == 200, out
        assert await self.state(self.fid) == "rejected"
        [(st, value, _)] = await self.gesture_rows(out["gesture"])
        assert st.prop == Prop.REVIEW and value["verdict"] == "reject"
        assert value["reason"] == "different wing"
        status, body = await self.post(
            client,
            "/api/review/findings/options",
            {"finding": self.fid, "kept": self.members, "title": "x"},
        )
        assert status == 404, body

    async def test_staging_refuses_a_person_without_a_passkey(self):
        from stk.user.models import WebAuthn

        async with ext.async_session_factory() as s, s.begin():
            await s.execute(delete(WebAuthn))
        client = await self.client()
        for path, item in (
            (
                "/api/review/findings/options",
                {"finding": self.fid, "kept": self.members, "title": "x"},
            ),
            ("/api/review/findings/reject", {"finding": self.fid, "reason": ""}),
        ):
            status, body = await self.post(client, path, item)
            assert status == 409 and body["reason"] == "no_passkey", (path, body)
            assert body["setup_url"].startswith("/wan-register"), body

    async def test_finding_hidden_when_one_member_invisible(self):
        # Only the member's value is gone; the finding itself still stands.
        async with ext.async_session_factory() as s, s.begin():
            st = await s.get(Statement, uuid.UUID(self.members[0]))
            await s.execute(delete(Payload).where(Payload.commitment == st.commitment))
        async with ext.async_session_factory() as s:
            assert await findings.queue(s, await self.analyst(s), None) == []
        client = await self.client()
        r = await client.get(f"/api/discover/findings/{self.fid}")
        assert r.status_code == 404

    async def test_erasing_a_person_removes_their_findings(self):
        async with ext.async_session_factory() as s, s.begin():
            await erase(s, self.people[0].removeprefix("person:"), self.clerk, "test")
        async with ext.async_session_factory() as s:
            assert await findings.queue(s, await self.analyst(s), None) == []

    async def test_accept_needs_title_for_new_incident(self):
        client = await self.client()
        status, _ = await self.post(
            client,
            "/api/review/findings/options",
            {"finding": self.fid, "kept": self.members, "title": " "},
        )
        assert status == 400
        status, _ = await self.post(
            client,
            "/api/review/findings/options",
            {"finding": self.fid, "kept": [str(uuid.uuid4())], "title": "x"},
        )
        assert status == 400

    async def test_shared_presence_attaches_to_existing_incident(self):
        client = await self.client()
        await self.accept(client, title="Branch 999, July 2013")
        [incident] = await self.current_incidents()
        out = await self.accept(
            client, finding=self.shared, title="", incident=incident["id"]
        )
        rows = await self.gesture_rows(out["gesture"])
        assert {st.prop for st, _, _ in rows} == {
            Prop.INVOLVED,
            Prop.SUPPORTED_BY,
            Prop.REVIEW,
        }
        assert all(
            st.subject == incident["id"] for st, _, _ in rows if st.prop != "review"
        )

    async def test_new_since_last_looked(self):
        client = await self.client()
        async with ext.async_session_factory() as s:
            q1 = await findings.queue(s, await self.analyst(s), None, self.fid)
        assert q1[0]["new"]
        r = await client.get(f"/api/discover/findings/{self.fid}")
        assert r.status_code == 200
        async with ext.async_session_factory() as s:
            q2 = await findings.queue(s, await self.analyst(s), None, self.fid)
            others = await findings.queue(s, await self.analyst(s), None, self.shared)
        assert not q2[0]["new"] and others[0]["new"]

    async def test_old_review_path_keeps_the_key_in_step(self):
        client = await self.client()
        async with ext.async_session_factory() as s:
            fk = await s.get(FindingKey, self.fid)
        status, body = await self.sign(
            client,
            "/api/review/options",
            {"statement": str(fk.statement_id), "verdict": "reject"},
        )
        assert status == 200, body
        assert await self.state(self.fid) == "rejected"
        status, body = await self.sign(
            client, "/api/review/options", {"undo": body["review"]}
        )
        assert status == 200, body
        assert await self.state(self.fid) == "open"

    async def test_page_renders(self):
        client = await self.client()
        r = await client.get("/discover")
        assert r.status_code == 200
        assert "Discover" in await r.get_data(as_text=True)


def orjson_key(sig):
    return (sig["challenge"], sig["signature"])


class GestureSignatureTest(unittest.TestCase):
    def test_batch_sig_covers_each_listed_row_only(self):
        from sealbook.ledger.offline_sigs import verify_statement_signature

        hashes = ["aa" * 32, "bb" * 32]
        sig = SoftPasskey().sig(gesture_digest(hashes)) | {"batch": hashes}
        for h in hashes:
            assert verify_statement_signature(h, sig)
        with self.assertRaises(ValueError):
            verify_statement_signature("cc" * 32, sig)
        with self.assertRaises(ValueError):
            verify_statement_signature("aa" * 32, sig | {"batch": hashes[:1]})
        # A single-row signature over the same bytes is not a gesture.
        assert gesture_digest(["aa" * 32]) != "aa" * 32

    def test_malformed_batches_refused(self):
        from sealbook.ledger.offline_sigs import GESTURE_MAX, verify_statement_signature

        row = "aa" * 32
        many = [f"{i:064x}" for i in range(GESTURE_MAX)] + [row]
        for batch in ([], [row, row], many, [row.upper()], [row[:-2]], "aa"):
            sig = SoftPasskey().sig(gesture_digest(list(batch))) | {"batch": batch}
            with self.assertRaises(ValueError, msg=str(batch)[:40]):
                verify_statement_signature(row, sig)
