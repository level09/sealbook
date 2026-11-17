"""What left this server: the page and its paged API. Analysts and admins open it;
an admin sees every case, anyone else only rows for cases they may open, plus the
timestamp requests, which carry a hash and no case."""

import orjson
from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import auth_required, current_user
from sqlalchemy import select

from sealbook.agents.tasks import can_read
from sealbook.cases.models import Case
from sealbook.monitor.views import ROLES
from sealbook.outbound import data

bp_outbound = Blueprint("outbound", __name__)


@bp_outbound.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_outbound.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


async def readable(db) -> set[str] | None:
    if current_user.has_role("admin"):
        return None
    slugs = await db.scalars(select(Case.slug))
    return {s for s in slugs if await can_read(db, current_user.id, s)}


def _filter() -> dict:
    args = request.args
    kind = args.get("kind") or None
    if kind not in (None, *data.KINDS):
        abort(400, description=f"kind is one of {', '.join(data.KINDS)}")
    try:
        page = max(1, int(args.get("page", 1)))
    except ValueError:
        abort(400, description="page is a number")
    return {
        "investigation": args.get("investigation") or None,
        "kind": kind,
        "sensitive": args.get("sensitive") in ("1", "true"),
        "page": page,
    }


@bp_outbound.get("/outbound")
async def page():
    return await render_template("outbound/index.html")


@bp_outbound.get("/api/outbound")
async def listing():
    db, f = g.db_session, _filter()
    scope = await readable(db)
    body = await data.listing(db, scope, f)
    body["today"] = await data.summary(db, scope, f)
    return Response(orjson.dumps(body), content_type="application/json")
