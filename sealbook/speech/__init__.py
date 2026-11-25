"""Speech to text: a hosted transcriber on OpenRouter for ordinary recordings, or
faster-whisper on the worker, where no audio leaves the machine (the policy row
picks the route, sealbook.ingest.service.speech_route).

A transcribe run decodes one window of the recording, keeps the speech segments
that start inside it and queues a continuation from where the last kept segment
ends, so words are not cut at window edges and every run stays inside its lease.
Each segment becomes a derived text blob located by {"ms": [start, end]}, the same
shape page text uses, so search, the evidence page and embeddings read it unchanged.
The last run joins the segments into one transcript blob for claim extraction."""

from __future__ import annotations

import asyncio
import base64
import functools
import json
import math
import re
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import orjson

from sealbook import blobs
from sealbook.ledger import chain, queue, write
from sealbook.ledger.contracts import Prop, Route, StatementIn
from sealbook.ledger.models import Blob, Run
from sealbook.runs import routes
from sealbook.settings import Config

RATE = 16000
# Whisper reads 30 s at a time; decoding past the window lets the segment that
# straddles its end finish instead of being cut mid-word.
TAIL_SECONDS = 30
# Below this detection confidence the language is recorded as uncertain. The
# detected language is still the one read: no language is preferred.
LANGUAGE_CONFIDENCE = 0.5
# Whisper writes subtitle boilerplate ("Thank you.", "subscribe to the channel")
# over water, wind and rain. A segment the model itself holds with little
# confidence on both of its scores is dropped. Measured on 2026-11-22 (docs/
# decisions.md): spoken segments in English and Arabic scored avg_logprob -0.10
# to -0.19 and a mean word probability of 0.83 or more; every invented one -0.63
# or lower and 0.58 or lower. no_speech_prob was 0.000 on every segment, spoken or
# not, so it decides nothing here. A forced language defeats this: with Arabic
# forced the same water came back as a confident Arabic line, which is why the
# detected language is always the one read.
LOW_LOGPROB = -0.45
LOW_WORD_P = 0.7
# The hosted transcriber is asked for word times; lines are cut from them.
HOSTED = {
    "response_format": "verbose_json",
    "timestamp_granularities": ["word"],
    "temperature": 0,
}
SENTENCE_END = re.compile(r"[.!?؟।。！？…]$")
PAUSE_MS = 1000
LINE_MS = 30000
# Stop taking segments this long before the lease would expire.
LEASE_MARGIN = timedelta(seconds=60)
DECODE = {
    "beam_size": 5,
    "vad_filter": True,
    "word_timestamps": True,
    # Conditioning on earlier text is what lets Whisper loop on a phrase.
    "condition_on_previous_text": False,
}


class SpeechError(RuntimeError):
    """A transcription failure worded for the analyst who sees it on the intake page."""


@functools.cache
def load_model():
    """The model and what identifies it, loaded once per worker process."""
    import ctranslate2
    from faster_whisper import WhisperModel, __version__
    from faster_whisper.utils import download_model

    try:
        path = Path(
            download_model(
                Config.SPEECH_MODEL,
                cache_dir=Config.SPEECH_MODEL_DIR,
                local_files_only=True,
            )
        )
    except Exception as exc:
        raise SpeechError(
            f"The speech model {Config.SPEECH_MODEL} is not downloaded on this "
            "server. An administrator runs `stk speech-model` once, then Retry."
        ) from exc
    cuda = ctranslate2.get_cuda_device_count() > 0
    device = Config.SPEECH_DEVICE
    if device == "auto":
        device = "cuda" if cuda else "cpu"
    compute = Config.SPEECH_COMPUTE_TYPE
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    model = WhisperModel(str(path), device=device, compute_type=compute)
    weights = (path / "model.bin").resolve()
    return model, {
        "model": Config.SPEECH_MODEL,
        "repo": path.parent.parent.name.removeprefix("models--").replace("--", "/"),
        # The Hugging Face cache names a snapshot by commit and an LFS blob by sha256.
        "revision": path.name,
        "weights_sha256": weights.name,
        "runtime": f"faster-whisper {__version__}, ctranslate2 {ctranslate2.__version__}",
        "device": device,
        "compute_type": compute,
    }


def download() -> dict:
    """Fetch the weights into SPEECH_MODEL_DIR (the only network use) and load them."""
    from faster_whisper.utils import download_model

    download_model(Config.SPEECH_MODEL, cache_dir=Config.SPEECH_MODEL_DIR)
    load_model.cache_clear()
    return load_model()[1]


