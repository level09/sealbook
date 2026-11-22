"""Local transcription runs with the model mocked at its boundary (load_model), so
decoding, chunking, segment writing, provenance and failures run for real.
STK_SPEECH_LIVE=1 also runs the downloaded model on the synthetic witness clip."""

import base64
import os
import re
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx2
import numpy as np
import orjson
from alembic import command
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)

from sealbook import blobs, speech
from sealbook.discovery import evidence
from sealbook.discovery.models import IndexProgress, Segment
from sealbook.ingest import batch, service
from sealbook.ledger.contracts import Prop
from sealbook.ledger.models import Blob, Payload, Principal, Run, RunEvent, Statement
from sealbook.ledger.write import verify_db
from sealbook.outbound import data as outbound
from sealbook.runs.worker import work_once
from sealbook.settings import Config

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
ROOT = Path(__file__).parent.parent / "acceptance"
PROVENANCE = {
    "model": "large-v3-turbo",
    "repo": "test/whisper",
    "revision": "abc123",
    "weights_sha256": "0" * 64,
    "runtime": "faster-whisper test",
    "device": "cpu",
    "compute_type": "int8",
}


def tone(path: Path, seconds: int, video=False) -> bytes:
    """A synthetic recording; the fake model decides what is "said" in it."""
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    if video:
        command += ["-f", "lavfi", "-i", f"color=c=black:s=32x32:d={seconds}"]
    else:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    subprocess.run([*command, str(path)], check=True, capture_output=True)
    return path.read_bytes()


class FakeModel:
    """Says "line N" every 4 s of whatever audio it is given, 3.5 s long."""

    def __init__(self, probability=0.3, silent=False):
        self.probability, self.silent, self.calls = probability, silent, []
        # Lines the model itself scores as noise it put words to.
        self.noise = False

    def detect_language(self, audio, vad_filter):
        self.calls.append(("detect", audio is not None and len(audio)))
        return "fa", np.float64(self.probability), []

    def transcribe(self, audio, language, **kw):
        self.calls.append(("transcribe", len(audio), language, kw))
        seconds = len(audio) / speech.RATE
        out = []
        for i, start in enumerate(range(0, int(seconds), 4)):
            end = min(start + 3.5, seconds)
            if self.silent or end - start < 1:
                continue
            word = SimpleNamespace(
                word=f" w{i}",
                start=start,
                end=end,
                probability=np.float64(0.3 if self.noise else 0.9),
            )
            out.append(
                SimpleNamespace(
                    start=start,
                    end=end,
                    text=f" line at {start}",
                    avg_logprob=np.float64(-0.8 if self.noise else -0.2),
                    no_speech_prob=0.01,
                    words=[word],
                )
            )
        return iter(out), None


