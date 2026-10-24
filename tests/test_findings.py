"""Discovery findings: pure candidate rules, then the bounded worker run against a
throwaway Postgres with synthetic, human-accepted position claims."""

import tempfile
import unittest
import uuid
from datetime import UTC, date, datetime
from io import BytesIO
from unittest import mock

from PIL import Image
from sqlalchemy import func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from stk import blobs
from stk.discovery.findings import (
    INCIDENT,
    SHARED,
    candidate_incidents,
    key,
    rank,
    shared_presence,
)
from stk.discovery.models import FindingKey, FindingSalt
from stk.ledger.contracts import Prop, StatementIn
from stk.ledger.models import Payload, Principal, Run, Statement, StatementTier
from stk.ledger.write import append, record_acquisition, verify_db
from stk.migrations import (
    build_alembic_config,
    get_database_url,
    throwaway_database_url,
)
from stk.people.data import erase
from stk.places import resolve
from stk.places.models import Place, point
from stk.runs.worker import work_once
from stk.settings import Config
from tests.passkey import attester

POSTGRES = make_url(get_database_url()).get_backend_name() == "postgresql"
JUL13 = (date(2013, 7, 1), date(2013, 7, 31))
AUG13 = (date(2013, 8, 1), date(2013, 8, 31))


def pt(i, person, place, span, source=None):
    return {
        "id": i,
        "person": f"person:{person}",
        "place": f"place:{place}",
        "interval": span,
        "source": source or i,
    }


class RulesTest(unittest.TestCase):
    def test_shared_presence_two_people_same_place_month(self):
        c = shared_presence(
            [pt("a", "P1", "b999", JUL13), pt("b", "P2", "b999", JUL13)]
        )
        assert len(c) == 1 and c[0]["features"] == {
            "overlap": 1.0,
            "days": 31,
            "sources": 2,
        }
        assert c[0]["people"] == ["person:P1", "person:P2"]
        assert c[0]["members"] == ["a", "b"] and c[0]["window"] == JUL13
        assert c[0]["month"] == "2013-07"

    def test_different_places_no_finding(self):
        assert (
            shared_presence(
                [pt("a", "P1", "b999", JUL13), pt("b", "P2", "branch_x", JUL13)]
            )
            == []
        )

    def test_near_places_join_and_name_their_distance(self):
        gap = {("place:a", "place:b"): {"m": 420, "precision_m": [250, 500]}}
        pts = [pt("x", "P1", "a", JUL13), pt("y", "P2", "b", JUL13)]
        [c] = shared_presence(pts, gap)
        assert c["places"] == ["place:a", "place:b"] and c["members"] == ["x", "y"]
        assert c["features"]["distance_m"] == 420
        assert c["features"]["precision_m"] == [250, 500]
        assert c["at"] == {"x": "place:a", "y": "place:b"}
        # One person at each of two near places, no one in common: nothing more.
        assert shared_presence(pts) == []

    def test_a_near_pair_never_repeats_a_same_place_set(self):
        gap = {("place:a", "place:b"): {"m": 100, "precision_m": [250, 250]}}
        pts = [pt("x", "P1", "a", JUL13), pt("y", "P2", "a", JUL13)]
        pts.append(pt("z", "P3", "b", AUG13))
        found = shared_presence(pts, gap)
        assert [c["places"] for c in found] == [["place:a"]]
        assert "distance_m" not in found[0]["features"]

    def test_reason_names_distance_and_precisions(self):
        from stk.review.findings import reason

        f = {"distance_m": 1250, "precision_m": [250, 1000]}
        text = reason(SHARED, 2, ["Branch 999", "Al Jadida"], "2013-07", f)
        assert text == (
            "Both held 1.2 km apart, at Branch 999 and Al Jadida, in July 2013 "
            "(places known to within 250 m and 1 km)"
        )
        assert reason(SHARED, 3, ["Branch 999"], "2013-07") == (
            "3 people held at Branch 999 in July 2013"
        )

    def test_disjoint_dates_no_finding(self):
        assert (
            shared_presence([pt("a", "P1", "x", JUL13), pt("b", "P2", "x", AUG13)])
            == []
        )

    def test_a_day_claim_does_not_weaken_a_month_overlap(self):
        day = (date(2013, 7, 5), date(2013, 7, 5))
        [c] = shared_presence(
            [
                pt("a", "P1", "x", JUL13),
                pt("d", "P1", "x", day),
                pt("b", "P2", "x", JUL13),
            ]
        )
        assert c["features"]["overlap"] == 1.0 and c["members"] == ["a", "b", "d"]

    def test_three_together_is_one_set_not_three_pairs(self):
        c = shared_presence([pt(i, i.upper(), "x", JUL13) for i in "abc"])
        assert [x["people"] for x in c] == [["person:A", "person:B", "person:C"]]

    def test_key_has_no_identifiers(self):
        salt = b"s" * 32

        def k(kind=SHARED, inv="case-a", people=("P1", "P2"), month="2013-07", s=salt):
            return key(
                s, kind, inv, [f"person:{p}" for p in people], ["place:x"], month
            )

        assert "P1" not in k() and "case" not in k() and len(k()) == 64
        assert k() == k(people=("P2", "P1"))
        others = {k(INCIDENT), k(inv="case-b"), k(month="2013-08"), k(s=b"t" * 32)}
        assert k() not in others and len(others) == 4

    def test_a_later_month_is_a_new_candidate(self):
        sep = (date(2013, 9, 1), date(2013, 9, 30))
        pts = [pt(i, i.upper(), "x", JUL13, f"doc{i}") for i in "abc"] + [
            pt(i + "2", i.upper(), "x", sep, f"doc{i}2") for i in "abc"
        ]
        shared = shared_presence(pts)
        incidents = candidate_incidents(pts)
        assert sorted(c["month"] for c in shared) == ["2013-07", "2013-09"]
        assert sorted(c["month"] for c in incidents) == ["2013-07", "2013-09"]

    def test_candidate_incident_needs_people_sources_and_a_window(self):
        dense = [
            pt("a", "A", "x", JUL13, "doc1"),
            pt("b", "B", "x", JUL13, "doc1"),
            pt("c", "C", "x", (date(2013, 7, 10),) * 2, "doc2"),
        ]
        [c] = candidate_incidents(dense)
        assert c["kind"] == INCIDENT and c["members"] == ["a", "b", "c"]
        assert c["features"] == {
            "people": 3,
            "sources": 2,
            "window": 14,
            "span_days": 31,
        }
        assert candidate_incidents(dense, min_sources=3) == []
        late = dense[:2] + [pt("c", "C", "x", AUG13, "doc2")]
        assert candidate_incidents(late) == []

    def test_rank(self):
        c = {"people": ["a", "b"], "features": {"sources": 3, "overlap": 0.5}}
        assert rank(c) == 30 + 10 + 5