def _ffmpeg(command, timeout):
    try:
        return subprocess.run(command, check=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SpeechError(
            f"{command[0]} is not installed on the server, so the recording could "
            "not be decoded."
        ) from exc
    except subprocess.CalledProcessError as exc:
        reason = exc.stderr.decode(errors="replace").strip().splitlines()[-1:]
        raise SpeechError(
            f"The recording could not be decoded: {''.join(reason)[:200]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SpeechError(
            f"Decoding the recording took longer than {timeout} seconds."
        ) from exc


def probe(path) -> tuple[int, bool]:
    """Duration in ms and whether there is an audio stream."""
    out = _ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type"]
        + ["-of", "json", str(path)],
        timeout=30,
    ).stdout
    meta = json.loads(out)
    seconds = float(meta["format"].get("duration") or "nan")
    if not math.isfinite(seconds) or seconds <= 0:
        raise SpeechError("The recording has no readable duration.")
    audio = any(s["codec_type"] == "audio" for s in meta.get("streams", []))
    return round(seconds * 1000), audio


def decode(path, start_ms, length_ms) -> np.ndarray:
    """Mono 16 kHz float samples of one window. The timeout scales with its length."""
    pcm = _ffmpeg(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start_ms / 1000:.3f}"]
        + ["-t", f"{length_ms / 1000:.3f}", "-i", str(path), "-map", "0:a:0"]
        + ["-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1"],
        timeout=60 + length_ms // 1000,
    ).stdout
    return np.frombuffer(pcm, np.int16).astype(np.float32) / 32768


def language(model, audio) -> dict:
    detected, probability, _ = model.detect_language(audio, vad_filter=True)
    # Strings: run inputs carry it to continuations and canonical JSON has no floats.
    return {
        "language": detected,
        "detected": detected,
        "probability": f"{probability:.3f}",
        "uncertain": bool(probability < LANGUAGE_CONFIDENCE),
    }


def not_speech(seg) -> bool:
    """The model's own verdict that a segment is noise it put words to."""
    p = [w.probability for w in seg.words or []]
    return seg.avg_logprob < LOW_LOGPROB and bool(p) and sum(p) / len(p) < LOW_WORD_P


def recognise(model, audio, lang, offset_ms, deadline) -> tuple[list[dict], bool]:
    """Segments on the source timeline, and whether the window was read to its end.
    Segments are a generator, so stopping at the deadline loses at most one window.
    A segment the model scores as noise is kept marked `not_speech`, for the record."""

    def ms(seconds):
        return offset_ms + int(round(seconds * 1000))

    def p(value):
        # faster-whisper hands back numpy floats; ledger canonical JSON refuses floats.
        return f"{float(value):.3f}"

    found, _ = model.transcribe(audio, language=lang, **DECODE)
    segments = []
    for seg in found:
        text = seg.text.strip()
        if text:
            segments.append(
                ({"not_speech": True} if not_speech(seg) else {})
                | {
                    "ms": [ms(seg.start), ms(seg.end)],
                    "text": text,
                    "avg_logprob": p(seg.avg_logprob),
                    "no_speech_prob": p(seg.no_speech_prob),
                    "words": [
                        {
                            "w": w.word.strip(),
                            "ms": [ms(w.start), ms(w.end)],
                            "p": p(w.probability),
                        }
                        for w in seg.words or []
                    ],
                }
            )
        if time.monotonic() > deadline:
            return segments, False
    return segments, True


