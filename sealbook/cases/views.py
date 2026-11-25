"""Case list, creation and a case's Overview. Analysts and admins only."""

from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import current_user

from sealbook.cases import chronology, conclusions, overview, service
from sealbook.ledger.write import principal_of
from sealbook.web import json_response, protect

bp_cases = Blueprint("cases", __name__, url_prefix="/api/cases")
protect(bp_cases)
bp_case_pages = Blueprint("case_pages", __name__)
protect(bp_case_pages)


@bp_cases.get("")
async def listing():
    status = await conclusions.statuses(g.db_session)
    items = await service.listing(g.db_session)
    return json_response(
        {"items": [c | {"status": status.get(c["slug"], "open")} for c in items]}
    )


@bp_cases.post("")
async def create():
    item = ((await request.get_json(silent=True)) or {}).get("item") or {}
    db = g.db_session
    who = await principal_of(db, current_user)
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
        return json_response(
            {"reason": reason, "message": str(exc), "match": service.row(exc.match)},
            409,
        )
    except ValueError as exc:
        return json_response({"reason": "invalid", "message": str(exc)}, 400)
    await db.commit()
    return json_response({"item": service.row(case)}, 201)


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
    return json_response(await overview.overview(db, current_user, case.slug))


@bp_cases.get("/chronology.xlsx")
async def chronology_sheet():
    """Accepted and corroborated claims in date order, as a spreadsheet."""
    db = g.db_session
    try:
        case = await service.require(db, request.args.get("investigation"))
    except service.Unknown:
        abort(404)
    body = await chronology.workbook(
        db, current_user, case.slug, case.name, request.host_url
    )
    return Response(
        body,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{case.slug}-chronology.xlsx"'
        },
    )


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
    return json_response(out)


@bp_cases.get("/conclusions/citeable")
async def conclusion_citeable():
    db = g.db_session
    return json_response(await conclusions.citeable(db, await _slug(db)))


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
    return json_response(out)
