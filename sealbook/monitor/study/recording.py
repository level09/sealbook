"""Model answers recorded once from a real run and replayed at seed time.

A case whose sources are marked `read` keeps `recordings.json` beside its
case.json: every chat request the worker's own tools sent (OCR, extraction, the
first pass) with the response the route returned, and the speech model's
segments for each recording. Replay answers the same request with the same
response through the route code's HTTP transport, so the tools, the route checks
and the provenance they write are the real ones; only the network is replaced.
A request with no recording (the prompt, the input or the model changed) fails
the run, and seeding stops with the reason. The file is written by
`stk demo-seed --case <slug> --record` on a throwaway database (make.py --record
does that) and is never edited by hand.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import httpx2

from sealbook.ledger import chain
from sealbook.settings import Config

FILE = "recordings.json"
ROUTE = "cloud-zdr"


def key(body: dict) -> str:
    """What makes two requests the same question: model, messages, schema."""
    return chain.sha256(
        chain.canon(
            {
                "model": body.get("model"),
                "messages": body.get("messages"),
                "response_format": body.get("response_format"),
            }
        )
    )


class Missing(Exception):
    pass


class Tape:
    """One case's recordings: `calls` (chat answers) and `speech` (segments)."""

    def __init__(self, path: Path, data: dict, recording: bool):
        self.path, self.data, self.recording = path, data, recording
        self.calls = {c["request"]: c for c in data.get("calls", [])}
        self.speech = {s["input_sha256"]: s for s in data.get("speech", [])}
        self.used = 0
        self.seen: set[str] = set()  # requests asked in this run

    @classmethod
    def open(cls, root: Path, recording: bool) -> Tape:
        path = root / FILE
        if recording:
            models = {
                "extract": Config.RUN_MODELS.get(ROUTE),
                "ocr": Config.OCR_MODEL,
                "assess": Config.ASSESS_MODEL,
                "speech": Config.SPEECH_MODEL,
            }
            if not all(models.values()):
                raise Missing(f"recording needs a model for each tool on {ROUTE}")
            # An interrupted recording resumes: answers already recorded for the
            # same models are kept, and only those asked again are saved.
            old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            keep = old if old.get("models") == models else {}
            return cls(
                path,
                {"route": ROUTE, "models": models}
                | {k: keep[k] for k in ("calls", "speech") if k in keep},
                True,
            )
        if not path.exists():
            raise Missing(
                f"{path} is missing: the case's sources are read by the app, and "
                "seeding replays recorded answers. Record them with make.py --record."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        for c in data.get("calls", []):
            if (
                hashlib.sha256(c["response"].encode()).hexdigest()
                != c["response_sha256"]
            ):
                raise Missing(
                    f"{path}: the answer to {c['tool']} on {c['source']} no longer "
                    "matches its recording; record again instead of editing it"
                )
        return cls(path, data, False)

    @contextlib.contextmanager
    def pinned(self):
        """Config as it was recorded: one route, the recorded model per tool, so
        intake queues the same runs on any machine. Replay needs no route key."""
        m = self.data["models"]
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(Config, "RUN_MODELS", {ROUTE: m["extract"]})
            )
            stack.enter_context(mock.patch.object(Config, "OCR_MODEL", m["ocr"]))
            stack.enter_context(mock.patch.object(Config, "ASSESS_MODEL", m["assess"]))
            stack.enter_context(mock.patch.object(Config, "SPEECH_MODEL", m["speech"]))
            if not self.recording and not os.environ.get("OPENROUTER_API_KEY"):
                # The route code names its key before it sends; replay sends nothing.
                stack.enter_context(
                    mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "replay"})
                )
            yield

    def transport(self, label: dict) -> httpx2.AsyncBaseTransport:
        return _Recorder(self, label) if self.recording else _Replayer(self)

    def note(self, run) -> str:
        if self.recording:
            return ""
        return (
            f"demo: answer recorded once from {run.model_requested} on the "
            f"{run.route} route ({self.data.get('recorded_at', 'unknown date')}) and "
            "replayed by stk demo-seed; the model did not run at seed time"
        )

    def save(self) -> None:
        self.data["recorded_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.data["recorded_by"] = "stk demo-seed --record"
        self.data["calls"] = sorted(
            (c for c in self.calls.values() if c["request"] in self.seen),
            key=lambda c: (c["source"], c["tool"], c["request"]),
        )
        self.data["speech"] = sorted(self.speech.values(), key=lambda s: s["source"])
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )


class _Replayer(httpx2.AsyncBaseTransport):
    def __init__(self, tape: Tape):
        self.tape = tape

    async def handle_async_request(self, request):
        body = json.loads(request.content)
        found = self.tape.calls.get(key(body))
        if found is None:
            raise Missing(
                "no recorded answer for this request: the prompt, the input or the "
                "model changed since it was recorded. Record again with make.py "
                "--record."
            )
        self.tape.used += 1
        return httpx2.Response(
            found["status"],
            content=found["response"].encode(),
            headers={"content-type": "application/json"},
        )


class _Recorder(httpx2.AsyncBaseTransport):
    def __init__(self, tape: Tape, label: dict):
        self.tape, self.label = tape, label
        self.inner = httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        body = json.loads(request.content)
        self.tape.seen.add(key(body))
        if (known := self.tape.calls.get(key(body))) and known["status"] == 200:
            return httpx2.Response(
                200,
                content=known["response"].encode(),
                headers={"content-type": "application/json"},
            )
        resp = await self.inner.handle_async_request(request)
        raw = await resp.aread()
        self.tape.calls[key(body)] = self.label | {
            "request": key(body),
            "model_requested": body.get("model"),
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "status": resp.status_code,
            "response": raw.decode(),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
        }
        # The body is already decoded: hand it back without its transfer encoding.
        return httpx2.Response(
            resp.status_code,
            content=raw,
            headers={"content-type": "application/json"},
        )
