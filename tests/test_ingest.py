"""Ingest against a throwaway Postgres database migrated to head."""

import hashlib
import json
import re
import subprocess
import tempfile
import unittest
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest import mock

import httpx2
import stk
import stk.extensions as ext
from alembic import command
from PIL import Image
from quart_security import hash_password
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from stk.agent_login import create_agent_login_token
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)
from stk.user.models import User
from stk.utils import netguard

from sealbook.app import create_app
from sealbook.ingest import batch, service
from sealbook.ledger.models import Acquisition, Blob, Principal, Run
from sealbook.ledger.write import verify_db
from sealbook.settings import Config

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"


def jpeg(path: Path, signed: bool) -> bytes:
    """A tiny synthetic JPEG, optionally signed with c2patool's built-in test cert."""
    Image.new("RGB", (16, 16), "red").save(path / "a.jpg")
    if not signed:
        return (path / "a.jpg").read_bytes()
    (path / "m.json").write_text(
        json.dumps({"claim_generator_info": [{"name": "test"}], "assertions": []})
    )
    subprocess.run(
        ["c2patool", str(path / "a.jpg"), "-m", str(path / "m.json")]
        + ["-o", str(path / "s.jpg"), "-f"],
        check=True,
        capture_output=True,
    )
    return (path / "s.jpg").read_bytes()


