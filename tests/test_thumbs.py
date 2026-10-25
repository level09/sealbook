"""Thumbnails: generation per media type on the synthetic fixtures, provenance on the
sealed run, and the serving rules (analyst only, sensitive after reveal, erased gone)."""

import io
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path

import orjson
import stk.extensions as ext
from PIL import Image
from quart_security import hash_password
from sqlalchemy import select
from stk.user.models import User

from sealbook import blobs
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Blob, Run, RunEvent
from sealbook.ledger.write import append, record_acquisition
from sealbook.runs.worker import work_once
from sealbook.settings import Config
from sealbook.thumbs import make
from tests import test_review

ROOT = Path(__file__).resolve().parent.parent / "acceptance"
PHOTO = ROOT / "data" / "photo_damaged_building.jpg"
PDF = ROOT / "data" / "transfer_order.pdf"
AUDIO = ROOT / "data" / "witness_clip.ogg"
VIDEO = ROOT / "public" / "jdeideh_2015.webm"


def decode(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


class MakeTest(unittest.TestCase):
    def test_image_is_oriented_and_stripped(self):
        source = Image.new("RGB", (400, 200), "red")
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 degrees clockwise to display
        exif[0x010F] = "Camera Maker"
        buffer = io.BytesIO()
        source.save(buffer, "JPEG", exif=exif)
        path = Path(self.enterContext(tempfile.TemporaryDirectory()), "a.jpg")
        path.write_bytes(buffer.getvalue())
        files, meta = make.image_files(path)
        big, tile = decode(files["thumb_480"]), decode(files["thumb_160"])
        self.assertEqual((big.format, big.size), ("WEBP", (200, 400)))
        self.assertEqual((tile.format, tile.size), ("WEBP", (160, 160)))
        for image in (big, tile):
            self.assertEqual(len(image.getexif()), 0)
            self.assertNotIn("exif", image.info)
        self.assertEqual(meta, {"width": 400, "height": 200})

    def test_photo_sizes(self):
        files, _ = make.image_files(PHOTO)
        self.assertEqual(max(decode(files["thumb_480"]).size), 480)
        self.assertEqual(decode(files["thumb_160"]).size, (160, 160))

    def test_video_poster_strip_and_timestamps(self):
        info = make.probe(VIDEO)
        files, meta = make.video_files(VIDEO, info["duration"])
        self.assertEqual(max(decode(files["thumb_480"]).size), 480)
        frames = meta["strip"]["frames"]
        self.assertEqual(len(frames), 12)
        self.assertEqual([f["ms"] for f in frames], sorted(f["ms"] for f in frames))
        self.assertTrue(0 < frames[0]["ms"] and frames[-1]["ms"] < meta["duration_ms"])
        sprite = decode(files["strip"])
        self.assertEqual(sprite.size[0], meta["strip"]["cell_width"] * 12)
        self.assertGreater(meta["poster_contrast"], 0)

    def test_black_frames_are_not_the_poster(self):
        self.assertEqual(make.interest(Image.new("RGB", (32, 32), "black")), 0.0)
        gradient = Image.linear_gradient("L").convert("RGB")
        self.assertGreater(make.interest(gradient), 0)

    def test_pdf_first_page_and_per_page(self):
        files, meta = make.pdf_files(PDF)
        self.assertEqual(meta["page_count"], len(files["pages"]))
        self.assertEqual(max(decode(files["thumb_480"]).size), 480)
        self.assertLessEqual(
            max(decode(files["pages"][0]).size), make.PARAMS["pdf_page_side"]
        )

    def test_audio_waveform_and_peaks(self):
        info = make.probe(AUDIO)
        files, meta = make.audio_files(AUDIO, info["duration"])
        self.assertEqual(decode(files["thumb_480"]).format, "PNG")
        self.assertEqual(decode(files["thumb_160"]).size, (160, 160))
        peaks = orjson.loads(files["peaks"])
        self.assertEqual(len(peaks["peaks"]), meta["buckets"])
        self.assertEqual(max(peaks["peaks"]), 1.0)
        self.assertAlmostEqual(peaks["duration_ms"] / 1000, 33, delta=2)


class ThumbEndpointTest(unittest.IsolatedAsyncioTestCase):
    setUp = test_review.ReviewTest.setUp
    asyncSetUp = test_review.ReviewTest.asyncSetUp
    asyncTearDown = test_review.ReviewTest.asyncTearDown
    client = test_review.ReviewTest.client

    async def reveal(self, client, sha):
        r = await client.post(
            f"/api/evidence/{sha}/reveal", headers=client.csrf, json={"item": {}}
        )
        self.assertEqual(r.status_code, 200)

    async def make(self, path, mime, sensitivity):
        from sealbook.ledger.models import Acquisition

        async with ext.async_session_factory() as s, s.begin():
            blob = await blobs.put(s, path.read_bytes(), mime, Config.BLOB_DIR)
            await record_acquisition(
                s,
                blob_sha256=blob.sha256,
                origin="device:test",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={"investigation": "case-r"},
                sensitivity=sensitivity,
                doc_type="register",
            )
            acq = await s.scalar(
                select(Acquisition).where(Acquisition.blob_sha256 == blob.sha256)
            )
            s.add(
                Run(
                    principal_id=self.agent.id,
                    tool="thumbnail",
                    route="local",
                    inputs={"blobs": [blob.sha256], "acquisition": str(acq.id)},
                )
            )
        self.assertTrue(await work_once(ext.async_session_factory, ["thumbnail"]))
        return blob.sha256

    async def test_run_is_sealed_with_version_and_parameters(self):
        sha = await self.make(PHOTO, "image/jpeg", "public")
        async with ext.async_session_factory() as s:
            run = await s.scalar(select(Run).where(Run.tool == "thumbnail"))
            stages = (
                await s.scalars(select(RunEvent.stage).where(RunEvent.run_id == run.id))
            ).all()
            manifest = orjson.loads(
                blobs.read(await s.get(Blob, run.raw_output_ref), Config.BLOB_DIR)
            )
        self.assertEqual(run.status, "done")
        self.assertEqual(run.inputs["version"], make.VERSION)
        self.assertEqual(run.inputs["params"], make.PARAMS)
        self.assertEqual(run.output_hash, run.raw_output_ref)
        self.assertIn("thumbnails", stages)
        self.assertEqual((manifest["source"], manifest["kind"]), (sha, "image"))

    async def test_access_rules(self):
        public = await self.make(PHOTO, "image/jpeg", "public")
        secret = await self.make(PDF, "application/pdf", "sensitive")
        client = await self.client()
        r = await client.get(f"/api/evidence/{public}/thumb?size=160")
        self.assertEqual((r.status_code, r.mimetype), (200, "image/webp"))
        self.assertIn("private", r.headers["Cache-Control"])
        self.assertNotIn("public", r.headers["Cache-Control"])
        tag = r.headers["ETag"]
        again = await client.get(
            f"/api/evidence/{public}/thumb?size=160", headers={"If-None-Match": tag}
        )
        self.assertEqual(again.status_code, 304)
        self.assertEqual(
            (await client.get(f"/api/evidence/{public}/thumb?size=99")).status_code, 400
        )
        self.assertEqual(
            (await client.get(f"/api/evidence/{public}/strip")).status_code, 404
        )
        self.assertEqual(
            (await client.get(f"/api/evidence/{'0' * 64}/thumb")).status_code, 404
        )
        for path in ("thumb?size=480", "thumb?page=1", "thumbs"):
            r = await client.get(f"/api/evidence/{secret}/{path}")
            self.assertEqual(r.status_code, 403, path)
            self.assertEqual((await r.get_json())["reason"], "reveal_required")
        await self.reveal(client, secret)
        r = await client.get(f"/api/evidence/{secret}/thumb?size=480")
        self.assertEqual((r.status_code, r.mimetype), (200, "image/webp"))
        info = await (await client.get(f"/api/evidence/{secret}/thumbs")).get_json()
        self.assertEqual((info["kind"], info["pages"]), ("pdf", info["page_count"]))
        r = await client.get(f"/api/evidence/{secret}/thumb?page=1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            (await client.get(f"/api/evidence/{secret}/thumb?page=99")).status_code, 404
        )

    async def test_source_without_thumbnail_answers_no_content(self):
        async with ext.async_session_factory() as s, s.begin():
            blob = await blobs.put(s, b"plain words", "text/plain", Config.BLOB_DIR)
            await record_acquisition(
                s,
                blob_sha256=blob.sha256,
                origin="device:test",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={"investigation": "case-r"},
                sensitivity="public",
                doc_type="register",
            )
        client = await self.client()
        r = await client.get(f"/api/evidence/{blob.sha256}/thumb?size=160")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(
            (await client.get(f"/api/evidence/{blob.sha256}/thumbs")).status_code, 404
        )

    async def test_a_filler_without_bytes_says_no_file(self):
        sha = "f" * 64
        async with ext.async_session_factory() as s, s.begin():
            s.add(
                Blob(
                    sha256=sha,
                    md5="0" * 32,
                    size=1,
                    mime="image/jpeg",
                    storage_ref="synthetic",
                )
            )
            await s.flush()
            await record_acquisition(
                s,
                blob_sha256=sha,
                origin="synthetic:scale-test/1",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={"investigation": "case-scale-test-x", "synthetic": True},
                sensitivity="public",
                doc_type="register",
            )
        client = await self.client()
        r = await client.get(f"/api/evidence/{sha}/content")
        self.assertEqual(r.status_code, 404)
        self.assertEqual((await r.get_json())["reason"], "no_file")
        item = await (await client.get(f"/api/evidence/{sha}/item")).get_json()
        self.assertEqual((item["stored"], item["synthetic"]), (False, True))
        self.assertEqual(
            (await client.get(f"/api/evidence/{sha}/thumb")).status_code, 204
        )

    async def test_audio_and_video_endpoints(self):
        sound = await self.make(AUDIO, "audio/ogg", "public")
        video = await self.make(VIDEO, "video/webm", "public")
        client = await self.client()
        peaks = await client.get(f"/api/evidence/{sound}/peaks")
        self.assertEqual((peaks.status_code, peaks.mimetype), (200, "application/json"))
        self.assertEqual(
            (await client.get(f"/api/evidence/{sound}/thumb?size=480")).mimetype,
            "image/png",
        )
        strip = await client.get(f"/api/evidence/{video}/strip")
        self.assertEqual((strip.status_code, strip.mimetype), (200, "image/webp"))
        info = await (await client.get(f"/api/evidence/{video}/thumbs")).get_json()
        self.assertEqual(len(info["strip"]["frames"]), 12)

    async def test_non_analyst_is_refused_and_erased_source_is_gone(self):
        from sealbook.people.data import erase

        sha = await self.make(PHOTO, "image/jpeg", "sensitive")
        async with ext.async_session_factory() as s, s.begin():
            s.add(
                User(
                    email="plain@example.com",
                    name="plain",
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
            )
        outsider = await self.client("plain@example.com")
        self.assertEqual(
            (await outsider.get(f"/api/evidence/{sha}/thumb")).status_code, 403
        )
        subject = f"person:{uuid.uuid4()}"
        async with ext.async_session_factory() as s, s.begin():
            await append(
                s,
                StatementIn(
                    subject=subject,
                    prop=Prop.NAME,
                    value={"text": "x", "lang": "ar"},
                    author_id=self.viewer.id,
                    span_blob=sha,
                    span={"start": 0, "end": 1},
                ),
            )
        client = await self.client()
        await self.reveal(client, sha)
        self.assertEqual(
            (await client.get(f"/api/evidence/{sha}/thumb")).status_code, 200
        )
        async with ext.async_session_factory() as s, s.begin():
            await erase(s, subject, self.viewer.id, "test erasure")
        self.assertEqual(
            (await client.get(f"/api/evidence/{sha}/thumb")).status_code, 404
        )

    async def test_second_run_is_unchanged(self):
        sha = await self.make(PHOTO, "image/jpeg", "public")
        async with ext.async_session_factory() as s, s.begin():
            first = await s.scalar(select(Run).where(Run.tool == "thumbnail"))
            s.add(
                Run(
                    principal_id=self.agent.id,
                    tool="thumbnail",
                    route="local",
                    inputs=first.inputs | {"rebuild": False},
                )
            )
        await work_once(ext.async_session_factory, ["thumbnail"])
        async with ext.async_session_factory() as s:
            runs = (await s.scalars(select(Run).where(Run.tool == "thumbnail"))).all()
        self.assertEqual(sorted(r.status for r in runs), ["done", "done"])
        self.assertEqual(sum(r.raw_output_ref is None for r in runs), 1)
        self.assertEqual(sha, first.inputs["blobs"][0])
