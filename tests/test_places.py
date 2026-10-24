"""Gazetteer import against a throwaway Postgres database migrated to head."""

import re
import tempfile
import unittest
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PIL import Image
from quart_security import hash_password
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

import stk.extensions as ext
from alembic import command
from stk import blobs
from stk.agent_login import create_agent_login_token
from stk.discovery.models import FindingKey
from stk.ledger.contracts import Prop, StatementIn
from stk.ledger.models import Principal, Run, RunEvent, Statement, StatementTier
from stk.ledger.write import append, record_acquisition
from stk.migrations import (
    build_alembic_config,
    get_database_url,
    throwaway_database_url,
)
from stk.places import boundaries, normalise, resolve
from stk.places import cli as places_cli
from stk.places.models import AdminArea, BoundarySet, Place, PlaceName, point
from stk.settings import Config
from stk.user.models import Role, User
from tests.passkey import SoftPasskey, attester
from tests.test_people import _app

FIXTURE = places_cli.FIXTURE
POSTGRES = make_url(get_database_url()).get_backend_name() == "postgresql"


async def names_matching(db, norm):
    q = (
        select(PlaceName)
        .where(PlaceName.norm == norm)
        .options(selectinload(PlaceName.place).selectinload(Place.governorate))
    )
    return (await db.execute(q)).scalars().all()


async def count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


class NormaliseTest(unittest.TestCase):
    def test_folds_arabic_variants_case_and_space(self):
        self.assertEqual(normalise("  Al  Jadida "), "al jadida")
        self.assertEqual(normalise("إدلب"), normalise("ادلب"))


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(throwaway_database_url(sync=True))
        config = build_alembic_config()
        config.config_file_name = None
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        self.url = make_url(url).set(drivername="postgresql+asyncpg")

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.url)
        self.addAsyncCleanup(self.engine.dispose)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.addAsyncCleanup(self.db.close)


@unittest.skipUnless(POSTGRES, "places need Postgres (set SQLALCHEMY_DATABASE_URI)")
class PlacesTest(Base):
    async def test_import_fixture_idempotent(self):
        a = await places_cli.import_file(self.db, FIXTURE)
        b = await places_cli.import_file(self.db, FIXTURE)
        assert a["imported"] > 0 and b == {"imported": 0, "updated": 0}
        assert await count(self.db, Place) == a["imported"]

    async def test_aliases_indexed_by_norm(self):
        await places_cli.import_file(self.db, FIXTURE)
        rows = await names_matching(self.db, normalise("حرستا"))
        assert len(rows) == 1
        assert rows[0].place.governorate.name_en == "Rif Dimashq Governorate"

    async def test_ambiguous_name_has_two_places_in_two_governorates(self):
        await places_cli.import_file(self.db, FIXTURE)
        rows = await names_matching(self.db, normalise("الجديدة"))
        assert len(rows) == 2
        assert len({r.place.governorate_id for r in rows}) == 2

    async def test_person_sites_marked_and_changes_counted_as_updates(self):
        await places_cli.import_file(self.db, FIXTURE)
        sites = (
            (await self.db.execute(select(Place).where(Place.source == "person")))
            .scalars()
            .all()
        )
        assert {p.name_ar for p in sites} == {"الفرع 999", "سجن ك المركزي"}
        sites[0].geom = point(sites[0].lon, sites[0].lat + 1)
        again = await places_cli.import_file(self.db, FIXTURE)
        assert again == {"imported": 0, "updated": 1}

    async def test_finding_key_round_trips(self):
        from stk.ledger.models import Principal, Run

        agent = Principal(kind="agent", name="discover", on_behalf_of=None)
        self.db.add(agent)
        await self.db.flush()
        run = Run(
            principal_id=agent.id, tool="discover", route="local", pubkey=b"k" * 32
        )
        self.db.add(run)
        await self.db.flush()
        self.db.add(
            FindingKey(
                key="ab" * 32,
                kind="same_place",
                investigation="case-a",
                state="deferred",
                rank=1,
                run_id=run.id,
                members=[],
                features={},
                notes=[],
            )
        )
        await self.db.flush()
        assert (await self.db.get(FindingKey, "ab" * 32)).state == "deferred"