@unittest.skipUnless(POSTGRES, "runs need Postgres (set SQLALCHEMY_DATABASE_URI)")
class SpeechTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        self.enterContext(mock.patch.object(Config, "RUN_MODELS", {"local": "m/l"}))
        self.enterContext(mock.patch.object(Config, "SPEECH_MODEL", "large-v3-turbo"))
        # The local model unless a test turns the hosted one on.
        self.enterContext(mock.patch.object(Config, "TRANSCRIBE_MODEL", ""))
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config("sealbook", url, quiet=True)
        command.upgrade(config, "heads")
        self.url = make_url(url).set(drivername="postgresql+asyncpg")
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(Config, "BLOB_DIR", str(self.tmp / "b")))
        self.model = FakeModel()
        self.load = self.enterContext(
            mock.patch.object(
                speech, "load_model", return_value=(self.model, PROVENANCE)
            )
        )

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.url)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as s, s.begin():
            self.human = Principal(kind="human", name="analyst")
            s.add(self.human)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def ingest(self, data, mime="audio/wav", **kw):
        async with self.Session() as s, s.begin():
            return await service.ingest_bytes(
                s,
                data,
                blob_dir=Config.BLOB_DIR,
                mime=mime,
                origin="device:test",
                collector_id=self.human.id,
                context={"batch": "b1"},
                **kw,
            )

    async def drain(self, tools=("transcribe",)):
        while await work_once(self.Session, list(tools)):
            pass

    async def runs(self, tool="transcribe"):
        async with self.Session() as s:
            q = select(Run).where(Run.tool == tool).order_by(Run.created_at)
            return list((await s.execute(q)).scalars())

    async def test_sensitive_recording_is_transcribed_locally_into_located_lines(self):
        out = await self.ingest(tone(self.tmp / "a.wav", 10), sensitivity="sensitive")
        self.assertEqual(out["processing"]["queued"], "transcribe")
        self.assertEqual(out["processing"]["route"], "local")
        await self.drain()
        [run] = await self.runs()
        self.assertEqual(run.status, "done", run.error)
        self.assertEqual(run.model_served, "test/whisper@abc123")
        self.assertEqual(run.endpoint, "local:cpu:int8")
        self.assertEqual(run.provider_served, "faster-whisper test")
        self.assertEqual(run.prompt_hash and len(run.prompt_hash), 64)
        # Detection was unsure (0.3): the detected language is still read, never a
        # preferred one, and the doubt is recorded.
        self.assertEqual(self.model.calls[1][2], "fa")
        self.assertTrue(self.model.calls[1][3]["vad_filter"])
        self.assertTrue(self.model.calls[1][3]["word_timestamps"])
        async with self.Session() as s:
            raw = orjson.loads(
                blobs.read(await s.get(Blob, run.raw_output_ref), Config.BLOB_DIR)
            )
            self.assertEqual(
                raw["language"],
                {
                    "language": "fa",
                    "detected": "fa",
                    "probability": "0.300",
                    "uncertain": True,
                },
            )
            self.assertEqual(raw["model"]["weights_sha256"], "0" * 64)
            self.assertEqual(
                [s["ms"] for s in raw["segments"]],
                [[0, 3500], [4000, 7500], [8000, 10000]],
            )
            self.assertEqual(raw["segments"][1]["words"][0]["ms"], [4000, 7500])
            index = [
                r.inputs
                for r in (
                    await s.execute(select(Run).where(Run.tool == "index"))
                ).scalars()
            ]
            self.assertEqual(
                sorted(i["location"]["ms"][0] for i in index), [0, 4000, 8000]
            )
            self.assertTrue(all(i["segment_kind"] == "text" for i in index))
            [extract] = (
                await s.execute(select(Run).where(Run.tool == "extract"))
            ).scalars()
            self.assertEqual(extract.route, "local")
            transcript = await s.get(Blob, extract.inputs["blobs"][0])
            self.assertEqual(
                blobs.read(transcript, Config.BLOB_DIR).decode(),
                "line at 0\nline at 4\nline at 8",
            )
            derived = (
                await s.execute(
                    select(Payload.value)
                    .join(Statement, Statement.commitment == Payload.commitment)
                    .where(
                        Statement.prop == Prop.DERIVED_FROM,
                        Statement.subject == f"blob:{transcript.sha256}",
                    )
                )
            ).scalar()
            self.assertEqual(derived, {"blob": out["sha256"]})
            progress = await s.get(IndexProgress, (out["sha256"], "transcribe", ""))
            self.assertEqual((progress.completed, progress.total), (10000, 10000))
        await self.drain(["index"])
        async with self.Session() as s:
            located = (
                await s.execute(
                    select(Segment.location).where(
                        Segment.source_sha256 == out["sha256"]
                    )
                )
            ).scalars()
            self.assertEqual(sorted(loc["ms"][0] for loc in located), [0, 4000, 8000])
            self.assertEqual(await verify_db(s), [])
            status = await batch.status(s, "b1")
        self.assertEqual(status["items"][0]["stages"]["read"], "done")
        self.assertEqual(status["items"][0]["read_progress"]["unit"], "ms")

    async def test_claims_from_a_transcript_are_heard_not_printed(self):
        """The model calls every text printed; a transcript's words were heard. The
        claims say so, and a name carries that it was heard, so a person reads
        "heard as" and a matcher can weigh it below a written one."""
        await self.ingest(tone(self.tmp / "a.wav", 10))
        await self.drain()
        answer = {
            "statements": [
                {
                    "mention": "Line",
                    "prop": "name",
                    "value": {"text": "line at 4", "lang": "en"},
                    "occurrence": 1,
                    "evidence": ["printed_text"],
                    "valid": None,
                }
            ],
            "events": [],
        }

        def reply(request):
            return httpx2.Response(
                200,
                json={
                    "model": "m/l",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": orjson.dumps(answer).decode()},
                        }
                    ],
                },
            )

        os.environ.setdefault("ROUTE_LOCAL_BASE_URL", "http://local.test/v1")
        await work_once(self.Session, ["extract"], httpx2.MockTransport(reply))
        async with self.Session() as s:
            [extract] = (
                await s.execute(select(Run).where(Run.tool == "extract"))
            ).scalars()
            self.assertEqual(extract.status, "done", extract.error)
            st, value = (
                await s.execute(
                    select(Statement, Payload.value)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .where(Statement.prop == Prop.NAME)
                )
            ).one()
        self.assertEqual(st.evidence, ["spoken"])
        self.assertTrue(value["heard"])

    async def test_long_recording_continues_in_windows_without_cutting_lines(self):
        self.enterContext(mock.patch.object(Config, "SPEECH_CHUNK_SECONDS", 10))
        self.enterContext(mock.patch.object(speech, "TAIL_SECONDS", 3))
        out = await self.ingest(tone(self.tmp / "a.ogg", 25), mime="audio/ogg")
        await self.drain()
        runs = await self.runs()
        self.assertGreater(
            len(runs), 2, [(r.inputs.get("cursor"), r.status, r.error) for r in runs]
        )
        self.assertTrue(all(r.status == "done" for r in runs), [r.error for r in runs])
        self.assertEqual(sum(c[0] == "detect" for c in self.model.calls), 1)
        async with self.Session() as s:
            spans = []
            for sha in runs[-1].inputs["parts"] + [runs[-1].raw_output_ref]:
                part = orjson.loads(blobs.read(await s.get(Blob, sha), Config.BLOB_DIR))
                spans += [tuple(x["ms"]) for x in part["segments"]]
            spans = sorted(set(spans))
            # Lines never overlap: each window starts where the last kept line ended.
            self.assertTrue(
                all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
            )
            self.assertLessEqual(spans[-1][1], 25000)
            [extract] = (
                await s.execute(select(Run).where(Run.tool == "extract"))
            ).scalars()
            text = blobs.read(
                await s.get(Blob, extract.inputs["blobs"][0]), Config.BLOB_DIR
            ).decode()
            self.assertEqual(len(text.splitlines()), len(spans))
            progress = await s.get(IndexProgress, (out["sha256"], "transcribe", ""))
            self.assertEqual(progress.completed, progress.total)
            self.assertEqual(await verify_db(s), [])

    async def test_a_run_out_of_time_continues_after_its_last_line(self):
        self.enterContext(mock.patch.object(speech, "LEASE_MARGIN", speech.queue.LEASE))
        await self.ingest(tone(self.tmp / "a.wav", 10))
        await work_once(self.Session, ["transcribe"])
        first, follow = await self.runs()
        self.assertEqual(first.status, "done", first.error)
        self.assertEqual(follow.inputs["cursor"], 3500)
        self.assertEqual(follow.inputs["language"]["language"], "fa")

    async def test_plain_failures_and_retry(self):
        self.load.side_effect = speech.SpeechError(
            "The speech model large-v3-turbo is not downloaded on this server."
        )
        await self.ingest(tone(self.tmp / "a.wav", 5))
        await self.drain()
        async with self.Session() as s:
            [item] = (await batch.status(s, "b1"))["items"]
        [failure] = item["failures"]
        self.assertEqual(item["stages"]["read"], "failed")
        self.assertEqual(
            failure["message"].split(".")[0], "Reading the speech did not finish"
        )
        self.assertEqual(
            failure["detail"],
            "The speech model large-v3-turbo is not downloaded on this server.",
        )
        self.load.side_effect = None
        async with self.Session() as s, s.begin():
            self.assertIsNotNone(await batch.retry(s, uuid.UUID(failure["run"])))
        await self.drain()
        [run] = await self.runs()
        self.assertEqual(run.status, "done", run.error)

    async def test_missing_ffmpeg_is_said_in_plain_words(self):
        await self.ingest(tone(self.tmp / "a.wav", 5))
        with mock.patch.object(speech.subprocess, "run", side_effect=FileNotFoundError):
            await self.drain()
        [run] = await self.runs()
        self.assertEqual(run.status, "failed")
        self.assertIn("ffprobe is not installed on the server", run.error)

    async def test_no_speech_and_no_audio_track_are_results(self):
        self.model.silent = True
        await self.ingest(tone(self.tmp / "a.wav", 5))
        await self.ingest(tone(self.tmp / "v.mp4", 3, video=True), mime="video/mp4")
        await self.drain()
        runs = await self.runs()
        self.assertEqual(
            [r.status for r in runs], ["done", "done"], [r.error for r in runs]
        )
        async with self.Session() as s:
            reasons = set(
                (
                    await s.execute(
                        select(RunEvent.drop_reason).where(
                            RunEvent.run_id.in_([r.id for r in runs])
                        )
                    )
                ).scalars()
            )
            self.assertFalse(
                (await s.execute(select(Run).where(Run.tool == "extract"))).first()
            )
        self.assertLessEqual({"no_speech", "no_audio_track"}, reasons)
        async with self.Session() as s:
            raw = orjson.loads(
                blobs.read(await s.get(Blob, runs[0].raw_output_ref), Config.BLOB_DIR)
            )
            [item] = [
                i
                for i in (await batch.status(s, "b1"))["items"]
                if i["mime"] == "audio/wav"
            ]
        self.assertEqual((raw["note"], raw["segments"]), ("no speech found", []))
        self.assertEqual((item["stages"]["read"], item["failures"]), ("done", []))

    async def test_transcription_off_and_queued_once(self):
        with mock.patch.object(Config, "SPEECH_MODEL", ""):
            off = await self.ingest(tone(self.tmp / "a.wav", 2))
        self.assertIn("SPEECH_MODEL", off["processing"]["reason"])
        async with self.Session() as s, s.begin():
            [acq] = (await s.execute(select(service.Acquisition))).scalars()
            queued = await service.queue_transcribe(s, acq)
            again = await service.queue_transcribe(s, acq)
        self.assertEqual(queued["queued"], "transcribe")
        self.assertEqual((await self.runs())[0].inputs["extract_route"], "local")
        self.assertIsNone(again["queued"])

    async def test_route_follows_the_policy_and_the_models_set_up(self):
        """Ordinary recordings go to the hosted transcriber, sensitive ones stay on
        the local model as sensitive scans do for OCR, and the local model is the
        fallback when no hosted one is set."""
        hosted = "elevenlabs/scribe-v2"
        cases = [
            (hosted, "large-v3-turbo", "ordinary", "cloud-zdr", hosted),
            (hosted, "large-v3-turbo", "sensitive", "local", "large-v3-turbo"),
            ("", "large-v3-turbo", "ordinary", "local", "large-v3-turbo"),
            # No local model: a sensitive recording may use the zero-retention
            # route, as the general sensitive row allows (local, cloud-zdr).
            (hosted, "", "sensitive", "cloud-zdr", hosted),
        ]
        for i, (remote, local, sensitivity, route, model) in enumerate(cases):
            with (
                mock.patch.object(Config, "TRANSCRIBE_MODEL", remote),
                mock.patch.object(Config, "SPEECH_MODEL", local),
            ):
                out = await self.ingest(
                    tone(self.tmp / f"r{i}.wav", 2 + i), sensitivity=sensitivity
                )
            self.assertEqual(out["processing"]["route"], route, (remote, sensitivity))
            async with self.Session() as s:
                run = await s.get(Run, uuid.UUID(out["processing"]["run"]))
            self.assertEqual(run.model_requested, model)

    def hosted(self, answer, seen=None, status=200):
        def reply(request):
            if seen is not None:
                seen.append(orjson.loads(request.content))
            return httpx2.Response(
                status, json=answer, headers={"x-generation-id": "gen-stt-1"}
            )

        self.enterContext(mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}))
        self.enterContext(
            mock.patch.object(Config, "TRANSCRIBE_MODEL", "elevenlabs/scribe-v2")
        )
        return httpx2.MockTransport(reply)

    async def test_hosted_transcriber_writes_located_lines(self):
        seen = []
        transport = self.hosted(
            {
                "text": "Jo, it's me. [water] Start without me",
                "language": "eng",
                "language_confidence": 0.99,
                "words": [
                    {"word": "Jo,", "start": 0.1, "end": 0.4, "confidence": 0.98},
                    {"word": "it's", "start": 0.5, "end": 0.8},
                    {"word": "me.", "start": 0.9, "end": 1.2},
                    {
                        "word": "[water]",
                        "start": 1.3,
                        "end": 2.0,
                        "type": "audio_event",
                    },
                    {"word": "Start", "start": 2.4, "end": 2.7},
                    {"word": "without", "start": 2.8, "end": 3.1},
                    {"word": "me", "start": 3.2, "end": 3.5},
                ],
                "usage": {"seconds": 4},
            },
            seen,
        )
        out = await self.ingest(tone(self.tmp / "a.wav", 4), sensitivity="ordinary")
        self.assertEqual(out["processing"]["route"], "cloud-zdr")
        while await work_once(self.Session, ["transcribe"], transport):
            pass
        [run] = await self.runs()
        self.assertEqual(run.status, "done", run.error)
        self.assertEqual(
            (run.model_served, run.provider_served, run.endpoint),
            (
                "elevenlabs/scribe-v2",
                "a zero-retention provider",
                "https://openrouter.ai/api/v1/audio/transcriptions",
            ),
        )
        [body] = seen
        self.assertEqual(body["provider"], {"zdr": True})
        self.assertEqual(body["timestamp_granularities"], ["word"])
        self.assertEqual(body["input_audio"]["format"], "flac")
        self.assertTrue(
            base64.b64decode(body["input_audio"]["data"]).startswith(b"fLaC")
        )
        async with self.Session() as s:
            raw = orjson.loads(
                blobs.read(await s.get(Blob, run.raw_output_ref), Config.BLOB_DIR)
            )
            [extract] = (
                await s.execute(select(Run).where(Run.tool == "extract"))
            ).scalars()
            transcript = blobs.read(
                await s.get(Blob, extract.inputs["blobs"][0]), Config.BLOB_DIR
            ).decode()
            listed = await outbound.listing(
                s,
                None,
                {"investigation": None, "kind": "model", "sensitive": False, "page": 1},
            )
        # A line ends at a sentence's end; the tagged sound is no word heard.
        self.assertEqual(
            [(x["ms"], x["text"]) for x in raw["segments"]],
            [([100, 1200], "Jo, it's me."), ([2400, 3500], "Start without me")],
        )
        self.assertEqual(raw["segments"][0]["words"][0]["p"], "0.980")
        self.assertEqual(raw["model"]["generation"], "gen-stt-1")
        self.assertEqual(raw["language"]["language"], "eng")
        self.assertEqual(transcript, "Jo, it's me.\nStart without me")
        [row] = [i for i in listed["items"] if i["id"] == f"model:{run.id}"]
        self.assertEqual(row["model"], "elevenlabs/scribe-v2")
        self.assertIn("openrouter.ai", row["host"])

    async def test_hosted_no_speech_is_a_result_and_a_failure_is_not_rerouted(self):
        transport = self.hosted(
            {
                "text": "[background noise]",
                "language": "eng",
                "language_confidence": 0.12,
                "words": [
                    {
                        "word": "[background noise]",
                        "start": 0,
                        "end": 3,
                        "type": "audio_event",
                    }
                ],
            }
        )
        await self.ingest(tone(self.tmp / "a.wav", 3), sensitivity="ordinary")
        while await work_once(self.Session, ["transcribe"], transport):
            pass
        [run] = await self.runs()
        self.assertEqual(run.status, "done", run.error)
        async with self.Session() as s:
            raw = orjson.loads(
                blobs.read(await s.get(Blob, run.raw_output_ref), Config.BLOB_DIR)
            )
            self.assertFalse(
                (await s.execute(select(Run).where(Run.tool == "extract"))).first()
            )
            # The Read tab says what was read, not "nothing was read".
            said = await evidence.speech_read(s, run.inputs["blobs"][0])
            [item] = (await batch.status(s, "b1"))["items"]
        self.assertEqual((raw["note"], raw["segments"]), ("no speech found", []))
        self.assertTrue(raw["language"]["uncertain"])
        self.assertEqual(
            said,
            {"state": "none", "model": "elevenlabs/scribe-v2", "route": "cloud-zdr"},
        )
        read = next(s for s in item["steps"] if s["key"] == "read")
        self.assertTrue(read["words"].endswith("No speech found."), read)
        down = self.hosted({"error": {"message": "overloaded"}}, status=503)
        await self.ingest(tone(self.tmp / "b.wav", 2), sensitivity="ordinary")
        while await work_once(self.Session, ["transcribe"], down):
            pass
        failed = (await self.runs())[-1]
        self.assertEqual((failed.status, failed.route), ("failed", "cloud-zdr"))
        self.assertIn("503", failed.error)
        self.assertEqual(len(await self.runs()), 2)

    async def test_local_noise_lines_are_dropped_and_kept_for_the_record(self):
        self.model.noise = True
        await self.ingest(tone(self.tmp / "a.wav", 5))
        await self.drain()
        [run] = await self.runs()
        self.assertEqual(run.status, "done", run.error)
        async with self.Session() as s:
            raw = orjson.loads(
                blobs.read(await s.get(Blob, run.raw_output_ref), Config.BLOB_DIR)
            )
        self.assertEqual(raw["note"], "no speech found")
        self.assertEqual(raw["segments"], [])
        self.assertEqual(len(raw["not_speech"]), 2)


