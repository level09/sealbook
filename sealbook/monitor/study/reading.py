"""`read`: the app reads the act's sources marked `read` itself. The runs intake
queued for them (OCR, speech to text, extraction, the first pass) and the runs those
queue in turn are processed by the worker's own code until none is left, each model
answer replayed from the case's recordings (`recording.py`). The speech model's
segments are replayed through the transcript writer. Afterwards each planted claim
of a read source is looked for among what the app wrote: a recovered claim stands
for its key (conclusions and later steps cite it), a record is matched to the
person the app wrote, and the counts go to `out["read"]`."""

from __future__ import annotations

import asyncio
import re
from datetime import date

import orjson
import stk.extensions as ext
from sqlalchemy import select

from sealbook import blobs, speech
from sealbook.ingest.service import queue_read
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Run,
    Statement,
    StatementTier,
)
from sealbook.monitor import seed_flows as flows
from sealbook.monitor.data import LOCATED
from sealbook.monitor.study import step
from sealbook.runs.worker import process
from sealbook.search.arabic import natural
from sealbook.settings import Config

MODEL = ("ocr", "extract", "assess")
LOCAL = ("match", "index", "geotag", "thumbnail", "fingerprint")


async def _acqs(st, keys) -> dict[str, str]:
    """Acquisition id -> source key, for the sources `keys`."""
    rows = await st.session.execute(
        select(Acquisition.id, Acquisition.blob_sha256).where(
            Acquisition.context["investigation"].astext == st.inv,
            Acquisition.blob_sha256.in_([st.s.blobs[k] for k in keys]),
        )
    )
    by_sha = {st.s.blobs[k]: k for k in keys}
    return {str(a): by_sha[sha] for a, sha in rows.all()}


async def _transcribe(st, run, key: str) -> None:
    tape, s = st.tape, st.s
    sha = s.blobs[key]
    blob = await st.session.get(Blob, sha)
    path = await asyncio.to_thread(blobs.verified_path, blob, Config.BLOB_DIR)
    if not (await asyncio.to_thread(speech.probe, path))[1]:
        # A video without a sound track: the worker's own run says so and queues
        # the first pass on the frames alone. No model is loaded, nothing to record.
        await st.session.commit()
        await process(ext.async_session_factory, run.id)
        return
    if tape.recording:
        await process(ext.async_session_factory, run.id)
        await st.session.refresh(run)
        parts = [run]
        while True:  # a long recording continues in further runs
            nxt = await st.session.scalar(
                select(Run).where(Run.inputs["continued_from"].astext == str(run.id))
            )
            if nxt is None:
                break
            await process(ext.async_session_factory, nxt.id)
            await st.session.refresh(nxt)
            parts.append(run := nxt)
        segments, out = [], None
        for r in parts:
            if r.status != "done":
                raise RuntimeError(f"speech to text failed on {key}: {r.error}")
            out = orjson.loads(
                blobs.read(
                    await st.session.get(Blob, r.raw_output_ref), Config.BLOB_DIR
                )
            )
            segments += out["segments"]
        tape.heard.add(sha)
        tape.speech[sha] = {
            "source": key,
            "input_sha256": sha,
            "model": out["model"],
            "language": out["language"],
            "duration_ms": out["duration_ms"],
            "segments": segments,
        }
        return
    said = tape.speech.get(sha)
    if said is None:
        raise RuntimeError(f"no recorded transcript of {key}; record again")
    await flows.transcript(
        s,
        key,
        [
            {"start_ms": x["ms"][0], "end_ms": x["ms"][1], "text": x["text"]}
            for x in said["segments"]
        ],
        said["duration_ms"],
        lang=said["language"]["language"],
        origin=f"the {said['model']['repo']} output recorded in recordings.json",
        recorded={"model": said["model"]["repo"], "at": tape.data.get("recorded_at")},
    )
    # The transcribe tool queues extraction of its transcript when it finishes, and
    # nothing when no speech was heard.
    if said["segments"]:
        acq = await flows._acq(s, key)
        await queue_read(st.session, acq, "extract", s.blobs[f"{key}_tx"])


@step("read")
async def read(st, act, args) -> None:
    keys = [k for k in act["sources"] if st.read(k)]
    if not keys:
        return
    acqs = await _acqs(st, keys)
    done: set = set()
    while True:
        await st.session.commit()
        runs = list(
            await st.session.scalars(
                select(Run)
                .where(
                    Run.status == "queued",
                    Run.tool.in_([*MODEL, *LOCAL, "transcribe"]),
                    Run.inputs["acquisition"].astext.in_(list(acqs)),
                )
                .order_by(Run.available_at)
            )
        )
        runs = [r for r in runs if r.id not in done]
        if not runs:
            break
        for run in runs:
            # The worker's run opens its own session and takes the ledger's write
            # lock: nothing this session wrote (a replayed transcript) may hold it.
            await st.session.commit()
            done.add(run.id)
            key = acqs[run.inputs["acquisition"]]
            # Hosted speech to text is a model call like the others; the local
            # model's segments are replayed through the transcript writer.
            hosted = run.tool == "transcribe" and run.route != "local"
            if run.tool == "transcribe" and not hosted:
                await _transcribe(st, run, key)
                continue
            transport = None
            if run.tool in MODEL or hosted:
                label = {
                    "source": key,
                    "tool": run.tool,
                    "input_sha256": run.inputs["blobs"][0],
                } | ({"cursor": run.inputs.get("cursor", 0)} if hosted else {})
                transport = st.tape.transport(label)
                if note := st.tape.note(run):
                    run.inputs = {
                        **run.inputs,
                        "demo": note,
                        "answer": "recorded",
                        "recorded_at": st.tape.data.get("recorded_at"),
                    }
                    await st.session.commit()
            await process(ext.async_session_factory, run.id, transport)
            await st.session.refresh(run)
            if run.status != "done":
                raise RuntimeError(f"{run.tool} on {key} failed: {run.error}")
            if st.tape.recording and (run.tool in MODEL or hosted):
                for c in st.tape.calls.values():
                    if (
                        c["source"] == key
                        and c["tool"] == run.tool
                        and not c.get("prompt_hash")
                    ):
                        c |= {
                            "prompt_hash": run.prompt_hash,
                            "model_served": run.model_served,
                            "provider_served": run.provider_served,
                            "route": run.route,
                        }
    await texts(st, keys)
    await recover(st, keys)


