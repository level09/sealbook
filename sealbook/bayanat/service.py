"""Bring a team's Bayanat work into a case. Each bulletin becomes a text item (its
acquisition names the export it came from), its media files become items of their own, and what its
analysts decided (analyst title, labels, verified labels) is proposed as that item's
first pass by one signed import run, for a person to accept. Nothing is accepted on
import. Callers check access; this module reads what they allow."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import func, select

from sealbook import blobs
from sealbook.bayanat import export
from sealbook.ingest import service as ingest
from sealbook.ledger import chain, write
from sealbook.ledger.contracts import ASSESSED_PROPS, Prop, StatementIn, Tier
from sealbook.ledger.models import Acquisition, Blob, Run, Statement, StatementTier
from sealbook.method import alleged_labels, observed_labels
from sealbook.settings import Config

TOOL = "import_bayanat"
TEXT = "text/plain; charset=utf-8"
# ponytail: the import runs inside the request; past a few hundred bulletins with
# media, move it to a worker run that reports progress.
MAX_BULLETINS = 2000
ARABIC = re.compile(r"[؀-ۿ]")


class Conflict(ValueError):
    """The request is valid but the state does not allow it (409)."""


def _lang(text: str) -> str:
    return "ar" if ARABIC.search(text) else "en"


def _case(acq: Acquisition) -> str:
    case = (acq.context or {}).get("investigation")
    if not case:
        raise ValueError("this export was added without a case; add it to a case first")
    return case


async def read(blob: Blob) -> export.Export:
    data = await asyncio.to_thread(blobs.read, blob, Config.BLOB_DIR)
    return await asyncio.to_thread(export.read, data)


async def run_of(db, acq: Acquisition) -> Run | None:
    return await db.scalar(
        select(Run).where(
            Run.tool == TOOL, Run.inputs["acquisition"].astext == str(acq.id)
        )
    )


def _media(b: dict) -> list[dict]:
    return [m for m in b.get("medias") or [] if m.get("filename")]


async def preview(db, acq: Acquisition, blob: Blob) -> dict:
    """What confirming would bring in. Writes nothing."""
    ex = await read(blob)
    known = set(observed_labels()) | set(alleged_labels())
    shas = {
        b["id"]: hashlib.sha256(export.render(b).text.encode()).hexdigest()
        for b in ex.bulletins
    }
    here = set(
        await db.scalars(select(Blob.sha256).where(Blob.sha256.in_(shas.values())))
    )
    labels = [
        export.label_name(lb)
        for b in ex.bulletins
        for lb in [*(b.get("labels") or []), *(b.get("verLabels") or [])]
    ]
    media = [m["filename"] for b in ex.bulletins for m in _media(b)]
    statuses: dict[str, int] = {}
    for b in ex.bulletins:
        key = b.get("status") or "No status"
        statuses[key] = statuses.get(key, 0) + 1
    run = await run_of(db, acq)
    return {
        "case": _case(acq),
        "bulletins": len(ex.bulletins),
        "already_here": sum(s in here for s in shas.values()),
        "worked": sum(export.worked(b) for b in ex.bulletins),
        "analyst_titles": sum(bool(b.get("sjac_title")) for b in ex.bulletins),
        "statuses": dict(sorted(statuses.items(), key=lambda kv: -kv[1])),
        "labels": len(labels),
        "labels_unknown": sorted({lb for lb in labels if lb not in known}),
        "media": len(media),
        "media_in_file": sum(m in ex.media for m in media),
        "too_many": len(ex.bulletins) > MAX_BULLETINS,
        "imported": _run_row(run),
    }


def _run_row(run: Run | None) -> dict | None:
    return run and {"id": str(run.id), "counts": run.inputs.get("counts")}


def _origin(b: dict) -> str:
    try:
        return ingest.make_origin("url", b.get("source_link") or "")
    except ValueError:
        return ingest.make_origin("device", f"Bayanat bulletin {b['id']}")


def _proposals(b: dict, r: export.Rendered) -> list[tuple[Prop, dict, dict]]:
    """(prop, value, span) for what Bayanat's analysts decided on this bulletin."""
    imported = {"from": "Bayanat", "bulletin": b["id"], "status": b.get("status")}
    imported |= export.people(b)
    out = []
    title = (b.get("sjac_title") or "").strip()
    title_ar = (b.get("sjac_title_ar") or "").strip()
    if title or title_ar:
        quote = title or title_ar
        span = r.spans[("title", None) if title else ("title_ar", None)]
        value = {"title": title, "title_ar": title_ar, "text": quote}
        out.append((Prop.TITLE, value | {"lang": _lang(quote)}, span))
    for kind, basis in (("label", "alleged"), ("observed", "observed")):
        for (k, name), span in r.spans.items():
            if k == kind:
                value = {"label": name, "basis": basis, "text": name, "lang": "en"}
                out.append((Prop.LABEL, value, span))
    return [(p, v | {"imported": imported}, s) for p, v, s in out]