# The script the synthetic clip was rendered from, with each sentence's ms range.
WITNESS = orjson.loads((ROOT / "data" / "expected.json").read_bytes())[
    "witness_clip.ogg"
]["sentences"]


def words(text):
    """Arabic folded for word error: no diacritics or punctuation, one alef, ya and
    ha for the letters spelling varies on."""
    text = re.sub(r"[ً-ْـ]", "", text)
    text = re.sub("[أإآ]", "ا", text).replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"[^\w\s]", " ", text).split()


def wer(reference, hypothesis):
    r, h = words(reference), words(hypothesis)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[-1] / len(r)


class CanonicalTest(unittest.TestCase):
    def test_transcription_output_survives_ledger_canonicalisation(self):
        from sealbook.ledger import chain

        model = FakeModel()
        segments, done = speech.recognise(
            model, np.zeros(9 * speech.RATE, np.float32), "ar", 1000, 1e12
        )
        lang = speech.language(model, None)
        inputs = {"cursor": segments[-1]["ms"][1], "language": lang}
        # Numpy scalars from the model come out as int ms and decimal strings.
        self.assertEqual(
            segments[0]["words"][0], {"w": "w0", "ms": [1000, 4500], "p": "0.900"}
        )
        self.assertEqual(segments[0]["avg_logprob"], "-0.200")
        chain.canon([segments, inputs])  # raises on any float
        self.assertTrue(done)


