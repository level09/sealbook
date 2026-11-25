"""Bayanat import API. Analysts and admins only; the export is evidence, so a
sensitive one needs the logged reveal before it is read."""

import uuid

from quart import Blueprint, abort, g
from quart_security import current_user

from sealbook.bayanat import export, service
from sealbook.discovery import evidence
from sealbook.ledger import write
from sealbook.ledger.models import Acquisition
from sealbook.web import json_response, protect

bp = Blueprint("bayanat", __name__)
protect(bp)


@bp.errorhandler(service.Conflict)
async def conflict(exc):
    return json_response({"message": str(exc)}, 409)


@bp.errorhandler(ValueError)
async def invalid(exc):
    return json_response({"message": str(exc)}, 400)


async def _export(acq_id: uuid.UUID):
    acq = await g.db_session.get(Acquisition, acq_id)
    if acq is None:
        abort(404)
    blob = await evidence.source_blob(g.db_session, acq.blob_sha256)
    if blob is None:
        abort(404)
    if blob.mime not in export.MIMES:
        abort(415, description="This source is not a Bayanat export")
    if not await evidence.viewable(g.db_session, acq.blob_sha256, current_user):
        return (
            acq,
            blob,
            json_response(
                {
                    "message": "Reveal this export before reading it",
                    "reason": "reveal_required",
                },
                403,
            ),
        )
    return acq, blob, None


@bp.get("/api/bayanat/<uuid:acq_id>/preview")
async def preview(acq_id):
    acq, blob, refused = await _export(acq_id)
    if refused:
        return refused
    return json_response(await service.preview(g.db_session, acq, blob))


@bp.post("/api/bayanat/<uuid:acq_id>/confirm")
async def confirm(acq_id):
    acq, blob, refused = await _export(acq_id)
    if refused:
        return refused
    person = await write.principal_of(g.db_session, current_user)
    out = await service.confirm(g.db_session, acq, blob, person)
    await g.db_session.commit()
    return json_response(out)
