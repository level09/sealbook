"""Case map: held-together bands, the path search with a weak step, open ends and the
case news, on synthetic claims at one resolved place."""

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
from sqlalchemy import select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)
from stk.user.models import Role, User

from sealbook import blobs
from sealbook.app import create_app
from sealbook.casemap import data
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Principal, Run, Statement
from sealbook.ledger.write import append, record_acquisition
from sealbook.places import resolve
from sealbook.places.models import Place, point
from sealbook.settings import Config
from tests.passkey import attester

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
CASE = "case-m"


def pt(i, person, place, lo, hi, prop="detained_at", tier="accepted"):
    return {
        "id": i,
        "subject": person,
        "prop": prop,
        "place_id": place,
        "from": lo,
        "to": hi,
        "tier": tier,
        "source": f"s{i}",
        "href": f"/evidence/s{i}",
        "file": f"Source {i}",
    }


class PureTest(unittest.TestCase):
    def view(self):
        points = [
            pt("a", "person:1", "P", "2013-07-01", "2013-07-31"),
            pt("b", "person:2", "P", "2013-07-01", "2013-07-31"),
            pt("c", "person:3", "P", "2014-02-12", "2014-02-12"),
            pt("d", "person:1", "Q", "2013-09-17", "2013-09-17", "transferred_to"),
        ]
        return {
            "rows": [{"key": f"person:{i}", "label": f"Person {i}"} for i in (1, 2, 3)],
            "points": points,
            "places": [
                {"id": "P", "en": "Branch 999", "ar": "الفرع 999"},
                {"id": "Q", "en": "Central Prison K", "ar": None},
            ],
            "bands": data.bands(points),
            "pairs": [],
        }

    def test_band_joins_people_at_one_place_at_once(self):
        [band] = self.view()["bands"]
        assert band["people"] == ["person:1", "person:2"]
        assert band["label"] == "held together, July 2013"
        assert band["from"] == "2013-07-01" and band["to"] == "2013-07-31"

    def test_path_through_a_place_months_apart_is_weak(self):
        v = self.view()
        chain = data.path(data.edges(v), "person:3", "person:1")
        steps = data.steps(chain, v, {})
        assert [s["text"] for s in steps] == [
            "Person 3 was held at Branch 999, 12 February 2014",
            "Person 1 was held at Branch 999, July 2013",
        ]
        assert steps[0]["weak"] is None
        assert steps[1]["weak"].startswith("Not at the same time: about 6 months")
        assert steps[1]["sources"] == [{"title": "Source a", "href": "/evidence/sa"}]

    def test_path_prefers_held_together_and_is_bounded(self):
        v = self.view()
        links = data.edges(v)
        [step] = data.steps(data.path(links, "person:2", "person:1"), v, {})
        assert step["kind"] == "with" and step["weak"] is None
        assert "were held together at Branch 999, July 2013" in step["text"]
        assert data.path(links, "person:3", "place:Q", depth=1) is None
        assert data.path(links, "person:3", "place:Q")[-1][0]["kind"] == "transfer"

    def test_open_end_follows_the_gap_finder(self):
        points = [pt("a", "person:1", "P", "2013-07-01", "2013-07-31")]
        [end] = data.open_ends(points, today=date(2014, 1, 1))
        assert end["label"] == "No record after 31 July 2013"
        release = mock.Mock(
            id="r", person="person:1", prop=Prop.EVENT, lo=date(2013, 8, 2)
        )
        release.hi = release.lo
        assert data.open_ends(points, [release], {"r": "released"}) == []


