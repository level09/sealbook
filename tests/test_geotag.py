"""Embedded photo and video locations: parsing EXIF, ISO 6709 and C2PA, and the
geotag tool's unconfirmed proposals."""

import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from sqlalchemy import select

import stk.extensions as ext
from stk import blobs
from stk.ledger.contracts import Prop
from stk.ledger.models import Acquisition, Payload, Run, Statement, StatementTier
from stk.ledger.write import record_acquisition
from stk.maps import geotag
from stk.runs.worker import work_once
from stk.settings import Config
from tests import test_review


def photo(lat=None, lon=None, error=None, bearing=None) -> bytes:
    exif = Image.Exif()
    if lat is not None:
        gps = exif.get_ifd(geotag.GPS_IFD)
        gps[1], gps[3] = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
        gps[2] = (abs(int(lat)), int(abs(lat) % 1 * 60), 0.0)
        gps[4] = (abs(int(lon)), int(abs(lon) % 1 * 60), 0.0)
        if error is not None:
            gps[31] = error
        if bearing is not None:
            gps[17] = bearing
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (lat or 0) and "white").save(buf, "JPEG", exif=exif)
    return buf.getvalue()


class ParseTest(unittest.TestCase):
    def path(self, data):
        p = Path(self.enterContext(tempfile.TemporaryDirectory()), "a.jpg")
        p.write_bytes(data)
        return p

    def test_exif_gps_with_error_and_bearing(self):
        loc = geotag.exif(self.path(photo(33.5, 36.25, error=120.4, bearing=271.6)))
        assert loc == {
            "method": "exif",
            "lat": 33.5,
            "lon": 36.25,
            "precision_m": 121,
            "bearing": 272,
        }

    def test_no_fix_and_null_island_propose_nothing(self):
        assert geotag.exif(self.path(photo())) is None
        assert geotag.exif(self.path(photo(0.0, 0.0))) is None

    def test_precision_is_never_finer_than_the_default(self):
        assert geotag.exif(self.path(photo(33.5, 36.25, error=3)))["precision_m"] == 50

    def test_iso6709_from_video_containers(self):
        loc = geotag.iso6709("+33.5138+036.2765+000.000/")
        assert (loc["lat"], loc["lon"], loc["method"]) == (33.5138, 36.2765, "exif")
        assert geotag.iso6709("") is None

    def test_c2pa_stds_exif(self):
        report = {
            "active_manifest": "m",
            "manifests": {
                "m": {
                    "assertions": [
                        {
                            "label": "stds.exif",
                            "data": {
                                "exif:GPSLatitude": "33,30.6N",
                                "exif:GPSLongitude": "36,15W",
                                "exif:GPSHPositioningError": "80/1",
                            },
                        }
                    ]
                }
            },
        }
        loc = geotag.c2pa(report)
        assert (loc["lat"], loc["lon"], loc["precision_m"]) == (33.51, -36.25, 80)
        assert loc["method"] == "c2pa"
        assert geotag.c2pa(None) is None

    def test_value_has_no_floats(self):
        v = geotag.value(
            {"method": "exif", "lat": 33.5, "lon": 36.25, "precision_m": 50}
        )
        assert v["geometry"]["coordinates"] == ["36.250000", "33.500000"]


class GeotagToolTest(unittest.IsolatedAsyncioTestCase):
    setUp = test_review.ReviewTest.setUp
    asyncSetUp = test_review.ReviewTest.asyncSetUp
    asyncTearDown = test_review.ReviewTest.asyncTearDown
    client = test_review.ReviewTest.client

    async def test_exif_location_is_an_unconfirmed_proposal(self):
        async with ext.async_session_factory() as s, s.begin():
            blob = await blobs.put(
                s, photo(33.5, 36.25, bearing=90), "image/jpeg", Config.BLOB_DIR
            )
            await record_acquisition(
                s,
                blob_sha256=blob.sha256,
                origin="device:test",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={"investigation": "case-r"},
                sensitivity="ordinary",
                doc_type="register",
            )
            acq = await s.scalar(
                select(Acquisition).where(Acquisition.blob_sha256 == blob.sha256)
            )
            s.add(
                Run(
                    principal_id=self.agent.id,
                    tool="geotag",
                    route="local",
                    inputs={"blobs": [blob.sha256], "acquisition": str(acq.id)},
                )
            )
        assert await work_once(ext.async_session_factory, ["geotag"])
        async with ext.async_session_factory() as s:
            run = await s.scalar(select(Run).where(Run.tool == "geotag"))
            rows = (
                await s.execute(
                    select(Statement.prop, StatementTier.tier, Payload.value)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .where(Statement.subject == f"blob:{blob.sha256}")
                    .where(Statement.prop == Prop.PHOTOGRAPHED_AT)
                )
            ).all()
        assert run.status == "done", run.error
        assert len(rows) == 1
        prop, tier, value = rows[0]
        assert tier == "unconfirmed"
        assert value["method"] == "exif" and value["bearing"] == 90
        assert value["geometry"]["coordinates"] == ["36.250000", "33.500000"]
        client = await self.client()
        r = await client.get(f"/api/map/evidence/{blob.sha256}/locations")
        items = (await r.get_json())["items"]
        assert [(i["lon"], i["lat"], i["tier"]) for i in items] == [
            (36.25, 33.5, "unconfirmed")
        ]
        assert (await client.get("/api/map/evidence/nope/locations")).status_code == 404