async def texts(st, keys) -> None:
    """Register the text the app read from each scan or recording (its OCR text,
    or its joined transcript, the longest text derived from it), so a later step
    can quote it."""
    for key in keys:
        sha = st.s.blobs[key]
        rows = await st.session.execute(
            select(Statement.subject)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(Run, Run.id == Statement.run_id)
            .where(
                Statement.prop == Prop.DERIVED_FROM,
                Payload.value["blob"].astext == sha,
                Run.tool.in_(["ocr", "transcribe"]),
            )
        )
        best = None
        for (subject,) in rows.all():
            blob = await st.session.get(Blob, subject.removeprefix("blob:"))
            text = blobs.read(blob, Config.BLOB_DIR).decode()
            if best is None or len(text) > len(best[1]):
                best = (blob.sha256, text)
        if best:
            st.s.blobs[f"{key}_read"], st.s.texts[f"{key}_read"] = best
            st.text_of[key] = f"{key}_read"


def _norm(text) -> str:
    text = str(text or "").replace("’", "'").casefold()
    return re.sub(r"[\s.,;:]+", " ", text).strip()


def same_value(prop: str, got: str, planted: str) -> bool:
    """The same words; for a place, one naming inside the other ("Tanner's Row"
    and "Tanner's Row, Larkhaven"); for a name, in natural order ("KASSAR /
    ADRIAN" is Adrian Kassar)."""
    if prop == Prop.NAME:
        got, planted = natural(str(got or "")), natural(str(planted or ""))
    a, b = _norm(got), _norm(planted)
    if prop in LOCATED:
        return bool(a and b) and (a in b or b in a)
    return a == b


def _shown(value: dict) -> str:
    return value.get("text") or value.get("date") or ""


async def _written(st, keys) -> list[tuple]:
    """What the app's runs wrote about people for the sources `keys`: statements
    whose span lies in a source or in text derived from it, with their tier."""
    shas = {st.s.blobs[k]: k for k in keys}
    derived = await st.session.execute(
        select(Statement.subject, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            Statement.prop == Prop.DERIVED_FROM,
            Payload.value["blob"].astext.in_(list(shas)),
        )
    )
    for subject, value in derived.all():
        shas[subject.removeprefix("blob:")] = shas[value["blob"]]
    rows = await st.session.execute(
        select(Statement, Payload.value, StatementTier.tier)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .join(Run, Run.id == Statement.run_id)
        .where(
            Run.tool == "extract",
            Statement.span_blob.in_(list(shas)),
            Statement.subject.startswith("person:"),
        )
    )
    return [(found, value, tier, shas[found.span_blob]) for found, value, tier in rows]


async def recover(st, keys) -> None:
    """Match each planted claim of the read sources to what extraction wrote."""
    got = await _written(st, keys)
    usable = [g for g in got if g[2] != Tier.REJECTED]
    # Claims about people only: what a post reports is planted (`claims`).
    planted = [c for k in keys for c in st.claims[k] if c["record"] in st.records]
    # A record is the person whose name the app wrote in the record's source; a
    # name written there wins over one heard in a recording of it.
    for c in planted:
        if c["prop"] != "name" or c["record"] in st.subjects:
            continue
        source = st.records[c["record"]]["source"]
        same = sorted(
            (
                (bool(v.get("heard")), f.subject)
                for f, v, _, k in usable
                if k == source
                and f.prop == Prop.NAME
                and same_value(Prop.NAME, _shown(v), c["value"])
            ),
            key=lambda x: x[0],
        )
        if same:
            st.subjects[c["record"]] = same[0][1]
    recovered = 0
    for c in planted:
        source = st.records[c["record"]]["source"]
        subject = st.subjects.get(c["record"])
        want = date.fromisoformat(c["from"]) if c["from"] else None
        same = [
            f
            for f, v, _, k in usable
            if k == source
            and f.subject == subject
            and f.prop == c["prop"]
            and same_value(c["prop"], _shown(v), c["value"])
            and (want is None or f.valid_from == want)
        ]
        if same:
            st.written[c["key"]] = same[0]
            recovered += 1
        else:
            st.missed.append(c["key"])
    total = st.out.setdefault("read", {"planted": 0, "recovered": 0, "written": 0})
    total["planted"] += len(planted)
    total["recovered"] += recovered
    total["written"] += len(got)
