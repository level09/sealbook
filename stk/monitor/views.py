"""Evidence monitor: one page of live panels that all answer one shared filter."""

import uuid
from datetime import UTC, datetime

import orjson
from quart import (
    Blueprint,
    Response,
    abort,
    current_app,
    g,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from quart_security import auth_required, current_user
from sqlalchemy import func, select

from stk.ledger.contracts import PRIVATE_PROPS, Prop, StatementIn
from stk.ledger.models import Payload, RunEvent, Statement
from stk.ledger.write import append, principal_for_user
from stk.monitor import data
from stk.monitor import filter as mfilter
from stk.monitor.stream import Hub, events
from stk.places import resolve

monitor = Blueprint("monitor", __name__)

# ponytail: any analyst or admin sees every investigation; per-investigation access
# groups replace this check once the ledger has an access model.
ROLES = ("analyst", "admin")


@monitor.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@monitor.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


def _filter() -> dict:
    try:
        return mfilter.parse(request.args.get("filter"))
    except ValueError as exc:
        abort(400, description=str(exc))


@monitor.get("/monitor")
async def page():
    f = _filter()
    investigations = await data.investigations(g.db_session)
    if f["investigation"] is None and investigations:
        f["investigation"] = investigations[0]
    boot = {"filter": mfilter.dump(f), "investigations": investigations}
    return await render_template("monitor/index.html", boot=boot)


@monitor.get("/api/monitor/investigations")
async def investigations():
    """Case switcher options for the top bar on every page."""
    return _json({"items": await data.investigations(g.db_session)})


@monitor.get("/api/monitor/panels")
async def panels():
    db, f = g.db_session, _filter()
    last_event_id = (await db.execute(select(func.max(RunEvent.id)))).scalar() or 0
    case = {"investigation": f["investigation"], "time": None}
    visible = Statement.prop.not_in(data.META)
    everything = await data.statement_rows(
        db, mfilter.statement_clause(case), visible, limit=2000
    )
    rows = (
        await data.statement_rows(db, mfilter.statement_clause(f), visible, limit=2000)
        if f["time"]
        else everything
    )
    await _named(db, rows)
    located = [uuid.UUID(r["id"]) for r in rows if r["prop"] in data.LOCATED]
    places = await resolve.resolved(db, located, f["investigation"])
    return _json(
        {
            "filter": mfilter.dump(f),
            "last_event_id": last_event_id,
            "feed": await data.feed_rows(db, f),
            "documents": await data.document_rows(db, f),
            "queue": await _named(db, await data.queue_cards(db, f)),
            "map": data.map_layer(rows, places),
            "timeline": [
                {k: r[k] for k in ("id", "from", "to", "tier", "prop")}
                | {"agent": r["actor"]["agent"]}
                for r in everything
                if r["from"]
            ],
        }
    )


async def _named(db, rows: list[dict]) -> list[dict]:
    """Person subjects labelled by the one label rule (`people.names`), numbered
    "Unnamed person N" when every name is masked: never an id slice."""
    from stk.people import data as people  # people builds on monitor.data

    ids = sorted(
        {r["subject"][7:] for r in rows if r.get("subject", "").startswith("person:")}
    )
    names = await people.names(db, ids) if ids else {}
    unnamed: dict[str, str] = {}
    for r in rows:
        if r.get("subject", "").startswith("person:"):
            p = r["subject"][7:]
            r["subject_label"] = names.get(p) or unnamed.setdefault(
                p, f"Unnamed person {len(unnamed) + 1}"
            )
    return rows


async def _page(page):
    """A side panel page with every statement row in it named."""
    if page is None:
        abort(404)
    rows = [page] + [
        r
        for v in page.values()
        if isinstance(v, list)
        for r in v
        if isinstance(r, dict) and "subject_label" in r
    ]
    await _named(g.db_session, rows)
    return _json(page)


@monitor.get("/api/monitor/document/<sha>")
async def document(sha):
    return await _page(await data.document_page(g.db_session, sha))


@monitor.get("/api/monitor/blob/<sha>")
async def blob(sha):
    return await _page(await data.blob_page(g.db_session, sha))


@monitor.get("/api/monitor/statement/<uuid:sid>")
async def statement(sid):
    return await _page(await data.statement_page(g.db_session, sid))


@monitor.get("/api/monitor/run/<uuid:rid>")
async def run(rid):
    return await _page(await data.run_page(g.db_session, rid))


@monitor.get("/api/monitor/principal/<uuid:pid>")
async def principal(pid):
    return await _page(await data.principal_page(g.db_session, pid))


@monitor.get("/api/monitor/statements")
async def statements():
    try:
        where = data.statements_where(request.args)
    except ValueError as exc:
        abort(400, description=str(exc))
    rows = await data.statement_rows(g.db_session, *where, order=Statement.recorded_at)
    return _json({"items": await _named(g.db_session, rows)})


@monitor.post("/api/monitor/reveal")
async def reveal():
    """Show masked text. Every reveal is an access statement on the chain."""
    db = g.db_session
    item = (await request.get_json())["item"]
    if "statement" in item:
        sid = uuid.UUID(item["statement"])
        st = await db.get(Statement, sid)
        if st is None or st.prop in PRIVATE_PROPS:
            abort(404)
        value = (
            await db.execute(
                select(Payload.value)
                .join(Statement, Statement.commitment == Payload.commitment)
                .where(Statement.id == sid)
            )
        ).scalar()
        subject, result = f"statement:{sid}", {"display": data.display(value)}
    elif "blob" in item:
        page = await data.document_page(db, item["blob"], revealed=True)
        if page is None:
            abort(404)
        subject, result = f"blob:{item['blob']}", {"text": page["text"]}
    else:
        abort(400, description="reveal a statement or a blob")
    who = await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    # The time makes each reveal its own statement: the idempotency key ignores the
    # author, so two analysts' identical reveals would otherwise collapse into one.
    await append(
        db,
        StatementIn(
            subject=subject,
            prop=Prop.ACCESS,
            value={"action": "reveal", "at": datetime.now(UTC).isoformat()},
            author_id=who.id,
        ),
    )
    await db.commit()
    return _json(result)


@monitor.get("/monitor/stream")
async def stream():
    if "text/event-stream" not in request.headers.get("Accept", ""):
        # A plain navigation (a person, or `stk smoke`) gets the page, not a stream
        # that never ends.
        return redirect(url_for("monitor.page"))
    f = _filter()
    last = request.headers.get("Last-Event-ID") or request.args.get("after")
    try:
        last_id = int(last) if last else None
    except ValueError:
        abort(400, description="Last-Event-ID must be an integer")
    uniquifier, token = current_user.get_id(), session.get("_id")
    # Long-lived: hand the request's connection back to the pool now.
    db = g.pop("db_session", None)
    if db is not None:
        await db.close()
    if "monitor_hub" not in current_app.extensions:
        current_app.extensions["monitor_hub"] = Hub(
            current_app.config["SQLALCHEMY_DATABASE_URI"]
        )
    hub = current_app.extensions["monitor_hub"]
    response = await make_response(
        events(hub, f, last_id, uniquifier, token),
        {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
    response.timeout = None
    return response
