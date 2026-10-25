"""Claim tiles: the SQL resolution twin, layer content, erasure and access."""

import random
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import stk.extensions as ext
from quart_security import hash_password
from sqlalchemy import select
from stk.agent_login import create_agent_login_token
from stk.user.models import Role, User

from sealbook.ledger.contracts import Prop
from sealbook.maps import tiles
from sealbook.places import boundaries, resolve
from sealbook.settings import Config
from tests.test_people import _app
from tests.test_places import POSTGRES, ResolveCase, ResolveTest


class ClusterTest(unittest.TestCase):
    def test_every_point_is_in_exactly_one_tiles_clusters(self):
        rng = random.Random(1)
        pts = []
        for i in range(2000):
            mx, my = tiles.mercator(rng.uniform(35.8, 42.2), rng.uniform(32.4, 37.2))
            pts.append((mx, my, str(i), "accepted", False, "s", "p", "pl", "d", 0))
        for z in (4, 5, 7, 9):
            size = tiles.WORLD / 2**z
            owners = {
                (
                    int((p[0] + tiles.WORLD / 2) // size),
                    int((tiles.WORLD / 2 - p[1]) // size),
                )
                for p in pts
            }
            counted = sum(
                c["count"] for x, y in owners for c in tiles._clusters(pts, z, x, y)
            )
            assert counted == len(pts), z


@unittest.skipUnless(POSTGRES, "tiles need Postgres (set SQLALCHEMY_DATABASE_URI)")
class SqlResolutionTest(ResolveTest):
    """Every resolution scenario again, with the SQL twin checked against
    `resolve.resolved` each time a scenario asks for an answer."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        original = resolve.resolved

        async def both(db, ids, investigation):
            want = await original(db, ids, investigation)
            f = {"investigation": investigation, "time": None}
            rows = (await db.execute(tiles.claims(f, tiles.TIERS, ids=ids))).all()
            got = {r.id: r.place for r in rows}
            assert got, "the twin saw none of the claims"
            for claim, place in got.items():
                assert place == (want[claim].id if want[claim] else None), claim
            return want

        self.enterContext(mock.patch.object(resolve, "resolved", both))


@unittest.skipUnless(POSTGRES, "tiles need Postgres (set SQLALCHEMY_DATABASE_URI)")
class TileTest(ResolveCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await boundaries.import_set(self.db, boundaries.FIXTURE)
        self.harasta = await self.place("حرستا", "populated")
        self.claim = await self.position("حرستا")
        await self.accept(self.claim, self.harasta)
        await self.db.commit()

    async def body(self, z, lon=36.365, lat=33.558, **f):
        from sealbook.maps.scale import _tile_of

        model = await tiles.build(
            self.db, {"investigation": None, "time": None} | f, tiles.TIERS[:3]
        )
        return await tiles.tile(self.db, model, z, *_tile_of(lon, lat, z))

    async def test_point_layers_and_boundaries(self):
        near = await self.body(13)
        assert str(self.claim.id).encode() in near
        for layer in (b"points", b"uncertainty", b"admin"):
            assert layer in near
        assert b"SY03" in near  # Rural Damascus, from the boundary fixture

    async def test_low_zoom_clusters_keep_ids(self):
        other = await self.position("حرستا")
        await self.accept(other, self.harasta)
        far = await self.body(6)
        assert b"cluster" in far and str(self.claim.id).encode() in far

    async def test_filters_drop_claims(self):
        assert str(self.claim.id).encode() not in await self.body(
            13, investigation="case-b"
        )
        window = [date(2014, 1, 1), date(2014, 2, 1)]
        assert str(self.claim.id).encode() not in await self.body(13, time=window)

    async def test_cache_follows_map_statements_not_access_logs(self):
        cache, f = tiles.Cache(), {"investigation": None, "time": None}
        first = await cache.get(self.db, 1, f, tiles.TIERS[:3])
        await self.say(
            f"blob:{self.blobs['case-a']}",
            Prop.ACCESS,
            {"action": "view", "at": "2026-10-24T00:00:00+00:00", "statements": []},
        )
        assert await cache.get(self.db, 1, f, tiles.TIERS[:3]) is first
        assert await cache.get(self.db, 2, f, tiles.TIERS[:3]) is not first
        await self.position("حرستا")
        assert await cache.get(self.db, 1, f, tiles.TIERS[:3]) is not first

    async def test_erased_value_leaves_the_tile(self):
        from sealbook.ledger.models import Payload, Statement

        sid = self.claim.id
        commitment = await self.db.scalar(
            select(Statement.commitment).where(Statement.id == sid)
        )
        # The erasure procedure deletes payloads; the tile must follow.
        await self.db.execute(
            Payload.__table__.delete().where(Payload.commitment == commitment)
        )
        assert str(sid).encode() not in await self.body(13)


@unittest.skipUnless(POSTGRES, "tiles need Postgres (set SQLALCHEMY_DATABASE_URI)")
class TileApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = _app(self)

    async def asyncSetUp(self):
        async with ext.async_session_factory() as s, s.begin():
            role = await s.scalar(select(Role).where(Role.name == "analyst"))
            user = User(
                email="maps@example.com",
                name="maps",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            user.roles.append(role)
            s.add(user)

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def test_tiles_are_private_and_checked(self):
        client = self.app.test_client()
        r = await client.get("/api/map/tiles/6/38/25.mvt")
        assert r.status_code in (302, 401)
        async with self.app.app_context():
            token = create_agent_login_token("maps@example.com", "/monitor")
        await client.get(f"/_test/login?token={token}")
        r = await client.get("/api/map/tiles/6/38/25.mvt?tier=accepted,rejected")
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "private, no-store"
        assert r.content_type == "application/vnd.mapbox-vector-tile"
        assert (await client.get("/api/map/tiles/6/99/25.mvt")).status_code == 404
        assert (
            await client.get("/api/map/tiles/6/38/25.mvt?tier=x")
        ).status_code == 400
        meta = await (
            await client.get("/api/map/tiles.json?investigation=c")
        ).get_json()
        assert meta["tiles"][0].endswith(
            "/api/map/tiles/{z}/{x}/{y}.mvt?investigation=c"
        )
        assert meta["attribution"] == ""  # no boundary set imported here

    async def test_basemap_is_served_by_range_or_reported_absent(self):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token("maps@example.com", "/monitor")
        await client.get(f"/_test/login?token={token}")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "region.pmtiles"
            with mock.patch.object(Config, "BASEMAP_PATH", str(path)):
                meta = await (await client.get("/api/map/basemap.json")).get_json()
                assert meta == {"url": None, "attribution": None}
                assert (await client.get("/api/map/basemap.pmtiles")).status_code == 404
                path.write_bytes(bytes(range(256)) * 4)
                meta = await (await client.get("/api/map/basemap.json")).get_json()
                assert meta["url"] == "/api/map/basemap.pmtiles"
                assert "OpenStreetMap" in meta["attribution"]
                r = await client.get(
                    "/api/map/basemap.pmtiles", headers={"Range": "bytes=10-19"}
                )
                assert r.status_code == 206
                assert await r.get_data() == bytes(range(10, 20))
                assert r.headers["Cache-Control"] == "private, max-age=86400"