@unittest.skipUnless(POSTGRES, "findings need Postgres (set SQLALCHEMY_DATABASE_URI)")
class DiscoverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(throwaway_database_url(sync=True))
        config = build_alembic_config()
        config.config_file_name = None
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        self.url = make_url(url).set(drivername="postgresql+asyncpg")
        blob_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(Config, "BLOB_DIR", blob_dir))

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.url)
        self.addAsyncCleanup(self.engine.dispose)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.addAsyncCleanup(self.db.close)
        self.human, self.passkey = await attester(self.db, "reviewer")
        self.agent = Principal(kind="agent", name="discover")
        self.db.add(self.agent)
        self.places = []
        for i in range(5):
            place = Place(
                name_ar=f"موقع {i}",
                kind="facility",
                geom=point(36, 33),
                geo_source="analyst",
                source="person",
            )
            self.db.add(place)
            self.places.append(place)
        await self.db.flush()
        # Plain values: objects go stale once the worker commits elsewhere.
        self.human_id, self.agent_id = self.human.id, self.agent.id
        self.places = [(p.id, p.name_ar) for p in self.places]
        self.docs = 0

    async def doc(self, inv="case-a"):
        """A new source document: one acquisition of a distinct image."""
        self.docs += 1
        buf = BytesIO()
        Image.new("RGB", (8, 8), (self.docs, 0, 0)).save(buf, "PNG")
        sha = (
            await blobs.put(self.db, buf.getvalue(), "image/png", Config.BLOB_DIR)
        ).sha256
        await record_acquisition(
            self.db,
            blob_sha256=sha,
            origin=f"device:{self.docs}",
            collector_id=self.human_id,
            at=datetime.now(UTC),
            context={"investigation": inv},
            sensitivity="sensitive",
            doc_type="register",
        )
        return sha

    async def say(self, subject, prop, value, **kw):
        return await append(
            self.db,
            StatementIn(
                subject=subject, prop=prop, value=value, author_id=self.human_id, **kw
            ),
            sign=self.passkey.sig,
        )

    async def claim(self, person, place=0, span=JUL13, inv="case-a", located=True):
        """An accepted detained_at claim, linked by a person to its place."""
        place_id, name = self.places[place]
        st = await self.say(
            person,
            Prop.DETAINED_AT,
            {"text": name, "lang": "ar"},
            span_blob=await self.doc(inv),
            span={"page": 1, "bbox": [0, 0, 4, 4]},
            valid_from=span and span[0],
            valid_to=span and span[1],
        )
        if located:
            await append(
                self.db,
                resolve.confirmation(st.id, place_id, name, self.human_id),
                sign=self.passkey.sig,
            )
        return st

    async def run_discover(self, inv="case-a"):
        run = Run(
            principal_id=self.agent_id,
            tool="discover",
            route="local",
            inputs={"investigation": inv},
        )
        self.db.add(run)
        await self.db.flush()
        run_id = run.id
        await self.db.commit()
        assert await work_once(self.Session, ["discover"])
        run = await self.db.get(Run, run_id, populate_existing=True)
        assert run.status == "done", run.error
        return run

    async def states(self):
        rows = await self.db.execute(
            select(FindingKey.state, func.count()).group_by(FindingKey.state)
        )
        return dict(rows.all())

    async def findings(self):
        q = (
            select(Statement, Payload.value)
            .outerjoin(Payload, Payload.commitment == Statement.commitment)
            .where(Statement.prop == Prop.FINDING)
            .order_by(Statement.recorded_at)
        )
        return (await self.db.execute(q)).all()

    async def set_state(self, state, where):
        await self.db.execute(
            update(FindingKey).where(FindingKey.state == where).values(state=state)
        )
        await self.db.commit()

    async def pair(self, place=0):
        a, b = f"person:{uuid.uuid4()}", f"person:{uuid.uuid4()}"
        return a, b, [await self.claim(a, place), await self.claim(b, place)]

    async def test_near_pairs_need_both_precisions_within_reach(self):
        from stk.discovery.findings import near

        ids = []
        for lon, prec in (
            (36.2900, 250),
            (36.2945, 500),
            (36.3200, 250),
            (36.2901, None),
        ):
            p = Place(
                name_ar="موقع قريب",
                kind="facility",
                geom=point(lon, 33.5),
                precision_m=prec,
                geo_source="analyst",
                source="person",
            )
            self.db.add(p)
            await self.db.flush()
            ids.append(f"place:{p.id}")
        pairs = await near(self.db, ids)
        # 417 m apart, within the larger precision (500 m); the far one and the
        # one without a precision join nothing.
        [(key, gap)] = pairs.items()
        assert set(key) == {ids[0], ids[1]} and 400 < gap["m"] < 440
        assert sorted(gap["precision_m"]) == [250, 500]

    async def test_discover_joins_two_near_places(self):
        for i, lon in ((0, 36.2900), (1, 36.2945)):
            place = await self.db.get(Place, self.places[i][0])
            place.geom, place.precision_m = point(lon, 33.5), 500
        await self.db.flush()
        a, b = f"person:{uuid.uuid4()}", f"person:{uuid.uuid4()}"
        x, y = await self.claim(a, 0), await self.claim(b, 1)
        await self.run_discover()
        [(_, value)] = await self.findings()
        assert value["places"] == sorted(f"place:{self.places[i][0]}" for i in (0, 1))
        assert value["at"] == {
            str(x.id): f"place:{self.places[0][0]}",
            str(y.id): f"place:{self.places[1][0]}",
        }
        assert 400 < value["features"]["distance_m"] < 440
        assert value["features"]["precision_m"] == [500, 500]

    async def test_finding_is_a_signed_agent_proposal_with_its_run(self):
        a, b, claims = await self.pair()
        run = await self.run_discover()
        [(st, value)] = await self.findings()
        assert st.author_id == self.agent_id and st.run_id == run.id
        assert st.sig["kind"] == "ed25519" and run.output_hash
        tier = await self.db.get(StatementTier, st.id)
        assert tier.tier == "unconfirmed"
        assert value["kind"] == SHARED and value["people"] == sorted([a, b])
        assert value["members"] == sorted(str(c.id) for c in claims)
        assert value["features"] == {"overlap": "1.00", "days": 31, "sources": 2}
        assert value["window"] == ["2013-07-01", "2013-07-31"]
        assert st.subject == f"finding:{value['key']}"
        fk = await self.db.get(FindingKey, value["key"], populate_existing=True)
        assert fk.state == "open" and fk.statement_id == st.id
        assert await verify_db(self.db) == []

    async def test_cap_defers_beyond_budget(self):
        for place in range(5):
            await self.pair(place)
        with mock.patch.object(Config, "FINDINGS_PER_KIND", 2):
            await self.run_discover()
            assert await self.states() == {"open": 2, "deferred": 3}
            # Nothing reviewed yet: the queue is full, so nothing more is released.
            await self.run_discover()
            assert await self.states() == {"open": 2, "deferred": 3}
            # The review flow sets this; it lands with the findings queue.
            await self.set_state("rejected", "open")
            await self.run_discover()
            assert await self.states() == {"rejected": 2, "open": 2, "deferred": 1}
        assert len(await self.findings()) == 4

    async def test_rejected_set_not_reproposed_new_evidence_noted(self):
        a, b, _ = await self.pair()
        await self.run_discover()
        [(_, value)] = await self.findings()
        await self.set_state("rejected", "open")
        third = await self.claim(a)
        await self.run_discover()
        fk = await self.db.get(FindingKey, value["key"], populate_existing=True)
        assert fk.state == "rejected" and fk.notes == [str(third.id)]
        assert "open" not in await self.states()
        await self.run_discover()
        assert (
            await self.db.get(FindingKey, value["key"], populate_existing=True)
        ).notes == [str(third.id)]

    async def test_accepted_never_returns_but_a_superset_is_new(self):
        a, b, _ = await self.pair()
        await self.run_discover()
        await self.set_state("accepted", "open")
        await self.run_discover()
        assert await self.states() == {"accepted": 1}
        c = f"person:{uuid.uuid4()}"
        await self.claim(c)
        await self.run_discover()
        opened = [v for _, v in await self.findings()][1:]
        assert {v["kind"] for v in opened} == {SHARED, INCIDENT}
        assert all(v["people"] == sorted([a, b, c]) for v in opened)

    async def test_unresolved_or_undated_never_used(self):
        a, b = f"person:{uuid.uuid4()}", f"person:{uuid.uuid4()}"
        await self.claim(a)
        await self.claim(b, located=False)
        c, d = f"person:{uuid.uuid4()}", f"person:{uuid.uuid4()}"
        await self.claim(c, place=1)
        await self.claim(d, place=1, span=None)
        await self.run_discover()
        assert await self.findings() == [] and await self.states() == {}

    async def test_other_investigation_never_joins(self):
        a, b = f"person:{uuid.uuid4()}", f"person:{uuid.uuid4()}"
        await self.claim(a, inv="case-a")
        await self.claim(b, inv="case-b")
        await self.run_discover("case-a")
        await self.run_discover("case-b")
        assert await self.findings() == []

    async def test_a_decision_in_one_investigation_never_reaches_another(self):
        a, b = f"person:{uuid.uuid4()}", f"person:{uuid.uuid4()}"
        for inv in ("case-a", "case-b"):
            await self.claim(a, inv=inv)
            await self.claim(b, inv=inv)
        await self.run_discover("case-a")
        await self.set_state("rejected", "open")
        await self.run_discover("case-b")
        rows = (
            await self.db.execute(
                select(FindingKey.investigation, FindingKey.state).order_by(
                    FindingKey.investigation
                )
            )
        ).all()
        assert rows == [("case-a", "rejected"), ("case-b", "open")]

    async def test_key_uses_the_stored_salt_not_secret_key(self):
        a, b, _ = await self.pair()
        await self.run_discover()
        [(_, value)] = await self.findings()
        stored = await self.db.scalar(select(FindingSalt.salt))
        assert len(stored) == 32
        expect = key(
            stored, SHARED, "case-a", value["people"], value["places"], "2013-07"
        )
        assert value["key"] == expect and value["month"] == "2013-07"
        await self.set_state("rejected", "open")
        with mock.patch.object(Config, "SECRET_KEY", "rotated-" + Config.SECRET_KEY):
            await self.run_discover()
        assert await self.states() == {"rejected": 1}

    async def test_erase_reaches_findings(self):
        a, b, _ = await self.pair()
        await self.run_discover()
        [(_, value)] = await self.findings()
        await erase(self.db, a, self.human_id, "request")
        await self.db.commit()
        assert [v for _, v in await self.findings()] == [None]
        fk = await self.db.get(FindingKey, value["key"], populate_existing=True)
        assert fk.members is None and fk.features is None
        assert fk.state == "open" and fk.key == value["key"]
        assert await verify_db(self.db) == []
