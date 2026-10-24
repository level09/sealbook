import uuid
from datetime import UTC, date, datetime

import orjson
from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import auth_required, current_user
from sqlalchemy import select

from stk.collaboration import assignments, notifications, service
from stk.ledger import write
from stk.ledger.contracts import Prop, Refused
from stk.ledger.models import Payload, Principal, Statement
from stk.user.models import User

bp = Blueprint("collaboration", __name__)


@bp.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ("analyst", "admin")):
        abort(403)


@bp.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def output(value, status=200):
    return Response(orjson.dumps(value), status=status, content_type="application/json")


@bp.errorhandler(ValueError)
async def invalid(exc):
    return output({"error": str(exc)}, 400)


@bp.errorhandler(Refused)
async def refused(exc):
    return output({"error": exc.reason}, 409)


@bp.errorhandler(PermissionError)
async def forbidden(exc):
    return output({"error": str(exc)}, 403)


@bp.errorhandler(LookupError)
async def missing(exc):
    return output({"error": str(exc)}, 404)


@bp.get("/review")
async def page():
    return await render_template("review/index.html")


@bp.get("/inbox")
async def inbox_page():
    return await render_template("inbox/index.html")


@bp.get("/api/collaboration/analysts")
async def analysts():
    users = (
        await g.db_session.execute(select(User).where(User.active.is_(True)))
    ).scalars()
    return output(
        {
            "items": [
                {"id": u.id, "name": u.name or u.email}
                for u in users
                if u.id != current_user.id
                and any(u.has_role(r) for r in ("analyst", "admin"))
            ]
        }
    )


@bp.get("/api/collaboration/requests")
async def inbox():
    db = g.db_session
    q = (
        select(Statement.id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(Principal, Principal.id == Statement.author_id)
        .where(Statement.prop == Prop.REVIEW_REQUEST)
    )
    box = request.args.get("box", "received")
    if box not in ("received", "sent"):
        raise ValueError("box must be received or sent")
    q = q.where(
        Principal.user_id == current_user.id
        if box == "sent"
        else Payload.value["reviewer"].as_integer() == current_user.id
    )
    ids = (
        await db.execute(q.order_by(Statement.recorded_at.desc()).limit(100))
    ).scalars()
    items = [await service.task(db, sid, current_user) for sid in list(ids)]
    return output({"items": items})


@bp.get("/api/collaboration/requests/<uuid:rid>")
async def detail(rid):
    return output(await service.task(g.db_session, rid, current_user))


@bp.post("/api/collaboration/requests")
@bp.post("/api/collaboration/suggestions")
async def create():
    db = g.db_session
    item = ((await request.get_json(silent=True)) or {}).get("item", {})
    try:
        sid, reviewer = uuid.UUID(item["statement"]), int(item["reviewer"])
    except (KeyError, ValueError, TypeError):
        raise ValueError("statement and reviewer are required") from None
    await write.lock_chain(db)
    original = await db.get(Statement, sid)
    if original is None or original.prop in (Prop.REVIEW_REQUEST, Prop.REVIEW_COMMENT):
        abort(404)
    proposal = (
        await service.suggest(db, original, current_user, item)
        if request.path.endswith("suggestions")
        else original
    )
    row = await service.request_review(
        db, proposal, current_user, reviewer, item.get("reason")
    )
    await db.commit()
    await service.assigned(db, reviewer, row.id, proposal.id)
    return output({"id": str(row.id), "proposal": str(proposal.id)}, 201)


@bp.post("/api/collaboration/requests/<uuid:rid>/comments")
async def comment(rid):
    item = ((await request.get_json(silent=True)) or {}).get("item", {})
    db, text = g.db_session, item.get("text")
    found = await service.comment(db, rid, current_user, text)
    await db.commit()
    await service.comment_notices(db, rid, found, current_user, text)
    return output({"message": "Comment added"}, 201)


async def _body():
    return ((await request.get_json(silent=True)) or {}).get("item") or {}


def _int(name, default=None):
    try:
        raw = request.args.get(name)
        return default if raw is None else int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


@bp.get("/api/notifications")
async def notification_list():
    raw = request.args.get("before")
    before = notifications.parse_cursor(raw) if raw else None
    return output(
        await notifications.listing(
            g.db_session,
            current_user.id,
            request.args.get("unread") == "1",
            _int("limit", 25),
            before,
        )
    )


@bp.post("/api/notifications/read")
async def notification_read():
    item = await _body()
    if item.get("all") is True:
        ids = None
    elif "ids" in item:
        ids = notifications.parse_ids(item["ids"])
    else:
        raise ValueError("send ids or all")
    return output(
        {"unread": await notifications.mark_read(g.db_session, current_user.id, ids)}
    )


async def _assignment_body() -> dict:
    """A person cannot set a due date already behind them; seeding history can."""
    item = await _body()
    due = item.get("due")
    if isinstance(due, str) and due < datetime.now(UTC).date().isoformat():
        try:
            date.fromisoformat(due)
        except ValueError:
            return item
        raise ValueError("due date is in the past")
    return item


@bp.post("/api/assignments/bulk")
async def assign_bulk():
    return output(
        await assignments.bulk(g.db_session, current_user, await _assignment_body()),
        201,
    )


@bp.patch("/api/assignments/<uuid:aid>")
async def assign_update(aid):
    return output(
        await assignments.update(
            g.db_session, current_user, aid, await _assignment_body()
        )
    )


@bp.get("/api/assignments")
async def assign_list():
    return output(
        await assignments.listing(
            g.db_session,
            request.args.get("investigation"),
            _int("assignee"),
            request.args.get("status"),
            _int("page", 1),
            _int("per_page", 50),
        )
    )


@bp.get("/api/assignments/workload")
async def assign_workload():
    return output(
        await assignments.workload(g.db_session, request.args.get("investigation"))
    )


@bp.get("/api/analysts")
async def analyst_picker():
    return output(
        await assignments.analysts(g.db_session, request.args.get("investigation"))
    )
