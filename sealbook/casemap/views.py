"""Case map page and its reads. Every read that shows people's claims leaves the
access record a story read leaves."""

import orjson
from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import auth_required, current_user

from sealbook.casemap import data
from sealbook.ledger.write import principal_for_user
from sealbook.monitor.views import ROLES
from sealbook.people import data as people
from sealbook.stories.views import shown

bp_casemap = Blueprint("casemap", __name__)


@bp_casemap.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_casemap.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


def _case() -> str:
    found = request.args.get("investigation")
    if not found:
        abort(400, description="pick a case")
    return found


@bp_casemap.get("/case-map")
async def page():
    return await render_template(
        "casemap/index.html", boot={"admin": current_user.has_role("admin")}
    )


@bp_casemap.get("/api/case-map")
async def case_map():
    db = g.db_session
    view, _ = await data.build(db, current_user, _case())
    await shown(db, view)
    return _json(view)


@bp_casemap.get("/api/case-map/path")
async def connect():
    """The answer names people and their claims, so each person on it is logged as
    viewed with the claims the answer cites."""
    db, args = g.db_session, request.args
    try:
        found = await data.connect(
            db, current_user, _case(), args.get("from", ""), args.get("to", "")
        )
    except LookupError as exc:
        abort(404, description=str(exc))
    who = await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    seen: dict[str, list] = {}
    for step in found["steps"]:
        for node in step["nodes"]:
            if node.startswith("person:"):
                seen.setdefault(node.removeprefix("person:"), []).extend(step["claims"])
    for person, ids in seen.items():
        await people.log_access(db, person, "view", sorted(set(ids)), who.id)
    await db.commit()
    found.pop("view")
    return _json(found)


@bp_casemap.get("/api/case-map/news")
async def news():
    return _json({"items": await data.news(g.db_session, _case())})
