"""Entity pages for places and incidents: one layout, their batched data, the places
index with unresolved names, and the evidence table behind each."""

import uuid

import orjson
from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import auth_required, current_user

from stk.entities import data
from stk.ledger.write import principal_for_user
from stk.monitor.views import ROLES
from stk.people import data as people

bp_entities = Blueprint("entities", __name__)
PER_PAGE = 25
MAX_PAGE = 100


@bp_entities.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_entities.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


def _filter() -> dict:
    return {"investigation": request.args.get("investigation") or None, "time": None}


def _paging(default_sort: str) -> dict:
    args = request.args
    try:
        page = int(args.get("page", 1))
        per_page = int(args.get("per_page", PER_PAGE))
    except ValueError:
        abort(400, description="page and per_page must be integers")
    if page < 1 or not 1 <= per_page <= MAX_PAGE:
        abort(400, description=f"page from 1, per_page from 1 to {MAX_PAGE}")
    return {
        "page": page,
        "per_page": per_page,
        "sort": args.get("sort") or default_sort,
        "desc": args.get("desc", "1") == "1",
        "q": args.get("q", "").strip()[:200],
        "all": args.get("all") == "1",
    }


async def _log(db, story: dict) -> None:
    """The story view's record: one access statement per person row it showed."""
    who = await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    for row in story["rows"]:
        view = {"person": row["key"].removeprefix("person:"), "track": row}
        await people.log_view(db, view, who.id)
    await db.commit()


async def _evidence(ids: list[str]):
    p = _paging("claims")
    try:
        found = await data.evidence(
            g.db_session, ids, p["sort"], p["desc"], p["page"], p["per_page"]
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    return _json(found)


@bp_entities.get("/places")
async def places_page():
    return await render_template("places/index.html")


@bp_entities.get("/api/places/table")
async def places_table():
    try:
        return _json(await data.place_table(g.db_session, _filter(), _paging("claims")))
    except ValueError as exc:
        abort(400, description=str(exc))


@bp_entities.get("/api/places/unresolved")
async def places_unresolved():
    return _json({"items": await data.unresolved(g.db_session, _filter())})


@bp_entities.get("/places/<uuid:place_id>")
async def place_page(place_id):
    return await render_template(
        "places/place.html", boot={"kind": "place", "id": str(place_id)}
    )


@bp_entities.get("/api/places/<uuid:place_id>")
async def place_view(place_id):
    db = g.db_session
    try:
        found = await data.place(db, current_user, place_id, _filter())
    except LookupError as exc:
        abort(404, description=str(exc))
    await _log(db, found["story"])
    return _json(found)


@bp_entities.get("/api/places/<uuid:place_id>/evidence")
async def place_evidence(place_id):
    return await _evidence(await data.place_claims(g.db_session, place_id, _filter()))


@bp_entities.get("/incidents/<uuid:incident_id>")
async def incident_page(incident_id):
    return await render_template(
        "incidents/incident.html", boot={"kind": "incident", "id": str(incident_id)}
    )


@bp_entities.get("/api/incidents/<uuid:incident_id>")
async def incident_view(incident_id: uuid.UUID):
    db = g.db_session
    try:
        found = await data.incident(db, current_user, f"incident:{incident_id}")
    except LookupError as exc:
        abort(404, description=str(exc))
    await _log(db, found["story"])
    return _json(found)


@bp_entities.get("/api/incidents/<uuid:incident_id>/evidence")
async def incident_evidence(incident_id: uuid.UUID):
    try:
        ids = await data.incident_claims(g.db_session, f"incident:{incident_id}")
    except LookupError as exc:
        abort(404, description=str(exc))
    return await _evidence(ids)
