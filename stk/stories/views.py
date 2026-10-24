"""Story view: one page for any lens, its batched data, and saved selections."""

import uuid

import orjson
from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import auth_required, current_user
from sqlalchemy import select

from stk.discovery.evidence import granted
from stk.ledger.write import principal_for_user
from stk.monitor import filter as mfilter
from stk.monitor.views import ROLES
from stk.packages.service import PROFILES
from stk.people import data as people
from stk.stories import data
from stk.stories.models import SavedStory

bp_stories = Blueprint("stories", __name__)
QUERY = ("lens", "ref", "investigation", "from", "to")


@bp_stories.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_stories.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


def _filter(args) -> dict:
    """The monitor filter from `investigation`, `from` and `to`."""
    lo, hi = args.get("from") or None, args.get("to") or None
    if bool(lo) != bool(hi):
        abort(400, description="from and to go together")
    raw = {"investigation": args.get("investigation") or None, "time": None}
    if lo:
        raw["time"] = [lo, hi]
    try:
        return mfilter.parse(orjson.dumps(raw).decode())
    except ValueError as exc:
        abort(400, description=str(exc))


@bp_stories.get("/stories")
async def page():
    boot = {k: request.args.get(k) for k in QUERY} | {"audiences": list(PROFILES)}
    return await render_template("stories/index.html", boot=boot)


@bp_stories.get("/api/stories")
async def story():
    """Every story read is an access statement per person row, listing the points it
    showed: the same record a person page view leaves."""
    db, args = g.db_session, request.args
    try:
        found = await data.story(
            db, current_user, args.get("lens", ""), args.get("ref"), _filter(args)
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    except LookupError as exc:
        abort(404, description=str(exc))
    for p in found["points"]:
        # Sensitive and not revealed in this session: the client must not ask for it.
        p["locked"] = p.pop("sensitive_source", False) and not granted(p["source"])
    who = await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    for row in found["rows"]:
        view = {"person": row["key"].removeprefix("person:"), "track": row}
        await people.log_view(db, view, who.id)
    await db.commit()
    return _json(found)


@bp_stories.get("/api/stories/saved")
async def saved():
    rows = await g.db_session.scalars(
        select(SavedStory)
        .where(SavedStory.user_id == current_user.id)
        .order_by(SavedStory.created_at.desc())
    )
    return _json(
        {
            "items": [
                {"id": str(s.id), "query": s.query, "at": s.created_at.isoformat()}
                for s in rows
            ]
        }
    )


@bp_stories.post("/api/stories/saved")
async def save():
    item = ((await request.get_json()) or {}).get("item") or {}
    query = {k: item.get(k) or None for k in QUERY}
    if set(item) - set(QUERY) or any(
        v is not None and not isinstance(v, str) for v in query.values()
    ):
        abort(400, description=f"a saved story holds only {', '.join(QUERY)}")
    if query["lens"] not in data.LENSES:
        abort(400, description=f"lens must be one of {', '.join(data.LENSES)}")
    _filter(query)
    db = g.db_session
    mine = await db.scalars(
        select(SavedStory).where(SavedStory.user_id == current_user.id)
    )
    found = next((s for s in mine if s.query == query), None)
    if found is None:
        found = SavedStory(user_id=current_user.id, query=query)
        db.add(found)
        await db.commit()
    return _json({"id": str(found.id), "query": found.query})


@bp_stories.delete("/api/stories/saved/<uuid:sid>")
async def unsave(sid: uuid.UUID):
    db = g.db_session
    found = await db.get(SavedStory, sid)
    if found is None or found.user_id != current_user.id:
        abort(404)
    await db.delete(found)
    await db.commit()
    return _json({"deleted": str(sid)})
