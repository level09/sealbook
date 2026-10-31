"""Run worker: claim a run, check its route against policy, dispatch by tool, seal.
Every stage is an event; a failure fails the run and stays visible."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import uuid
from calendar import monthrange
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

import httpx
import orjson
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sealbook import blobs, speech
from sealbook.agents import read, tasks
from sealbook.agents.approximate import approximate
from sealbook.agents.brief import brief
from sealbook.agents.contradictions import contradictions
from sealbook.agents.gaps import gaps
from sealbook.agents.linker import link
from sealbook.agents.noticer import notice
from sealbook.agents.standing import standing
from sealbook.collaboration import intake
from sealbook.discovery import findings, indexing
from sealbook.discovery import service as discovery
from sealbook.ingest.service import queue_read
from sealbook.ledger import chain, queue, write
from sealbook.ledger.contracts import (
    EXTRACTED_PROPS,
    REPORT_KINDS,
    Event,
    EvidenceKind,
    Prop,
    Refused,
    Route,
    StatementIn,
)
from sealbook.ledger.gate import resolve
from sealbook.ledger.models import Acquisition, Blob, Run, RunEvent
from sealbook.maps.geotag import geotag
from sealbook.method import assess as method
from sealbook.research.capture import capture
from sealbook.runs import routes
from sealbook.settings import Config
from sealbook.sheets.extract import extract_sheet
from sealbook.thumbs.tool import thumbnail
from sealbook.watch import matcher

log = logging.getLogger(__name__)
POLL_SECONDS = 2
DOCUMENT_PROPS = {Prop.DOCUMENT_TYPE, Prop.DOCUMENT_DATE, Prop.REPORTS}


class Events:
    """Emits run events in their own transactions so they survive a failed run."""

    def __init__(self, Session: async_sessionmaker, run_id: uuid.UUID, seq: int):  # noqa: N803
        self.Session, self.run_id, self.seq = Session, run_id, seq

    async def __call__(self, stage: str, drop_reason: str | None = None, **counts):
        self.seq += 1
        async with self.Session() as s, s.begin():
            await write.emit(
                s,
                Event(
                    run_id=self.run_id,
                    seq=self.seq,
                    stage=stage,
                    counts=counts,
                    drop_reason=drop_reason,
                    correlation_id=self.run_id,
                ),
            )


@dataclass
class Ctx:
    s: AsyncSession
    run: Run
    acq: Acquisition | None
    emit: Events
    sign: Callable[[str], dict]
    transport: httpx.AsyncBaseTransport | None

    async def call(self, body: dict) -> routes.Served:
        served = await routes.chat(
            Route(self.run.route),
            {"model": self.run.model_requested, **body},
            self.transport,
        )
        self.run.endpoint = served.endpoint
        self.run.model_served = served.model
        self.run.provider_served = served.provider
        raw = await blobs.put(self.s, served.raw, "application/json", Config.BLOB_DIR)
        self.run.raw_output_ref = raw.sha256
        usage = served.data.get("usage") or {}
        await self.emit(
            "model_called",
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
        return served


# --- extract ---------------------------------------------------------------------

# Real models echoed a format placeholder ("YYYY-MM-DD") or the string "null" into
# `valid` and never proposed a name, since the mention already carried it.
EXTRACT_SYSTEM = (
    "You extract facts about people and documents from the text the user gives you. "
    "Return only facts the text states. Each fact has: "
    "mention, the person it is about as the text names them, the same string for "
    "every fact about that person, or 'document' for a fact about the document "
    "itself; two people with the same name get different mentions (add their row "
    "or entry number); "
    "prop, what the fact is, and every person gets a 'name' fact; "
    "value.text, the value alone (the name, date, place or number, not the words "
    "around it) copied character for character from the text, and value.lang, its "
    "language code; "
    "occurrence, counting every appearance of that exact string anywhere in the "
    "text, which one the fact comes from, 1 for the first; "
    "evidence, the kinds of text it rests on (printed_text for ordinary typed or "
    "printed text); "
    "valid, the date the fact held written in digits like 2013, 2013-09 or "
    "2013-09-17 when the text dates the fact, otherwise JSON null. A birth date or "
    "document date is the value itself, so its valid is null. The document_date is "
    "when the document was written, issued, published or posted; give it whenever "
    "the text states one. "
    "arrested_at is the place of arrest, detained_at the place held, "
    "detention_branch the security branch holding the person, transferred_to where "
    "they were moved, seen_at and last_seen where they were seen; each quotes the "
    "place. A person's mother and father are facts about that person, not people of "
    "their own. A person is a human being the text names: outlets, pages, channels, "
    "organisations, places, crossings, headlines, links, hashtags and unnamed people "
    "(a child, a witness) are not people and get no facts. List each fact once and "
    "stop when every fact is listed. "
    "Separately, in events, list each event the text reports (an attack, strike, "
    "shelling, raid, arrest, killing, explosion or the like): what, the words saying "
    "what happened; kind; place, the words naming where, or null; town, the name of "
    "the town, city, village, airport, crossing or site inside place, or null; each "
    "copied exactly from the text, with lang for what; and date, the day it happened "
    "in digits like 2013-09-17 when the text gives it, or null. A posting date is not "
    "the event date unless the text says it happened that day. " + read.DATA_ONLY
)
VALID = r"^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$"

EXTRACT_SCHEMA = {
    "name": "statements",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["statements", "events"],
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["what", "lang", "kind", "place", "town", "date"],
                    "properties": {
                        "what": {"type": "string"},
                        "lang": {"type": "string"},
                        "kind": {"enum": list(REPORT_KINDS)},
                        "place": {"type": ["string", "null"]},
                        "town": {"type": ["string", "null"]},
                        "date": {
                            "anyOf": [
                                {"type": "string", "pattern": VALID},
                                {"type": "null"},
                            ]
                        },
                    },
                },
            },
            "statements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "mention",
                        "prop",
                        "value",
                        "occurrence",
                        "evidence",
                        "valid",
                    ],
                    "properties": {
                        "mention": {"type": "string"},
                        "prop": {
                            "type": "string",
                            "enum": sorted(EXTRACTED_PROPS - {Prop.REPORTS}),
                        },
                        "value": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["text", "lang"],
                            "properties": {
                                "text": {"type": "string"},
                                "lang": {"type": "string"},
                            },
                        },
                        "occurrence": {"type": "integer"},
                        "valid": {
                            "anyOf": [
                                {"type": "string", "pattern": VALID},
                                {"type": "null"},
                            ]
                        },
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [str(e) for e in EvidenceKind],
                            },
                        },
                    },
                },
            },
        },
    },
}


def valid_range(valid: str | None) -> tuple[date | None, date | None]:
    """A year, month or day as the first and last day it covers."""
    if not valid:
        return None, None
    parts = [int(p) for p in valid.split("-")]
    if len(parts) == 3:
        return date(*parts), date(*parts)
    if len(parts) == 2:
        return date(*parts, 1), date(*parts, monthrange(*parts)[1])
    if len(parts) == 1:
        return date(parts[0], 1, 1), date(parts[0], 12, 31)
    raise ValueError(f"not a date: {valid!r}")


def locate(text: str, quote: str, occurrence: int) -> dict | None:
    """Character span of the nth exact occurrence of quote. Models cannot count
    characters, so they quote and we compute offsets."""
    start = -1
    for _ in range(max(occurrence, 1)):
        start = text.find(quote, start + 1) if quote else -1
        if start < 0:
            return None
    return {"start": start, "end": start + len(quote)}


def facts(answer: dict, text: str) -> tuple[list[dict], int]:
    """The answer's items that may be written, and how many facts were dropped
    because their mention is not a person. Shared with acceptance/eval_extract.py."""
    items = answer["statements"] + [_reported(e) for e in answer.get("events", [])]
    # A headline or a channel filed as a person would reach review and the linker as
    # someone; every fact of such a mention goes.
    unnamed = {
        i["mention"]
        for i in items
        if i["prop"] == Prop.NAME and not name_shaped(i["value"]["text"])
    }
    out, dropped = [], 0
    for item in items:
        prop = Prop(item["prop"])
        if prop not in EXTRACTED_PROPS:
            raise ValueError(f"model returned non-extracted prop {prop}")
        if prop not in DOCUMENT_PROPS and item["mention"] in unnamed:
            dropped += 1
            continue
        if prop == Prop.REPORTS:
            # Place words the text does not contain are the model's, not the source's.
            value = item["value"]
            item = item | {
                "value": {
                    k: v
                    for k, v in value.items()
                    if k not in ("place", "town") or (v and v in text)
                }
            }
        out.append(item)
    return out, dropped


def _reported(e: dict) -> dict:
    """An event of the answer as a statement item on the document."""
    value = {"text": e["what"], "lang": e["lang"], "kind": e["kind"]}
    value |= {k: e[k] for k in ("place", "town") if e[k]}
    return {
        "mention": "document",
        "prop": Prop.REPORTS,
        "value": value,
        "occurrence": 1,
        "evidence": ["printed_text"],
        "valid": e["date"],
    }


def name_shaped(text: str) -> bool:
    """Could be a person's name: no link, hashtag or handle, and at most eight words
    (Arabic names with a nasab run to six or seven)."""
    return not re.search(r"https?://|www\.|[#@]", text) and len(text.split()) <= 8


async def extract(c: Ctx) -> str | None:
    blob = await c.s.get(Blob, c.run.inputs["blobs"][0])
    try:
        text = blobs.read(blob, Config.BLOB_DIR).decode("utf-8")
    except UnicodeDecodeError:
        await c.emit("input_read", drop_reason="not_utf8", chars=0)
        return None
    if not text.strip():
        await c.emit("input_read", drop_reason="empty_text", chars=0)
        return None
    await c.emit("input_read", chars=len(text))
    c.run.prompt_hash = chain.sha256(chain.canon([EXTRACT_SYSTEM, EXTRACT_SCHEMA]))
    served = await c.call(
        {
            "messages": [
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": text},
            ],
            "response_format": {"type": "json_schema", "json_schema": EXTRACT_SCHEMA},
            "temperature": 0,
            # Local thinking models (gemma4, qwen3.5) otherwise reason at length first.
            **({"reasoning_effort": "none"} if c.run.route == "local" else {}),
        }
    )
    items, unnamed = facts(orjson.loads(routes.content(served)), text)
    for _ in range(unnamed):
        await c.emit("dropped", drop_reason="not_a_person")
    subjects: dict[str, str] = {}
    names = []
    written = undated = 0
    dropped = unnamed
    for item in items:
        prop = Prop(item["prop"])
        if prop in DOCUMENT_PROPS:
            subject = f"blob:{blob.sha256}"
        else:
            subject = subjects.setdefault(item["mention"], f"person:{uuid.uuid4()}")
        span = locate(text, item["value"]["text"], item["occurrence"])
        try:
            valid_from, valid_to = valid_range(item.get("valid"))
        except ValueError:
            # A quoted value is still worth a review; only its date is unusable.
            valid_from = valid_to = None
            undated += 1
            await c.emit("undated", drop_reason="bad_valid_date")
        value = item["value"]
        st = StatementIn(
            subject=subject,
            prop=prop,
            value=value,
            author_id=c.run.principal_id,
            run_id=c.run.id,
            span_blob=blob.sha256,
            # Without a span the gate rejects it mechanically; the row stays as audit.
            span=span,
            valid_from=valid_from,
            valid_to=valid_to,
            evidence=tuple(EvidenceKind(e) for e in item["evidence"]),
        )
        try:
            st_row = await write.append(c.s, st, sign=c.sign)
        except Refused as exc:
            dropped += 1
            await c.emit("dropped", drop_reason=f"refused: {exc.reason}")
            continue
        written += 1
        if span is None:
            dropped += 1
            await c.emit("dropped", drop_reason="quote_not_in_text")
        elif prop == Prop.NAME:
            names.append(st_row.id)
    await c.emit(
        "statements_written",
        proposed=len(items),
        written=written,
        dropped=dropped,
        undated=undated,
        subjects=len(subjects),
    )
    for subject in set(subjects.values()):
        await discovery.queue_actor(c.s, subject, c.acq)
    if names:
        agent = await matcher.matcher_agent(c.s)
        c.s.add(
            matcher.match_run(agent, c.run.inputs["acquisition"], blob.sha256, names)
        )
    # A photo gets the first pass that looks at the picture (queued by ocr) instead.
    source = await c.s.get(Blob, c.acq.blob_sha256) if c.acq else None
    if source and not source.mime.startswith("image/"):
        await queue_read(c.s, c.acq, "assess", blob.sha256)
    return c.run.raw_output_ref


async def assess(c: Ctx) -> str | None:
    """The first pass of the analyst's method: on the text extraction read, or on
    the picture itself for a photo."""
    blob = await c.s.get(Blob, c.run.inputs["blobs"][0])
    raw = blobs.read(blob, Config.BLOB_DIR)
    photo = blob.mime.startswith("image/")
    if photo:
        width, height = Image.open(io.BytesIO(raw)).size
        system, schema = method.SEE_SYSTEM, method.SEE_SCHEMA
        url = f"data:{blob.mime};base64,{base64.b64encode(raw).decode()}"
        user = [{"type": "image_url", "image_url": {"url": url}}]
        await c.emit("input_read", bytes=blob.size)
    else:
        text = raw.decode("utf-8")
        system, schema, user = method.SYSTEM, method.SCHEMA, text
        await c.emit("input_read", chars=len(text))
    c.run.prompt_hash = chain.sha256(chain.canon([system, schema]))
    served = await c.call(
        {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_schema", "json_schema": schema},
            "temperature": 0,
            **({"reasoning_effort": "none"} if c.run.route == "local" else {}),
        }
    )
    answer = orjson.loads(routes.content(served))
    parts, dropped = (
        method.seen(answer, width, height) if photo else method.items(answer, text)
    )
    for _ in range(dropped):
        await c.emit(
            "dropped", drop_reason="box_unusable" if photo else "quote_not_in_text"
        )
    for prop, value, span in parts:
        await write.append(
            c.s,
            StatementIn(
                subject=f"blob:{blob.sha256}",
                prop=prop,
                value=value,
                author_id=c.run.principal_id,
                run_id=c.run.id,
                span_blob=blob.sha256,
                span=span,
            ),
            sign=c.sign,
        )
    await c.emit("statements_written", written=len(parts), dropped=dropped)
    return c.run.raw_output_ref


# --- ocr -------------------------------------------------------------------------

OCR_PROMPT = (
    "Transcribe all text in this image exactly as written, in its original script. "
    "Output only the transcription. Output nothing if the image has no text."
)


async def ocr(c: Ctx) -> str | None:
    blob = await c.s.get(Blob, c.run.inputs["blobs"][0])
    if not blob.mime.startswith("image/"):
        raise ValueError(f"ocr needs an image, got {blob.mime}")
    data_url = f"data:{blob.mime};base64,{base64.b64encode(blobs.read(blob, Config.BLOB_DIR)).decode()}"
    await c.emit("input_read", bytes=blob.size)
    if c.acq:
        await queue_read(c.s, c.acq, "assess", blob.sha256)
    c.run.prompt_hash = chain.sha256(chain.canon([OCR_PROMPT]))
    served = await c.call(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": 0,
            **({"reasoning_effort": "none"} if c.run.route == "local" else {}),
        }
    )
    text = routes.content(served)
    if not text.strip():
        await c.emit("statements_written", drop_reason="ocr_empty", written=0)
        return c.run.raw_output_ref
    derived = await blobs.put(
        c.s, text.encode(), "text/plain; charset=utf-8", Config.BLOB_DIR
    )
    await write.append(
        c.s,
        StatementIn(
            subject=f"blob:{derived.sha256}",
            prop=Prop.DERIVED_FROM,
            value={"blob": blob.sha256},
            author_id=c.run.principal_id,
            run_id=c.run.id,
        ),
        sign=c.sign,
    )
    await c.emit("statements_written", written=1, chars=len(text))
    # Extraction has its own policy row, so it may run on another route than OCR.
    if c.run.inputs.get("extract_model"):
        c.s.add(
            Run(
                principal_id=c.run.principal_id,
                tool="extract",
                route=c.run.inputs["extract_route"],
                model_requested=c.run.inputs["extract_model"],
                inputs={"blobs": [derived.sha256], "acquisition": str(c.acq.id)},
            )
        )
        await c.s.flush()
        await c.emit("extract_queued")
    await discovery.queue(c.s, c.acq, derived.mime, blob_sha256=derived.sha256)
    return c.run.raw_output_ref


# --- match -----------------------------------------------------------------------


async def match(c: Ctx) -> str | None:
    ids = [uuid.UUID(i) for i in c.run.inputs["statements"]]
    out = await matcher.match(c.s, ids, c.run, c.sign)
    for reason in out["refused"]:
        await c.emit("dropped", drop_reason=f"refused: {reason}")
    await c.emit(
        "statements_written", written=len(out["written"]), dropped=len(out["refused"])
    )
    return chain.sha256(chain.canon(out["written"]))


TOOLS: dict[str, Callable[[Ctx], Awaitable[str | None]]] = {
    "extract": extract,
    "assess": assess,
    "ocr": ocr,
    "match": match,
    "transcribe": speech.transcribe,
    "embed": discovery.embed,
    "embed_actor": discovery.embed_actor,
    "index": indexing.index,
    "pdf": indexing.pdf,
    "fingerprint": discovery.fingerprint,
    "discover": findings.discover,
    "thumbnail": thumbnail,
    "geotag": geotag,
    "extract_sheet": extract_sheet,
    "capture": capture,
    "brief": brief,
    "link": link,
    "standing": standing,
    "contradictions": contradictions,
    "gaps": gaps,
    "notice": notice,
    "approximate": approximate,
}


# --- loop ------------------------------------------------------------------------


async def _check_route(s: AsyncSession, run: Run, acq: Acquisition | None) -> None:
    # Without a document, the defaults the gate uses for statements without one.
    doc_type, sensitivity = (
        (acq.doc_type, acq.sensitivity) if acq else ("none", "sensitive")
    )
    policy = resolve(await write.policies(s), run.tool, doc_type, sensitivity)
    if Route(run.route) not in policy.routes:
        raise routes.RouteError(
            f"route {run.route} not allowed for {run.tool}/{doc_type}/"
            f"{sensitivity} (allowed: {', '.join(policy.routes) or 'none'})"
        )


async def process(
    Session: async_sessionmaker,  # noqa: N803
    run_id: uuid.UUID,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    async with Session() as s:
        last = await s.scalar(
            select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)
        )
    emit = Events(Session, run_id, last or 0)
    await emit("claimed")
    try:
        async with Session() as s, s.begin():
            run = await s.get(Run, run_id)
            tool = TOOLS.get(run.tool)
            if tool is None:
                raise ValueError(f"no tool {run.tool!r}")
            # An investigation-wide run (discover) reads ledger rows, not a document.
            ref = run.inputs.get("acquisition")
            acq = await s.get(Acquisition, uuid.UUID(ref)) if ref else None
            await tasks.check_access(s, run)
            await _check_route(s, run, acq)
            # Per-run key: the public half is sealed with the run, the private half
            # never leaves this process.
            key = Ed25519PrivateKey.generate()
            run.pubkey = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            ctx = Ctx(
                s=s,
                run=run,
                acq=acq,
                emit=emit,
                sign=lambda h: {"kind": "ed25519", "sig": key.sign(h.encode()).hex()},
                transport=transport,
            )
            await write.seal_run(s, run, await tool(ctx))
            await tasks.evidence(s, run)
    except Exception as exc:
        log.exception("run %s failed", run_id)
        async with Session() as s, s.begin():
            await queue.fail(
                s, await s.get(Run, run_id), f"{type(exc).__name__}: {exc}"
            )
        await emit("failed", drop_reason=f"{type(exc).__name__}: {exc}"[:500])
        await intake.finished(Session, run_id)
        await tasks.settle(Session, run_id)
        return
    await emit("sealed")
    await intake.finished(Session, run_id)
    await tasks.settle(Session, run_id)


async def work_once(
    Session: async_sessionmaker,  # noqa: N803
    tools: list[str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    async with Session() as s, s.begin():
        run = await queue.claim(s, tools)
    if run is None:
        return False
    await process(Session, run.id, transport)
    return True


async def serve(Session: async_sessionmaker, tools: list[str] | None = None) -> None:  # noqa: N803
    while True:
        try:
            await tasks.tick(Session)
        except Exception:
            log.exception("standing task scheduling failed")
        if not await work_once(Session, tools):
            await asyncio.sleep(POLL_SECONDS)
