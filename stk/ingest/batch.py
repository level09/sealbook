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
from stk.ingest import service
from stk.ledger.contracts import EXTRACTED_PROPS, Prop
from stk.ledger.models import Acquisition, Blob, Principal, Run, Statement
from stk.settings import Config
from stk.sheets import parse as sheets
from stk.sheets.models import DatasetVersion
from stk.sources.data import title

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
    # A local model server that is down or busy reads as the route's exception text.
    if " unreachable: " in (run.error or "") or (run.error or "").startswith(
        "ReadTimeout"
    ):
        detail = "The model on this server did not answer in time; try again later."
    return {
        "run": str(run.id),
        "tool": run.tool,
        "stage": STAGE_OF.get(run.tool, "linked"),
        "message": f"{LABEL.get(run.tool, run.tool)} did not finish. The file is "
        "hashed and stored, so nothing is lost.",
        "detail": detail[:300],
        "retryable": run.tool in RETRYABLE,
    }


READS = {"ocr": "the text in pictures", "extract": "text", "transcribe": "speech"}


async def _no_model(db: AsyncSession, acq: Acquisition, mime: str) -> str | None:
    """Why a new item that needs reading has no reading run: the decision
    queue_processing makes, against the settings now. Nothing queues it later on
    its own, so a model set up since then is said too."""
    tool = service._tool(mime)
    if tool is None:
        return None
    if (
        tool == "transcribe"
        and Config.SPEECH_MODEL
        or (
            tool != "transcribe"
            and (await service._route(db, tool, acq))[0] is not None
        )
    ):
        return (
            "Not read: it arrived before a model could take it. A model is set up "
            "now; an administrator can queue the reading again."
        )
    if tool == "transcribe":
        why = "speech reading is not set up on this server"
    elif acq.sensitivity == "sensitive":
        why = (
            "sensitive items are read only by a model on this server, and none is "
            f"set up to read {READS[tool]}"
        )
    else:
        why = f"no model allowed for this item is set up to read {READS[tool]}"
    return f"Waiting for a model: {why}. The file is stored and nothing is lost."


async def _ahead(db: AsyncSession, run: Run) -> int:
    """Runs of the same tool and route the worker takes before this one."""
    return await db.scalar(
        select(func.count())
        .select_from(Run)
        .where(
            Run.tool == run.tool,
            Run.route == run.route,
            Run.status.in_(("queued", "running")),
            Run.id != run.id,
            Run.created_at <= run.created_at,
        )
    )


def _queue_words(tool: str, ahead: int) -> str:
    where = "the model on this server" if tool != "transcribe" else "speech reading"
    return (
        f"Waiting for {where}, which is busy with {ahead} other "
        f"item{'' if ahead == 1 else 's'}. It continues on its own."
    )


async def _originals(db: AsyncSession, shas: set[str]) -> dict[str, dict]:
    """The first acquisition of each blob anywhere: what a repeat upload points at."""
    rows = (
        await db.execute(
            select(Acquisition, Principal.name)
            .join(Principal, Principal.id == Acquisition.collector_id)
            .where(Acquisition.blob_sha256.in_(shas))
            .distinct(Acquisition.blob_sha256)
            .order_by(Acquisition.blob_sha256, Acquisition.at, Acquisition.id)
        )
    ).all()
    return {
        a.blob_sha256: {
            "acquisition": str(a.id),
            "title": title(
                a.context.get("title"),
                a.context.get("file") or a.context.get("filename"),
                a.doc_type,
            ),
            "at": a.at.isoformat(),
            "origin": a.origin,
            "by": by,
            "investigation": a.context.get("investigation"),
        }
        for a, by in rows
    }


async def _captures(db: AsyncSession, batch: str) -> list[dict]:
    """Web captures queued into this batch from search results: one row each, whose
    files arrive as ordinary items carrying the run id."""
    rows = (
        await db.execute(
            select(Run)
            .where(Run.tool == "capture", Run.inputs["batch"].astext == batch)
            .order_by(Run.created_at, Run.id)
        )
    ).scalars()
    return [
        {
            "run": str(r.id),
            "page": r.inputs["page"],
            "title": r.inputs.get("title"),
            "kind": r.inputs.get("kind"),
            "doc_type": r.inputs.get("doc_type"),
            "sensitivity": r.inputs.get("sensitivity"),
            "status": r.status,
            "error": (r.error or "").split(": ", 1)[-1][:300] or None,
        }
        for r in rows
    ]


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
    originals = await _originals(db, shas) if shas else {}
    read_runs = {
        r.id: sha for sha, rs in runs.items() for r in rs if r.tool in READ_TOOLS
    }
    claims = (
        dict(
            (
                await db.execute(
                    select(Statement.run_id, func.count())
                    .where(
                        Statement.run_id.in_(list(read_runs)),
                        Statement.prop.in_([str(p) for p in EXTRACTED_PROPS]),
                    )
                    .group_by(Statement.run_id)
                )
            ).all()
        )
        if read_runs
        else {}
    )
    confirmed = (
        set(
            (
                await db.execute(
                    select(DatasetVersion.blob_sha256).where(
                        DatasetVersion.blob_sha256.in_(shas)
                    )
                )
            ).scalars()
        )
        if shas
        else set()
    )

    items = []
    for a in acqs:
        blob, rs = blobs[a.blob_sha256], runs.get(a.blob_sha256, [])
        duplicate = originals[a.blob_sha256]["acquisition"] != str(a.id)
        states = {"hashed": "done"}
        for stage in ("c2pa", "read", "fingerprint", "embed"):
            states[stage] = _stage([r for r in rs if STAGE_OF.get(r.tool) == stage])
        states["linked"] = "done" if all(r.status == "done" for r in rs) else "queued"
        pending = any(r.status in ("queued", "running") for r in rs)
        sheet = blob.mime in sheets.MIMES
        wait = None
        reads = [r for r in rs if r.tool in READ_TOOLS]
        if not duplicate and not reads and not sheet:
            wait = await _no_model(db, a, blob.mime)
        queued = next((r for r in reads if r.status == "queued"), None)
        if queued and (ahead := await _ahead(db, queued)):
            wait = _queue_words(queued.tool, ahead)
        if wait:
            states["read"] = "waiting"
        items.append(
            {
                "acquisition": str(a.id),
                "name": a.context.get("filename")
                or a.context.get("final_url")
                or a.origin,
                "sha256": a.blob_sha256,
                "mime": blob.mime,
                "sheet": sheet,
                "sheet_confirmed": a.blob_sha256 in confirmed,
                "size": blob.size,
                "doc_type": a.doc_type,
                "sensitivity": a.sensitivity,
                "origin": a.origin,
                "investigation": a.context.get("investigation"),
                "duplicate": duplicate,
                "original": originals[a.blob_sha256] if duplicate else None,
                "capture": a.context.get("capture", {}).get("run"),
                "role": a.context.get("capture", {}).get("role"),
                "stages": states,
                "read_progress": read_progress.get(a.blob_sha256),
                "claims": sum(claims.get(r.id, 0) for r in reads),
                "waiting": wait,
                "pending": pending,
                "failures": [_failure(r) for r in rs if r.status == "failed"],
            }
        )
    captures = await _captures(db, batch)
    done = bool(items or captures) and not any(i["pending"] for i in items)
    done = done and not any(c["status"] in ("queued", "running") for c in captures)
    return {
        "batch": batch,
        "items": items,
        "captures": captures,
        "done": done,
        "summary": await _summary(db, acqs, items, runs) if done and items else None,
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
