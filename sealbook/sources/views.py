"""Sources: the dense, server-paged table of every acquired source in a case."""

import orjson
from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import current_user

from sealbook.collaboration.views import guard, no_store
from sealbook.discovery.evidence import granted
from sealbook.ledger.contracts import Refused
from sealbook.sources import data, review

bp = Blueprint("sources", __name__)
bp.before_request(guard)
bp.after_request(no_store)


def output(value, status=200):
    return Response(orjson.dumps(value), status=status, content_type="application/json")


@bp.errorhandler(ValueError)
async def invalid(exc):
    return output({"message": str(exc)}, 400)


@bp.errorhandler(PermissionError)
async def forbidden(exc):
    return output({"message": str(exc)}, 403)


@bp.errorhandler(Refused)
async def refused(exc):
    return output({"message": exc.reason}, 409)


def _filter() -> dict:
    try:
        return data.parse(request.args, current_user.id)
    except ValueError as exc:
        abort(400, description=str(exc))


@bp.get("/sources")
async def page():
    return await render_template(
        "sources/index.html",
        boot={"collectors": await data.collectors(g.db_session), "me": current_user.id},
    )


@bp.get("/api/sources")
async def listing():
    found = await data.listing(g.db_session, _filter())
    for r in found["items"]:
        # A held reveal means the tile may be fetched; otherwise the client keeps it locked.
        r["revealed"] = granted(r["sha256"]) or not r["sensitive"]
    return output(found)


@bp.get("/api/sources/ids")
async def ids():
    return output(await data.ids(g.db_session, _filter()))


@bp.post("/api/sources/review")
async def request_review():
    item = ((await request.get_json(silent=True)) or {}).get("item") or {}
    out = await review.request(g.db_session, current_user, item)
    return output(out, 200 if "proposals" in out else 201)
