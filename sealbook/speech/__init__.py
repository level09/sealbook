"""Local speech to text: faster-whisper on the worker, no audio leaves the machine.

A transcribe run decodes one window of the recording, keeps the speech segments
that start inside it and queues a continuation from where the last kept segment
ends, so words are not cut at window edges and every run stays inside its lease.
Each segment becomes a derived text blob located by {"ms": [start, end]}, the same
shape page text uses, so search, the evidence page and embeddings read it unchanged.
The last run joins the segments into one transcript blob for claim extraction."""

from __future__ import annotations

import asyncio
import functools
import json
import math
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import orjson

from sealbook import blobs
from sealbook.ledger import chain, queue, write
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Blob, Run
from sealbook.settings import Config

RATE = 16000
# Whisper reads 30 s at a time; decoding past the window lets the segment that
# straddles its end finish instead of being cut mid-word.
TAIL_SECONDS = 30
# Below this detection confidence the recording is read as Arabic, the language
# most of this evidence is in; the detected guess is still recorded.
LANGUAGE_CONFIDENCE = 0.5
PREFERRED_LANGUAGE = "ar"
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
    use = detected if probability >= LANGUAGE_CONFIDENCE else PREFERRED_LANGUAGE
    # A string: run inputs carry it to continuations and canonical JSON has no floats.
    return {"language": use, "detected": detected, "probability": f"{probability:.3f}"}


def recognise(model, audio, lang, offset_ms, deadline) -> tuple[list[dict], bool]:
    """Segments on the source timeline, and whether the window was read to its end.
    Segments are a generator, so stopping at the deadline loses at most one window."""

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
                {
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
    model, provenance = await asyncio.to_thread(load_model)
    window_end = min(duration, cursor + Config.SPEECH_CHUNK_SECONDS * 1000)
    decoded_end = min(duration, window_end + TAIL_SECONDS * 1000)
    audio = await asyncio.to_thread(decode, path, cursor, decoded_end - cursor)
    await c.emit("input_read", ms=[cursor, decoded_end], duration_ms=duration)
    lang = c.run.inputs.get("language") or await asyncio.to_thread(
        language, model, audio
    )
    c.run.model_served = f"{provenance['repo']}@{provenance['revision']}"
    c.run.provider_served = provenance["runtime"]
    c.run.endpoint = f"local:{provenance['device']}:{provenance['compute_type']}"
    c.run.prompt_hash = chain.sha256(chain.canon(DECODE))
    # The worker's clock, not the queue's: a slow machine reads less per run.
    budget = queue.LEASE - LEASE_MARGIN
    started = time.monotonic()
    segments, complete = await asyncio.to_thread(
        recognise,
        model,
        audio,
        lang["language"],
        cursor,
        started + budget.total_seconds(),
    )
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
        await _derived(c, child, blob)
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
                "decode": DECODE,
                "elapsed_ms": elapsed_ms,
                "segments": kept,
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


async def _derived(c, child, source):
    await write.append(
        c.s,
        StatementIn(
            subject=f"blob:{child.sha256}",
            prop=Prop.DERIVED_FROM,
            value={"blob": source.sha256},
            author_id=c.run.principal_id,
            run_id=c.run.id,
        ),
        sign=c.sign,
    )


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
    await _derived(c, transcript, source)
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
