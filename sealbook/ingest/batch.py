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

from sealbook.discovery.models import FindingKey, IndexProgress
from sealbook.ingest import service
from sealbook.ledger import write
from sealbook.ledger.contracts import EXTRACTED_PROPS, Prop, Route
from sealbook.ledger.gate import resolve
from sealbook.ledger.models import Acquisition, Blob, Principal, Run, Statement
from sealbook.settings import Config
from sealbook.sheets import parse as sheets
from sealbook.sheets.models import DatasetVersion
from sealbook.sources.data import title

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
    error = run.error or ""
    detail = re.sub(r"'?/\S+'?", "", error.split(": ", 1)[-1])
    # A failed ffmpeg or ffprobe call reads as its whole command line.
    if error.startswith("CalledProcessError"):
        detail = "The media tools could not decode this file."
    # A model service that is down, busy or refusing reads as its exception text.
    if " unreachable: " in error or error.startswith(("ReadTimeout", "Connect")):
        detail = "The model did not answer in time; try again later."
    elif error.startswith("RouteError"):
        detail = next(
            (words for part, words in ROUTE_ERRORS if part in error),
            "The model service did not finish the request; try again later.",
        )
    return {
        "run": str(run.id),
        "tool": run.tool,
        "stage": STAGE_OF.get(run.tool, "linked"),
        "message": f"{LABEL.get(run.tool, run.tool)} did not finish. The file is "
        "hashed and stored, so nothing is lost.",
        "detail": detail[:300],
        "retryable": run.tool in RETRYABLE,
    }


# What sealbook.runs.routes raises, in words; the setting names stay in the log.
ROUTE_ERRORS = (
    (" is not set", "The model service for this work is not set up on this server."),
    ("not allowed", "The rules no longer allow this model for this source."),
    ("stopped early", "The model stopped before it finished; try again."),
    (" returned ", "The model service refused the request; try again later."),
)
# What each read tool does, for "no model is set up to ...".
WHAT = {
    "ocr": "read text in pictures",
    "extract": "read claims from text",
    "extract_sheet": "read sheet rows",
    "transcribe": "read speech",
}
ROUTE_WORDS = {
    "local": "a model on a private server",
    "cloud-zdr": "a hosted model that keeps no data",
    "cloud": "a hosted model",
}
LATE = (
    "Not read yet: it arrived before a model was set up for this work. One is set "
    "up now, so it can be read."
)


async def no_model(db: AsyncSession, acq: Acquisition, tool: str) -> str | None:
    """Why no model can take `tool` for this source now, in words from the policy
    table and the models set up, or None when one can. The decision is the one the
    queueing helpers make; this only says it. A source no policy row covers is
    never read, and says so instead of failing the page that asks."""
    try:
        return await _no_model_for(db, acq, tool)
    except LookupError:
        return (
            f"No rule in the policy table covers {acq.sensitivity} sources of this "
            "type, so no model may read it. An administrator adds one."
        )


async def _no_model_for(db: AsyncSession, acq: Acquisition, tool: str) -> str | None:
    if tool == "transcribe":
        policy = resolve(await write.policies(db), tool, acq.doc_type, acq.sensitivity)
        if not Config.SPEECH_MODEL:
            return "Speech reading is not set up on this server."
        if Route.LOCAL not in policy.routes:
            return (
                f"The rules for {acq.sensitivity} sources do not allow speech reading."
            )
        return None
    if (await service._route(db, tool, acq))[0] is not None:
        return None
    policy = resolve(await write.policies(db), tool, acq.doc_type, acq.sensitivity)
    if not policy.routes:
        return f"The rules for {acq.sensitivity} sources let no model {WHAT[tool]}."
    may = " or ".join(ROUTE_WORDS.get(r, r) for r in policy.routes)
    return (
        f"No model is set up to {WHAT[tool]} for {acq.sensitivity} sources. The "
        f"rules allow {may}; an administrator sets one up."
    )


async def _no_model(db: AsyncSession, acq: Acquisition, mime: str) -> str | None:
    """Why a new item that needs reading has no reading run, against the settings
    now. Nothing queues it later on its own, so a model set up since then is said."""
    tool = service._tool(mime)
    if tool is None:
        return None
    why = await no_model(db, acq, tool)
    if why is None:
        return LATE
    return f"Waiting for a model. {why} The file is stored and nothing is lost."


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


