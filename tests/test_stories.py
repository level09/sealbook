"""Story view: lenses over synthetic accepted position claims at a resolved place, with
a discovery run for pending findings and an incident written as accepted statements."""

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
from sealbook.app import app as sealbook_app
from sealbook.app import create_app
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Payload, Principal, Run, Statement
from sealbook.ledger.write import append, record_acquisition
from sealbook.people.data import erase
from sealbook.places import normalise, resolve
from sealbook.places.models import Place, PlaceName, point
from sealbook.runs.worker import work_once
from sealbook.settings import Config
from sealbook.stories.data import gaps, story
from tests.passkey import attester

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
JUL13 = (date(2013, 7, 1), date(2013, 7, 31))


class GapsTest(unittest.TestCase):
    def test_silence_runs_from_the_latest_end(self):
        def p(i, lo, hi):
            return {"id": i, "from": lo, "to": hi}

        out = gaps([p("a", "2013-07-01", "2013-07-31"), p("b", "2013-09-17", None)])
        self.assertEqual(out, [{"from": "2013-07-31", "to": "2013-09-17", "days": 48}])
        self.assertEqual(gaps([p("a", "2013-07-01", "2013-07-31")]), [])


@unittest.skipUnless(POSTGRES, "stories need Postgres (set SQLALCHEMY_DATABASE_URI)")
class StoryTest(unittest.IsolatedAsyncioTestCase):
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
            self.user_id = user.id
            clerk, self.key = await attester(s, "clerk")
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
            self.clerk, self.place = clerk.id, place.id
            self.people = [f"person:{uuid.uuid4()}" for _ in range(3)]
            self.members = []
            for i, person in enumerate(self.people):
                st = await self.claim(s, person, i, *JUL13)
                self.members.append(str(st.id))
            # A later sighting of the first person, after 48 days without evidence.
            await self.claim(s, self.people[0], 9, date(2013, 9, 17), None)
            s.add(
                Run(
                    principal_id=agent.id,
                    tool="discover",
                    route="local",
                    inputs={"investigation": "case-s"},
                )
            )
        assert await work_once(ext.async_session_factory, ["discover"])
        async with ext.async_session_factory() as s, s.begin():
            self.incident = f"incident:{uuid.uuid4()}"
            facts = [
                (Prop.INCIDENT, {"title": {"text": "Branch 999, July", "lang": "en"}}),
                (Prop.OCCURRED_AT, {"place": f"place:{self.place}"}),
                *[
                    (Prop.INVOLVED, {"person": p, "role": "victim"})
                    for p in self.people[:2]
                ],
                *[(Prop.SUPPORTED_BY, {"statement": m}) for m in self.members[:2]],
            ]
            for prop, value in facts:
                await append(
                    s,
                    StatementIn(
                        subject=self.incident,
                        prop=prop,
                        value=value,
                        author_id=self.clerk,
                        valid_from=JUL13[0] if prop == Prop.OCCURRED_AT else None,
                        valid_to=JUL13[1] if prop == Prop.OCCURRED_AT else None,
                    ),
                    sign=self.key.sig,
                )

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def claim(self, s, person, i, lo, hi):
        buf = BytesIO()
        Image.new("RGB", (8, 8), (i + 1, 0, 0)).save(buf, "PNG")
        sha = (await blobs.put(s, buf.getvalue(), "image/png", Config.BLOB_DIR)).sha256
        await record_acquisition(
            s,
            blob_sha256=sha,
            origin=f"device:{i}",
            collector_id=self.clerk,
            at=datetime.now(UTC),
            context={"investigation": "case-s"},
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
                valid_from=lo,
                valid_to=hi,
            ),
            sign=self.key.sig,
        )
        await append(
            s,
            resolve.confirmation(st.id, self.place, "الفرع 999", self.clerk),
            sign=self.key.sig,
        )
        return st

    async def story(self, lens, ref, f=None):
        async with ext.async_session_factory() as s:
            user = await s.get(User, self.user_id)
            return await story(
                s, user, lens, ref, f or {"investigation": None, "time": None}
            )

    async def client(self):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token("analyst@example.com", "/stories")
        r = await client.get(f"/_test/login?token={token}")
        self.assertEqual(r.status_code, 302)
        page = await (await client.get("/stories")).get_data(as_text=True)
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        client.csrf = {"X-CSRFToken": csrf}
        return client

    async def test_person_lens_rows_gaps_and_map(self):
        s = await self.story("person", self.people[0])
        [row] = s["rows"]
        assert row["key"] == self.people[0]
        assert row["gaps"] == [{"from": "2013-07-31", "to": "2013-09-17", "days": 48}]
        assert len(s["points"]) == 2
        assert all(p["lonlat"] == [36.0, 33.0] for p in s["points"])
        assert all(p["href"].endswith("?page=1") for p in s["points"])
        assert s["attribution"].startswith("GeoNames")

    async def test_incident_lens_rows_are_involved_people(self):
        s = await self.story("incident", self.incident)
        assert {r["key"] for r in s["rows"]} == set(self.people[:2])
        [inc] = s["incidents"]
        assert inc["title"] == "Branch 999, July" and inc["sources"] == 2
        assert inc["window"] == ["2013-07-01", "2013-07-31"]

    async def test_place_lens_counts_visible_only(self):
        s = await self.story("place", str(self.place))
        assert len(s["points"]) == 4 and len(s["rows"]) == 3
        async with ext.async_session_factory() as db, db.begin():
            st = await db.get(Statement, uuid.UUID(self.members[2]))
            await db.execute(delete(Payload).where(Payload.commitment == st.commitment))
        s = await self.story("place", str(self.place))
        assert len(s["points"]) == 3
        assert self.people[2] not in {r["key"] for r in s["rows"]}

    async def test_pending_finding_shown_as_suggestion(self):
        s = await self.story("person", self.people[0])
        assert s["suggestions"][0]["kind"] == "shared_presence"
        other = await self.story("person", f"person:{uuid.uuid4()}")
        assert other["rows"] == [] and other["suggestions"] == []

    async def test_erased_person_drops_from_story(self):
        async with ext.async_session_factory() as db, db.begin():
            await erase(db, self.people[1].removeprefix("person:"), self.clerk, "x")
        s = await self.story("incident", self.incident)
        assert {r["key"] for r in s["rows"]} == {self.people[0]}
        s = await self.story("place", str(self.place))
        assert self.people[1] not in {p["subject"] for p in s["points"]}

    async def test_filter_lens_scopes_to_investigation(self):
        s = await self.story("filter", None, {"investigation": "case-s", "time": None})
        assert len(s["rows"]) == 3
        s = await self.story("filter", None, {"investigation": "other", "time": None})
        assert s["rows"] == []

    async def test_api_logs_access_and_rejects_bad_requests(self):
        client = await self.client()
        assert (await client.get("/api/stories?lens=route")).status_code == 400
        assert (await client.get("/api/stories?lens=place&ref=x")).status_code == 400
        r = await client.get(f"/api/stories?lens=place&ref={uuid.uuid4()}")
        assert r.status_code == 404
        r = await client.get(f"/api/stories?lens=incident&ref={self.incident}")
        assert (
            r.status_code == 200 and r.headers["Cache-Control"] == "private, no-store"
        )
        # The client is told which sources to leave alone, so it never provokes a 403.
        assert all(
            isinstance(p["locked"], bool) for p in (await r.get_json())["points"]
        )
        async with ext.async_session_factory() as db:
            logged = await db.scalars(
                select(Statement.subject).where(Statement.prop == Prop.ACCESS)
            )
            assert set(self.people[:2]) <= set(logged)

    async def test_save_is_per_user_and_holds_only_the_query(self):
        client = await self.client()
        item = {"lens": "group", "ref": ",".join(self.people[:2])}
        r = await client.post(
            "/api/stories/saved", json={"item": item}, headers=client.csrf
        )
        assert r.status_code == 200
        again = await client.post(
            "/api/stories/saved", json={"item": item}, headers=client.csrf
        )
        assert (await again.get_json())["id"] == (await r.get_json())["id"]
        bad = await client.post(
            "/api/stories/saved",
            json={"item": item | {"title": "a name"}},
            headers=client.csrf,
        )
        assert bad.status_code == 400
        [saved] = (await (await client.get("/api/stories/saved")).get_json())["items"]
        assert saved["query"]["ref"] == item["ref"]
        r = await client.delete(
            f"/api/stories/saved/{saved['id']}", headers=client.csrf
        )
        assert r.status_code == 200

    async def unresolved_claim(self):
        """A claim naming Branch 999 in words no one has confirmed."""
        async with ext.async_session_factory() as s, s.begin():
            s.add(
                PlaceName(
                    place_id=self.place,
                    norm=normalise("الفرع 999"),
                    name="الفرع 999",
                    lang="ar",
                    kind="official",
                    source="person",
                )
            )
            buf = BytesIO()
            Image.new("RGB", (8, 8), (0, 9, 0)).save(buf, "PNG")
            sha = (
                await blobs.put(s, buf.getvalue(), "image/png", Config.BLOB_DIR)
            ).sha256
            await record_acquisition(
                s,
                blob_sha256=sha,
                origin="device:u",
                collector_id=self.clerk,
                at=datetime.now(UTC),
                context={"investigation": "case-s"},
                doc_type="register",
            )
            st = await append(
                s,
                StatementIn(
                    subject=self.people[2],
                    prop=Prop.SEEN_AT,
                    value={"text": "الفرع 999", "lang": "ar"},
                    author_id=self.clerk,
                    span_blob=sha,
                    span={"page": 1, "bbox": [0, 0, 4, 4]},
                    valid_from=date(2013, 8, 1),
                ),
                sign=self.key.sig,
            )
            return str(st.id)

    async def test_place_page_relations_evidence_and_unresolved(self):
        claim = await self.unresolved_claim()
        client = await self.client()
        r = await client.get(f"/api/places/{self.place}")
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "private, no-store"
        p = await r.get_json()
        assert p["claims"] == 4 and p["sources"] == 4
        assert {x["person"] for x in p["relations"]["people"]} == {
            x.removeprefix("person:") for x in self.people
        }
        [inc] = p["relations"]["incidents"]
        assert inc["id"] == self.incident.removeprefix("incident:")
        [u] = p["unresolved"]
        assert u["claims"][0]["id"] == claim
        assert [c["id"] for c in u["candidates"]] == [str(self.place)]
        assert p["spellings"] == [{"name": "الفرع 999", "claims": 4, "gazetteer": True}]
        assert p["geometry"] == {"type": "Point", "coordinates": [36, 33]}
        async with ext.async_session_factory() as db:
            logged = set(
                await db.scalars(
                    select(Statement.subject).where(Statement.prop == Prop.ACCESS)
                )
            )
        assert set(self.people) <= logged
        r = await client.get(f"/api/places/{self.place}/evidence?per_page=3")
        page = await r.get_json()
        assert page["total"] == 4 and len(page["items"]) == 3
        assert page["items"][0]["href"].endswith("?page=1")
        assert page["items"][0]["tier"] == "accepted"
        bad = await client.get(f"/api/places/{self.place}/evidence?sort=size")
        assert bad.status_code == 400
        assert (await client.get(f"/api/places/{uuid.uuid4()}")).status_code == 404
        table = await (await client.get("/api/places/table")).get_json()
        [row] = table["items"]
        assert (row["claims"], row["people"], row["sources"], row["incidents"]) == (
            4,
            3,
            4,
            1,
        )
        none = await (await client.get("/api/places/table?q=nowhere")).get_json()
        assert none == {"items": [], "total": 0}
        [u] = (await (await client.get("/api/places/unresolved")).get_json())["items"]
        assert u["count"] == 1 and u["name"] == "الفرع 999"

    async def test_incident_page_people_by_role_and_history(self):
        client = await self.client()
        ref = self.incident.removeprefix("incident:")
        r = await client.get(f"/api/incidents/{ref}")
        assert r.status_code == 200
        inc = await r.get_json()
        assert inc["title"] == "Branch 999, July"
        assert inc["window"] == ["2013-07-01", "2013-07-31"]
        people = inc["relations"]["people"]
        assert {p["person"] for p in people} == {
            x.removeprefix("person:") for x in self.people[:2]
        }
        assert all(p["roles"] == ["victim"] for p in people)
        assert inc["relations"]["places"][0]["id"] == str(self.place)
        assert {h["prop"] for h in inc["history"]} >= {"incident", "involved"}
        assert inc["undoable"] == [] and inc["signed"]["by"] == "clerk"
        ev = await (await client.get(f"/api/incidents/{ref}/evidence")).get_json()
        assert ev["total"] == 2
        assert (await client.get(f"/api/incidents/{uuid.uuid4()}")).status_code == 404
        async with ext.async_session_factory() as db, db.begin():
            for m in self.members[:2]:
                st = await db.get(Statement, uuid.UUID(m))
                await db.execute(
                    delete(Payload).where(Payload.commitment == st.commitment)
                )
        # Without visible support it stops being current, as on the story view.
        assert (await client.get(f"/api/incidents/{ref}")).status_code == 404


if __name__ == "__main__":
    unittest.main()