async def transcribe(c) -> str:
    from sealbook.discovery import service as discovery
    from sealbook.discovery.indexing import progress

    blob = await discovery._input(c)
    if not blob.mime.startswith(("audio/", "video/")):
        raise ValueError(f"transcribe needs audio or video, got {blob.mime}")
    cursor = c.run.inputs.get("cursor", 0)
    if type(cursor) is not int or cursor < 0:
        raise ValueError("invalid transcription cursor")
    path = await asyncio.to_thread(blobs.verified_path, blob, Config.BLOB_DIR)
    duration, has_audio = await asyncio.to_thread(probe, path)
    if not has_audio:
        await c.emit("input_read", drop_reason="no_audio_track", duration_ms=duration)
        await _watch(c, blob)
        return chain.sha256(chain.canon({"blob": blob.sha256, "audio": False}))
    window_end = min(duration, cursor + Config.SPEECH_CHUNK_SECONDS * 1000)
    decoded_end = min(duration, window_end + TAIL_SECONDS * 1000)
    # The worker's clock, not the queue's: a slow machine reads less per run.
    budget = queue.LEASE - LEASE_MARGIN
    started = time.monotonic()
    if c.run.route == Route.LOCAL:
        segments, complete, lang, provenance, settings = await _local(
            c, path, cursor, decoded_end, duration, started + budget.total_seconds()
        )
    else:
        segments, lang, provenance, settings = await _hosted(
            c, path, cursor, decoded_end, duration
        )
        complete = True
    noise = [s for s in segments if s.get("not_speech")]
    segments = [s for s in segments if not s.get("not_speech")]
    elapsed_ms = int((time.monotonic() - started) * 1000)
    ends = [s["ms"][1] for s in segments]
    if not complete:
        # Out of time: keep what was read and continue after it.
        kept, reached = segments, max(ends, default=cursor)
        if reached <= cursor:
            raise SpeechError(
                "Transcription is too slow on this server: no speech segment was "
                f"finished within {int(budget.total_seconds())} seconds."
            )
    elif decoded_end == duration:
        kept, reached = segments, duration
    else:
        # The tail belongs to the next run, except a segment that started before it.
        kept = [s for s in segments if s["ms"][0] < window_end]
        reached = max([window_end, *(s["ms"][1] for s in kept)])
    for seg in kept:
        child = await blobs.put(
            c.s, seg["text"].encode(), "text/plain; charset=utf-8", Config.BLOB_DIR
        )
        await write.derived_from(c, child, blob)
        await discovery.queue(
            c.s,
            c.acq,
            child.mime,
            blob_sha256=child.sha256,
            location={"ms": seg["ms"]},
            ordinal=seg["ms"][0],
        )
    parts = c.run.inputs.get("parts", [])
    lines = None
    if reached >= duration:
        lines = [*await _lines(c, parts), *(s["text"] for s in kept)]
    artifact = await blobs.put(
        c.s,
        orjson.dumps(
            {
                # A result, not a failure: Retry would find no speech either.
                "note": "no speech found" if lines == [] else None,
                "source": blob.sha256,
                "read": [cursor, reached],
                "duration_ms": duration,
                "language": lang,
                "model": provenance,
                "decode": settings,
                "elapsed_ms": elapsed_ms,
                "segments": kept,
                # Dropped as noise the model put words to (not_speech).
                "not_speech": noise,
            }
        ),
        "application/json",
        Config.BLOB_DIR,
    )
    c.run.raw_output_ref = artifact.sha256
    parts = [*parts, artifact.sha256]
    await progress(c, reached, duration, "ms")
    await c.emit(
        "transcribed",
        segments=len(kept),
        not_speech=len(noise),
        completed_ms=reached,
        total_ms=duration,
        elapsed_ms=elapsed_ms,
        language=lang["language"],
    )
    if reached < duration:
        c.s.add(
            Run(
                principal_id=c.run.principal_id,
                tool=c.run.tool,
                route=c.run.route,
                model_requested=c.run.model_requested,
                inputs={
                    **c.run.inputs,
                    "cursor": reached,
                    "language": lang,
                    "parts": parts,
                    "continued_from": str(c.run.id),
                },
            )
        )
        return artifact.sha256
    await _finish(c, blob, lines)
    return artifact.sha256


async def _local(c, path, cursor, decoded_end, duration, deadline):
    model, provenance = await asyncio.to_thread(load_model)
    audio = await asyncio.to_thread(decode, path, cursor, decoded_end - cursor)
    await c.emit("input_read", ms=[cursor, decoded_end], duration_ms=duration)
    lang = c.run.inputs.get("language") or await asyncio.to_thread(
        language, model, audio
    )
    c.run.model_served = f"{provenance['repo']}@{provenance['revision']}"
    c.run.provider_served = provenance["runtime"]
    c.run.endpoint = f"local:{provenance['device']}:{provenance['compute_type']}"
    c.run.prompt_hash = chain.sha256(chain.canon(DECODE))
    segments, complete = await asyncio.to_thread(
        recognise, model, audio, lang["language"], cursor, deadline
    )
    return segments, complete, lang, provenance, DECODE


async def _hosted(c, path, cursor, decoded_end, duration):
    """One window sent to the hosted transcriber as lossless 16 kHz FLAC. The
    language is the model's to detect in each window."""
    flac = await asyncio.to_thread(encode, path, cursor, decoded_end - cursor)
    await c.emit("input_read", ms=[cursor, decoded_end], duration_ms=duration)
    c.run.prompt_hash = chain.sha256(chain.canon(HOSTED))
    served = await routes.transcription(
        Route(c.run.route),
        {
            "model": c.run.model_requested,
            "input_audio": {"data": base64.b64encode(flac).decode(), "format": "flac"},
            **HOSTED,
        },
        c.transport,
    )
    c.run.endpoint = served.endpoint
    c.run.model_served = served.model
    c.run.provider_served = served.provider
    raw = await blobs.put(c.s, served.raw, "application/json", Config.BLOB_DIR)
    data = served.data
    if "words" not in data:
        raise routes.RouteError(
            f"{served.model} returned no word times, so its lines cannot be located"
        )
    p = data.get("language_confidence")
    lang = {
        "language": data.get("language") or "",
        "detected": data.get("language") or "",
        "probability": f"{p:.3f}" if p is not None else "",
        "uncertain": p is None or p < LANGUAGE_CONFIDENCE,
    }
    provenance = {
        "model": served.model,
        "route": c.run.route,
        "endpoint": served.endpoint,
        # OpenRouter's id for the call: its generation record names the served
        # model version and provider.
        "generation": data.get("generation"),
        "response": raw.sha256,
    }
    await c.emit("model_called", seconds=(data.get("usage") or {}).get("seconds"))
    return lines_of(data, cursor), lang, provenance, HOSTED


