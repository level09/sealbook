"""Map tiles: one vector tile endpoint that the monitor and story maps share, its
TileJSON with attribution, and the offline basemap file."""

import re
import uuid
from pathlib import Path

import orjson
from quart import Blueprint, Response, abort, g, request, send_file
from quart_security import auth_required, current_user
from sqlalchemy import select

from stk.discovery.service import available
from stk.ledger.contracts import Prop
from stk.ledger.models import Payload, Principal, Statement, StatementTier
from stk.ledger.write import current_clause
from stk.maps import cli, tiles
from stk.monitor import filter as mfilter
from stk.monitor.views import ROLES
from stk.people import data as people
from stk.places import boundaries
from stk.settings import Config
from stk.stories import data as stories

bp_maps = Blueprint("maps", __name__)
MAX_ZOOM = 18
CACHE = tiles.Cache()
SHA = re.compile(r"[0-9a-f]{64}")


@bp_maps.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_maps.after_request
async def no_store(response):
    # Tiles are built from what this reader may see: never shared, never kept.
    response.headers.setdefault("Cache-Control", "private, no-store")
    return response


def _filter(args) -> dict:
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


def _tiers(args) -> list[str]:
    raw = args.get("tier") or ",".join(t for t in tiles.TIERS if t != "rejected")
    picked = [t for t in raw.split(",") if t]
    if not picked or set(picked) - set(tiles.TIERS):
        abort(400, description=f"tier takes {', '.join(tiles.TIERS)}")
    return picked


async def _lens(db, args):
    """(subjects, place) for a lens: the people of a person, group or incident
    story, or one place; None for the whole filter."""
    lens, ref = args.get("lens") or "filter", args.get("ref") or ""
    if lens not in stories.LENSES:
        abort(400, description=f"lens must be one of {', '.join(stories.LENSES)}")
    if lens == "filter":
        return None, None
    if lens == "place":
        try:
            return None, uuid.UUID(ref)
        except ValueError:
            abort(400, description="a place lens needs a place id")
    groups, _ = await people.identity(db)
    if lens == "incident":
        found = await stories.incidents(db, groups, [ref])
        if not found:
            abort(404, description="no current incident with that id that you may see")
        refs = found[0]["people"]
    else:
        refs = [r for r in ref.split(",") if r.strip()]
    try:
        return stories._subjects(groups, refs), None
    except ValueError:
        abort(400, description="person ids must be UUIDs")


@bp_maps.get("/api/map/tiles/<int:z>/<int:x>/<int:y>.mvt")
async def tile(z, x, y):
    if not (0 <= z <= MAX_ZOOM and 0 <= x < 2**z and 0 <= y < 2**z):
        abort(404)
    db, args = g.db_session, request.args
    subjects, place = await _lens(db, args)
    model = await CACHE.get(
        db, current_user.id, _filter(args), _tiers(args), subjects, place
    )
    body = await tiles.tile(db, model, z, x, y)
    return Response(body, content_type="application/vnd.mapbox-vector-tile")


@bp_maps.get("/api/map/tiles.json")
async def tilejson():
    """TileJSON for the claim tiles, carrying the query through, with the boundary
    credit the admin layer needs. Each map keeps its own GeoNames credit."""
    query = request.query_string.decode()
    return Response(
        orjson.dumps(
            {
                "tilejson": "3.0.0",
                "tiles": [
                    f"{request.host_url.rstrip('/')}/api/map/tiles/{{z}}/{{x}}/{{y}}.mvt"
                    + (f"?{query}" if query else "")
                ],
                "minzoom": 0,
                "maxzoom": MAX_ZOOM,
                "attribution": await boundaries.attribution(g.db_session) or "",
                "vector_layers": [
                    {"id": layer, "fields": {}}
                    for layer in (
                        "points",
                        "sequences",
                        "uncertainty",
                        "footprints",
                        "admin",
                    )
                ],
            }
        ),
        content_type="application/json",
    )


@bp_maps.get("/api/map/basemap.json")
async def basemap_meta():
    """Where the offline basemap is, or url null when `stk basemap` was not run."""
    found = Path(Config.BASEMAP_PATH).is_file()
    return Response(
        orjson.dumps(
            {
                "url": "/api/map/basemap.pmtiles" if found else None,
                "attribution": cli.ATTRIBUTION if found else None,
            }
        ),
        content_type="application/json",
    )


@bp_maps.get("/api/map/basemap.pmtiles")
async def basemap_file():
    """The extract itself, read by range requests. Public map data, so the
    browser may keep it, but only privately."""
    path = Path(Config.BASEMAP_PATH)
    if not path.is_file():
        abort(404)
    response = await send_file(
        path, mimetype="application/vnd.pmtiles", conditional=True
    )
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@bp_maps.get("/api/map/evidence/<sha>/locations")
async def evidence_locations(sha):
    """Where a source says it was photographed or filmed: every current, kept
    claim with its tier, method, precision and bearing, for the evidence page."""
    if not SHA.fullmatch(sha):
        abort(404)
    rows = await g.db_session.execute(
        select(Statement, StatementTier.tier, Payload.value, Principal.kind)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(Principal, Principal.id == Statement.author_id)
        .where(
            Statement.subject == f"blob:{sha}",
            Statement.prop.in_((Prop.PHOTOGRAPHED_AT, Prop.FILMED_AT)),
            current_clause(),
            available(sha),
        )
        .order_by(Statement.recorded_at)
    )
    items = []
    for st, tier, value, kind in rows.all():
        lon, lat = value["geometry"]["coordinates"]
        items.append(
            {
                "id": str(st.id),
                "prop": st.prop,
                "tier": tier,
                "agent": kind == "agent",
                "method": value["method"],
                "lon": float(lon),
                "lat": float(lat),
                "precision_m": value["precision_m"],
                "bearing": value.get("bearing"),
            }
        )
    return Response(orjson.dumps({"items": items}), content_type="application/json")