def _queue_words(ahead: int) -> str:
    return (
        f"Queued behind {ahead} other item{'' if ahead == 1 else 's'} for the same "
        "model. It starts on its own."
    )


# --- what happens next: one ordered list of steps per source ---------------------
# States: done, running, queued, you (a person acts), model (no model can take it),
# failed (with the runs to try again), later (waits on an earlier step), skipped.
INDEX_TOOLS = ("embed", "index", "fingerprint", "thumbnail", "geotag")
CHIP = {
    "you": "Waiting for you",
    "model": "Waiting for a model",
    "failed": "Needs a retry",
}


def _step(key: str, label: str, state: str, words: str, **more) -> dict:
    return {"key": key, "label": label, "state": state, "words": words, **more}


def _claims_words(n: int) -> str:
    if not n:
        return "Read. No claims were proposed."
    return f"{n} claim{'' if n == 1 else 's'} proposed, waiting for review."


async def _run_step(
    db, key: str, label: str, runs: list[Run], done: str, progress=None
) -> dict:
    state = _stage(runs)
    if state == "failed":
        bad = [r for r in runs if r.status == "failed"]
        detail = _failure(bad[0])["detail"]
        return _step(
            key,
            label,
            "failed",
            detail or "No reason was given.",
            retry=[str(r.id) for r in bad if r.tool in RETRYABLE],
        )
    if state == "running":
        part = ""
        if progress and progress["unit"] != "ms":
            part = (
                f", {progress['completed']} of {progress['total']} {progress['unit']}"
            )
        return _step(key, label, "running", f"Running now{part}.")
    if state == "queued":
        queued = next(r for r in runs if r.status == "queued")
        ahead = await _ahead(db, queued)
        words = _queue_words(ahead) if ahead else "Queued. It starts on its own."
        return _step(key, label, "queued", words)
    return _step(key, label, "done", done)


def _read_label(mime: str) -> str:
    if mime.startswith(("audio/", "video/")):
        return "Speech read"
    return "Pages read" if mime == "application/pdf" else "Text read"


async def steps(
    db: AsyncSession,
    acq: Acquisition,
    blob: Blob,
    runs: list[Run],
    version: DatasetVersion | None,
    claims: dict,
    stored: bool = True,
    progress: dict | None = None,
) -> list[dict]:
    """The source's steps in order, each with its state and words. `runs` are the
    runs on the source (and text derived from it); `claims` counts claims per run."""
    out = [
        _step("hashed", "Fingerprinted", "done", "Its SHA-256 was taken on arrival."),
        _step("stored", "Stored", "done", "The original bytes are kept unchanged.")
        if stored
        else _step("stored", "Stored", "skipped", "The bytes are not on this server."),
    ]
    index = [r for r in runs if r.tool in INDEX_TOOLS]
    if index:
        s = await _run_step(
            db,
            "indexed",
            "Indexed for search",
            index,
            "It shows up in search and among similar items.",
        )
        if s["state"] == "failed":
            s["words"] = (
                f"{s['words']} This is a side step: the source is stored and can "
                "still be read and reviewed."
            )
        out.append(s | {"side": True})
    else:
        out.append(
            _step(
                "indexed",
                "Indexed for search",
                "skipped",
                "Not needed for this kind of file.",
                side=True,
            )
        )
    if blob.mime in sheets.MIMES:
        if version is None:
            return [
                *out,
                _step(
                    "columns",
                    "Columns mapped",
                    "you",
                    "Say what each column holds, then confirm. Nothing is read "
                    "before that.",
                    action="map",
                    chip="Map the columns",
                ),
                _step(
                    "rows",
                    "Rows read",
                    "later",
                    "Starts once the columns are confirmed.",
                ),
            ]
        out.append(
            _step(
                "columns",
                "Columns mapped",
                "done",
                f"Confirmed by a person as version {version.number} of the dataset.",
            )
        )
        rows = [r for r in runs if r.id == version.run_id]
        counts = version.diff or {}
        if rows:
            n = sum(claims.get(r.id, 0) for r in rows)
            out.append(await _run_step(db, "rows", "Rows read", rows, _claims_words(n)))
        elif not counts.get("new") and not counts.get("changed"):
            out.append(
                _step(
                    "rows",
                    "Rows read",
                    "done",
                    "Every row was already in the last version, so nothing new "
                    "needed reading.",
                )
            )
        else:
            why = await no_model(db, acq, "extract_sheet")
            out.append(
                _step(
                    "rows",
                    "Rows read",
                    "model",
                    why
                    or "No model could take the rows when the columns were "
                    "confirmed. One is set up now.",
                )
            )
        return out
    label, tool = _read_label(blob.mime), service._tool(blob.mime)
    reads = [r for r in runs if r.tool in READ_TOOLS]
    if reads:
        route = ROUTE_WORDS.get(reads[0].route, "a model")
        done = (
            "Read on this server."
            if reads[0].tool in ("transcribe", "pdf")
            else f"Read by {route}."
        )
        read = await _run_step(db, "read", label, reads, done, progress)
    elif tool is None:
        return [
            *out,
            _step(
                "read",
                label,
                "skipped",
                "Nothing reads this kind of file on its own. A person reads it.",
            ),
        ]
    elif why := await no_model(db, acq, tool):
        read = _step("read", label, "model", why)
    else:
        read = _step("read", label, "you", LATE, action="read", chip="Ready to read")
    out.append(read)
    if read["state"] == "done":
        n = sum(claims.get(r.id, 0) for r in reads)
        out.append(_step("claims", "Claims proposed", "done", _claims_words(n)))
    else:
        out.append(
            _step(
                "claims", "Claims proposed", "later", "Proposed once it has been read."
            )
        )
    return out


