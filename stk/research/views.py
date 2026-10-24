"""Research drawer API: proposed queries, send, results gallery, capture and its
progress, and the per-case switches (admin)."""

import re

import orjson
from quart import Blueprint, Response, abort, g, request
from quart_security import auth_required, current_user

from stk.ingest import batch as batches
from stk.ledger.write import principal_for_user
from stk.monitor.views import ROLES
from stk.research import queries, service, store

bp_research = Blueprint("research", __name__, url_prefix="/api/research")
SCOPE = re.compile(
    r"(person|place|incident):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{12}|blob:[0-9a-f]{64}"
)


@bp_research.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_research.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


def _refuse(reason: str, status=409, code="refused"):
    return _json({"reason": code, "message": reason}, status)


async def _item() -> dict:
    return ((await request.get_json(silent=True)) or {}).get("item") or {}


def _scope(item) -> str:
    scope = str(item.get("scope") or "")
    if not SCOPE.fullmatch(scope):
        abort(400, description="scope must be person:, place:, incident: or blob:")
    return scope


async def _who():
    return await principal_for_user(
        g.db_session, current_user.id, current_user.name or current_user.email
    )


@bp_research.get("/status")
async def status():
    return _json(await service.status(g.db_session, request.args.get("investigation")))


@bp_research.post("/queries")
async def propose():
    """What would be sent, before anything is. Nothing leaves the server here."""
    item = await _item()
    scope = _scope(item)
    db = g.db_session
    try:
        found = await queries.propose(db, scope, await service.blocked(db))
    except LookupError:
        abort(404)
    return _json({"queries": found, "kinds": queries.KINDS})


@bp_research.post("/search")
async def search():
    item = await _item()
    scope = _scope(item)
    try:
        sent = service.clean(item.get("queries"))
    except ValueError as exc:
        abort(400, description=str(exc))
    db = g.db_session
    try:
        out = await service.search(
            db,
            current_user.id,
            await _who(),
            scope,
            item.get("investigation"),
            sent,
        )
    except service.Refusal as exc:
        return _refuse(str(exc))
    await db.commit()
    return _json(out)


@bp_research.get("/thumb/<cid>")
async def thumb(cid):
    """The provider thumbnail the server fetched, so the browser never contacts it."""
    c = store.get(current_user.id, cid)
    if c is None or not c["thumb"]:
        abort(404)
    data, mime = c["thumb"]
    return Response(data, content_type=mime)


@bp_research.post("/capture")
async def capture():
    item = await _item()
    db = g.db_session
    try:
        out = await service.queue_capture(
            db,
            current_user.id,
            await _who(),
            [str(i) for i in item.get("ids") or []],
            item.get("sensitivity") or "sensitive",
            (item.get("doc_type") or "web_post").strip(),
        )
    except service.Refusal as exc:
        return _refuse(str(exc))
    except LookupError as exc:
        return _refuse(str(exc), 410, "expired")
    except ValueError as exc:
        abort(400, description=str(exc))
    await db.commit()
    return _json(out)


@bp_research.get("/batch/<uuid:batch>")
async def progress(batch):
    db = g.db_session
    captures = await service.capture_status(db, str(batch))
    if not captures:
        abort(404)
    return _json({"captures": captures, "intake": await batches.status(db, str(batch))})


@bp_research.post("/case")
async def case():
    if not current_user.has_role("admin"):
        abort(403)
    item = await _item()
    investigation = str(item.get("investigation") or "").strip()
    if not investigation:
        abort(400, description="investigation is required")
    flags = {k: item[k] for k in ("sensitive", "research") if k in item}
    if not flags or not all(isinstance(v, bool) for v in flags.values()):
        abort(400, description="set sensitive and/or research to true or false")
    who = await _who()
    await service.set_case(g.db_session, investigation, who.id, **flags)
    await g.db_session.commit()
    return _json(await service.status(g.db_session, investigation))
