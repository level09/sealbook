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
from sqlalchemy import func, select
from stk.user.models import User

from sealbook import blobs
from sealbook.agents import brief, contradictions, gaps, linker, read
from sealbook.cases import conclusions
from sealbook.cases.service import ensure as ensure_case
from sealbook.discovery.evidence import copy_reason
from sealbook.ingest.service import system_agent
from sealbook.ledger import write
from sealbook.ledger.contracts import EvidenceKind as E
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Blob, Payload, Run, Statement, StatementTier
from sealbook.ledger.write import append, principal_for_user
from sealbook.monitor import seed_flows as flows
from sealbook.monitor.data import LOCATED
from sealbook.monitor.seed import Seeder, seeded
from sealbook.monitor.seed_full import LLM, intake, users
from sealbook.people import data as people
from sealbook.places import cli as places
from sealbook.places import resolve
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
    return {"text": text, "lang": lang}


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


async def seed_study(session, app, path: Path) -> dict:
    """Additive: refuses when the case's investigation already has a source."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    inv = spec["investigation"]
    records = spec.get("records") or {}
    if await seeded(session, inv):
        raise click.ClickException(f"{inv} is already seeded")
    story = spec["story"]
    # The case header adds its own counts after the description.
    await ensure_case(
        session, inv, name=story["title"], description=story["puzzle"].rstrip(".")
    )
    s = Seeder(session)
    s.case = inv
    owner, *_ = await users(session, app, ())
    analyst = await colleague(s, inv, *ANALYST)
    extractor = await system_agent(session, "extractor")
    collector = await principal_for_user(session, owner.id, owner.name or owner.email)
    batch = str(uuid.uuid4()) if spec.get("batch") else None
    channel = f"channel:case-study/{root.name}"

    # A claim's record is a person record, or else the source itself: a claim
    # about the document, such as the event a post reports.
    def source_of(c: dict) -> str:
        return records[c["record"]]["source"] if c["record"] in records else c["record"]

    claims = defaultdict(list)
    for c in spec["claims"]:
        claims[source_of(c)].append(c)
    pins, text_of = {}, {}
    order = sorted(spec["sources"], key=lambda k: (spec["sources"][k]["date"] or "", k))
    for key in order:
        src = spec["sources"][key]
        origin = src.get("origin") or channel
        context = {"title": src["title"], "fictional": True}
        if batch and key in spec["batch"]:
            context["batch"] = batch
        if post := src.get("post"):
            # The post a file arrived with, kept as its own item the way a page
            # capture keeps the page beside its media; the claims quote it.
            await intake(
                s,
                f"{key}_post",
                post["text"].encode(),
                "text/plain",
                origin,
                collector,
                post.get("doc_type", "social_post"),
                src["sensitivity"],
                context | {"title": post["title"], "file": f"{key}_post.txt"},
            )
            text_of[key] = f"{key}_post"
            await settle(s, f"{key}_post")
        if src.get("file"):
            file = file_of(root, src["file"])
            data, mime = file.read_bytes(), MIME[file.suffix.lower()]
        elif src.get("text") is not None:
            data, mime, file = src["text"].encode(), "text/plain", Path(f"{key}.txt")
        else:
            text, spans = stand_in(spec, key, claims[key])
            data, mime, file = text.encode(), "text/plain", Path(f"{key}.txt")
            pins |= spans
        await intake(
            s,
            key,
            data,
            mime,
            origin,
            collector,
            src["doc_type"],
            src["sensitivity"],
            context | {"file": file.name},
        )
        text_of.setdefault(key, key)
        if src.get("transcript"):
            said = file_of(root, src["transcript"])
            text_of[key] = await transcribe(
                s, key, mime, said, f"acceptance/cases/{root.name}/files/{said.name}"
            )
        await settle(s, key)
        await session.commit()

    # A sheet's rows are separate mentions, as the app's sheet intake writes them:
    # a record's first row is the record, each later row its own subject, and the
    # desk that kept the log has already joined them.
    first_row = {}
    for c in spec["claims"]:
        if (row := (c.get("pin") or {}).get("row")) and c["record"] in records:
            first_row[c["record"]] = min(first_row.get(c["record"], row), row)

    def subject_of(c: dict, doc: str) -> str:
        if c["record"] not in records:
            return f"blob:{s.blobs[doc]}"
        row = (c.get("pin") or {}).get("row")
        if row is None or row == first_row[c["record"]]:
            return person(inv, c["record"])
        return person(inv, f"{c['record']}/row {row}")

    written, accepted, rows = {}, [], {}
    for key, mine in claims.items():
        doc = text_of[key]
        run = await s.run(extractor, "extract", [doc], **LLM)
        for c in mine:
            pin = c.get("pin") or pins.get(c["key"])
            needle, after, at, on = None, "", None, doc
            if pin is None:
                needle = c["value"]
            elif "text" in pin:
                needle, after = pin["text"], pin.get("after", "")
            else:
                at, on = pin, key  # a cell or a box sits in the file itself
            subject = subject_of(c, doc)
            st = written[c["key"]] = await s.say(
                run,
                on,
                subject,
                c["prop"],
                value_of(c),
                needle,
                after,
                valid=[c["from"], c["to"] or c["from"]] if c["from"] else None,
                evidence=[E.PRINTED_TEXT],
                at=at,
            )
            if c["tier"] == "accepted":
                accepted.append(st)
            if subject.startswith("person:") and subject != person(inv, c["record"]):
                rows.setdefault((c["record"], subject), st)
        await s.done(run, len(mine))
        await session.commit()
    for st in accepted:
        await s.review(analyst, st, "accept", "planted as accepted in the case study")

    # Each planted cluster: records a person already joined, one link per member.
    names = {
        c["record"]: written[c["key"]] for c in spec["claims"] if c["prop"] == "name"
    }
    joiner = await s.principal("case-linker")
    for cluster in spec.get("clusters") or []:
        first, *rest = cluster
        run = await s.run(joiner, "join", [records[first]["source"]])
        for other in rest:
            basis = [f"statement:{names[r].id}" for r in (other, first)]
            link = await s.say(
                run,
                records[other]["source"],
                person(inv, other),
                Prop.SAME_AS,
                {"person": person(inv, first).removeprefix("person:"), "basis": basis},
                evidence=[E.CORROBORATING_FIELD],
            )
            await s.review(analyst, link, "accept", "joined earlier by this desk")
        await s.done(run, len(rest))
    by_record = defaultdict(list)
    for (record, subject), st in rows.items():
        by_record[record].append((subject, st))
    for record, mentions in by_record.items():
        run = await s.run(joiner, "join", [records[record]["source"]])
        for subject, st in mentions:
            basis = [f"statement:{x.id}" for x in (st, names[record])]
            link = await s.say(
                run,
                records[record]["source"],
                subject,
                Prop.SAME_AS,
                {"person": person(inv, record).removeprefix("person:"), "basis": basis},
                evidence=[E.CORROBORATING_FIELD],
            )
            await s.review(analyst, link, "accept", "one name in one log")
        await s.done(run, len(mentions))
    await session.commit()

    sites = await import_places(s, spec)
    for c in spec["claims"]:
        if c["place"] in sites and c["prop"] in LOCATED:
            await append(
                session,
                resolve.confirmation(
                    written[c["key"]].id, sites[c["place"]].id, c["value"], analyst.id
                ),
                sign=s.assertion,
            )
    await session.commit()

    out = {
        "investigation": inv,
        "sources": len(spec["sources"]),
        "claims": len(written),
        "clusters": len(spec.get("clusters") or []),
    }
    matches = await session.scalar(
        select(func.count())
        .select_from(Statement)
        .where(
            Statement.prop == Prop.MEDIA_MATCH,
            Statement.subject.in_([f"blob:{sha}" for sha in s.blobs.values()]),
        )
    )
    if matches:
        out["match proposals"] = matches
    for name in agents_for(spec):
        got = await run_agent(session, name, inv, owner)
        out[f"{name} proposals"] = len(got.get("written", got.get("items", [])))
    if batch:
        run = await brief.queue(session, batch, owner.id, read.FIXED)
        await session.commit()
        await process(ext.async_session_factory, run.id)
        out["briefs"] = 1
    if spec.get("conclusion"):
        await conclude(s, spec, inv, written, accepted, analyst)
        out["signed conclusion"] = 1
    return out


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


async def same_person(s: Seeder, spec: dict, inv: str) -> list:
    """The linker's proposals that join two records the truth plants as one
    person, read before any is accepted. A proposal with a namesake is left."""
    groups, _ = await people.identity(s.s)

    def cluster(subject: str) -> str:
        p = people.pid(subject)
        return min(groups.get(p, {p}))

    true = {
        frozenset(cluster(person(inv, r)) for r in pair)
        for pair in spec["truth"]["same_person"]
    }
    subjects = [person(inv, r) for r in spec["records"]]
    return [
        st
        for st, v in await pending(s.s, Prop.SAME_AS, subjects)
        if frozenset({cluster(st.subject), cluster(f"person:{v['person']}")}) in true
    ]


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


async def conclude(s: Seeder, spec: dict, inv: str, written, accepted, analyst):
    """The case's signed conclusion. The analyst first accepts exactly what it
    rests on: claims by key, the linker's proposals for the planted same-person
    pairs ("same_person"), the strong matches inside the planted copies
    ("copies"). "links" cites every accepted same-person link. A match proposal
    cannot be cited, so "copies" is accepted and not cited. With `peer_review` a
    second analyst signs agreement."""
    c = spec["conclusion"]
    cite, accept = [], []
    for item in c["rests_on"]:
        if item == "links":
            cite.append(item)
        elif item == "same_person":
            accept += await same_person(s, spec, inv)
        elif item == "copies":
            accept += await copies(s, spec)
        else:
            st = written[item]
            cite.append(str(st.id))
            if st not in accepted:
                accept.append(st)
    for st in accept:
        await s.review(analyst, st, "accept", "what the case's conclusion rests on")
    await s.s.commit()
    form = {k: c[k] for k in ("text", "standard", "alternatives", "not_established")}
    rows = await conclusions.seed(
        s.s, inv, form | {"rests_on": cite}, analyst.id, s.assertion
    )
    if c.get("peer_review"):
        # A second Seeder: each holds the passkey of the analyst it made.
        other = Seeder(s.s)
        reviewer = await colleague(other, inv, *REVIEWER)
        await conclusions.seed_peer_review(
            s.s, rows[0], analyst.id, reviewer.id, other.assertion
        )
    await s.s.commit()
    return rows[0]