def attention(found: list[dict]) -> dict | None:
    """The step a person should know about first: one waiting for them, for a
    model, or failed. A failed side step does not hold the source up."""
    return next(
        (
            s | {"chip": s.get("chip") or CHIP[s["state"]]}
            for s in found
            if s["state"] in CHIP and not (s.get("side") and s["state"] != "failed")
        ),
        None,
    )


async def claim_counts(db: AsyncSession, run_ids: list) -> dict:
    if not run_ids:
        return {}
    q = (
        select(Statement.run_id, func.count())
        .where(
            Statement.run_id.in_(run_ids),
            Statement.prop.in_([str(p) for p in EXTRACTED_PROPS]),
        )
        .group_by(Statement.run_id)
    )
    return dict((await db.execute(q)).all())


async def source_steps(
    db: AsyncSession, family: dict[str, list[str]], stored: dict | None = None
) -> dict[str, list[dict]]:
    """Steps per source sha, reading runs on each sha and the blobs derived from it
    (`family`: sha to its family, the sha included)."""
    shas = list(family)
    owner = {f: sha for sha, fam in family.items() for f in fam}
    acqs = {
        a.blob_sha256: a
        for a in (
            await db.execute(
                select(Acquisition)
                .where(Acquisition.blob_sha256.in_(shas))
                .distinct(Acquisition.blob_sha256)
                .order_by(Acquisition.blob_sha256, Acquisition.at, Acquisition.id)
            )
        ).scalars()
    }
    found = {
        b.sha256: b
        for b in (await db.execute(select(Blob).where(Blob.sha256.in_(shas)))).scalars()
    }
    runs: dict[str, list[Run]] = {}
    for r in (
        await db.execute(
            select(Run)
            .where(Run.inputs["blobs"][0].astext.in_(list(owner)))
            .order_by(Run.created_at)
        )
    ).scalars():
        runs.setdefault(owner[r.inputs["blobs"][0]], []).append(r)
    versions = {
        v.blob_sha256: v
        for v in (
            await db.execute(
                select(DatasetVersion).where(DatasetVersion.blob_sha256.in_(shas))
            )
        ).scalars()
    }
    claims = await claim_counts(
        db, [r.id for rs in runs.values() for r in rs if r.tool in READ_TOOLS]
    )
    return {
        sha: await steps(
            db,
            acqs[sha],
            found[sha],
            runs.get(sha, []),
            versions.get(sha),
            claims,
            stored=(stored or {}).get(sha, True),
        )
        for sha in shas
        if sha in acqs and sha in found
    }


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
    claims = await claim_counts(db, list(read_runs))
    versions = {
        v.blob_sha256: v
        for v in (
            await db.execute(
                select(DatasetVersion).where(DatasetVersion.blob_sha256.in_(shas))
            )
        ).scalars()
    }

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
            wait = _queue_words(ahead)
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
                "sheet_confirmed": a.blob_sha256 in versions,
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
                "steps": None
                if duplicate
                else await steps(
                    db,
                    a,
                    blob,
                    rs,
                    versions.get(a.blob_sha256),
                    claims,
                    progress=read_progress.get(a.blob_sha256),
                ),
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