@unittest.skipUnless(POSTGRES, "the case map needs Postgres")
class CaseMapTest(unittest.IsolatedAsyncioTestCase):
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
        async with ext.async_session_factory() as s, s.begin():
            analyst = await s.scalar(select(Role).where(Role.name == "analyst"))
            user = User(
                email="mapper@example.com",
                name="mapper",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            user.roles.append(analyst)
            s.add(user)
            await s.flush()
            self.user_id = user.id
            self.clerk, self.key = await attester(s, "clerk")
            place = Place(
                name_ar="الفرع 999",
                name_en="Branch 999",
                kind="facility",
                geom=point(36, 33),
                geo_source="analyst",
                source="person",
                precision_m=250,
            )
            s.add(place)
            await s.flush()
            self.place = place.id
            self.people = [f"person:{uuid.uuid4()}" for _ in range(3)]
            jul = (date(2013, 7, 1), date(2013, 7, 31))
            self.claims = [
                await self.claim(s, self.people[0], 0, *jul),
                await self.claim(s, self.people[1], 1, *jul),
                await self.claim(s, self.people[2], 2, date(2014, 2, 12), None),
            ]
            await append(
                s,
                StatementIn(
                    subject=f"statement:{self.claims[0].id}",
                    prop=Prop.REVIEW,
                    value={"verdict": "accept", "gesture": str(uuid.uuid4())},
                    author_id=self.clerk.id,
                ),
                sign=self.key.sig,
            )
            agent = Principal(kind="agent", name="Linker", on_behalf_of=self.clerk.id)
            s.add(agent)
            await s.flush()
            s.add(
                Run(
                    principal_id=agent.id,
                    tool="link",
                    route="local",
                    status="done",
                    inputs={"investigation": CASE, "for_user": self.user_id},
                    finished_at=datetime.now(UTC),
                )
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
            collector_id=self.clerk.id,
            at=datetime.now(UTC),
            context={"investigation": CASE, "batch": "b1", "title": f"Roster {i}"},
            sensitivity="ordinary",
            doc_type="register",
        )
        st = await append(
            s,
            StatementIn(
                subject=person,
                prop=Prop.DETAINED_AT,
                value={"text": "الفرع 999", "lang": "ar"},
                author_id=self.clerk.id,
                span_blob=sha,
                span={"page": 1, "bbox": [0, 0, 4, 4]},
                valid_from=lo,
                valid_to=hi,
            ),
            sign=self.key.sig,
        )
        await append(
            s,
            resolve.confirmation(st.id, self.place, "الفرع 999", self.clerk.id),
            sign=self.key.sig,
        )
        return st

    async def client(self):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token("mapper@example.com", "/case-map")
        assert (await client.get(f"/_test/login?token={token}")).status_code == 302
        page = await (await client.get("/case-map")).get_data(as_text=True)
        assert re.search(r'name="csrf-token"', page)
        return client

    async def test_build_bands_and_open_ends(self):
        async with ext.async_session_factory() as s:
            user = await s.get(User, self.user_id)
            view, _ = await data.build(s, user, CASE)
        [band] = view["bands"]
        assert set(band["people"]) == set(self.people[:2])
        assert band["label"] == "held together, July 2013"
        assert {o["person"] for o in view["open_ends"]} == set(self.people)
        [place] = view["places"]
        assert place["approximate"] is False and place["en"] == "Branch 999"

    async def test_path_api_names_the_weak_step_and_logs_access(self):
        client = await self.client()
        r = await client.get(
            "/api/case-map/path",
            query_string={
                "investigation": CASE,
                "from": self.people[2],
                "to": self.people[0],
            },
        )
        body = await r.get_json()
        assert body["found"] and len(body["steps"]) == 2
        assert body["steps"][1]["weak"].startswith("Not at the same time")
        async with ext.async_session_factory() as s:
            logged = await s.scalars(
                select(Statement.subject).where(Statement.prop == Prop.ACCESS)
            )
            assert {self.people[0], self.people[2]} <= set(logged)
        r = await client.get(
            "/api/case-map/path",
            query_string={"investigation": CASE, "from": "x", "to": self.people[0]},
        )
        assert r.status_code == 404

    async def test_news_lists_arrivals_agents_and_decisions(self):
        async with ext.async_session_factory() as s:
            items = await data.news(s, CASE)
        texts = {i["kind"]: i["text"] for i in items}
        assert texts["arrival"] == (
            "Added 3 sources: Roster 0; Roster 1 and 1 more, naming 3 people, "
            "all new to the case"
        )
        assert texts["agent"] == "Looked for links and proposed none"
        assert texts["decision"] == "Accepted 1 claim: detained at"
        assert [i["who"] for i in items if i["kind"] == "agent"] == ["Linker for clerk"]

    async def test_monitor_moves_to_the_case_map(self):
        client = await self.client()
        r = await client.get("/monitor")
        assert r.status_code == 302 and r.headers["Location"].endswith("/case-map")
        r = await client.get("/monitor?statement=x")
        assert "/monitor/runs" in r.headers["Location"]
        assert (await client.get("/monitor/runs")).status_code == 403
