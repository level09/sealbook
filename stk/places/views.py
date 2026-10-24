"""Places: search with statement counts, and case facilities entered by people."""

import uuid
from decimal import Decimal, InvalidOperation

import orjson
from quart import Blueprint, Response, abort, g, request
from quart_security import auth_required, current_user
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from stk.ledger.contracts import Tier
from stk.ledger.models import StatementTier
from stk.ledger.write import principal_for_user
from stk.monitor.views import ROLES
from stk.places import boundaries, normalise, resolve
from stk.places.models import AdminArea, Place, PlaceKind, PlaceName, point
from stk.review.views import stage

bp_places = Blueprint("places", __name__)

# Syria, with a margin for border sites.
LON, LAT = (35.7, 42.4), (32.3, 37.4)


@bp_places.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_places.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


def _governorate(p: Place) -> str | None:
    """The boundary area when the place is inside one, else the gazetteer's."""
    if p.admin1 is not None:
        return p.admin1.name_ar or p.admin1.name_en
    return p.governorate.name_ar if p.governorate else None


def _row(p: Place, statements: int, governorate: str | None) -> dict:
    return {
        "id": str(p.id),
        "name_ar": p.name_ar,
        "name_en": p.name_en,
        "kind": p.kind,
        "governorate": governorate,
        "lon": float(p.lon),
        "lat": float(p.lat),
        "source": p.source,
        "statements": statements,
    }


@bp_places.get("/api/places")
async def listing():
    db = g.db_session
    q = select(Place).order_by(Place.name_ar, Place.id).limit(200)
    if text := request.args.get("q", "").strip():
        q = q.where(
            Place.id.in_(
                select(PlaceName.place_id).where(
                    PlaceName.norm.startswith(normalise(text), autoescape=True)
                )
            )
        )
    counts = await resolve.link_counts(db, request.args.get("investigation") or None)
    places = (
        await db.execute(
            q.options(selectinload(Place.governorate), selectinload(Place.admin1))
        )
    ).scalars()
    return _json(
        {"items": [_row(p, counts.get(p.id, 0), _governorate(p)) for p in places]}
    )


def _text(body, key) -> str | None:
    value = body.get(key) or ""
    if not isinstance(value, str) or len(value.strip()) > 200:
        abort(400, description=f"{key} must be text of up to 200 characters")
    return value.strip() or None


def _coord(body, key, lo, hi) -> Decimal:
    try:
        value = Decimal(str(body[key])).quantize(Decimal("0.000001"))
    except (KeyError, InvalidOperation):
        abort(400, description=f"{key} must be a number")
    if not value.is_finite() or not lo <= value <= hi:
        abort(400, description=f"{key} must be inside Syria ({lo} to {hi})")
    return value


@bp_places.post("/api/places")
async def create():
    db = g.db_session
    body = await request.get_json()
    if not isinstance(body, dict):
        abort(400, description="body must be a JSON object")
    name, english = (_text(body, k) for k in ("name_ar", "name_en"))
    if not name:
        abort(400, description="name_ar is required")
    lon, lat = _coord(body, "lon", *LON), _coord(body, "lat", *LAT)
    author = await principal_for_user(db, current_user.id, current_user.name)
    place = Place(
        id=uuid.uuid4(),
        name_ar=name,
        name_en=english,
        kind="facility",
        geom=point(lon, lat),
        precision_m=(await db.get(PlaceKind, "facility")).default_precision_m,
        geo_source="analyst",
        source="person",
        created_by=author.id,
    )
    db.add(place)
    for n, lang in ((name, "ar"), (english, "en")):
        if n:
            db.add(
                PlaceName(
                    place_id=place.id,
                    name=n,
                    norm=normalise(n),
                    lang=lang,
                    kind="official",
                    source="person",
                )
            )
    await db.flush()
    await boundaries.assign_new(db, [place])
    area = await db.get(AdminArea, place.admin1_id) if place.admin1_id else None
    await db.commit()
    return _json(_row(place, 0, (area.name_ar or area.name_en) if area else None), 201)


@bp_places.post("/api/places/confirm")
async def confirm():
    """A person's choice of place for one position claim, signed like a review: start
    here, then send the assertion to /api/review/commit. Once accepted, the same name
    resolves by itself in that investigation and governorate when it has one
    candidate; a name with several candidates needs a person per claim."""
    db = g.db_session
    try:
        item = (await request.get_json())["item"]
        claim, place = uuid.UUID(item["statement"]), uuid.UUID(item["place"])
    except (KeyError, TypeError, ValueError, AttributeError):
        abort(400, description="item needs statement and place ids")
    names = await resolve.claim_names(db, [claim])
    if claim not in names:
        abort(404, description="not a position claim")
    if await db.get(Place, place) is None:
        abort(404, description="no such place")
    if (
        await db.scalar(
            select(StatementTier.tier).where(StatementTier.statement_id == claim)
        )
        == Tier.REJECTED
    ):
        abort(409, description="the claim is rejected")
    # ponytail: a confirmation cannot be undone yet (review undo covers reviews only);
    # add retraction of a located_at in the review flow when analysts need to change one.
    who = await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    return await stage(db, resolve.confirmation(claim, place, names[claim], who.id))
