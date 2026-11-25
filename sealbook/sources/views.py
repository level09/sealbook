"""Sources: the dense, server-paged table of every acquired source in a case."""

from quart import Blueprint, abort, g, render_template, request
from quart_security import current_user

from sealbook.discovery.evidence import granted
from sealbook.ingest import batch
from sealbook.ledger.contracts import Refused
from sealbook.sources import data, review
from sealbook.web import json_response, protect

bp = Blueprint("sources", __name__)
protect(bp)


@bp.errorhandler(ValueError)
async def invalid(exc):
    return json_response({"message": str(exc)}, 400)


@bp.errorhandler(PermissionError)
async def forbidden(exc):
    return json_response({"message": str(exc)}, 403)


@bp.errorhandler(Refused)
async def refused(exc):
    return json_response({"message": exc.reason}, 409)


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
    steps = await batch.source_steps(
        g.db_session, {r["sha256"]: [r["sha256"]] for r in found["items"]}
    )
    for r in found["items"]:
        # A held reveal means the tile may be fetched; otherwise the client keeps it locked.
        r["revealed"] = granted(r["sha256"]) or not r["sensitive"]
        # What waits on a person or a model, so the next step shows from the list.
        nxt = batch.attention(steps.get(r["sha256"], []))
        r["next"] = (
            {k: nxt[k] for k in ("state", "chip", "label", "words")} if nxt else None
        )
    return json_response(found)


@bp.get("/api/sources/ids")
async def ids():
    return json_response(await data.ids(g.db_session, _filter()))


@bp.post("/api/sources/review")
async def request_review():
    item = ((await request.get_json(silent=True)) or {}).get("item") or {}
    out = await review.request(g.db_session, current_user, item)
    return json_response(out, 200 if "proposals" in out else 201)
