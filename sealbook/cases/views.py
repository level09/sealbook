"""Case list, creation and a case's Overview. Analysts and admins only."""

import orjson
from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import current_user

from sealbook.cases import conclusions, overview, service
from sealbook.collaboration.views import guard, no_store
from sealbook.ledger.write import principal_for_user

bp_cases = Blueprint("cases", __name__, url_prefix="/api/cases")
bp_cases.before_request(guard)
bp_cases.after_request(no_store)
bp_case_pages = Blueprint("case_pages", __name__)
bp_case_pages.before_request(guard)
bp_case_pages.after_request(no_store)


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


@bp_cases.get("")
async def listing():
    status = await conclusions.statuses(g.db_session)
    items = await service.listing(g.db_session)
    return _json(
        {"items": [c | {"status": status.get(c["slug"], "open")} for c in items]}
    )


@bp_cases.post("")
async def create():
    item = ((await request.get_json(silent=True)) or {}).get("item") or {}
    db = g.db_session
    who = await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    try:
        case = await service.create(
            db,
            str(item.get("name") or ""),
            str(item.get("description") or ""),
            who.id,
            confirm=item.get("confirm") is True,
        )
    except (service.Taken, service.Close) as exc:
        reason = "taken" if isinstance(exc, service.Taken) else "close"
        return _json(
            {"reason": reason, "message": str(exc), "match": service.row(exc.match)},
            409,
        )
    except ValueError as exc:
        return _json({"reason": "invalid", "message": str(exc)}, 400)
    await db.commit()
    return _json({"item": service.row(case)}, 201)


@bp_case_pages.get("/cases/overview")
async def overview_page():
    return await render_template("cases/overview.html")


@bp_cases.get("/overview")
async def case_overview():
    db = g.db_session
    try:
        case = await service.require(db, request.args.get("investigation"))
    except service.Unknown:
        abort(404)
    return _json(await overview.overview(db, current_user, case.slug))


# --- conclusions ---------------------------------------------------------------
# Reads only; every write is a signed gesture through /api/review/conclusions/*.


async def _slug(db) -> str:
    try:
        return (await service.require(db, request.args.get("investigation"))).slug
    except service.Unknown:
        abort(404)


@bp_case_pages.get("/cases/conclusions/new")
async def conclusion_form():
    return await render_template(
        "cases/conclusion_form.html", boot={"scale": conclusions.standards()}
    )


@bp_case_pages.get("/cases/conclusions/<uuid:cid>")
async def conclusion_page(cid):
    return await render_template("cases/conclusion.html", boot={"id": str(cid)})


@bp_cases.get("/conclusions")
async def conclusion_list():
    db = g.db_session
    slug = await _slug(db)
    out = await conclusions.listing(db, slug, current_user)
    shown = [
        i
        for c in out["conclusions"]
        if c.get("text")
        for i in [c["id"], *c["rests_on"], *c["contradicts"]]
    ]
    await conclusions.log_view(db, current_user, slug, shown)
    await db.commit()
    return _json(out)


@bp_cases.get("/conclusions/citeable")
async def conclusion_citeable():
    db = g.db_session
    return _json(await conclusions.citeable(db, await _slug(db)))


@bp_cases.get("/conclusions/<uuid:cid>")
async def conclusion_detail(cid):
    db = g.db_session
    out = await conclusions.detail(db, cid, current_user)
    if out is None:
        abort(404)
    c = out["conclusion"]
    if c.get("text"):
        await conclusions.log_view(
            db,
            current_user,
            out["investigation"],
            [c["id"], *c["rests_on"], *c["contradicts"]],
        )
        await db.commit()
    return _json(out)
