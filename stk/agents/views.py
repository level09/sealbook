"""Agent API: read and regenerate an intake brief, the briefs a person has not read
yet, start the linker from a source or a person, and follow a run. Runs act for the
person who started them; nothing here accepts or writes a fact."""

import os
import re
import uuid

import orjson
from quart import Blueprint, Response, abort, g, request
from quart_security import auth_required, current_user
from sqlalchemy import select

from stk.agents import brief, linker
from stk.agents.read import read_output
from stk.collaboration.models import Notification
from stk.ledger.models import Acquisition, Blob, Run, Statement
from stk.ledger.write import principal_for_user
from stk.monitor import filter as mfilter
from stk.monitor.views import ROLES
from stk.settings import Config

bp_agents = Blueprint("agents", __name__, url_prefix="/api/agents")
SCOPE = re.compile(
    r"person:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|blob:[0-9a-f]{64}"
)


def missing() -> list[str]:
    """Settings the agents still need; empty when they can run."""
    return [
        name
        for name, value in (
            ("AGENT_MODEL", Config.AGENT_MODEL),
            ("ROUTE_LOCAL_BASE_URL", os.environ.get("ROUTE_LOCAL_BASE_URL")),
        )
        if not value
    ]


@bp_agents.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_agents.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


async def _item() -> dict:
    return ((await request.get_json(silent=True)) or {}).get("item") or {}


def _batch(raw) -> str:
    try:
        return str(uuid.UUID(str(raw)))
    except ValueError:
        abort(400, description="batch must be an intake batch id")


@bp_agents.get("/brief")
async def read_brief():
    batch = _batch(request.args.get("batch"))
    try:
        out = await brief.show(
            g.db_session, current_user, batch, request.args.get("version")
        )
    except LookupError:
        abort(404)
    return _json(out | {"missing": missing()})


@bp_agents.post("/brief")
async def regenerate():
    """A new version of the brief; earlier ones stay readable."""
    batch = _batch((await _item()).get("batch"))
    db = g.db_session
    if not await db.scalar(
        select(Acquisition.id)
        .where(Acquisition.context["batch"].astext == batch)
        .limit(1)
    ):
        abort(404)
    if gap := missing():
        return _json({"reason": "off", "missing": gap}, 409)
    if any(r.status in ("queued", "running") for r in await brief.runs(db, batch)):
        return _json(
            {"reason": "busy", "message": "A new version is being written."}, 409
        )
    await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    run = await brief.queue(db, batch, current_user.id, Config.AGENT_MODEL)
    await db.commit()
    return _json({"run": str(run.id)}, 202)


@bp_agents.get("/briefs")
async def unread():
    """Briefs of finished batches whose notification this person has not read,
    newest first: the Desk shows the first until it is read."""
    db = g.db_session
    case = request.args.get("investigation") or None
    notes = (
        await db.scalars(
            select(Notification)
            .where(
                Notification.user_id == current_user.id,
                Notification.kind == "intake_done",
                Notification.read_at.is_(None),
                Notification.subject.startswith("batch:"),
            )
            .order_by(Notification.created_at.desc(), Notification.id)
            .limit(5)
        )
    ).all()
    items = []
    for n in notes:
        shown = await brief.show(db, current_user, n.subject.removeprefix("batch:"))
        if case and shown.get("investigation") not in (case, None):
            continue
        if shown["brief"] is None and shown["pending"] is None:
            continue
        items.append(
            shown | {"notification": str(n.id), "at": n.created_at.isoformat()}
        )
    return _json({"items": items, "missing": missing()})


@bp_agents.post("/link")
async def find_links():
    """Run the linker over the case of a source or a person, focused on it."""
    scope = str((await _item()).get("scope") or "")
    if not SCOPE.fullmatch(scope):
        abort(400, description="scope must be person:<uuid> or blob:<sha256>")
    db = g.db_session
    investigation = await _case(db, scope)
    if investigation is None:
        abort(404)
    if gap := missing():
        return _json({"reason": "off", "missing": gap}, 409)
    await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    run = await linker.queue(
        db, investigation, current_user.id, Config.AGENT_MODEL, focus=scope
    )
    await db.commit()
    return _json({"run": str(run.id)}, 202)


async def _case(db, scope: str) -> str | None:
    if scope.startswith("blob:"):
        sha = scope.removeprefix("blob:")
    else:
        sha = await db.scalar(
            select(mfilter.source(mfilter.statement_blob()))
            .where(Statement.subject == scope, Statement.span_blob.is_not(None))
            .limit(1)
        )
    if sha is None:
        return None
    return await db.scalar(
        select(Acquisition.context["investigation"].astext)
        .where(Acquisition.blob_sha256 == sha)
        .order_by(Acquisition.at)
        .limit(1)
    )


@bp_agents.get("/runs/<uuid:rid>")
async def run_status(rid):
    db = g.db_session
    run = await db.get(Run, rid)
    if run is None or run.tool not in (brief.TOOL, linker.TOOL):
        abort(404)
    if run.inputs.get("for_user") != current_user.id and not current_user.has_role(
        "admin"
    ):
        abort(404)
    out = (
        read_output(await db.get(Blob, run.output_hash))
        if run.status == "done" and run.output_hash
        else {}
    )
    return _json(
        {
            "id": str(run.id),
            "tool": run.tool,
            "status": run.status,
            "error": run.error,
            "model": run.model_served or run.model_requested,
            "written": out.get("written", []),
            "candidates": out.get("candidates"),
            "already_known": out.get("already_known"),
            "valid": out.get("valid"),
            "cost": out.get("cost"),
        }
    )