class ResolveCase(Base):
    """Two cases, the gazetteer fixture and helpers to state and confirm claims."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.enterContext(
            mock.patch.object(
                Config, "BLOB_DIR", self.enterContext(tempfile.TemporaryDirectory())
            )
        )
        await places_cli.import_file(self.db, FIXTURE)
        self.human, self.passkey = await attester(self.db, "reviewer")
        self.blobs = {}
        for case, color in (("case-a", "white"), ("case-b", "black")):
            buf = BytesIO()
            Image.new("RGB", (8, 8), color).save(buf, "PNG")
            sha = (
                await blobs.put(self.db, buf.getvalue(), "image/png", Config.BLOB_DIR)
            ).sha256
            await record_acquisition(
                self.db,
                blob_sha256=sha,
                origin=f"device:{case}",
                collector_id=self.human.id,
                at=datetime.now(UTC),
                context={"investigation": case},
                sensitivity="sensitive",
                doc_type="register",
            )
            self.blobs[case] = sha

    async def place(self, name, kind=None):
        found = await resolve.candidates(self.db, name)
        return next(p for p in found if kind in (None, p.kind))

    async def position(self, name, inv="case-a"):
        return await self.say(
            f"person:{uuid.uuid4()}",
            Prop.SEEN_AT,
            {"text": name, "lang": "ar"},
            span_blob=self.blobs[inv],
            span={"page": 1, "bbox": [0, 0, 4, 4]},
            valid_from=date(2013, 5, 2),
        )

    async def say(self, subject, prop, value, **kw):
        return await append(
            self.db,
            StatementIn(
                subject=subject, prop=prop, value=value, author_id=self.human.id, **kw
            ),
            sign=self.passkey.sig,
        )

    async def accept(self, claim, place, name=None):
        return await self.say(
            f"statement:{claim.id}",
            Prop.LOCATED_AT,
            {"place": f"place:{place.id}", "name": normalise(name or place.name_ar)},
        )


@unittest.skipUnless(POSTGRES, "places need Postgres (set SQLALCHEMY_DATABASE_URI)")
class ResolveTest(ResolveCase):
    async def test_ambiguous_name_never_autoresolves(self):
        other = await self.position("الجديدة")
        quarter = await self.place("الجديدة", "neighbourhood")
        await self.accept(other, quarter)
        st = await self.position("الجديدة")
        r = await resolve.resolved(self.db, [other.id, st.id], "case-a")
        assert r[other.id].id == quarter.id and r[st.id] is None
        assert len(await resolve.candidates(self.db, "الجديدة")) == 2

    async def test_variant_resolves_within_investigation_only(self):
        harasta = await self.place("حرستا", "populated")
        a = await self.position("حرستا", "case-a")
        b = await self.position("حرستا", "case-b")
        await self.accept(a, harasta)
        c = await self.position("حرستا", "case-a")
        r = await resolve.resolved(self.db, [b.id, c.id], None)
        assert r[c.id].id == harasta.id and r[b.id] is None
        scoped = await resolve.resolved(self.db, [b.id, c.id], "case-b")
        assert scoped[c.id] is None

    async def test_confirmed_place_wins_inside_its_governorate_only(self):
        douma = await self.place("دوما", "populated")
        harasta = await self.place("حرستا", "populated")
        homs = await self.place("حمص", "populated")
        a = await self.position("دوما")
        await self.accept(a, harasta, "دوما")  # same governorate (Rif Dimashq) as Douma
        b = await self.position("دوما")
        r = await resolve.resolved(self.db, [b.id], None)
        assert r[b.id].id == harasta.id != douma.id
        c = await self.position("دوما", "case-b")
        await self.accept(c, homs, "دوما")  # another governorate: no help for case-b
        d = await self.position("دوما", "case-b")
        assert (await resolve.resolved(self.db, [d.id], None))[d.id] is None

    async def test_candidate_without_governorate_never_autoresolves(self):
        site = Place(
            id=uuid.uuid4(),
            name_ar="موقع",
            kind="facility",
            geom=point(36, 34),
            geo_source="analyst",
            source="person",
        )
        self.db.add(site)
        self.db.add(
            PlaceName(
                place_id=site.id,
                norm="موقع",
                name="موقع",
                lang="ar",
                kind="official",
                source="person",
            )
        )
        await self.db.flush()
        a = await self.position("موقع")
        await self.accept(a, site)
        b = await self.position("موقع")
        r = await resolve.resolved(self.db, [a.id, b.id], None)
        assert r[a.id].id == site.id and r[b.id] is None

    async def test_link_counts_skip_rejected_claims_and_other_cases(self):
        harasta = await self.place("حرستا", "populated")
        kept, dropped, other = (
            await self.position("حرستا"),
            await self.position("حرستا"),
            await self.position("حرستا", "case-b"),
        )
        for claim in (kept, dropped, other):
            await self.accept(claim, harasta)
        await self.say(
            f"statement:{dropped.id}",
            Prop.REVIEW,
            {"verdict": "reject", "reason": "x", "at": "2026-01-01T00:00:00+00:00"},
        )
        assert await resolve.link_counts(self.db, None) == {harasta.id: 2}
        assert await resolve.link_counts(self.db, "case-a") == {harasta.id: 1}

    async def test_propose_only_for_a_single_candidate(self):
        one = await self.position("دوما")
        await self.position("الجديدة")
        await self.position("قرية مجهولة")
        agent = Principal(kind="agent", name="locator")
        self.db.add(agent)
        await self.db.flush()
        key = Ed25519PrivateKey.generate()
        run = Run(
            principal_id=agent.id,
            tool="locate",
            pubkey=key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
        )
        self.db.add(run)
        await self.db.flush()

        def sign(h):
            return {"kind": "ed25519", "sig": key.sign(h.encode()).hex()}

        assert await resolve.propose(self.db, run.id, agent.id, sign) == 1
        assert await resolve.propose(self.db, run.id, agent.id, sign) == 0
        subjects = (
            (
                await self.db.execute(
                    select(Statement.subject).where(Statement.prop == Prop.LOCATED_AT)
                )
            )
            .scalars()
            .all()
        )
        assert subjects == [f"statement:{one.id}"]


@unittest.skipUnless(POSTGRES, "places need Postgres (set SQLALCHEMY_DATABASE_URI)")
class PlacesApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = _app(self)

    async def asyncSetUp(self):
        self.passkey = SoftPasskey()
        async with ext.async_session_factory() as s, s.begin():
            analyst = (
                await s.execute(select(Role).where(Role.name == "analyst"))
            ).scalar_one()
            for email, roles in (
                ("analyst@example.com", [analyst]),
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
                if roles:
                    await s.flush()
                    s.add(self.passkey.register(user))
            await places_cli.import_file(s, FIXTURE)
            human, key = await attester(s, "reviewer")
            buf = BytesIO()
            Image.new("RGB", (8, 8), "white").save(buf, "PNG")
            sha = (
                await blobs.put(s, buf.getvalue(), "image/png", Config.BLOB_DIR)
            ).sha256
            await record_acquisition(
                s,
                blob_sha256=sha,
                origin="device:case-a",
                collector_id=human.id,
                at=datetime.now(UTC),
                context={"investigation": "case-a"},
                sensitivity="sensitive",
                doc_type="register",
            )

            async def say(prop, value, **kw):
                return await append(
                    s,
                    StatementIn(
                        subject=f"person:{uuid.uuid4()}",
                        prop=prop,
                        value=value,
                        author_id=human.id,
                        span_blob=sha,
                        span={"page": 1, "bbox": [0, 0, 4, 4]},
                        **kw,
                    ),
                    sign=key.sig,
                )

            agent = Principal(kind="agent", name="extractor")
            s.add(agent)
            await s.flush()
            agent_key = Ed25519PrivateKey.generate()
            run = Run(
                principal_id=agent.id,
                tool="extract",
                inputs={"blobs": [sha]},
                pubkey=agent_key.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                ),
            )
            s.add(run)
            await s.flush()
            self.claim = await append(
                s,
                StatementIn(
                    subject=f"person:{uuid.uuid4()}",
                    prop=Prop.SEEN_AT,
                    value={"text": "حرستا", "lang": "ar"},
                    author_id=agent.id,
                    run_id=run.id,
                    span_blob=sha,
                    span={"page": 1, "bbox": [0, 0, 4, 4]},
                    valid_from=date(2013, 5, 2),
                ),
                sign=lambda h: {
                    "kind": "ed25519",
                    "sig": agent_key.sign(h.encode()).hex(),
                },
            )
            self.name = await say(Prop.NAME, {"text": "يوسف", "lang": "ar"})
            self.bad = await say(
                Prop.SEEN_AT, {"text": "حمص", "lang": "ar"}, valid_from=date(2013, 5, 2)
            )
            await append(
                s,
                StatementIn(
                    subject=f"statement:{self.bad.id}",
                    prop=Prop.REVIEW,
                    value={"verdict": "reject", "reason": "x", "at": "2026-01-01"},
                    author_id=human.id,
                ),
                sign=key.sig,
            )
            self.harasta = (
                await s.execute(
                    select(Place)
                    .join(PlaceName)
                    .where(PlaceName.norm == normalise("حرستا"))
                )
            ).scalar_one()

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def client(self, email):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(email, "/people")
        await client.get(f"/_test/login?token={token}")
        page = await (await client.get("/people")).get_data(as_text=True)
        client.csrf = {
            "X-CSRFToken": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        }
        return client

    async def confirm(self, client, item):
        return await client.post(
            "/api/places/confirm", json={"item": item}, headers=client.csrf
        )

    async def test_search_and_create(self):
        client = await self.client("analyst@example.com")
        r = await client.get("/api/places", query_string={"q": "حرستا"})
        names = {p["name_ar"] for p in (await r.get_json())["items"]}
        assert "حرستا" in names
        for body in (
            {"name_ar": "x", "lon": 2.3, "lat": 48.8},
            {"name_ar": "x", "lon": "nan", "lat": 34},
            {"name_ar": "x", "name_en": "e" * 201, "lon": 36.3, "lat": 33.5},
            {"name_ar": ["x"], "lon": 36.3, "lat": 33.5},
            [1],
        ):
            r = await client.post("/api/places", json=body, headers=client.csrf)
            assert r.status_code == 400, body
        r = await client.post(
            "/api/places",
            json={"name_ar": "مخفر جديد", "lon": 36.3, "lat": 33.5},
            headers=client.csrf,
        )
        assert r.status_code == 201
        found = (
            await (
                await client.get("/api/places", query_string={"q": "مخفر"})
            ).get_json()
        )["items"]
        assert [p["source"] for p in found] == ["person"]

    async def test_role_required(self):
        client = await self.client("nobody@example.com")
        assert (await client.get("/api/places")).status_code == 403
        r = await self.confirm(
            client, {"statement": str(self.claim.id), "place": str(self.harasta.id)}
        )
        assert r.status_code == 403

    async def test_confirm_refuses_bad_requests(self):
        client = await self.client("analyst@example.com")
        place = str(self.harasta.id)
        for item, status in (
            ({"statement": str(self.name.id), "place": place}, 404),
            ({"statement": str(self.claim.id), "place": str(uuid.uuid4())}, 404),
            ({"statement": str(uuid.uuid4()), "place": place}, 404),
            ({"statement": str(self.bad.id), "place": place}, 409),
            ({"statement": 5, "place": place}, 400),
            ({"statement": [1], "place": place}, 400),
            ({"statement": str(self.claim.id)}, 400),
            ("x", 400),
        ):
            assert (await self.confirm(client, item)).status_code == status, item

    async def test_confirm_without_a_passkey_points_to_setup(self):
        from stk.user.models import WebAuthn

        async with ext.async_session_factory() as s, s.begin():
            await s.execute(delete(WebAuthn))
        client = await self.client("analyst@example.com")
        item = {"statement": str(self.claim.id), "place": str(self.harasta.id)}
        r = await self.confirm(client, item)
        body = await r.get_json()
        assert r.status_code == 409 and body["reason"] == "no_passkey", body

    async def test_confirm_needs_the_passkey_before_anything_is_written(self):
        client = await self.client("analyst@example.com")
        item = {"statement": str(self.claim.id), "place": str(self.harasta.id)}
        r = await self.confirm(client, item)
        started = await r.get_json()
        assert r.status_code == 200 and started["options"]["challenge"]

        async def located():
            async with ext.async_session_factory() as s:
                return (
                    (
                        await s.execute(
                            select(StatementTier.tier)
                            .join(Statement, Statement.id == StatementTier.statement_id)
                            .where(Statement.prop == Prop.LOCATED_AT)
                        )
                    )
                    .scalars()
                    .all()
                )

        assert await located() == []
        r = await client.post(
            "/api/review/commit",
            json={
                "item": {
                    "hash": started["hash"],
                    "credential": self.passkey.assert_(started["hash"]),
                }
            },
            headers=client.csrf,
        )
        assert r.status_code == 200, await r.get_data(as_text=True)
        assert await located() == ["accepted"]
        async with ext.async_session_factory() as s:
            stages = (await s.execute(select(RunEvent.stage))).scalars().all()
            counts = await resolve.link_counts(s, None)
        assert "place_confirmed" in stages
        assert counts == {self.harasta.id: 1}


@unittest.skipUnless(POSTGRES, "places need Postgres (set SQLALCHEMY_DATABASE_URI)")
class BoundariesTest(Base):
    async def areas_of(self):
        rows = await self.db.execute(
            select(Place.name_en, AdminArea.pcode)
            .join(AdminArea, AdminArea.id == Place.admin1_id)
            .where(Place.kind.in_(["populated", "facility"]))
        )
        return dict(rows.all())

    async def test_import_assigns_governorate_by_polygon_and_repeats_safely(self):
        await places_cli.import_file(self.db, FIXTURE)
        first = await boundaries.import_set(self.db, boundaries.FIXTURE)
        assert first["areas"] == 4 + 24
        found = await self.areas_of()
        # COD-AB pcodes: Damascus SY01, Aleppo SY02, Rural Damascus SY03, Homs SY04.
        assert found["Damascus"] == found["Branch 999"] == "SY01"
        assert found["Harasta"] == found["Douma"] == found["Jaramana"] == "SY03"
        assert found["Homs"] == "SY04" and found["Aleppo"] == "SY02"
        again = await boundaries.import_set(self.db, boundaries.FIXTURE)
        assert again["set"] == first["set"] and again["areas"] == 0
        assert await count(self.db, AdminArea) == 28
        bset = await self.db.get(BoundarySet, uuid.UUID(first["set"]))
        assert (bset.version, bset.licence) == ("v02", "CC BY 3.0 IGO")
        assert await boundaries.attribution(self.db) == boundaries.ATTRIBUTION

    async def test_places_imported_after_boundaries_are_assigned(self):
        await boundaries.import_set(self.db, boundaries.FIXTURE)
        await places_cli.import_file(self.db, FIXTURE)
        assert (await self.areas_of())["Homs"] == "SY04"

    async def test_scope_prefers_the_polygon(self):
        await places_cli.import_file(self.db, FIXTURE)
        await boundaries.import_set(self.db, boundaries.FIXTURE)
        harasta = (
            await self.db.execute(select(Place).where(Place.name_en == "Harasta"))
        ).scalar_one()
        assert harasta.governorate_id is not None
        assert resolve.scope(harasta) == harasta.admin1_id != harasta.governorate_id

    async def test_other_bytes_under_a_known_version_are_refused(self):
        await boundaries.import_set(self.db, boundaries.FIXTURE)
        with tempfile.TemporaryDirectory() as d:
            raw = (boundaries.FIXTURE / "syr_admin1.geojson").read_bytes()
            (Path(d) / "syr_admin1.geojson").write_bytes(raw.replace(b"Homs", b"Hims"))
            with self.assertRaisesRegex(ValueError, "already imported"):
                await boundaries.import_set(self.db, d)


@unittest.skipUnless(POSTGRES, "places need Postgres (set SQLALCHEMY_DATABASE_URI)")
class GeometryMigrationTest(unittest.TestCase):
    """Plain lon/lat rows become PostGIS geometry and come back on downgrade."""

    def test_backfill_and_round_trip(self):
        from sqlalchemy import create_engine, text

        url = self.enterContext(throwaway_database_url(sync=True))
        config = build_alembic_config()
        config.config_file_name = None
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "20261025_saved_story")
        engine = create_engine(url)
        self.addCleanup(engine.dispose)
        pid = uuid.uuid4()
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO place (id, name_ar, name_en, kind, lon, lat, source) "
                    "VALUES (:id, 'حرستا', 'Harasta', 'populated', 36.371, 33.558, "
                    "'geonames')"
                ),
                {"id": pid},
            )
            c.execute(
                text(
                    "INSERT INTO place_name (place_id, norm, name, lang) VALUES "
                    "(:id, 'harasta', 'Harasta', 'en'), (:id, 'harasta2', 'Harasta2', "
                    "'en'), (:id, 'حرستا', 'حرستا', 'ar')"
                ),
                {"id": pid},
            )
        command.upgrade(config, "head")
        with engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT lon, lat, ST_AsText(geom), ST_Y(centroid::geometry), "
                    "precision_m, geo_source FROM place"
                )
            ).one()
            kinds = dict(c.execute(text("SELECT norm, kind FROM place_name")).all())
        assert row == (
            Decimal("36.371000"),
            Decimal("33.558000"),
            "POINT(36.371 33.558)",
            33.558,
            3000,
            "geonames",
        )
        assert kinds == {
            "harasta": "official",
            "harasta2": "transliteration",
            "حرستا": "official",
        }
        command.downgrade(config, "20261025_saved_story")
        with engine.connect() as c:
            back = c.execute(text("SELECT lon, lat FROM place")).one()
        assert back == (Decimal("36.371000"), Decimal("33.558000"))


if __name__ == "__main__":
    unittest.main()
