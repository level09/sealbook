"""Located retrieval is a lead, with original offsets and visible coverage."""

import base64
import io
import unittest
from pathlib import Path
from unittest import mock

import httpx2
import orjson
from sqlalchemy import select

from sealbook.discovery import service
from sealbook.ledger.models import Run
from sealbook.runs.worker import work_once
from sealbook.settings import Config
from tests import test_review, test_runs


@unittest.skipUnless(test_runs.POSTGRES, "located search needs Postgres")
class SearchWorkflowTest(unittest.IsolatedAsyncioTestCase):
    setUp = test_runs.RunsTest.setUp
    asyncSetUp = test_runs.RunsTest.asyncSetUp
    asyncTearDown = test_runs.RunsTest.asyncTearDown
    queue = test_runs.RunsTest.queue
    get_run = test_runs.RunsTest.get_run
    transport = test_runs.RunsTest.transport

    async def test_same_model_name_with_changed_reference_vectors_requires_rebuild(
        self,
    ):
        from sealbook.discovery import embeddings
        from sealbook.discovery.models import EmbeddingSpace

        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_MODEL", "vendor/model-x")
        )
        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_URL", "http://embed.test")
        )
        epoch = [0]

        def respond(request):
            inputs = orjson.loads(request.content)["input"]
            data = []
            for i in range(len(inputs)):
                vector = [0.0] * 768
                vector[i + epoch[0]] = 1.0
                data.append(vector)
            return httpx2.Response(
                200, json={"model": "same-alias", "embeddings": data}
            )

        transport = httpx2.MockTransport(respond)
        async with self.Session() as db, db.begin():
            before = await embeddings.call(
                db, ["private source"], "vendor/model-x", transport
            )
        epoch[0] = 4
        async with self.Session() as db, db.begin():
            with self.assertRaisesRegex(ValueError, "drift"):
                await embeddings.call(
                    db, ["private source"], "vendor/model-x", transport
                )
            after = await embeddings.call(
                db, ["private source"], "vendor/model-x", transport, rebuild=True
            )
            self.assertNotEqual(before[-1], after[-1])
            rows = list((await db.execute(select(EmbeddingSpace))).scalars())
            self.assertEqual(sum(row.active for row in rows), 1)

    async def test_endpoint_that_ignores_content_is_refused(self):
        from sealbook.discovery import embeddings

        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", "gemma2"))
        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_URL", "http://embed.test")
        )

        def respond(request):
            inputs = orjson.loads(request.content)["input"]
            return httpx2.Response(
                200,
                json={
                    "model": "gemma2",
                    "embeddings": [[1.0] + [0.0] * 767 for _ in inputs],
                },
            )

        async with self.Session() as db, db.begin():
            with self.assertRaisesRegex(ValueError, "ignored"):
                await embeddings.call(
                    db, ["query"], "gemma2", httpx2.MockTransport(respond)
                )

    async def test_arabic_passage_match_points_to_the_original_characters(self):
        source = "قيد: أَحْمَد إبراهيم، رقم ١٢٣٤."
        await self.queue(data=source.encode(), tool="index", route="local")
        await work_once(self.Session, tools=["index"])
        async with self.Session() as db:
            hits = await service.search(
                db, "احمد", "keyword", {"investigation": None, "time": None}
            )
            self.assertEqual(len(hits), 1)
            self.assertEqual(
                source[hits[0]["span"]["start"] : hits[0]["span"]["end"]], "أَحْمَد"
            )
            self.assertEqual(hits[0]["signals"], ["normalized"])
            self.assertIn("segment", hits[0])

    async def test_literal_identifier_ranks_before_a_normalized_variant(self):
        for source in ("Registry ١٢٣٤, old copy", "Registry 1234, exact copy"):
            await self.queue(data=source.encode(), tool="index", route="local")
            await work_once(self.Session, tools=["index"])
        async with self.Session() as db:
            hits = await service.search(
                db, "1234", "keyword", {"investigation": None, "time": None}
            )
            self.assertEqual(len(hits), 2)
            self.assertEqual(hits[0]["signals"], ["literal"])
            self.assertEqual(hits[1]["signals"], ["normalized"])

    async def test_a_merged_spelling_signal_keeps_the_spelling_it_matched(self):
        await self.queue(
            data="Held at Branch 999, الفرع 999".encode(), tool="index", route="local"
        )
        await work_once(self.Session, tools=["index"])
        spelled = mock.AsyncMock(return_value={"الفرع 999": "place_name"})
        self.enterContext(mock.patch.object(service, "spellings", spelled))
        async with self.Session() as db:
            hits = await service.search(
                db, "Branch 999", "keyword", {"investigation": None, "time": None}
            )
        self.assertIn("place_name", hits[0]["signals"])
        self.assertEqual(hits[0]["matched"], "الفرع 999")

    async def test_exact_tiers_come_one_row_per_source(self):
        self.enterContext(mock.patch.object(Config, "INDEX_BATCH_CHUNKS", 128))
        # Visual RTL order from a PDF page: the words are present, the phrase is not.
        source = "4471 رقم نقل أمر\n" + "x" * 1900 + "\nأمر نقل again"
        await self.queue(data=source.encode(), tool="index", route="local")
        while await work_once(self.Session, tools=["index"]):
            pass
        everything = {"investigation": None, "time": None}
        async with self.Session() as db:
            sha = await db.scalar(select(Run.inputs["blobs"][0].astext))
            hits = await service.search(db, "أمر نقل رقم 4471", "keyword", everything)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["signals"], ["words"])
            self.assertEqual(hits[0]["group"], "text_match")
            self.assertEqual(source[slice(*hits[0]["span"].values())], "4471")
            rows = await service.search(db, "أمر نقل", "keyword", everything)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(rows[0]["also"]), 1)
            found = await service.search(db, sha[:10], "keyword", everything)
            self.assertEqual(
                [(r["sha256"], r["signals"]) for r in found], [(sha, ["sha256"])]
            )

    async def test_grouped_search_masks_sensitive_text_and_finds_hashes(self):
        from sealbook.search import find

        for text, sensitivity in (
            (b"Registry 1234, open copy", "ordinary"),
            (b"Registry 1234, closed copy", "sensitive"),
        ):
            await self.queue(
                data=text, tool="index", route="local", sensitivity=sensitivity
            )
            await work_once(self.Session, tools=["index"])
        everything = {"investigation": None, "time": None}
        async with self.Session() as db:
            passages = (await find.find(db, "registry 1234", everything))["groups"][
                "passages"
            ]
            self.assertEqual(passages["total"], 2)
            shown = {i["masked"]: i for i in passages["items"]}
            snippet = shown[False]["snippet"]
            ((start, end),) = snippet["highlights"]
            self.assertEqual(snippet["text"][start:end], "Registry 1234")
            self.assertIsNone(shown[True]["snippet"])
            self.assertIn("?segment=", shown[True]["link"])
            sha = shown[True]["sha256"]
            sources = (await find.find(db, sha[:12], everything))["groups"]["sources"]
            self.assertEqual([i["id"] for i in sources["items"]], [sha])

    async def test_query_embedding_rechecks_only_the_text_probes(self):
        from sealbook.discovery import embeddings

        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_URL", "http://embed.test")
        )
        sent, epoch = [], [0]

        def respond(request):
            inputs = orjson.loads(request.content)["input"]
            sent.append(len(inputs))
            rows = [[0.0] * 768 for _ in inputs]
            for i, row in enumerate(rows):
                row[i + epoch[0]] = 1.0
            return httpx2.Response(200, json={"model": "m", "embeddings": rows})

        transport = httpx2.MockTransport(respond)
        async with self.Session() as db, db.begin():
            _, created = await embeddings.query(db, "q", "m", transport)
            _, reused = await embeddings.query(db, "q", "m", transport)
            self.assertEqual(created, reused)
            self.assertEqual(sent, [7, 3])
            epoch[0] = 4
            with self.assertRaisesRegex(ValueError, "drift"):
                await embeddings.query(db, "q", "m", transport)

    async def test_incidental_identifier_substrings_do_not_exhaust_the_candidate_cap(
        self,
    ):
        self.enterContext(mock.patch.object(Config, "INDEX_BATCH_CHUNKS", 128))
        source = "\n".join(f"ABC1234XYZ record {i}" + "x" * 1800 for i in range(55))
        await self.queue(data=source.encode(), tool="index", route="local")
        while await work_once(self.Session, tools=["index"]):
            pass
        await self.queue(data=b"Registry 1234, exact copy", tool="index", route="local")
        await work_once(self.Session, tools=["index"])
        async with self.Session() as db:
            hits = await service.search(
                db, "1234", "keyword", {"investigation": None, "time": None}
            )
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["signals"], ["literal"])

    async def test_large_text_becomes_searchable_before_the_continuation(self):
        self.enterContext(
            mock.patch.object(Config, "INDEX_BATCH_CHUNKS", 1, create=True)
        )
        source = "first phrase " + "x" * 1800 + "\nlast phrase"
        await self.queue(data=source.encode(), tool="index", route="local")
        await work_once(self.Session, tools=["index"])
        async with self.Session() as db:
            hits = await service.search(
                db, "first phrase", "keyword", {"investigation": None, "time": None}
            )
            self.assertEqual(len(hits), 1)
            continuation = list(
                (
                    await db.execute(
                        select(Run).where(Run.tool == "index", Run.status == "queued")
                    )
                ).scalars()
            )
            self.assertEqual(len(continuation), 1)
            self.assertEqual(continuation[0].inputs["cursor"], 1)
            from sealbook.discovery.coverage import report

            coverage = await report(db, {"investigation": None, "time": None})
            self.assertFalse(coverage["complete"])
            self.assertEqual(coverage["items"][0]["state"], "partial")
            self.assertEqual(coverage["items"][0]["steps"][0]["completed"], 1)

    async def test_image_and_text_vectors_share_one_space_and_retrieve_the_source(self):
        from PIL import Image

        from sealbook.discovery.models import SegmentEmbedding
        from sealbook.ledger.write import verify_db

        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_MODEL", "vendor/model-x")
        )
        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_URL", "http://embed.test")
        )

        def respond(request):
            data = []
            for value in orjson.loads(request.content)["input"]:
                if isinstance(value, str):
                    dimension = 0 if "red" in value else 1
                else:
                    if "image" in value:
                        picture = Image.open(
                            io.BytesIO(base64.b64decode(value["image"]))
                        )
                        red, _, blue = picture.convert("RGB").getpixel((0, 0))
                        dimension = 0 if red > blue else 1
                    else:
                        import wave

                        import numpy as np

                        with wave.open(
                            io.BytesIO(base64.b64decode(value["audio"]))
                        ) as audio:
                            samples = np.frombuffer(
                                audio.readframes(audio.getnframes()), dtype="<i2"
                            )
                        crossings = np.count_nonzero(np.diff(samples > 0))
                        dimension = 2 if crossings < 2200 else 3
                vector = [0.0] * 768
                vector[dimension] = 1.0
                data.append(vector)
            return httpx2.Response(200, json={"model": "gemma2-q8", "embeddings": data})

        transport = httpx2.MockTransport(respond)
        expected = None
        for color in ("red", "blue"):
            buffer = io.BytesIO()
            Image.new("RGB", (256, 256), color).save(buffer, "JPEG")
            rid = await self.queue(
                data=buffer.getvalue(),
                mime="image/jpeg",
                tool="embed",
                route="local",
                investigation=color,
            )
            await work_once(self.Session, tools=["embed"], transport=transport)
            run = await self.get_run(rid)
            self.assertEqual(run.status, "done", run.error)
            if color == "red":
                expected = run.inputs["blobs"][0]
                artifact = run.inputs["prepared"][0]["artifact"]
                self.assertNotEqual(expected, artifact)
                self.assertIn("embedding_recipe", run.inputs)
                self.assertEqual(len(run.inputs["embedding_reference_hash"]), 64)
        async with self.Session() as db:
            hits = await service.search(
                db,
                "red image",
                "hybrid",
                {"investigation": None, "time": None},
                transport,
            )
            self.assertEqual(hits[0]["sha256"], expected)
            self.assertNotIn("span", hits[0])
            scoped = await service.search(
                db,
                "red image",
                "hybrid",
                {"investigation": "blue", "time": None},
                transport,
            )
            self.assertEqual(len(scoped), 1)
            self.assertNotEqual(scoped[0]["sha256"], expected)
            self.assertEqual(
                len(list((await db.execute(select(SegmentEmbedding))).scalars())), 2
            )
            self.assertEqual(await verify_db(db), [])

        from sealbook.discovery.coverage import report

        async with self.Session() as db:
            self.assertTrue(
                (await report(db, {"investigation": None, "time": None}))["complete"]
            )
            with mock.patch.object(Config, "EMBEDDING_REVISION", "changed-weights"):
                coverage = await report(db, {"investigation": None, "time": None})
                self.assertFalse(coverage["complete"])
                self.assertTrue(
                    all(row["state"] == "incomplete" for row in coverage["items"])
                )

        import uuid

        from sealbook.ledger.contracts import Prop, StatementIn
        from sealbook.ledger.write import append
        from sealbook.people.data import erase

        person = f"person:{uuid.uuid4()}"
        async with self.Session() as db, db.begin():
            await append(
                db,
                StatementIn(
                    subject=person,
                    prop=Prop.NAME,
                    value={"text": "synthetic name"},
                    span_blob=expected,
                    span={"page": 1, "bbox": [0, 0, 16, 16]},
                    author_id=self.human.id,
                ),
            )
            await erase(db, person, self.human.id, "test erasure")
            self.assertFalse(await db.scalar(select(service.available(artifact))))
            self.assertFalse(await db.scalar(select(service.available(expected))))
            hits = await service.search(
                db,
                "red image",
                "hybrid",
                {"investigation": "red", "time": None},
                transport,
            )
            self.assertEqual(hits, [])

    async def test_pdf_text_returns_its_source_page(self):
        from io import BytesIO

        from pypdf import PdfReader, PdfWriter

        fixture = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "acceptance/data/transfer_order.pdf"
        )
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.add_page(PdfReader(fixture).pages[0])
        buffer = BytesIO()
        writer.write(buffer)
        rid = await self.queue(
            data=buffer.getvalue(), mime="application/pdf", tool="pdf", route="local"
        )
        self.enterContext(mock.patch.object(Config, "RUN_MODELS", {"local": "m/l"}))
        await work_once(self.Session, tools=["pdf"])
        self.assertEqual((await self.get_run(rid)).status, "done")
        # Claims are read from each page with text, once, on the extraction route.
        async with self.Session() as db:
            [extract] = (
                await db.execute(select(Run).where(Run.tool == "extract"))
            ).scalars()
        self.assertEqual((extract.route, extract.model_requested), ("local", "m/l"))
        pdf = await self.get_run(rid)
        self.assertNotEqual(extract.inputs["blobs"], pdf.inputs["blobs"])
        while await work_once(self.Session, tools=["index"]):
            pass
        async with self.Session() as db:
            hits = await service.search(
                db, "999", "keyword", {"investigation": None, "time": None}
            )
            self.assertTrue(hits)
            self.assertEqual(hits[0]["location"]["page"], 2)


