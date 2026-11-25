"""The Desk: home for a signed-in analyst. Same access rule as the sources it reads."""

from quart import Blueprint, g, render_template, request
from quart_security import current_user

from sealbook.desk import data
from sealbook.discovery.evidence import granted
from sealbook.sources import data as sources
from sealbook.web import json_response, protect

bp = Blueprint("desk", __name__)
protect(bp)


@bp.errorhandler(ValueError)
async def invalid(exc):
    return json_response({"error": str(exc)}, 400)


def _args():
    try:
        page, per_page = (
            int(request.args.get("page", 1)),
            int(request.args.get("per_page", 10)),
        )
    except ValueError:
        raise ValueError("page and per_page must be integers") from None
    return request.args.get("investigation") or None, page, per_page


@bp.get("/desk")
async def page():
    return await render_template("desk/index.html")


@bp.get("/api/desk/changes")
async def changes():
    return json_response(await data.changes(g.db_session, current_user, *_args()))


@bp.get("/api/desk/needs")
async def needs():
    return json_response(await data.needs(g.db_session, current_user, *_args()))


@bp.get("/api/desk/recent")
async def recent():
    f = sources.parse(request.args, current_user.id)
    found = await data.recent(g.db_session, current_user, f)
    for r in found["items"]:
        # As on Sources: without a held reveal the tile stays locked and asks nothing.
        r["revealed"] = granted(r["sha256"]) or not r["sensitive"]
    return json_response(found)
