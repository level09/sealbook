"""Sheet intake between upload and review: preview with mapping suggestions, the
version diff, the confirmation that queues `extract_sheet`, and the proposals it
wrote, grouped by row. Callers check access; this module reads what they allow."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import func, select

from stk import blobs
from stk.discovery.service import available
from stk.ingest import service as ingest
from stk.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Run,
    Statement,
    StatementTier,
)
from stk.monitor.data import LOCATED
from stk.places import resolve
from stk.settings import Config
from stk.sheets import mapping, parse
from stk.sheets.models import Dataset, DatasetVersion

PREVIEW_ROWS = 500
TOOL = "extract_sheet"


class Conflict(ValueError):
    """The request is valid but the state does not allow it (409)."""


async def sheets_of(blob: Blob) -> tuple[parse.Sheet, ...]:
    data = await asyncio.to_thread(blobs.read, blob, Config.BLOB_DIR)
    return await asyncio.to_thread(parse.read, data, blob.mime)


def _sheet(sheets, number: int) -> parse.Sheet:
    if not 1 <= number <= len(sheets):
        raise ValueError(f"no sheet {number}")
    sheet = sheets[number - 1]
    if len(sheet.rows) < 2:
        raise ValueError("the sheet needs a header row and at least one row")
    return sheet


def table(sheets, number: int, limit: int = PREVIEW_ROWS) -> dict:
    sheet = sheets[number - 1]
    return {
        "sheet": number,
        "sheets": [s.name for s in sheets],
        "name": sheet.name,
        "header": list(sheet.rows[0]) if sheet.rows else [],
        "rows": [list(r) for r in sheet.rows[1 : limit + 1]],
        "total": max(len(sheet.rows) - 1, 0),
    }


def _dataset_row(d: Dataset, versions: int) -> dict:
    return {
        "id": str(d.id),
        "name": d.name,
        "key_column": d.key_column,
        "versions": versions,
        "mapping": d.mapping,
    }


async def datasets(db, investigation: str) -> list[dict]:
    q = (
        select(Dataset, func.count(DatasetVersion.id))
        .outerjoin(DatasetVersion, DatasetVersion.dataset_id == Dataset.id)
        .where(Dataset.investigation == investigation)
        .group_by(Dataset.id)
        .order_by(Dataset.name)
    )
    return [_dataset_row(d, n) for d, n in (await db.execute(q)).all()]


def _case(acq: Acquisition) -> str:
    case = (acq.context or {}).get("investigation")
    if not case:
        raise ValueError("this sheet was added without a case; add it to a case first")
    return case


async def version_of(db, acq: Acquisition) -> DatasetVersion | None:
    return await db.scalar(
        select(DatasetVersion).where(DatasetVersion.blob_sha256 == acq.blob_sha256)
    )


async def preview(db, acq: Acquisition, blob: Blob, dataset_id, number: int) -> dict:
    sheets = await sheets_of(blob)
    sheet = _sheet(sheets, number)
    case = _case(acq)
    dataset = await _dataset(db, dataset_id, case) if dataset_id else None
    cols = mapping.columns(sheet)
    out = {
        "table": table(sheets, number),
        "datasets": await datasets(db, case),
        "fields": [{"key": f.key, "label": f.label} for f in mapping.FIELDS],
        "dataset": str(dataset.id) if dataset else None,
        "key_column": dataset.key_column if dataset else None,
        "suggest_error": None,
    }
    version = await version_of(db, acq)
    out["version"] = await _version_row(db, version) if version else None
    try:
        out["columns"] = await mapping.suggest(cols, dataset.mapping if dataset else {})
    except (ValueError, OSError) as exc:
        # The embedding server being down is visible, and mapping by hand still works.
        out["suggest_error"] = str(exc)
        out["columns"] = [c | {"remembered": None, "suggestions": []} for c in cols]
    return out


async def _dataset(db, dataset_id, case: str) -> Dataset:
    dataset = await db.get(Dataset, uuid.UUID(str(dataset_id)))
    if dataset is None or dataset.investigation != case:
        raise ValueError("no such dataset in this case")
    return dataset


async def _previous(db, dataset: Dataset) -> tuple[DatasetVersion | None, object]:
    """The latest version whose sheet is still available, and that sheet."""
    q = (
        select(DatasetVersion)
        .where(
            DatasetVersion.dataset_id == dataset.id,
            available(DatasetVersion.blob_sha256),
        )
        .order_by(DatasetVersion.number.desc())
        .limit(1)
    )
    version = await db.scalar(q)
    if version is None:
        return None, None
    blob = await db.get(Blob, version.blob_sha256)
    return version, (await sheets_of(blob))[version.sheet - 1]


async def plan(db, acq: Acquisition, blob: Blob, item: dict) -> dict:
    """The diff a confirmation would act on. Writes nothing."""
    case = _case(acq)
    number = int(item.get("sheet") or 1)
    sheet = _sheet(await sheets_of(blob), number)
    key_column, columns = item.get("key_column") or "", item.get("mapping") or {}
    mapping.check(sheet, key_column, columns)
    dataset = None
    if item.get("dataset"):
        dataset = await _dataset(db, item["dataset"], case)
    elif not (item.get("dataset_name") or "").strip():
        raise ValueError("choose a dataset or name a new one")
    previous, before = (None, None)
    if dataset is not None:
        previous, before = await _previous(db, dataset)
    rows = mapping.diff(sheet, before, key_column, columns)
    return {
        "dataset": dataset,
        "sheet": number,
        "key_column": key_column,
        "mapping": columns,
        "previous": previous,
        "rows": rows,
        "counts": mapping.counts(rows),
    }


def plan_out(p: dict) -> dict:
    return {
        "dataset": str(p["dataset"].id) if p["dataset"] else None,
        "previous": p["previous"].number if p["previous"] else None,
        "rows": p["rows"],
        "counts": p["counts"],
        "to_read": sum(r["status"] in ("new", "changed") for r in p["rows"]),
    }


async def confirm(db, acq: Acquisition, blob: Blob, item: dict, person) -> dict:
    """Record the person's mapping as a dataset version and queue extraction of the
    new and changed rows. One version per sheet blob."""
    if await version_of(db, acq):
        raise Conflict("this sheet is already a confirmed version")
    p = await plan(db, acq, blob, item)
    dataset = p["dataset"]
    if dataset is None:
        dataset = Dataset(
            name=item["dataset_name"].strip(),
            investigation=_case(acq),
            key_column=p["key_column"],
            mapping={},
            created_by=person.id,
        )
        db.add(dataset)
        await db.flush()
    # Remembered for the next version: this person's confirmed choices.
    dataset.mapping = {**dataset.mapping, **p["mapping"]}
    dataset.key_column = p["key_column"]
    number = 1 + (
        await db.scalar(
            select(func.coalesce(func.max(DatasetVersion.number), 0)).where(
                DatasetVersion.dataset_id == dataset.id
            )
        )
    )
    version = DatasetVersion(
        dataset_id=dataset.id,
        number=number,
        blob_sha256=blob.sha256,
        acquisition_id=acq.id,
        sheet=p["sheet"],
        key_column=p["key_column"],
        mapping=p["mapping"],
        diff=p["counts"],
        confirmed_by=person.id,
    )
    db.add(version)
    await db.flush()
    rows = [r["row"] for r in p["rows"] if r["status"] in ("new", "changed")]
    queued = {"queued": None, "reason": "no new or changed rows to read"}
    if rows:
        queued = await _queue(db, acq, version, rows, person)
    return {"version": await _version_row(db, version), "plan": plan_out(p)} | queued


async def _queue(db, acq, version: DatasetVersion, rows, person) -> dict:
    route, reason = await ingest._route(db, TOOL, acq)
    if route is None:
        return {"queued": None, "reason": reason}
    run = Run(
        principal_id=(await ingest.system_agent(db, "extractor")).id,
        tool=TOOL,
        route=str(route),
        model_requested=Config.RUN_MODELS[route],
        inputs={
            "blobs": [acq.blob_sha256],
            "acquisition": str(acq.id),
            "version": str(version.id),
            "sheet": version.sheet,
            "rows": rows,
            "key_column": version.key_column,
            "mapping": version.mapping,
            "confirmed_by": str(person.id),
        },
    )
    db.add(run)
    await db.flush()
    version.run_id = run.id
    return {"queued": TOOL, "run": str(run.id), "route": str(route)}


async def _version_row(db, v: DatasetVersion) -> dict:
    dataset = await db.get(Dataset, v.dataset_id)
    run = await db.get(Run, v.run_id) if v.run_id else None
    return {
        "id": str(v.id),
        "dataset": str(dataset.id),
        "dataset_name": dataset.name,
        "number": v.number,
        "sheet": v.sheet,
        "key_column": v.key_column,
        "mapping": v.mapping,
        "counts": v.diff,
        "run": {"id": str(run.id), "status": run.status, "error": run.error}
        if run
        else None,
    }


async def proposals(db, acq: Acquisition, blob: Blob) -> dict:
    """Claims the sheet's extraction runs proposed, grouped by row, with the place
    each position claim resolves to (or the candidates that keep it open)."""
    version = await version_of(db, acq)
    if version is None:
        return {"version": None, "rows": []}
    q = (
        select(Statement, StatementTier.tier, StatementTier.reason, Payload.value)
        .join(Run, Run.id == Statement.run_id)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .outerjoin(Payload, Payload.commitment == Statement.commitment)
        .where(
            Run.tool == TOOL,
            Statement.span_blob == blob.sha256,
            Run.inputs["version"].astext == str(version.id),
        )
        .order_by(Statement.recorded_at, Statement.id)
    )
    found = (await db.execute(q)).all()
    located = [st.id for st, *_ in found if st.prop in LOCATED]
    places = await resolve.resolved(db, located, _case(acq)) if located else {}
    rows: dict[int, dict] = {}
    for st, tier, reason, value in found:
        if st.span is None:
            continue
        claim = {
            "id": str(st.id),
            "subject": st.subject,
            "prop": st.prop,
            "value": value,
            "span": st.span,
            "tier": tier,
            "reason": reason,
            "from": st.valid_from.isoformat() if st.valid_from else None,
            "to": st.valid_to.isoformat() if st.valid_to else None,
        }
        if st.prop in LOCATED:
            place = places.get(st.id)
            claim["place"] = (
                {"id": str(place.id), "name": place.name_en or place.name_ar}
                if place
                else None
            )
            if place is None and value:
                claim["candidates"] = len(
                    await resolve.candidates(db, value.get("text", ""))
                )
        row = rows.setdefault(st.span["row"], {"row": st.span["row"], "claims": []})
        row["claims"].append(claim)
    return {
        "version": await _version_row(db, version),
        "rows": sorted(rows.values(), key=lambda r: r["row"]),
    }