async def confirm(db, acq: Acquisition, blob: Blob, person) -> dict:
    """Import every bulletin of the export once, in one signed run."""
    if await run_of(db, acq):
        raise Conflict("this export is already imported")
    case = _case(acq)
    ex = await read(blob)
    if len(ex.bulletins) > MAX_BULLETINS:
        raise ValueError(
            f"this export has {len(ex.bulletins)} bulletins; import at most "
            f"{MAX_BULLETINS} at a time"
        )
    agent = await ingest.system_agent(db, "importer")
    key = Ed25519PrivateKey.generate()
    run = Run(
        principal_id=agent.id,
        tool=TOOL,
        route="local",
        inputs={"blobs": [blob.sha256], "acquisition": str(acq.id)},
        status="running",
        pubkey=key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
    )
    db.add(run)
    await db.flush()

    def sign(h: str) -> dict:
        return {"kind": "ed25519", "sig": key.sign(h.encode()).hex()}

    async def state(subject, prop, value, span_blob, span):
        return await write.append(
            db,
            StatementIn(
                subject=subject,
                prop=prop,
                value=value,
                author_id=agent.id,
                run_id=run.id,
                span_blob=span_blob,
                span=span,
            ),
            sign=sign,
        )

    batch = str(uuid.uuid4())
    counts = {"bulletins": 0, "new": 0, "proposals": 0, "media": 0, "media_missing": 0}
    written = []
    for b in ex.bulletins:
        r = export.render(b)
        about = {
            "export": blob.sha256,
            "bulletin": b["id"],
            "status": b.get("status"),
            "worked": export.worked(b),
        } | export.people(b)
        common = {
            "blob_dir": Config.BLOB_DIR,
            "origin": _origin(b),
            "collector_id": person.id,
            "sensitivity": acq.sensitivity,
        }
        item = await ingest.ingest_bytes(
            db,
            r.text.encode(),
            mime=TEXT,
            context={
                "investigation": case,
                "batch": batch,
                "filename": f"Bayanat bulletin {b['id']}",
                "bayanat": about,
            },
            **common,
        )
        counts["bulletins"] += 1
        sha = item["sha256"]
        if not item["new_blob"]:
            continue
        counts["new"] += 1
        for prop, value, span in _proposals(b, r):
            written.append(await state(f"blob:{sha}", prop, value, sha, span))
            counts["proposals"] += 1
        for m in _media(b):
            if m["filename"] not in ex.media:
                counts["media_missing"] += 1
                continue
            data = await asyncio.to_thread(ex.read_media, m["filename"])
            mime = mimetypes.guess_type(m["filename"])[0] or "application/octet-stream"
            await ingest.ingest_bytes(
                db,
                data,
                mime=mime,
                doc_type=ingest.suggest_doc_type(m["filename"], mime)["doc_type"],
                context={
                    "investigation": case,
                    "batch": batch,
                    "filename": m["filename"],
                    "bayanat": about | {"item": sha, "media": m["filename"]},
                },
                **common,
            )
            counts["media"] += 1
    run.inputs = run.inputs | {"batch": batch, "counts": counts}
    await write.seal_run(
        db, run, output_hash=chain.sha256(chain.canon([str(s.id) for s in written]))
    )
    return {"run": str(run.id), "batch": batch, "counts": counts}


async def open_proposals(db, run_id: uuid.UUID) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(Statement)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.run_id == run_id,
            Statement.prop.in_(ASSESSED_PROPS),
            StatementTier.tier == Tier.UNCONFIRMED,
        )
    )