@unittest.skipUnless(test_runs.POSTGRES, "source access needs Postgres")
class SourceAccessTest(unittest.IsolatedAsyncioTestCase):
    setUp = test_review.ReviewTest.setUp
    asyncSetUp = test_review.ReviewTest.asyncSetUp
    asyncTearDown = test_review.ReviewTest.asyncTearDown
    client = test_review.ReviewTest.client
    propose = test_review.ReviewTest.propose

    async def test_exact_excerpt_needs_reveal_and_disappears_after_erasure(self):
        import uuid

        import stk.extensions as ext

        from sealbook.discovery.models import Segment
        from sealbook.ledger.models import Acquisition
        from sealbook.people.data import erase

        subject = f"person:{uuid.uuid4()}"
        await self.propose(subject, "يوسف")
        async with ext.async_session_factory() as db, db.begin():
            acq = await db.scalar(select(Acquisition))
            await service.queue(db, acq, "text/plain")
        await work_once(ext.async_session_factory, tools=["index"])
        async with ext.async_session_factory() as db:
            segment = await db.scalar(select(Segment))
        client = await self.client()
        start = test_review.TEXT.index("يوسف")
        path = (
            f"/api/evidence/segments/{segment.id}/excerpt?start={start}&end={start + 4}"
        )
        page = await client.get(
            f"/evidence/segments/{segment.id}?start={start}&end={start + 4}"
        )
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("يوسف", await page.get_data(as_text=True))
        self.assertEqual((await client.get(path)).status_code, 403)
        self.assertEqual(
            (
                await client.post(
                    f"/api/evidence/{self.sha}/reveal",
                    headers=client.csrf,
                    json={"item": {}},
                )
            ).status_code,
            200,
        )
        excerpt = await (await client.get(path)).get_json()
        self.assertEqual(excerpt["match"], "يوسف")
        self.assertEqual(
            (
                await client.get(
                    f"/api/evidence/segments/{segment.id}/excerpt?start=0&end=999999"
                )
            ).status_code,
            400,
        )
        async with ext.async_session_factory() as db, db.begin():
            await erase(db, subject, self.viewer.id, "test erasure")
        self.assertEqual((await client.get(path)).status_code, 404)

    async def test_queue_processing_says_what_it_queued_and_why_not(self):
        client = await self.client()
        path = f"/api/evidence/{self.sha}/analyse"
        with mock.patch.object(Config, "RUN_MODELS", {}):
            off = await (
                await client.post(path, headers=client.csrf, json={"item": {}})
            ).get_json()
        self.assertIsNone(off["processing"]["queued"])
        self.assertIn("Set MODEL_LOCAL or MODEL_CLOUD_ZDR in settings.", off["message"])
        with mock.patch.object(Config, "RUN_MODELS", {"local": "m/l"}):
            on = await (
                await client.post(path, headers=client.csrf, json={"item": {}})
            ).get_json()
            again = await (
                await client.post(path, headers=client.csrf, json={"item": {}})
            ).get_json()
        self.assertEqual(on["processing"]["queued"], "extract")
        self.assertIn("reading the text", on["message"])
        self.assertIn("already done or queued", again["message"])
        item = await (await client.get(f"/api/evidence/{self.sha}/item")).get_json()
        [read] = item["reading"]
        self.assertEqual((read["tool"], read["status"]), ("extract", "queued"))

    async def test_pdf_page_preview_is_rendered_only_after_logged_reveal(self):
        import stk.extensions as ext
        from PIL import Image

        from sealbook import blobs

        fixture = (
            Path(__file__).resolve().parents[1] / "acceptance/data/transfer_order.pdf"
        )
        async with ext.async_session_factory() as db, db.begin():
            blob = await blobs.put(
                db, fixture.read_bytes(), "application/pdf", Config.BLOB_DIR
            )
        client = await self.client()
        path = f"/api/evidence/{blob.sha256}/content?page=1"
        self.assertEqual((await client.get(path)).status_code, 403)
        await client.post(
            f"/api/evidence/{blob.sha256}/reveal",
            headers=client.csrf,
            json={"item": {}},
        )
        response = await client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        with Image.open(io.BytesIO(await response.get_data())) as image:
            self.assertGreater(image.width, 100)
        self.assertEqual(
            (await client.get(path.replace("page=1", "page=999"))).status_code, 400
        )
