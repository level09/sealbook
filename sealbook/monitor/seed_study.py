"""`stk demo-seed --case <slug>`: write a case study from
acceptance/cases/<slug>/case.json through the write path the product uses, so it
opens on the real pages.

The case file is the one the agent evaluation scores (docs/case-studies.md). Each
source is ingested from its file, or from a text stand-in listing its claims when it
has none. Each claim is an extraction statement pinned where its `pin` says: a sheet
cell, a box on a scan, or a quote found in the text. Claims planted as accepted are
accepted by a case analyst with a passkey signature, each planted cluster is a
same-person link that analyst accepted, and planted places are confirmed by the same
analyst. Then the linker runs on the fixed answer, so its proposals wait in Review
for a person. Nothing is accepted on the linker's word."""

from __future__ import annotations

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

from sealbook import blobs
from sealbook.agents import linker, read
from sealbook.cases.service import ensure as ensure_case
from sealbook.ingest.service import system_agent
from sealbook.ledger.contracts import EvidenceKind as E
from sealbook.ledger.contracts import Prop
from sealbook.ledger.models import Blob
from sealbook.ledger.write import append, principal_for_user
from sealbook.monitor.data import LOCATED
from sealbook.monitor.seed import Seeder, seeded
from sealbook.monitor.seed_full import LLM, intake, users
from sealbook.places import cli as places
from sealbook.places import resolve
from sealbook.places.models import Place
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


def path_of(slug: str) -> Path:
    return CASES / slug / "case.json"


def person(investigation: str, record: str) -> str:
    key = f"sealbook-case/{investigation}/{record}"
    return f"person:{uuid.uuid5(uuid.NAMESPACE_URL, key)}"


def value_of(c: dict) -> dict:
    """The statement value extraction writes for a planted claim."""
    text = c["value"]
    lang = "ar" if ARABIC.search(text) else "en"
    if c["prop"] in (Prop.BIRTH_DATE, Prop.DOCUMENT_DATE):
        return {"date": text}
    if c["prop"] == Prop.EVENT:
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


async def import_places(s: Seeder, spec: dict) -> dict:
    """The planted places as person-recorded sites (no GeoNames id), by key."""
    if not spec["places"]:
        return {}
    rows = [
        [
            "",
            p.get("name_ar") or p["name_en"],
            p["name_en"],
            "",
            str(p["lat"]),
            str(p["lon"]),
            "S",
            "FAC",
            *[""] * 11,
        ]
        for p in spec["places"].values()
    ]
    with tempfile.TemporaryDirectory() as tmp:
        tsv = Path(tmp) / "places.tsv"
        tsv.write_text("".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
        await places.import_file(s.s, tsv)
    found = {
        p.name_ar: p
        for p in await s.s.scalars(select(Place).where(Place.source == "person"))
    }
    return {
        k: found[p.get("name_ar") or p["name_en"]] for k, p in spec["places"].items()
    }


async def seed_study(session, app, path: Path) -> dict:
    """Additive: refuses when the case's investigation already has a source."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    inv = spec["investigation"]
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
    analyst = await s.analyst("case-analyst", inv)
    extractor = await system_agent(session, "extractor")
    collector = await principal_for_user(session, owner.id, owner.name or owner.email)

    claims = defaultdict(list)
    for c in spec["claims"]:
        claims[spec["records"][c["record"]]["source"]].append(c)
    pins = {}
    for key, src in spec["sources"].items():
        if src.get("file"):
            file = root / src["file"]
            data, mime = file.read_bytes(), MIME[file.suffix.lower()]
        else:
            text, spans = stand_in(spec, key, claims[key])
            data, mime, file = text.encode(), "text/plain", Path(f"{key}.txt")
            pins |= spans
        await intake(
            s,
            key,
            data,
            mime,
            src.get("origin") or f"channel:case-study/{root.name}",
            collector,
            src["doc_type"],
            src["sensitivity"],
            {"title": src["title"], "file": file.name, "fictional": True},
        )
        await session.commit()

    written, accepted = {}, []
    for key, mine in claims.items():
        run = await s.run(extractor, "extract", [key], **LLM)
        for c in mine:
            pin = c.get("pin") or pins.get(c["key"])
            needle, after, at = None, "", None
            if pin and "text" in pin:
                needle, after = pin["text"], pin.get("after", "")
            else:
                at = pin
            st = written[c["key"]] = await s.say(
                run,
                key,
                person(inv, c["record"]),
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
        await s.done(run, len(mine))
        await session.commit()
    for st in accepted:
        await s.review(analyst, st, "accept", "planted as accepted in the case study")

    # Each planted cluster: records a person already joined, one link per member.
    names = {
        c["record"]: written[c["key"]] for c in spec["claims"] if c["prop"] == "name"
    }
    joiner = await s.principal("case-linker")
    for cluster in spec["clusters"]:
        first, *rest = cluster
        run = await s.run(joiner, "join", [spec["records"][first]["source"]])
        for other in rest:
            basis = [f"statement:{names[r].id}" for r in (other, first)]
            link = await s.say(
                run,
                spec["records"][other]["source"],
                person(inv, other),
                Prop.SAME_AS,
                {"person": person(inv, first).removeprefix("person:"), "basis": basis},
                evidence=[E.CORROBORATING_FIELD],
            )
            await s.review(analyst, link, "accept", "joined earlier by this desk")
        await s.done(run, len(rest))
    await session.commit()

    sites = await import_places(s, spec)
    for c in spec["claims"]:
        if c["place"] and c["prop"] in LOCATED:
            await append(
                session,
                resolve.confirmation(
                    written[c["key"]].id, sites[c["place"]].id, c["value"], analyst.id
                ),
                sign=s.assertion,
            )
    await session.commit()

    run = await linker.queue(session, inv, owner.id, read.FIXED)
    await session.commit()
    await process(ext.async_session_factory, run.id)
    await session.refresh(run)
    out = orjson.loads(
        blobs.read(await session.get(Blob, run.output_hash), Config.BLOB_DIR)
    )
    return {
        "investigation": inv,
        "sources": len(spec["sources"]),
        "claims": len(written),
        "clusters": len(spec["clusters"]),
        "linker proposals": len(out["written"]),
    }
