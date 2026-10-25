import re
import uuid

import httpx
import orjson as json
from quart import Blueprint, Response, abort, current_app, g, render_template, request
from quart_security import auth_required, current_user

from sealbook import blobs, netguard
from sealbook.cases import service as cases
from sealbook.ingest import batch as batches
from sealbook.ingest import service
from sealbook.ingest import suggest as suggestions
from sealbook.ledger import write
from sealbook.ledger.contracts import Sensitivity
from sealbook.monitor.views import ROLES
from sealbook.sheets import mapping, parse
from sealbook.sheets import service as sheets

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
    """Sensitivity and document type are set per item before any model sees the
    bytes; sensitivity defaults to sensitive."""
    try:
        sensitivity = Sensitivity(fields.get("sensitivity") or Sensitivity.SENSITIVE)
    except ValueError:
        abort(400, description="sensitivity must be sensitive or ordinary")
    doc_type = (fields.get("doc_type") or "unknown").strip()
    if doc_type not in service.DOC_TYPES:
        abort(400, description=f"Unknown document type: {doc_type}")
    return {"sensitivity": str(sensitivity), "doc_type": doc_type}


async def _case(fields) -> str:
    """The case must exist; intake never creates one."""
    try:
        return (await cases.require(g.db_session, fields.get("investigation"))).slug
    except cases.Unknown as exc:
        abort(400, description=str(exc))


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
        "ingest/index.html",
        boot={
            "doc_types": [
                {"value": k, "title": v} for k, v in service.DOC_TYPES.items()
            ],
            "fields": [{"value": f.key, "title": f.label} for f in mapping.FIELDS],
        },
    )


@bp_ingest.post("/suggest")
async def suggest():
    """A document type per staged item, from its name and type; never stored."""
    items = ((await request.get_json(silent=True)) or {}).get("items") or []
    if not isinstance(items, list) or len(items) > 200:
        abort(400, description="send up to 200 items")
    return _json(
        {
            "items": [
                service.suggest_doc_type(
                    str(i.get("name") or ""), str(i.get("mime") or "")
                )
                for i in items
            ]
        }
    )


PEEK_BYTES = 20 * 1024 * 1024


@bp_ingest.post("/sheet/peek")
async def peek():
    """Column preview of a sheet still on the person's device (sheets.service.peek).
    Multipart like /file, so it lives here and not under /api (JSON only)."""
    files = (await request.files).getlist("file")
    form = await request.form
    if len(files) != 1:
        abort(400, description="send one sheet")
    f = files[0]
    data = f.stream.read(PEEK_BYTES + 1)
    if len(data) > PEEK_BYTES:
        abort(413, description="Sheets over 20 MB are previewed after they are added")
    mime = parse.mime_for(f.filename, f.mimetype or "")
    if mime not in parse.MIMES:
        abort(415, description="Only CSV and XLSX sheets are previewed")
    case = (form.get("investigation") or "").strip() or None
    try:
        out = await sheets.peek(g.db_session, data, mime, f.filename, case)
    except ValueError as exc:
        abort(422, description=str(exc))
    return _json(out)


@bp_ingest.get("/batch/<batch>")
async def batch_status(batch):
    return _json(await batches.status(g.db_session, batch))


@bp_ingest.get("/batch/<batch>/case")
async def batch_case(batch):
    """Which case a read batch looks like, among the cases this person can open."""
    return _json(await suggestions.suggestion(g.db_session, current_user.id, batch))


@bp_ingest.post("/batch/<batch>/record")
async def batch_record(batch):
    """Record chosen sources of a batch in another case, on a click."""
    item = ((await request.get_json(silent=True)) or {}).get("item", {})
    chosen = item.get("sources")
    if not isinstance(chosen, list) or not all(isinstance(s, str) for s in chosen):
        abort(400, description="sources must be a list of fingerprints")
    try:
        out = await suggestions.record(
            g.db_session, current_user, batch, str(item.get("case") or ""), chosen
        )
    except LookupError as exc:
        abort(404, description=str(exc))
    except ValueError as exc:
        abort(400, description=str(exc))
    await g.db_session.commit()
    return _json(out)


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
    case = await _case(form)
    expect = (form.get("sha256") or "").strip() or None
    if expect and not re.fullmatch(r"[0-9a-fA-F]{64}", expect):
        abort(400, description="sha256 must be 64 hex characters")
    if expect and len(files) != 1:
        abort(400, description="send one file with its sha256")
    collector = await _collector()
    try:
        origin = service.make_origin(
            form.get("origin_kind", "device"), form.get("origin") or "upload"
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    batch = form.get("batch") or str(uuid.uuid4())
    context = _context(form) | {"batch": batch, "investigation": case}
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
                    expect_sha256=expect,
                    **intake,
                )
            )
        except service.Mismatch as exc:
            abort(422, description=str(exc))
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
    case = await _case(item)
    collector = await _collector()
    try:
        result = await service.capture_url(
            g.db_session,
            url,
            blob_dir=current_app.config["BLOB_DIR"],
            collector_id=collector.id,
            context=_context(item)
            | {"investigation": case}
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
