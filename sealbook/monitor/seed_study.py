"""`stk demo-seed --case <slug>`: write a case study from
acceptance/cases/<slug>/case.json through the write path the product uses, so it
opens on the real pages.

The case file is the one the agent evaluation scores (docs/case-studies.md). Sources
enter in date order through intake, and the worker's local tools (geotag,
thumbnail, fingerprint, index) run on each before the next arrives, so a clip is
matched against the clips already held. A scan or recording with a fixed
`transcript` is read by the app's own OCR or speech writer from that text. Each
claim is an extraction statement pinned where its `pin` says: a sheet cell, a box
on a scan, or a quote found in the source's text. Claims planted as accepted are
accepted by a case analyst with a passkey signature, each planted cluster is a
same-person link that analyst accepted, and planted places with coordinates are
confirmed by the same analyst. Then the agents the planted truth asks about run on
the fixed answer, so their proposals wait for a person. Nothing is accepted on an
agent's word."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path

import click
import orjson
import stk.extensions as ext
from sqlalchemy import select
from stk.user.models import User

from sealbook import blobs
from sealbook.agents import contradictions, gaps, linker, read
from sealbook.cases.service import ensure as ensure_case
from sealbook.discovery.evidence import copy_reason
from sealbook.ingest.service import system_agent
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Blob, Payload, Run, Statement, StatementTier
from sealbook.ledger.write import principal_for_user
from sealbook.monitor import seed_flows as flows
from sealbook.monitor.seed import Seeder, seeded
from sealbook.monitor.seed_full import users
from sealbook.places import cli as places
from sealbook.places.models import Place
from sealbook.runs import worker
from sealbook.runs.worker import process
from sealbook.settings import Config

CASES = Path(__file__).resolve().parents[2] / "acceptance" / "cases"
MIME = {
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".html": "text/html",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}
ARABIC = re.compile(r"[؀-ۿ]")
# The worker's tools that need no model: intake's own steps, run as it would.
LOCAL = ("geotag", "thumbnail", "fingerprint", "index")
# The agents a case's planted truth asks about, by the truth keys that score them.
AGENTS = {
    "linker": ("same_person", "twins"),
    "contradictions": ("contradictions", "not_contradictions", "trap_contradictions"),
    "gaps": ("gaps", "not_gaps"),
}


# The case's two people, invented: who accepts the planted items and signs the
# conclusion, and the colleague who reviews it.
ANALYST = ("ines.halvorsen", "Ines Halvorsen")
REVIEWER = ("tomas.reyes", "Tomas Reyes")


def path_of(slug: str) -> Path:
    return CASES / slug / "case.json"


def person(investigation: str, record: str) -> str:
    key = f"sealbook-case/{investigation}/{record}"
    return f"person:{uuid.uuid5(uuid.NAMESPACE_URL, key)}"


def agents_for(spec: dict) -> list[str]:
    truth = spec.get("truth") or {}
    return [a for a, keys in AGENTS.items() if any(truth.get(k) for k in keys)]


def file_of(root: Path, name: str) -> Path:
    """A case file named from the case directory or from its files/."""
    return root / name if (root / name).exists() else root / "files" / name


def value_of(c: dict) -> dict:
    """The statement value extraction writes for a planted claim."""
    text = c["value"]
    lang = "ar" if ARABIC.search(text) else "en"
    if c["prop"] in (Prop.BIRTH_DATE, Prop.DOCUMENT_DATE):
        return {"date": text}
    if c["prop"] in (Prop.EVENT, Prop.REPORTS):
        return {"text": text, "lang": lang, "kind": c["event"]}
    if c["prop"] in (Prop.SEX, Prop.AGE):
        return {"text": text, "lang": lang, "norm": text.lower()}
    # A place's time of day, as extraction writes it.
    return {"text": text, "lang": lang} | ({"time": c["time"]} if c.get("time") else {})


def stand_in(spec: dict, key: str, claims: list[dict]) -> tuple[str, dict]:
    """A text source for a planted source without a file: its title, then one line
    per claim, with the span of each value."""
    text, spans = spec["sources"][key]["title"] + "\n\n", {}
    for c in claims:
        line = f"{c['prop'].replace('_', ' ').capitalize()}: "
        start = len(text) + len(line)
        spans[c["key"]] = {"start": start, "end": start + len(c["value"])}
        text += line + c["value"] + "\n"
    return text, spans


def kind(precision_m: int | None) -> tuple[str, str]:
    """The GeoNames class and code whose place kind carries about the planted
    precision: a town (3 km), a neighbourhood (1 km) or a site (250 m)."""
    if (precision_m or 0) >= 2000:
        return "P", "PPL"
    return ("P", "PPLX") if (precision_m or 0) >= 1000 else ("S", "FAC")


async def import_places(s: Seeder, spec: dict) -> dict:
    """The planted places with coordinates as person-recorded sites (no GeoNames
    id), by key. A place without coordinates stays a name: nothing puts it on a map."""
    located = {k: p for k, p in spec["places"].items() if p.get("lat") is not None}
    if not located:
        return {}
    rows = [
        [
            "",
            p.get("name_ar") or p["name_en"],
            p["name_en"],
            "",
            str(p["lat"]),
            str(p["lon"]),
            *kind(p.get("precision_m")),
            *[""] * 11,
        ]
        for p in located.values()
    ]
    with tempfile.TemporaryDirectory() as tmp:
        tsv = Path(tmp) / "places.tsv"
        tsv.write_text("".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
        await places.import_file(s.s, tsv)
    found = {
        p.name_ar: p
        for p in await s.s.scalars(select(Place).where(Place.source == "person"))
    }
    return {k: found[p.get("name_ar") or p["name_en"]] for k, p in located.items()}


async def settle(s: Seeder, key: str) -> None:
    """Run the local intake tools queued for `key` now, as a worker keeping up
    would, so the next source is compared with this one."""
    acq = await flows._acq(s, key)
    await s.s.commit()
    queued = await s.s.scalars(
        select(Run.id)
        .where(
            Run.status == "queued",
            Run.tool.in_(LOCAL),
            Run.inputs["acquisition"].astext == str(acq.id),
        )
        .order_by(Run.available_at)
    )
    for run_id in list(queued):
        await process(ext.async_session_factory, run_id)


async def read_scan(s: Seeder, key: str, text: str, origin: str) -> None:
    """OCR of the scan `key` by the worker's own ocr tool, answered with the case's
    fixed text. Registers the text as `<key>_ocr`."""
    acq = await flows._acq(s, key)
    run = await s.s.scalar(
        select(Run).where(
            Run.tool == "ocr",
            Run.inputs["blobs"][0].astext == s.blobs[key],
            Run.status == "queued",
        )
    ) or Run(principal_id=(await system_agent(s.s, "extractor")).id, tool="ocr")
    # No extract_* inputs: the claims are the case's, not a model's.
    run.inputs = {"blobs": [s.blobs[key]], "acquisition": str(acq.id)}
    c = await flows._start(
        s,
        run,
        acq,
        f"OCR text copied from {origin}",
        flows.Answered,
        answer={"has_text": True, "text": text},
    )
    await write.seal_run(s.s, run, await worker.ocr(c))
    s.blobs[f"{key}_ocr"] = hashlib.sha256(text.encode()).hexdigest()
    s.texts[f"{key}_ocr"] = text


async def transcribe(s: Seeder, key: str, mime: str, path: Path, origin: str) -> str:
    """The fixed words of a scan or recording, written by the app's own reader.
    Returns the key of the text its claims are pinned in."""
    if mime.startswith("image/"):
        await read_scan(s, key, path.read_text(encoding="utf-8"), origin)
        return f"{key}_ocr"
    said = json.loads(path.read_text(encoding="utf-8"))
    lang = "ar" if any(ARABIC.search(x["text"]) for x in said["sentences"]) else "en"
    await flows.transcript(
        s, key, said["sentences"], said["duration_ms"], lang=lang, origin=origin
    )
    return f"{key}_tx"


async def run_agent(session, name: str, inv: str, owner) -> dict:
    """One agent on the fixed answer, processed now. Returns its stored output."""
    run = await {
        "linker": linker.queue,
        "contradictions": contradictions.queue,
        "gaps": gaps.queue,
    }[name](session, inv, owner.id, read.FIXED)
    await session.commit()
    await process(ext.async_session_factory, run.id)
    await session.refresh(run)
    if run.output_hash is None:
        raise click.ClickException(f"the {name} run failed: {run.error}")
    return orjson.loads(
        blobs.read(await session.get(Blob, run.output_hash), Config.BLOB_DIR)
    )


async def seed_study(
    session, app, path: Path, until: int | None = None, record: bool = False
) -> dict:
    """Additive: refuses when the case's investigation already has a source. Plays
    the case's acts (`sealbook/monitor/study`), up to act `until` when given. Sources
    marked `read` are read by the app, replaying `recordings.json`; with `record`
    the models answer live and the file is written again."""
    from sealbook.monitor import study
    from sealbook.monitor.study.recording import Missing, Tape

    spec = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    inv = spec["investigation"]
    if await seeded(session, inv):
        raise click.ClickException(f"{inv} is already seeded")
    acts = spec.get("acts") or study.default_acts(spec)
    study.load()
    study.check(spec, acts)
    tape = None
    if any(src.get("read") for src in spec["sources"].values()):
        try:
            tape = Tape.open(root, record)
        except Missing as exc:
            raise click.ClickException(str(exc)) from None
    try:
        with tape.pinned() if tape else contextlib.nullcontext():
            out = await _play(session, app, spec, root, acts, until, tape)
    finally:
        if tape and record:  # kept even when a run failed, so a rerun resumes
            tape.save()
    return out


async def _play(session, app, spec, root, acts, until, tape) -> dict:
    from sealbook.monitor import study

    inv = spec["investigation"]
    records = spec.get("records") or {}
    story = spec["story"]
    # The case header adds its own counts after the description.
    await ensure_case(
        session, inv, name=story["title"], description=story["puzzle"].rstrip(".")
    )
    s = Seeder(session)
    s.case = inv
    owner, *_ = await users(session, app, ())

    # A claim's record is a person record, or else the source itself: a claim
    # about the document, such as the event a post reports.
    def source_of(c: dict) -> str:
        return records[c["record"]]["source"] if c["record"] in records else c["record"]

    claims = defaultdict(list)
    for c in spec["claims"]:
        claims[source_of(c)].append(c)
    st = study.Study(
        session=session,
        app=app,
        spec=spec,
        root=root,
        s=s,
        owner=owner,
        analyst=await colleague(s, inv, *ANALYST),
        extractor=await system_agent(session, "extractor"),
        collector=await principal_for_user(
            session, owner.id, owner.name or owner.email
        ),
        batch=str(uuid.uuid4()) if spec.get("batch") else None,
        channel=f"channel:case-study/{root.name}",
        claims=claims,
        tape=tape,
    )
    played = await study.play(st, acts, until)
    return (
        {
            "investigation": inv,
            "sources": len(st.arrived),
            "claims": len(st.written),
            "clusters": len(spec.get("clusters") or []),
        }
        | st.out
        | {"acts": played, "missed": st.missed}
    )


async def colleague(s: Seeder, inv: str, handle: str, name: str):
    """A case analyst with a passkey on `s`, shown by a person's name."""
    p = await s.analyst(handle, inv)
    p.name = name
    (await s.s.get(User, p.user_id)).name = name
    await s.s.flush()
    return p


async def pending(db, prop: str, subjects: list[str]) -> list[tuple]:
    """Unconfirmed statements of `prop` about `subjects`, with their values."""
    return (
        await db.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == prop,
                Statement.subject.in_(subjects),
                StatementTier.tier == Tier.UNCONFIRMED,
            )
        )
    ).all()


def copy_family(spec: dict) -> set[str]:
    truth = spec["truth"]
    return {truth["original"]} | {c["file"] for c in truth["copies"]}


async def copies(s: Seeder, spec: dict) -> list:
    """The strong match proposals inside the planted family of copies. Weak ones
    and any outside the family stay for a person."""
    family = {f"blob:{s.blobs[k]}" for k in copy_family(spec)}
    return [
        st
        for st, v in await pending(s.s, Prop.MEDIA_MATCH, sorted(family))
        if f"blob:{v['blob']}" in family and copy_reason(v)[2] == "strong"
    ]