@unittest.skipUnless(POSTGRES, "ingest needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class IngestTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config("sealbook", url, quiet=True)
        command.upgrade(config, "heads")
        self.url = make_url(url).set(drivername="postgresql+asyncpg")
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.blobs = str(self.tmp / "blobs")

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.url)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as s, s.begin():
            self.human = Principal(kind="human", name="analyst")
            s.add(self.human)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def kw(self, origin, mime="application/pdf"):
        return {
            "blob_dir": self.blobs,
            "mime": mime,
            "origin": origin,
            "collector_id": self.human.id,
        }

    async def test_unsupported_container_is_absent_not_an_error(self):
        webm = self.tmp / "clip.webm"
        webm.write_bytes(b"\x1a\x45\xdf\xa3 synthetic")
        found = await service.c2pa_verify(webm)
        self.assertEqual(found["status"], "absent")

    async def test_batch_status_retry_and_summary(self):
        with mock.patch.object(Config, "RUN_MODELS", {"local": "m/l"}):
            async with self.Session() as s, s.begin():
                kw = self.kw("device:x", "text/plain") | {"context": {"batch": "b1"}}
                first = await service.ingest_bytes(s, b"synthetic memo", **kw)
                again = await service.ingest_bytes(s, b"synthetic memo", **kw)
            async with self.Session() as s, s.begin():
                st = await batch.status(s, "b1")
                self.assertFalse(st["done"])
                self.assertEqual([i["duplicate"] for i in st["items"]], [False, True])
                self.assertEqual(st["items"][0]["stages"]["read"], "queued")
                self.assertEqual(st["items"][0]["stages"]["hashed"], "done")
                run = await s.get(Run, uuid.UUID(first["processing"]["run"]))
                run.status, run.error = "failed", "TimeoutError: no answer"
                for r in (await s.execute(select(Run))).scalars():
                    if r.status == "queued":
                        r.status = "done"
            async with self.Session() as s, s.begin():
                st = await batch.status(s, "b1")
                self.assertTrue(st["done"])
                self.assertEqual(st["summary"]["failed"], 2)
                self.assertEqual(st["summary"]["exact_duplicates"], 1)
                self.assertEqual(st["items"][0]["failures"][0]["detail"], "no answer")
                self.assertTrue(st["items"][0]["failures"][0]["retryable"])
                self.assertIsNotNone(await batch.retry(s, run.id))
                self.assertIsNone(await batch.retry(s, run.id))
            async with self.Session() as s, s.begin():
                self.assertFalse((await batch.status(s, "b1"))["done"])
                ffprobe = Run(
                    principal_id=run.principal_id,
                    tool="thumbnail",
                    route="local",
                    inputs=run.inputs,
                    status="failed",
                    error="CalledProcessError: Command '['ffprobe', '-v']' returned 1.",
                )
                failure = batch._failure(ffprobe)
                self.assertEqual(
                    failure["message"].split(".")[0],
                    "Making the thumbnails did not finish",
                )
                self.assertNotIn("ffprobe", failure["detail"])
                self.assertTrue(failure["retryable"])
        self.assertEqual(again["acquisition_id"] != first["acquisition_id"], True)

    async def test_two_origins_one_blob(self):
        data = b"%PDF-1.4 synthetic register page\n" * 5000
        async with self.Session() as s, s.begin():
            a = await service.ingest_bytes(
                s, data, **self.kw(service.make_origin("device", "phone-1"))
            )
            b = await service.ingest_bytes(
                s, data, **self.kw(service.make_origin("channel", "telegram/xyz"))
            )
            self.assertEqual(a["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(a["md5"], hashlib.md5(data).hexdigest())
            self.assertEqual(a["sha256"], b["sha256"])
            self.assertNotEqual(a["acquisition_id"], b["acquisition_id"])
            self.assertTrue(a["new_blob"])
            self.assertFalse(b["new_blob"])
            self.assertEqual(await s.scalar(select(func.count()).select_from(Blob)), 1)
            origins = (await s.execute(select(Acquisition.origin))).scalars().all()
            self.assertEqual(
                sorted(origins), ["channel:telegram/xyz", "device:phone-1"]
            )
            values = {st["prop"]: st for st in a["statements"]}
            self.assertEqual(values["sha256"]["value"], a["sha256"])
            self.assertEqual(values["md5"]["value"], a["md5"])
            self.assertTrue(all(st["tier"] == "accepted" for st in a["statements"]))
            self.assertEqual(b["statements"], [])
            stored = service.blob_path(self.blobs, a["sha256"]).read_bytes()
            self.assertEqual(stored, data)
            self.assertEqual(await verify_db(s), [])

    async def test_new_bytes_queue_processing_on_the_most_private_allowed_route(self):
        text = b"synthetic transfer order"
        with mock.patch.object(Config, "RUN_MODELS", {"local": "m/l", "cloud": "m/c"}):
            async with self.Session() as s, s.begin():
                kw = self.kw("device:x", "text/plain")
                sensitive = await service.ingest_bytes(s, text, **kw)
                ordinary = await service.ingest_bytes(
                    s, text + b"!", **kw, sensitivity="ordinary"
                )
                again = await service.ingest_bytes(s, text, **kw)
                pdf = await service.ingest_bytes(s, b"%PDF-1.4", **self.kw("device:x"))
                runs = {
                    r.id: r
                    for r in (await s.execute(select(Run))).scalars()
                    if r.tool == "extract"
                }
        self.assertEqual(sensitive["processing"]["route"], "local")
        self.assertEqual(ordinary["processing"]["route"], "local")
        self.assertIsNone(again["processing"])  # known bytes are not reprocessed
        self.assertIsNone(pdf["processing"]["queued"])
        run = runs[uuid.UUID(sensitive["processing"]["run"])]
        self.assertEqual(
            (run.model_requested, run.inputs["blobs"]), ("m/l", [sensitive["sha256"]])
        )
        with mock.patch.object(Config, "RUN_MODELS", {"cloud": "m/c"}):
            async with self.Session() as s, s.begin():
                held = await service.ingest_bytes(
                    s, b"another", **self.kw("device:x", "text/plain")
                )
                free = await service.ingest_bytes(
                    s,
                    b"public",
                    **self.kw("device:x", "text/plain"),
                    sensitivity="ordinary",
                )
        # A sensitive document never falls back to a cloud route: it waits.
        self.assertIsNone(held["processing"]["queued"])
        self.assertIn(
            "Set MODEL_LOCAL or MODEL_CLOUD_ZDR in settings",
            held["processing"]["reason"],
        )
        self.assertEqual(free["processing"]["route"], "cloud")

    async def test_hosted_ocr_uses_the_ocr_model(self):
        models, png = {"cloud": "m/c"}, BytesIO()
        Image.new("RGB", (8, 8)).save(png, "PNG")
        with (
            mock.patch.object(Config, "RUN_MODELS", models),
            mock.patch.object(Config, "OCR_MODEL", "m/ocr"),
        ):
            async with self.Session() as s, s.begin():
                kw = self.kw("device:x", "image/png") | {"sensitivity": "ordinary"}
                photo = await service.ingest_bytes(s, png.getvalue(), **kw)
                note = await service.ingest_bytes(
                    s,
                    b"a note",
                    **self.kw("device:x", "text/plain"),
                    sensitivity="ordinary",
                )
                ocr = await s.get(Run, uuid.UUID(photo["processing"]["run"]))
                extract = await s.get(Run, uuid.UUID(note["processing"]["run"]))
        self.assertEqual((ocr.tool, ocr.model_requested), ("ocr", "m/ocr"))
        self.assertEqual((extract.tool, extract.model_requested), ("extract", "m/c"))

    async def test_hosted_first_pass_uses_the_assess_model(self):
        with (
            mock.patch.object(Config, "RUN_MODELS", {"cloud": "m/c"}),
            mock.patch.object(Config, "ASSESS_MODEL", "m/assess"),
        ):
            async with self.Session() as s, s.begin():
                kw = self.kw("device:x", "text/plain") | {"sensitivity": "ordinary"}
                note = await service.ingest_bytes(s, b"a second note", **kw)
                acq = await s.get(Acquisition, uuid.UUID(note["acquisition_id"]))
                out = await service.queue_read(s, acq, "assess", acq.blob_sha256)
                run = await s.get(Run, uuid.UUID(out["run"]))
        self.assertEqual((run.tool, run.model_requested), ("assess", "m/assess"))

    async def test_c2pa_valid_absent_invalid(self):
        signed = jpeg(self.tmp, signed=True)
        plain = jpeg(self.tmp, signed=False)
        tampered = bytearray(signed)
        tampered[-10] ^= 0xFF
        results = {}
        async with self.Session() as s, s.begin():
            for name, data in (
                ("signed", signed),
                ("plain", plain),
                ("tampered", bytes(tampered)),
            ):
                out = await service.ingest_bytes(
                    s, data, **self.kw("device:cam", mime="image/jpeg")
                )
                c2pa = [st for st in out["statements"] if st["prop"] == "c2pa_verify"]
                self.assertEqual(c2pa[0]["tier"], "accepted")
                results[name] = c2pa[0]["value"]
            self.assertEqual(await verify_db(s), [])
        self.assertEqual(results["signed"]["status"], "valid")
        self.assertEqual(results["signed"]["issuer"], "C2PA Test Signing Cert")
        self.assertEqual(
            results["plain"], {"status": "absent", "issuer": None, "codes": []}
        )
        self.assertEqual(results["tampered"]["status"], "invalid")
        self.assertIn("assertion.dataHash.mismatch", results["tampered"]["codes"])

    async def test_url_capture_and_recheck(self):
        page = {"body": b"<html>statement v1</html>", "status": 200}

        def handler(request):
            return httpx2.Response(
                page["status"],
                content=page["body"],
                headers={"content-type": "text/html; charset=utf-8"},
            )

        transport = httpx2.MockTransport(handler)
        with mock.patch.object(netguard, "_resolve", return_value=["93.184.215.14"]):
            async with self.Session() as s, s.begin():
                cap = await service.capture_url(
                    s,
                    "HTTPS://Example.ORG:443/news?id=1#top",
                    transport=transport,
                    blob_dir=self.blobs,
                    collector_id=self.human.id,
                )
                acq = await s.get(Acquisition, cap["acquisition_id"])
                self.assertEqual(acq.origin, "url:https://example.org/news?id=1")
                self.assertEqual(acq.context["status"], 200)
                self.assertEqual(
                    acq.context["content_type"], "text/html; charset=utf-8"
                )
                self.assertEqual(
                    acq.context["final_url"], "https://example.org/news?id=1"
                )

                async def liveness():
                    out = await service.recheck_url(s, acq.id, transport=transport)
                    (st,) = out["statements"]
                    self.assertEqual(st["tier"], "accepted")
                    return st["value"]["status"]

                self.assertEqual(await liveness(), "unchanged")
                page["body"] = b"<html>statement v2</html>"
                self.assertEqual(await liveness(), "edited")
                page["status"] = 404
                self.assertEqual(await liveness(), "gone")
                self.assertEqual(await verify_db(s), [])

    def test_normalise_url(self):
        self.assertEqual(service.normalise_url("http://A.b:8080"), "http://a.b:8080/")
        with self.assertRaises(ValueError):
            service.normalise_url("file:///etc/passwd")


@unittest.skipUnless(POSTGRES, "ingest needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class IngestRoleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
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

        self.app = create_app(TestConfig)

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def test_user_without_role_is_refused(self):
        email = "plain@example.com"
        async with ext.async_session_factory() as s, s.begin():
            s.add(
                User(
                    email=email,
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
            )
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(email, "/dashboard/")
        self.assertEqual(
            (await client.get(f"/_test/login?token={token}")).status_code, 302
        )
        page = await (await client.get("/dashboard/")).get_data(as_text=True)
        headers = {
            "X-CSRFToken": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        }
        for path, body in (
            ("/ingest/file", {"form": {"origin": "x"}}),
            ("/ingest/url", {"json": {"item": {"url": "https://example.org/"}}}),
        ):
            r = await client.post(path, headers=headers, **body)
            self.assertEqual(r.status_code, 403, path)
        self.assertEqual((await client.get("/ingest/batch/x")).status_code, 403)
        retry = f"/ingest/run/{uuid.uuid4()}/retry"
        self.assertEqual((await client.post(retry, headers=headers)).status_code, 403)


if __name__ == "__main__":
    unittest.main()
