"""Extraction runs and model routes against a throwaway Postgres per test."""

import hashlib
import os
import tempfile
import unittest
import uuid
from datetime import UTC, date, datetime
from unittest import mock

import httpx
import orjson
from alembic import command
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)

from sealbook import blobs
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import (
    Blob,
    Principal,
    Run,
    RunEvent,
    Statement,
    StatementTier,
)
from sealbook.ledger.write import record_acquisition, verify_db
from sealbook.runs.worker import locate, valid_range, work_once
from sealbook.settings import Config

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
TEXT = "Register entry: Samir Haddad, born 1990-03-14 in Aleppo."
NAME = "Samir Haddad"


def completion(content: str, model="vendor/model-x-2026", provider="AcmeCloud"):
    return {
        "model": model,
        "provider": provider,
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


def item(value, occurrence=1, prop="name", mention="Samir Haddad", valid=None):
    return {
        "mention": mention,
        "prop": prop,
        "value": {"text": value, "lang": "en"},
        "occurrence": occurrence,
        "evidence": ["printed_text"],
        "valid": valid,
    }


class LocateTest(unittest.TestCase):
    def test_nth_exact_occurrence(self):
        text = "يوسف خالد، ثم يوسف"
        self.assertEqual(locate(text, "يوسف", 1), {"start": 0, "end": 4})
        self.assertEqual(locate(text, "يوسف", 2), {"start": 14, "end": 18})
        self.assertIsNone(locate(text, "يوسف", 3))
        self.assertIsNone(locate(text, "", 1))
        self.assertIsNone(locate(text, "Yusuf", 1))

    def test_valid_range(self):
        d = date.fromisoformat
        self.assertEqual(valid_range(None), (None, None))
        self.assertEqual(valid_range("2013"), (d("2013-01-01"), d("2013-12-31")))
        self.assertEqual(valid_range("2013-02"), (d("2013-02-01"), d("2013-02-28")))
        self.assertEqual(valid_range("2013-09-17"), (d("2013-09-17"),) * 2)
        for bad in ("2013-13", "17-09-2013", "soon"):
            with self.assertRaises(ValueError):
                valid_range(bad)


@unittest.skipUnless(POSTGRES, "runs need Postgres (set SQLALCHEMY_DATABASE_URI)")
class RunsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_URL", "http://local.test")
        )
        # These tests mock an Ollama-style /api/embed server.
        self.enterContext(mock.patch.object(Config, "EMBEDDING_ROUTE", "local"))
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config("sealbook", url, quiet=True)
        command.upgrade(config, "heads")
        self.url = make_url(url).set(drivername="postgresql+asyncpg")
        blob_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(Config, "BLOB_DIR", blob_dir))
        self.enterContext(
            mock.patch.dict(
                os.environ,
                {
                    "ROUTE_LOCAL_BASE_URL": "http://local.test/v1",
                    # Mocked transports never send it; the live test uses the real one.
                    "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY")
                    or "test-key",
                },
            )
        )
        self.requests = []

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.url)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as s, s.begin():
            self.human = Principal(kind="human", name="analyst")
            self.agent = Principal(kind="agent", name="extractor")
            s.add_all([self.human, self.agent])
            await s.flush()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def transport(self, body=None, error=None):
        def handle(request):
            self.requests.append(request)
            if error:
                raise error
            from sealbook.discovery.embeddings import QUERY_PREFIX, TEXT_PROBES

            inputs = orjson.loads(request.content).get("input", [])
            response = body
            if (
                inputs[-6:-4] == [QUERY_PREFIX + q for q in TEXT_PROBES]
                and body
                and "embeddings" in body
            ):
                data = list(body["embeddings"])
                for index in range(6):
                    vector = [0.0] * 768
                    vector[index + 1] = 1.0
                    data.append(vector)
                response = body | {"embeddings": data}
            return httpx.Response(200, json=response)

        return httpx.MockTransport(handle)

    async def queue(
        self,
        data=TEXT.encode(),
        mime="text/plain",
        sensitivity="ordinary",
        route="cloud",
        tool="extract",
        investigation=None,
        **inputs,
    ):
        async with self.Session() as s, s.begin():
            blob = await blobs.put(s, data, mime, Config.BLOB_DIR)
            acq = await record_acquisition(
                s,
                blob_sha256=blob.sha256,
                origin="device:test",
                collector_id=self.human.id,
                at=datetime.now(UTC),
                context={"investigation": investigation} if investigation else {},
                sensitivity=sensitivity,
                doc_type="register",
            )
            run = Run(
                principal_id=self.agent.id,
                tool=tool,
                route=route,
                model_requested="vendor/model-x",
                inputs={"blobs": [blob.sha256], "acquisition": str(acq.id), **inputs},
            )
            s.add(run)
        return run.id

    async def events(self, run_id):
        async with self.Session() as s:
            q = select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id)
            return list((await s.execute(q)).scalars())

    async def get_run(self, run_id):
        async with self.Session() as s:
            return await s.get(Run, run_id)

    async def test_sensitive_input_refused_on_cloud(self):
        run_id = await self.queue(sensitivity="sensitive", route="cloud")
        await work_once(self.Session, transport=self.transport(completion("{}")))
        run = await self.get_run(run_id)
        self.assertEqual(run.status, "failed")
        self.assertIn("not allowed", run.error)
        self.assertEqual(self.requests, [])
        self.assertEqual([e.stage for e in await self.events(run_id)][-1], "failed")

    async def test_sensitive_input_refused_on_anthropic(self):
        run_id = await self.queue(sensitivity="sensitive", route="anthropic")
        await work_once(self.Session, transport=self.transport(completion("{}")))
        run = await self.get_run(run_id)
        self.assertEqual(run.status, "failed")
        self.assertIn("route anthropic not allowed", run.error)
        self.assertEqual(self.requests, [])

    async def test_local_down_fails_without_other_route(self):
        run_id = await self.queue(sensitivity="sensitive", route="local")
        down = self.transport(error=httpx.ConnectError("refused"))
        await work_once(self.Session, transport=down)
        run = await self.get_run(run_id)
        self.assertEqual(run.status, "failed")
        self.assertEqual([r.url.host for r in self.requests], ["local.test"])
        self.assertFalse(await work_once(self.Session, transport=down))

    async def test_extract_spans_tiers_events_and_tamper(self):
        out = {
            "statements": [
                item(NAME),
                item("Aleppo", 2, prop="place_of_birth"),  # no second occurrence
                item("Aleppo", prop="seen_at", valid="1990-03"),
            ]
        }
        run_id = await self.queue()
        await work_once(
            self.Session,
            transport=self.transport(completion(orjson.dumps(out).decode())),
        )
        run = await self.get_run(run_id)
        self.assertEqual(run.status, "done", run.error)
        self.assertEqual(
            (run.route, run.model_served, run.provider_served),
            ("cloud", "vendor/model-x-2026", "AcmeCloud"),
        )
        self.assertEqual(run.endpoint, "https://openrouter.ai/api/v1/chat/completions")
        sent = orjson.loads(self.requests[0].content)
        self.assertEqual(sent["response_format"]["type"], "json_schema")
        self.assertNotIn("tools", sent)
        async with self.Session() as s:
            raw = blobs.read(await s.get(Blob, run.raw_output_ref), Config.BLOB_DIR)
            self.assertEqual(run.output_hash, hashlib.sha256(raw).hexdigest())
            self.assertEqual(orjson.loads(raw)["provider"], "AcmeCloud")
            rows = (
                await s.execute(
                    select(Statement.prop, Statement.subject, StatementTier.tier)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .where(Statement.run_id == run_id)
                )
            ).all()
            tiers = {prop: tier for prop, _, tier in rows}
            seen = (
                await s.execute(select(Statement).where(Statement.prop == Prop.SEEN_AT))
            ).scalar_one()
            self.assertEqual(
                (seen.valid_from, seen.valid_to),
                (date(1990, 3, 1), date(1990, 3, 31)),
            )
            self.assertEqual(tiers[Prop.NAME], Tier.UNCONFIRMED)
            self.assertEqual(tiers[Prop.PLACE_OF_BIRTH], Tier.REJECTED)
            self.assertEqual(len({subj for _, subj, _ in rows}), 1)  # one mention
            self.assertEqual(await verify_db(s), [])

        events = await self.events(run_id)
        self.assertEqual([e.seq for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(
            [e.stage for e in events],
            [
                "claimed",
                "input_read",
                "model_called",
                "dropped",
                "statements_written",
                "sealed",
            ],
        )
        self.assertEqual(events[3].drop_reason, "quote_not_in_text")
        self.assertEqual(events[4].counts["dropped"], 1)

        async with self.Session() as s, s.begin():
            (await s.get(Run, run_id)).model_served = "vendor/other-model"
        async with self.Session() as s:
            self.assertTrue(any("modified" in p for p in await verify_db(s)))

    async def test_a_headline_or_link_is_not_a_person(self):
        headline = "#Aleppo airstrike hits the market this morning near the old souk"
        text = f"{TEXT}\n{headline}\nhttps://video.test/x\nPosted in Aleppo."
        out = {
            "statements": [
                item(NAME),
                item(headline, mention="post"),
                item("Aleppo", prop="seen_at", mention="post"),
                item("https://video.test/x", mention="link"),
            ]
        }
        run_id = await self.queue(data=text.encode())
        await work_once(
            self.Session,
            transport=self.transport(completion(orjson.dumps(out).decode())),
        )
        self.assertEqual((await self.get_run(run_id)).status, "done")
        async with self.Session() as s:
            written = (
                await s.execute(
                    select(Statement.prop).where(Statement.run_id == run_id)
                )
            ).scalars()
            self.assertEqual(list(written), [Prop.NAME])
        drops = [e.drop_reason for e in await self.events(run_id) if e.drop_reason]
        self.assertEqual(drops, ["not_a_person"] * 3)

    async def test_placeholder_dates_keep_the_quoted_claims(self):
        # Recorded from openai/gpt-4o-mini on OpenRouter, 2026-10-24: both claims
        # quote real spans, but `valid` echoed the string "null" and the old code
        # dropped them, so a real model wrote no claims at all.
        recorded = (
            '{"statements":[{"mention":"Samir Haddad","prop":"birth_date","value":'
            '{"text":"1990-03-14","lang":"en"},"occurrence":1,"valid":"null",'
            '"evidence":["registry_stamp"]},{"mention":"Samir Haddad","prop":'
            '"place_of_birth","value":{"text":"Aleppo","lang":"en"},"occurrence":1,'
            '"valid":"YYYY-MM-DD","evidence":["registry_stamp"]}]}'
        )
        run_id = await self.queue()
        await work_once(
            self.Session,
            transport=self.transport(completion(recorded, model="openai/gpt-4o-mini")),
        )
        self.assertEqual((await self.get_run(run_id)).status, "done")
        [counts] = [
            e.counts
            for e in await self.events(run_id)
            if e.stage == "statements_written"
        ]
        self.assertEqual(
            {k: counts[k] for k in ("proposed", "written", "dropped", "undated")},
            {"proposed": 2, "written": 2, "dropped": 0, "undated": 2},
        )
        async with self.Session() as s:
            rows = (
                await s.execute(
                    select(Statement.span, Statement.valid_from, StatementTier.tier)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .where(Statement.run_id == run_id)
                )
            ).all()
        self.assertEqual(
            sorted(rows, key=lambda r: r[0]["start"]),
            [
                ({"start": 35, "end": 45}, None, Tier.UNCONFIRMED),
                ({"start": 49, "end": 55}, None, Tier.UNCONFIRMED),
            ],
        )
        sent = orjson.loads(self.requests[0].content)
        valid = sent["response_format"]["json_schema"]["schema"]["properties"][
            "statements"
        ]["items"]["properties"]["valid"]
        self.assertEqual(valid["anyOf"][1], {"type": "null"})

    async def test_empty_input_is_a_drop_event(self):
        run_id = await self.queue(data=b"  \n")
        await work_once(self.Session, transport=self.transport(completion("{}")))
        events = await self.events(run_id)
        self.assertEqual(events[1].drop_reason, "empty_text")
        self.assertEqual(self.requests, [])
        self.assertEqual((await self.get_run(run_id)).status, "done")

    async def test_embedding_indexes_exact_spans_and_skips_existing_model(self):
        from sealbook.discovery.models import Segment, SegmentEmbedding

        run_id = await self.queue(tool="embed", route="local")
        body = {
            "model": "vendor/model-x",
            "embeddings": [[1.0] + [0.0] * 767],
        }
        await work_once(self.Session, transport=self.transport(body))
        self.assertEqual((await self.get_run(run_id)).status, "done")
        async with self.Session() as s:
            rows = (
                await s.execute(
                    select(Segment).join(
                        SegmentEmbedding, SegmentEmbedding.segment_id == Segment.id
                    )
                )
            ).scalars()
            self.assertEqual([(r.start, r.end) for r in rows], [(0, len(TEXT))])
            self.assertEqual(await verify_db(s), [])
        second = await self.queue(tool="embed", route="local")
        await work_once(self.Session, transport=self.transport(body))
        self.assertEqual((await self.get_run(second)).status, "done")
        # The second acquisition retains custody while the unchanged source is not embedded twice.
        self.assertEqual(len(self.requests), 1)

    async def test_embedding_dimension_failure_leaves_no_search_rows(self):
        from sealbook.discovery.models import SegmentEmbedding

        run_id = await self.queue(tool="embed", route="local")
        await work_once(
            self.Session,
            transport=self.transport(
                {"model": "vendor/model-x", "embeddings": [[1, 2]]}
            ),
        )
        self.assertEqual((await self.get_run(run_id)).status, "failed")
        async with self.Session() as s:
            self.assertEqual(
                (await s.execute(select(SegmentEmbedding))).scalars().all(), []
            )

    async def test_explicit_rebuild_replaces_an_old_model_alias_index(self):
        from sealbook.discovery.models import SegmentEmbedding

        body = {
            "model": "served-v1",
            "embeddings": [[1.0] + [0.0] * 767],
        }
        await self.queue(tool="embed", route="local")
        await work_once(self.Session, tools=["embed"], transport=self.transport(body))
        rid = await self.queue(tool="embed", route="local", rebuild=True)
        await work_once(
            self.Session,
            tools=["embed"],
            transport=self.transport(body | {"model": "served-v2"}),
        )
        async with self.Session() as s:
            row = (await s.execute(select(SegmentEmbedding))).scalar_one()
            self.assertEqual(row.run_id, rid)
            self.assertEqual((await s.get(Run, rid)).model_served, "served-v2")
            self.assertEqual(await verify_db(s), [])

    async def test_actor_change_queues_a_followup_behind_a_running_job(self):
        from sealbook.discovery.service import queue_actor
        from sealbook.ledger.models import Acquisition

        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_MODEL", "vendor/model-x")
        )
        rid = await self.queue(tool="embed_actor", route="local")
        async with self.Session() as s, s.begin():
            running = await s.get(Run, rid)
            running.status = "running"
            running.inputs = {
                **running.inputs,
                "subject": "person:example",
                "investigation": "",
            }
            acq = await s.get(Acquisition, uuid.UUID(running.inputs["acquisition"]))
            await queue_actor(s, "person:example", acq)
        async with self.Session() as s:
            statuses = list(
                (
                    await s.execute(select(Run.status).where(Run.tool == "embed_actor"))
                ).scalars()
            )
            self.assertCountEqual(statuses, ["running", "queued"])

    async def test_actor_embedding_is_excluded_after_its_profile_changes(self):
        from sealbook.discovery import service
        from sealbook.discovery.models import EmbeddingSpace, EntityEmbedding
        from sealbook.ledger.contracts import StatementIn
        from sealbook.ledger.write import append

        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_MODEL", "vendor/model-x")
        )
        await self.queue(route="local")
        await work_once(
            self.Session,
            tools=["extract"],
            transport=self.transport(
                completion(orjson.dumps({"statements": [item(NAME)]}).decode())
            ),
        )
        response = {
            "model": "vendor/model-x",
            "embeddings": [[1.0] + [0.0] * 767],
        }
        await work_once(
            self.Session, tools=["embed_actor"], transport=self.transport(response)
        )
        async with self.Session() as s:
            profile = (await s.execute(select(EntityEmbedding))).scalar_one()
            subject = profile.subject
            space = await s.scalar(select(EmbeddingSpace.id))
            found = await service.actor_search(
                s,
                "unmatched question",
                {"investigation": None, "time": None},
                ([1.0] + [0.0] * 767, space),
            )
            self.assertEqual([r["subject"] for r in found], [subject])
        async with self.Session() as s, s.begin():
            name = await s.scalar(select(Statement).where(Statement.prop == Prop.NAME))
            await append(
                s,
                StatementIn(
                    subject=subject,
                    prop=Prop.FATHER_NAME,
                    value={"text": "Samir", "lang": "en"},
                    author_id=self.human.id,
                    span_blob=name.span_blob,
                    span={"start": 16, "end": 21},
                ),
            )
        async with self.Session() as s:
            found = await service.actor_search(
                s,
                "unmatched question",
                {"investigation": None, "time": None},
                ([1.0] + [0.0] * 767, space),
            )
            self.assertEqual(found, [])

    async def test_new_actor_model_does_not_reuse_an_old_queued_job(self):
        from sealbook.discovery.service import queue_actor
        from sealbook.ledger.models import Acquisition

        rid = await self.queue(tool="embed_actor", route="local")
        async with self.Session() as s, s.begin():
            old = await s.get(Run, rid)
            old.inputs = {
                **old.inputs,
                "subject": "person:example",
                "investigation": "",
            }
            acq = await s.get(Acquisition, uuid.UUID(old.inputs["acquisition"]))
            with mock.patch.object(Config, "EMBEDDING_MODEL", "vendor/new-model"):
                await queue_actor(s, "person:example", acq, rebuild=True)
        async with self.Session() as s:
            models = list((await s.execute(select(Run.model_requested))).scalars())
            self.assertCountEqual(models, ["vendor/model-x", "vendor/new-model"])

    async def test_keyword_search_keeps_investigations_separate(self):
        from sealbook.discovery.service import search

        for case in ("one", "two"):
            await self.queue(
                data=f"shared phrase in case {case}".encode(),
                tool="index",
                route="local",
                investigation=case,
            )
            await work_once(self.Session, tools=["index"])
        async with self.Session() as s:
            found = await search(
                s, "shared phrase", "keyword", {"investigation": "one", "time": None}
            )
            self.assertEqual(len(found), 1)
            blob = await s.get(Blob, found[0]["sha256"])
            self.assertEqual(
                blobs.read(blob, Config.BLOB_DIR), b"shared phrase in case one"
            )

    async def test_audio_fingerprint_recovers_an_excerpt_without_picture(self):
        import subprocess
        from pathlib import Path

        from sealbook.ledger.models import Payload

        original = (
            Path(__file__).resolve().parents[1] / "acceptance/data/witness_clip.ogg"
        )
        excerpt = Path(Config.BLOB_DIR) / "excerpt.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-ss",
                "8",
                "-t",
                "12",
                "-i",
                str(original),
                str(excerpt),
            ],
            check=True,
            capture_output=True,
        )
        for path, mime in ((original, "audio/ogg"), (excerpt, "audio/wav")):
            rid = await self.queue(
                data=path.read_bytes(), mime=mime, tool="fingerprint", route="local"
            )
            await work_once(self.Session, tools=["fingerprint"])
            run = await self.get_run(rid)
            self.assertEqual(run.status, "done", run.error)
        async with self.Session() as s:
            value = await s.scalar(
                select(Payload.value)
                .join(Statement, Statement.commitment == Payload.commitment)
                .where(Statement.prop == Prop.MEDIA_MATCH)
            )
            self.assertIsNone(value["picture"])
            self.assertLess(abs(value["sound"]["offset_ms"] - 8000), 500)
            self.assertEqual(await verify_db(s), [])

    async def test_perceptual_media_match_is_a_reviewable_proposal(self):
        from io import BytesIO

        import numpy as np
        from PIL import Image

        pixels = np.random.default_rng(7).integers(
            0, 256, (256, 256, 3), dtype=np.uint8
        )
        image = Image.fromarray(pixels)
        png, jpg = BytesIO(), BytesIO()
        image.save(png, format="PNG")
        image.save(jpg, format="JPEG", quality=90)
        for data, mime in (
            (png.getvalue(), "image/png"),
            (jpg.getvalue(), "image/jpeg"),
        ):
            run_id = await self.queue(
                data=data, mime=mime, tool="fingerprint", route="local"
            )
            await work_once(self.Session)
            run = await self.get_run(run_id)
            self.assertEqual(run.status, "done", run.error)
        async with self.Session() as s:
            matches = (
                (
                    await s.execute(
                        select(Statement).where(Statement.prop == Prop.MEDIA_MATCH)
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(
                (await s.get(StatementTier, matches[0].id)).tier, "unconfirmed"
            )
            self.assertEqual(await verify_db(s), [])

    async def test_ocr_derives_text_and_queues_extract(self):
        run_id = await self.queue(
            data=b"\x89PNG fake",
            mime="image/png",
            tool="ocr",
            extract_route="local",
            extract_model="m/e",
        )
        await work_once(self.Session, transport=self.transport(completion(TEXT)))
        self.assertEqual((await self.get_run(run_id)).status, "done")
        async with self.Session() as s:
            derived = (
                await s.execute(select(Statement).where(Statement.run_id == run_id))
            ).scalar_one()
            self.assertEqual(derived.prop, Prop.DERIVED_FROM)
            child = (
                await s.execute(select(Run).where(Run.tool == "extract"))
            ).scalar_one()
            self.assertEqual(
                child.inputs["blobs"][0], derived.subject.removeprefix("blob:")
            )
            # Extraction keeps the route its own policy chose, not the OCR one.
            self.assertEqual((child.route, child.model_requested), ("local", "m/e"))

    @unittest.skipUnless(os.environ.get("OPENROUTER_API_KEY"), "no OPENROUTER_API_KEY")
    async def test_live_openrouter_extract(self):
        run_id = await self.queue(route="cloud")
        async with self.Session() as s, s.begin():
            (await s.get(Run, run_id)).model_requested = "openai/gpt-4o-mini"
        await work_once(self.Session)
        run = await self.get_run(run_id)
        self.assertEqual(run.status, "done", run.error)
        self.assertTrue(run.model_served.startswith("openai/gpt-4o-mini"))
        self.assertTrue(run.provider_served)
        written = [
            e for e in await self.events(run_id) if e.stage == "statements_written"
        ]
        counts = written[0].counts
        # Quotes located server-side: most proposals must land on real spans.
        self.assertGreater(counts["proposed"], 0)
        self.assertLess(counts["dropped"], counts["proposed"], counts)
        async with self.Session() as s:
            located = (
                await s.execute(
                    select(Statement.prop).where(
                        Statement.run_id == run_id, Statement.span.is_not(None)
                    )
                )
            ).scalars()
            self.assertIn(Prop.NAME, set(located))
            self.assertEqual(await verify_db(s), [])