def encode(path, start_ms, length_ms) -> bytes:
    """One window as mono 16 kHz FLAC, bit-exact so the same window is the same
    bytes on every run of one ffmpeg."""
    return _ffmpeg(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start_ms / 1000:.3f}"]
        + ["-t", f"{length_ms / 1000:.3f}", "-i", str(path), "-map", "0:a:0"]
        + ["-ac", "1", "-ar", str(RATE), "-c:a", "flac", "-fflags", "+bitexact"]
        + ["-flags:a", "+bitexact", "-f", "flac", "pipe:1"],
        timeout=60 + length_ms // 1000,
    ).stdout


def lines_of(data: dict, offset_ms: int) -> list[dict]:
    """A hosted answer's spoken words as lines on the source timeline: a line ends
    at a sentence's end, a pause of PAUSE_MS or LINE_MS. Each line's text is cut
    from the answer's own text, so spacing in every script is the model's. Tagged
    sounds ("[background noise]") are not words heard and start no line."""
    text = data.get("text") or ""
    lines, line, at = [], [], 0

    def close():
        if line:
            start, end = line[0]["at"], line[-1]["at"] + len(line[-1]["w"])
            lines.append(
                {
                    "ms": [line[0]["ms"][0], line[-1]["ms"][1]],
                    "text": text[start:end].strip(),
                    "words": [{k: v for k, v in w.items() if k != "at"} for w in line],
                }
            )
            line.clear()

    for w in data["words"]:
        word = w["word"].strip()
        if w.get("type", "word") != "word" or not word:
            continue
        found = text.find(word, at)
        if found < 0:
            raise routes.RouteError("the transcriber's words are not in its text")
        ms = [offset_ms + round(w["start"] * 1000), offset_ms + round(w["end"] * 1000)]
        if line and (
            ms[0] - line[-1]["ms"][1] >= PAUSE_MS or ms[1] - line[0]["ms"][0] > LINE_MS
        ):
            close()
        line.append(
            {"w": word, "ms": ms, "at": found}
            # A decimal string: canonical JSON refuses floats.
            | ({"p": f"{w['confidence']:.3f}"} if "confidence" in w else {})
        )
        at = found + len(word)
        if SENTENCE_END.search(word):
            close()
    close()
    return lines


async def _lines(c, parts):
    lines = []
    for sha in parts:
        part = await c.s.get(Blob, sha)
        lines += [
            s["text"]
            for s in orjson.loads(blobs.read(part, Config.BLOB_DIR))["segments"]
        ]
    return lines


async def _finish(c, source, lines):
    """Join every run's segments into the transcript claim extraction reads."""
    if not lines:
        await c.emit("statements_written", drop_reason="no_speech", written=0)
        await _watch(c, source)
        return
    text = "\n".join(lines)
    transcript = await blobs.put(
        c.s, text.encode(), "text/plain; charset=utf-8", Config.BLOB_DIR
    )
    await write.derived_from(c, transcript, source)
    await c.emit("statements_written", written=1, chars=len(text), lines=len(lines))
    if c.run.inputs.get("extract_model"):
        c.s.add(
            Run(
                principal_id=c.run.principal_id,
                tool="extract",
                route=c.run.inputs["extract_route"],
                model_requested=c.run.inputs["extract_model"],
                inputs={"blobs": [transcript.sha256], "acquisition": str(c.acq.id)},
            )
        )
        await c.s.flush()
        await c.emit("extract_queued")
    await _watch(c, source, transcript.sha256)


async def _watch(c, source, transcript: str | None = None):
    """A video's first pass reads its frames with the transcript, once speech is
    read, so it waits for this run."""
    from sealbook.ingest.service import queue_read

    if source.mime.startswith("video/") and c.acq:
        extra = {"transcript": transcript} if transcript else {}
        await queue_read(c.s, c.acq, "assess", source.sha256, **extra)


def init_app(app):
    from sealbook.speech.cli import speech_model

    app.cli.add_command(speech_model)
