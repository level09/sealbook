"""Sheet intake API. Analysts and admins only; cell values follow the evidence
rules: a sensitive sheet needs the logged reveal, any other logs a view."""

import uuid

from quart import Blueprint, abort, g, request
from quart_security import current_user

from sealbook.discovery import evidence
from sealbook.ledger import write
from sealbook.ledger.models import Acquisition
from sealbook.sheets import parse, service
from sealbook.web import json_response, protect

bp = Blueprint("sheets", __name__)
protect(bp)


@bp.errorhandler(service.Conflict)
async def conflict(exc):
    return json_response({"message": str(exc)}, 409)


@bp.errorhandler(ValueError)
async def invalid(exc):
    return json_response({"message": str(exc)}, 400)


async def _sheet_blob(sha: str):
    blob = await evidence.source_blob(g.db_session, sha)
    if blob is None:
        abort(404)
    if blob.mime not in parse.MIMES:
        abort(415, description="This source is not a sheet")
    if not await evidence.viewable(g.db_session, sha, current_user):
        return blob, json_response(
            {
                "message": "Reveal this sheet before reading it",
                "reason": "reveal_required",
            },
            403,
        )
    return blob, None


async def _acquisition(acq_id: uuid.UUID):
    acq = await g.db_session.get(Acquisition, acq_id)
    if acq is None:
        abort(404)
    blob, refused = await _sheet_blob(acq.blob_sha256)
    return acq, blob, refused


async def _item() -> dict:
    return ((await request.get_json(silent=True)) or {}).get("item", {})


@bp.get("/api/sheets/<uuid:acq_id>/preview")
async def preview(acq_id):
    acq, blob, refused = await _acquisition(acq_id)
    if refused:
        return refused
    out = await service.preview(
        g.db_session,
        acq,
        blob,
        request.args.get("dataset"),
        int(request.args.get("sheet", "1")),
    )
    if out.pop("asked"):
        person = await write.principal_of(g.db_session, current_user)
        await service.log_asked(
            g.db_session,
            person,
            f"blob:{acq.blob_sha256}",
            (acq.context or {}).get("investigation"),
        )
    return json_response(out)


@bp.post("/api/sheets/<uuid:acq_id>/diff")
async def diff(acq_id):
    acq, blob, refused = await _acquisition(acq_id)
    if refused:
        return refused
    return json_response(
        service.plan_out(await service.plan(g.db_session, acq, blob, await _item()))
    )


@bp.post("/api/sheets/<uuid:acq_id>/confirm")
async def confirm(acq_id):
    acq, blob, refused = await _acquisition(acq_id)
    if refused:
        return refused
    person = await write.principal_of(g.db_session, current_user)
    out = await service.confirm(g.db_session, acq, blob, await _item(), person)
    await g.db_session.commit()
    return json_response(out)


@bp.get("/api/sheets/<uuid:acq_id>/proposals")
async def proposals(acq_id):
    acq, blob, refused = await _acquisition(acq_id)
    if refused:
        return refused
    return json_response(await service.proposals(g.db_session, acq, blob))


@bp.get("/api/evidence/<sha>/sheet")
async def sheet(sha):
    """The whole table for the evidence page's SheetViewer."""
    blob, refused = await _sheet_blob(sha)
    if refused:
        return refused
    sheets = await service.sheets_of(blob)
    number = int(request.args.get("sheet", "1"))
    if not 1 <= number <= len(sheets):
        abort(404)
    return json_response(service.table(sheets, number, limit=parse.MAX_ROWS))
