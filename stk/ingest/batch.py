"""Where an intake batch stands: per item stage states read from the run table, and
what the batch found once nothing is left to run. A batch is the acquisitions
sharing context["batch"]. Runs are found by blob, so an item whose bytes were known
already shows the state of the earlier runs instead of an empty list."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stk.discovery.models import FindingKey, IndexProgress
from stk.ledger.contracts import EXTRACTED_PROPS, Prop
from stk.ledger.models import Acquisition, Blob, Run, Statement
from stk.sheets import parse as sheets

STAGES = ("hashed", "c2pa", "read", "fingerprint", "embed", "linked")
READ_TOOLS = ("ocr", "extract", "transcribe", "pdf", "extract_sheet")
STAGE_OF = {
    "c2pa_verify": "c2pa",
    "fingerprint": "fingerprint",
    "embed": "embed",
    **dict.fromkeys(READ_TOOLS, "read"),
}
# Tools whose failure a person can retry: the ones a worker runs.
RETRYABLE = frozenset(READ_TOOLS) | {
    "fingerprint",
    "embed",
    "index",
    "thumbnail",
    "geotag",
    "capture",
}
LABEL = {
    "ocr": "Reading the text in the image",
    "extract": "Reading the text",
    "transcribe": "Reading the speech",
    "pdf": "Reading the PDF",
    "extract_sheet": "Reading the sheet rows",
    "fingerprint": "Fingerprinting",
    "embed": "Embedding for search",
    "index": "Indexing for search",
    "thumbnail": "Making the thumbnails",
    "geotag": "Reading where it was taken",
    "capture": "Capturing the web page",
}
_RANK = {"failed": 4, "running": 3, "queued": 2, "done": 1}


def _stage(runs: list[Run]) -> str:
    if not runs:
        return "skipped"
    return max((r.status for r in runs), key=lambda s: _RANK.get(s, 0))


def _failure(run: Run) -> dict:
    # run.error is "ExceptionName: message"; the name means nothing to an analyst.
    # Paths on the server mean nothing to an analyst and are not shown.
    detail = re.sub(r"'?/\S+'?", "", (run.error or "").split(": ", 1)[-1])
    # A failed ffmpeg or ffprobe call reads as its whole command line.
    if (run.error or "").startswith("CalledProcessError"):
        detail = "The media tools could not decode this file."
    return {
        "run": str(run.id),
        "tool": run.tool,
        "stage": STAGE_OF.get(run.tool, "linked"),
        "message": f"{LABEL.get(run.tool, run.tool)} did not finish. The file is "
        "hashed and stored, so nothing is lost.",
        "detail": detail[:300],
        "retryable": run.tool in RETRYABLE,
    }


async def status(db: AsyncSession, batch: str) -> dict:
    acqs = (
        (
            await db.execute(
                select(Acquisition)
                .where(Acquisition.context["batch"].astext == batch)
                .order_by(Acquisition.at, Acquisition.id)
            )
        )
        .scalars()
        .all()
    )
    shas = {a.blob_sha256 for a in acqs}
    blobs = {
        b.sha256: b
        for b in (await db.execute(select(Blob).where(Blob.sha256.in_(shas)))).scalars()
    }
    runs: dict[str, list[Run]] = {}
    for r in (
        await db.execute(select(Run).where(Run.inputs["blobs"][0].astext.in_(shas)))
    ).scalars():
        runs.setdefault(r.inputs["blobs"][0], []).append(r)
    # How far a long read (PDF pages, transcribed ms) has got, for the read stage.
    read_progress = {
        p.source_sha256: {"completed": p.completed, "total": p.total, "unit": p.unit}
        for p in (
            await db.execute(
                select(IndexProgress).where(
                    IndexProgress.source_sha256.in_(shas),
                    IndexProgress.tool.in_(READ_TOOLS),
                    IndexProgress.part == "",
                )
            )
        ).scalars()
    }
    # The earliest acquisition of a blob anywhere is the original; later ones repeat it.
    first = dict(
        (
            await db.execute(
                select(Acquisition.blob_sha256, func.min(Acquisition.at))
                .where(Acquisition.blob_sha256.in_(shas))
                .group_by(Acquisition.blob_sha256)
            )
        ).all()
    )
    originals: dict[str, uuid.UUID] = {}
    for a in acqs:
        if a.at == first[a.blob_sha256]:
            originals.setdefault(a.blob_sha256, a.id)

    items = []
    for a in acqs:
        blob, rs = blobs[a.blob_sha256], runs.get(a.blob_sha256, [])
        states = {"hashed": "done"}
        for stage in ("c2pa", "read", "fingerprint", "embed"):
            states[stage] = _stage([r for r in rs if STAGE_OF.get(r.tool) == stage])
        states["linked"] = "done" if all(r.status == "done" for r in rs) else "queued"
        pending = any(r.status in ("queued", "running") for r in rs)
        items.append(
            {
                "acquisition": str(a.id),
                "name": a.context.get("filename")
                or a.context.get("final_url")
                or a.origin,
                "sha256": a.blob_sha256,
                "mime": blob.mime,
                "sheet": blob.mime in sheets.MIMES,
                "size": blob.size,
                "duplicate": originals.get(a.blob_sha256) != a.id,
                "stages": states,
                "read_progress": read_progress.get(a.blob_sha256),
                "pending": pending,
                "failures": [_failure(r) for r in rs if r.status == "failed"],
            }
        )
    done = bool(items) and not any(i["pending"] for i in items)
    return {
        "batch": batch,
        "items": items,
        "done": done,
        "summary": await _summary(db, acqs, items, runs) if done else None,
    }


async def _summary(db, acqs, items, runs) -> dict:
    new = {i["sha256"] for i in items if not i["duplicate"]}
    read_runs = [
        r.id
        for sha in new
        for r in runs.get(sha, [])
        if r.tool in READ_TOOLS and r.status == "done"
    ]
    claims = (
        await db.scalar(
            select(func.count())
            .select_from(Statement)
            .where(
                Statement.run_id.in_(read_runs),
                Statement.prop.in_([str(p) for p in EXTRACTED_PROPS]),
            )
        )
        if read_runs
        else 0
    )
    matches = await db.scalar(
        select(func.count())
        .select_from(Statement)
        .where(
            Statement.prop == str(Prop.MEDIA_MATCH),
            Statement.subject.in_([f"blob:{s}" for s in new]),
        )
    )
    case = acqs[0].context.get("investigation")
    findings = 0
    if case:
        findings = await db.scalar(
            select(func.count())
            .select_from(FindingKey)
            .join(Run, Run.id == FindingKey.run_id)
            .where(
                Run.inputs["investigation"].astext == case,
                FindingKey.created_at >= min(a.at for a in acqs),
            )
        )
    return {
        "items": len(items),
        "new_items": len(new),
        "exact_duplicates": sum(i["duplicate"] for i in items),
        "possible_matches": matches,
        "claims": claims,
        "findings": findings,
        "failed": sum(bool(i["failures"]) for i in items),
        "at": datetime.now(UTC).isoformat(),
    }


async def retry(db: AsyncSession, run_id: uuid.UUID) -> Run | None:
    """Queue a failed run again. The queue state is mutable and outside the chain;
    attempts and events keep the history."""
    run = await db.get(Run, run_id)
    if run is None or run.status != "failed" or run.tool not in RETRYABLE:
        return None
    run.status, run.error, run.lease_until, run.finished_at = "queued", None, None, None
    run.available_at = datetime.now(UTC)
    await db.flush()
    return run
