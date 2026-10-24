"""Person lookup: name search in any spelling, and one view per person."""

import orjson
from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import auth_required, current_user

from stk.ledger.write import principal_for_user
from stk.monitor.views import ROLES
from stk.people import data

bp_people = Blueprint("people", __name__)


@bp_people.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_people.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


@bp_people.get("/people")
async def index():
    return await render_template("people/index.html")


@bp_people.get("/api/people")
async def everyone():
    found = await data.roster(g.db_session, request.args.get("investigation") or None)
    names = await data.names(g.db_session, list(found))
    return _json(
        {
            "items": [
                {"person": p, "name": names.get(p)} | v
                for p, v in sorted(found.items())
            ]
        }
    )


@bp_people.get("/api/people/search")
async def search():
    query = request.args.get("q", "").strip()
    if not query:
        abort(400, description="q is required")
    items = await data.search(
        g.db_session, query, request.args.get("investigation") or None
    )
    names = await data.names(g.db_session, [i["person"] for i in items])
    return _json({"items": [i | {"name": names.get(i["person"])} for i in items]})


@bp_people.get("/people/<uuid:person_id>")
async def page(person_id):
    return await render_template(
        "people/person.html", boot={"person": str(person_id), "gap_days": data.GAP_DAYS}
    )


@bp_people.get("/api/people/<uuid:person_id>")
async def view(person_id):
    """Every view is an access statement listing the track it showed."""
    db = g.db_session
    try:
        found = await data.person(
            db, person_id, request.args.get("projection", "draft")
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    if found is None:
        abort(404)
    who = await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    await data.log_view(db, found, who.id)
    await db.commit()
    return _json(found)


@bp_people.post("/api/people/<uuid:person_id>/reveal")
async def reveal(person_id):
    """Show this person's masked values. One access statement lists every value
    revealed, the same record a single reveal in the monitor leaves."""
    db = g.db_session
    found = await data.person(db, person_id, "draft")
    if found is None:
        abort(404)
    masked = [r for rows in found["facts"].values() for r in rows if r["masked"]]
    values = await data.values(db, [r["id"] for r in masked])
    who = await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    await data.log_access(db, found["person"], "reveal", list(values), who.id)
    await db.commit()
    return _json({"values": values})
