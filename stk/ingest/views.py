import uuid

import httpx
import orjson as json
from quart import Blueprint, Response, abort, current_app, g, render_template, request
from quart_security import auth_required, current_user

from stk import blobs, netguard
from stk.ingest import batch as batches
from stk.ingest import service
from stk.ledger import write
from stk.ledger.contracts import Sensitivity
from stk.monitor import data
from stk.monitor.views import ROLES
from stk.sheets import parse

_C2PA_FAILED = (
    "The C2PA credentials check could not run on this file, so it was not added. "
    "Retry, and tell an administrator if it fails again."
)

bp_ingest = Blueprint("ingest", __name__, url_prefix="/ingest")


@bp_ingest.before_request
@auth_required("session")
async def before_request():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


def _intake(fields) -> dict:
    """Sensitivity and document type are set before any model sees the bytes;
    sensitivity defaults to sensitive."""
    try:
        sensitivity = Sensitivity(fields.get("sensitivity") or Sensitivity.SENSITIVE)
    except ValueError:
        abort(400, description="sensitivity must be sensitive or ordinary")
    return {
        "sensitivity": str(sensitivity),
        "doc_type": (fields.get("doc_type") or "unknown").strip(),
    }


def _json(data, status=200):
    return Response(json.dumps(data), status=status, content_type="application/json")


async def _collector():
    return await write.principal_for_user(
        g.db_session, current_user.id, current_user.email
    )


def _context(fields) -> dict:
    """Case and acquisition note, set once for the batch."""
    keys = {"investigation": "investigation", "note": "note"}
    return {k: v for f, k in keys.items() if (v := (fields.get(f) or "").strip())}


@bp_ingest.get("/")
async def index():
    return await render_template(
        "ingest/index.html", cases=await data.investigations(g.db_session)
    )


@bp_ingest.get("/batch/<batch>")
async def batch_status(batch):
    return _json(await batches.status(g.db_session, batch))


@bp_ingest.post("/run/<uuid:run_id>/retry")
async def retry(run_id):
    run = await batches.retry(g.db_session, run_id)
    if run is None:
        abort(409, description="Only a failed run can be retried")
    await g.db_session.commit()
    return _json({"run": str(run.id), "status": run.status})


@bp_ingest.post("/file")
async def upload():
    files = (await request.files).getlist("file")
    form = await request.form
    if not files:
        abort(400, description="No file")
    intake = _intake(form)
    collector = await _collector()
    try:
        origin = service.make_origin(
            form.get("origin_kind", "device"), form.get("origin") or "upload"
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    batch = form.get("batch") or str(uuid.uuid4())
    context = _context(form) | {"batch": batch}
    results = []
    for f in files:

        async def chunks(f=f):
            while chunk := f.stream.read(blobs.CHUNK):
                yield chunk

        try:
            results.append(
                await service.ingest(
                    g.db_session,
                    chunks(),
                    blob_dir=current_app.config["BLOB_DIR"],
                    mime=parse.mime_for(
                        f.filename, f.mimetype or "application/octet-stream"
                    ),
                    origin=origin,
                    collector_id=collector.id,
                    context=context | {"filename": f.filename},
                    **intake,
                )
            )
        except service.C2paError as exc:
            current_app.logger.warning("c2pa check failed for %s: %s", f.filename, exc)
            abort(422, description=_C2PA_FAILED)
    await g.db_session.commit()
    return _json({"items": results})


@bp_ingest.post("/url")
async def capture():
    item = ((await request.get_json(silent=True)) or {}).get("item", {})
    try:
        url = service.normalise_url(item.get("url", ""))
    except ValueError as exc:
        abort(400, description=str(exc))
    intake = _intake(item)
    collector = await _collector()
    try:
        result = await service.capture_url(
            g.db_session,
            url,
            blob_dir=current_app.config["BLOB_DIR"],
            collector_id=collector.id,
            context=_context(item)
            | ({"batch": item["batch"]} if item.get("batch") else {}),
            **intake,
        )
    except netguard.Blocked as exc:
        abort(400, description=str(exc))
    except httpx.HTTPError as exc:
        abort(
            422,
            description=f"The link could not be fetched: {exc or type(exc).__name__}",
        )
    except service.C2paError:
        abort(422, description=_C2PA_FAILED)
    await g.db_session.commit()
    return _json(result)
