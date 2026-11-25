"""Case map page and its reads. Every read that shows people's claims leaves the
access record a story read leaves."""

from quart import Blueprint, abort, g, render_template, request
from quart_security import current_user

from sealbook.casemap import data
from sealbook.ledger.write import principal_of
from sealbook.people import data as people
from sealbook.stories.views import shown
from sealbook.web import json_response, protect

bp_casemap = Blueprint("casemap", __name__)


protect(bp_casemap)


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
    return json_response(view)


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
    who = await principal_of(db, current_user)
    seen: dict[str, list] = {}
    for step in found["steps"]:
        for node in step["nodes"]:
            if node.startswith("person:"):
                seen.setdefault(node.removeprefix("person:"), []).extend(step["claims"])
    for person, ids in seen.items():
        await people.log_access(db, person, "view", sorted(set(ids)), who.id)
    await db.commit()
    found.pop("view")
    return json_response(found)


@bp_casemap.get("/api/case-map/news")
async def news():
    return json_response({"items": await data.news(g.db_session, _case())})