class NotSpeechTest(unittest.TestCase):
    def test_only_a_segment_low_on_both_scores_is_noise(self):
        def seg(logprob, *p):
            words = [SimpleNamespace(probability=np.float64(x)) for x in p]
            return SimpleNamespace(avg_logprob=np.float64(logprob), words=words)

        # As measured on copy_trim's "Thank you." over running water.
        self.assertTrue(speech.not_speech(seg(-0.81, 0.6, 0.5)))
        # Quiet speech: unsure words in a confident window, or the reverse.
        self.assertFalse(speech.not_speech(seg(-0.2, 0.4, 0.5)))
        self.assertFalse(speech.not_speech(seg(-0.7, 0.9, 0.8)))
        self.assertFalse(speech.not_speech(seg(-0.9)))


class WerTest(unittest.TestCase):
    def test_folded_word_error(self):
        self.assertEqual(wer("أنا من دوما.", "انا من دوما"), 0)
        self.assertAlmostEqual(wer("أنا من دوما", "انا في دوما"), 1 / 3)


@unittest.skipUnless(os.environ.get("STK_SPEECH_LIVE") == "1", "set STK_SPEECH_LIVE=1")
class LiveSpeechTest(unittest.TestCase):
    def test_witness_clip(self):
        clip = ROOT / "data" / "witness_clip.ogg"
        model, provenance = speech.load_model()
        duration, has_audio = speech.probe(clip)
        audio = speech.decode(clip, 0, duration)
        lang = speech.language(model, audio)
        segments, complete = speech.recognise(model, audio, lang["language"], 0, 1e12)
        hypothesis = " ".join(s["text"] for s in segments)
        self.assertEqual(lang["language"], "ar")
        self.assertTrue(complete)
        script = " ".join(x["text"] for x in WITNESS)
        self.assertLess(wer(script, hypothesis), 0.35, hypothesis)
        # The sentence naming Yusuf is heard where the script places it.
        fact = next(s for s in segments if "يوسف" in s["text"])
        self.assertLess(abs(fact["ms"][0] - WITNESS[2]["start_ms"]), 1500)
        self.assertTrue(provenance["revision"])


class TimelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_offsets_map_to_segment_ms_across_runs(self):
        from sealbook.discovery import evidence

        runs = {
            "b": {"segments": [{"ms": [5000, 7000], "text": "world"}]},
            "a": {"segments": [{"ms": [0, 2000], "text": "hello"}]},
        }
        db = mock.AsyncMock()
        db.scalars.return_value = ["b", "a"]
        db.get.side_effect = lambda _model, ref: ref
        with mock.patch.object(
            evidence.blobs,
            "read",
            side_effect=lambda ref, _dir: orjson.dumps(runs[ref]),
        ):
            timeline = await evidence.transcript_timeline(db, "sha")
        # "hello\nworld": the second line starts at character 6
        self.assertEqual(timeline, [[0, 0, 2000], [6, 5000, 7000]])


if __name__ == "__main__":
    unittest.main()
