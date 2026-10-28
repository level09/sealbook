"""Agent API: read and regenerate an intake brief, the briefs a person has not read
yet, start the linker from a source or a person, and follow a run. Runs act for the
person who started them; nothing here accepts or writes a fact."""

import os
import re
import uuid
from datetime import UTC, datetime

import orjson
from quart import Blueprint, Response, abort, g, request
from quart_security import auth_required, current_user
from sqlalchemy import select

from sealbook.agents import (
    approximate,
    brief,
    contradictions,
    linker,
    noticer,
    overview,
    standing,
    tasks,
)
from sealbook.agents.models import AgentTask
from sealbook.agents.read import read_output
from sealbook.collaboration.models import Notification
from sealbook.ledger.models import Acquisition, Blob, Run, Statement
from sealbook.ledger.write import principal_for_user
from sealbook.monitor import filter as mfilter
from sealbook.monitor.views import ROLES
from sealbook.settings import Config

bp_agents = Blueprint("agents", __name__, url_prefix="/api/agents")
SCOPE = re.compile(
    r"person:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|blob:[0-9a-f]{64}"
)


def missing() -> list[str]:
    """Settings the agents still need; empty when they can run."""
    route_setting = {
        "local": "ROUTE_LOCAL_BASE_URL",
        "anthropic": "ANTHROPIC_API_KEY",
    }.get(Config.AGENT_ROUTE, "OPENROUTER_API_KEY")
    return [
        name
        for name, value in (
            ("AGENT_MODEL", Config.AGENT_MODEL),
            (route_setting, os.environ.get(route_setting)),
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


@bp_agents.post("/contradictions")
async def find_contradictions():
    from sealbook.cases.service import Unknown, require

    db = g.db_session
    investigation = (await _item()).get("investigation")
    if not isinstance(investigation, str) or not investigation.strip():
        abort(400, description="Choose a case to check.")
    try:
        case = await require(db, investigation)
    except Unknown:
        abort(404)
    if gap := missing():
        return _json({"reason": "off", "missing": gap}, 409)
    await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    try:
        run = await contradictions.queue(
            db, case.slug, current_user.id, Config.AGENT_MODEL
        )
    except ValueError as exc:
        return _json({"reason": "busy", "message": str(exc)}, 409)
    await db.commit()
    return _json({"run": str(run.id)}, 202)


@bp_agents.get("/contradictions")
async def read_contradictions():
    from sealbook.cases.service import Unknown, require

    db = g.db_session
    try:
        case = await require(db, request.args.get("investigation"))
    except Unknown:
        abort(404)
    return _json(await contradictions.show(db, current_user, case.slug))


@bp_agents.get("/contradictions/runs/<uuid:rid>")
async def contradiction_status(rid):
    run = await g.db_session.get(Run, rid)
    if run is None or run.tool != contradictions.TOOL:
        abort(404)
    if run.inputs.get("for_user") != current_user.id and not current_user.has_role(
        "admin"
    ):
        abort(404)
    return _json({"status": run.status})


@bp_agents.post("/approximate")
async def find_approximate():
    """Ask the place finder to suggest approximate places for one case's names."""
    from sealbook.cases.service import Unknown, require

    db = g.db_session
    investigation = (await _item()).get("investigation")
    if not isinstance(investigation, str) or not investigation.strip():
        abort(400, description="Choose a case to read.")
    try:
        case = await require(db, investigation)
    except Unknown:
        abort(404)
    if gap := missing():
        return _json({"reason": "off", "missing": gap}, 409)
    await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    try:
        run = await approximate.queue(
            db, case.slug, current_user.id, Config.AGENT_MODEL
        )
    except ValueError as exc:
        return _json({"reason": "busy", "message": str(exc)}, 409)
    await db.commit()
    return _json({"run": str(run.id)}, 202)


@bp_agents.get("/approximate/runs/<uuid:rid>")
async def approximate_status(rid):
    run = await g.db_session.get(Run, rid)
    if run is None or run.tool != approximate.TOOL:
        abort(404)
    if run.inputs.get("for_user") != current_user.id:
        abort(404)
    out = None
    if run.status == "done":
        out = read_output(await g.db_session.get(Blob, run.output_hash))["outcome"]
    elif run.status == "failed":
        out = {"proposed": 0, "reason": tasks.failure_reason(run.error)}
    return _json({"status": run.status, "outcome": out})


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


@bp_agents.get("/gaps")
async def read_gaps():
    from sealbook.agents import gaps
    from sealbook.cases import service as cases

    try:
        case = await cases.require(g.db_session, request.args.get("investigation"))
    except cases.Unknown:
        abort(404)
    return _json(await gaps.show(g.db_session, current_user, case.slug))


@bp_agents.post("/gaps")
async def find_gaps():
    from sealbook.agents import gaps
    from sealbook.cases import service as cases

    db = g.db_session
    investigation = (await _item()).get("investigation")
    if not isinstance(investigation, str) or not investigation.strip():
        abort(400, description="Choose a case to find gaps.")
    try:
        case = await cases.require(db, investigation)
    except cases.Unknown:
        abort(404)
    if missing():
        return _json(
            {
                "reason": "off",
                "message": "Gap finder needs a local model. Ask an administrator to enable it.",
            },
            409,
        )
    if any(
        r.status in ("queued", "running")
        for r in await gaps.runs(db, case.slug, current_user.id)
    ):
        return _json(
            {"reason": "busy", "message": "Gap finder is reading this case."}, 409
        )
    await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    run = await gaps.queue(db, case.slug, current_user.id, Config.AGENT_MODEL)
    await db.commit()
    return _json({"run": str(run.id)}, 202)


async def _required_case(db, slug):
    from sealbook.cases import service as cases

    try:
        return (await cases.require(db, slug)).slug
    except cases.Unknown:
        abort(404)


@bp_agents.get("/overview")
async def agents_overview():
    """Every agent working for this person in the case, and their recent runs."""
    db = g.db_session
    investigation = await _required_case(db, request.args.get("investigation"))
    return _json(await overview.overview(db, current_user, investigation, missing()))


@bp_agents.get("/notice")
async def read_notice():
    db = g.db_session
    investigation = await _required_case(db, request.args.get("investigation"))
    sha = request.args.get("sha") or None
    return _json(await noticer.show(db, current_user, investigation, sha))


@bp_agents.post("/notice")
async def check_notice():
    """Check now: the whole case against everything this person may read."""
    db = g.db_session
    investigation = (await _item()).get("investigation")
    if not isinstance(investigation, str) or not investigation.strip():
        abort(400, description="Choose a case to check.")
    investigation = await _required_case(db, investigation)
    if missing():
        return _json(
            {
                "reason": "off",
                "message": "The noticer needs a local model. Ask an administrator to set it up.",
            },
            409,
        )
    if any(
        r.status in tasks.ACTIVE
        for r in await noticer.runs(db, investigation, current_user.id)
    ):
        return _json(
            {"reason": "busy", "message": "The noticer is reading this case."}, 409
        )
    await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    run = await noticer.queue(db, investigation, current_user.id, Config.AGENT_MODEL)
    await db.commit()
    return _json({"run": str(run.id)}, 202)


@bp_agents.post("/notice/link")
async def propose_notice_link():
    """The person's click: send one resemblance into review as a proposed link."""
    item = await _item()
    try:
        rid = uuid.UUID(str(item.get("run")))
    except ValueError:
        abort(400, description="Choose a resemblance.")
    key = item.get("key")
    if not isinstance(key, str):
        abort(400, description="Choose a resemblance.")
    db = g.db_session
    await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    try:
        statement = await noticer.propose(db, current_user, rid, key)
    except LookupError:
        abort(404)
    except noticer.NotProposed as exc:
        return _json({"message": str(exc)}, 409)
    return _json({"statement": statement}, 201)


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
    if run is None or run.tool not in (
        brief.TOOL,
        linker.TOOL,
        standing.TOOL,
        noticer.TOOL,
    ):
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


async def _owned_task(tid, lock=False):
    query = select(AgentTask).where(
        AgentTask.id == tid, AgentTask.user_id == current_user.id
    )
    task = await g.db_session.scalar(query.with_for_update() if lock else query)
    if task is None or not await tasks.can_read(
        g.db_session, current_user.id, task.investigation
    ):
        abort(404)
    return task


@bp_agents.post("/tasks")
async def create_task():
    item = await _item()
    kind, question = item.get("kind"), item.get("question")
    if kind not in tasks.KINDS:
        abort(400, description="Choose which agent keeps checking.")
    if kind == "standing" and (
        not isinstance(question, str) or not 3 <= len(question.strip()) <= 300
    ):
        abort(400, description="Ask a question of 3 to 300 characters.")
    db = g.db_session
    investigation = item.get("investigation")
    if not isinstance(investigation, str) or not await tasks.can_read(
        db, current_user.id, investigation
    ):
        abort(404)
    interval = item.get("every_seconds", 86400)
    if interval is not None and (type(interval) is not int or interval <= 0):
        abort(
            400, description="The check interval must be a positive number of seconds."
        )
    await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    if kind != "standing":
        # One standing check per person, case and agent: asking again resumes it.
        task = await db.scalar(
            select(AgentTask)
            .where(
                AgentTask.user_id == current_user.id,
                AgentTask.investigation == investigation,
                AgentTask.kind == kind,
            )
            .with_for_update()
        )
        if task:
            task.enabled, task.paused_reason, task.failures = True, None, 0
            task.next_at = datetime.now(UTC)
            await db.commit()
            return _json({"id": str(task.id)}, 200)
    task = AgentTask(
        user_id=current_user.id,
        investigation=investigation,
        kind=kind,
        spec={"question": question.strip()} if kind == "standing" else {},
        every_seconds=interval,
        next_at=datetime.now(UTC),
    )
    db.add(task)
    await db.commit()
    return _json({"id": str(task.id)}, 201)


@bp_agents.get("/tasks")
async def list_tasks():
    db = g.db_session
    query = select(AgentTask).where(AgentTask.user_id == current_user.id)
    if investigation := request.args.get("investigation"):
        query = query.where(AgentTask.investigation == investigation)
    query = query.where(AgentTask.kind == (request.args.get("kind") or "standing"))
    items = []
    for task in await db.scalars(
        query.order_by(AgentTask.created_at.desc(), AgentTask.id)
    ):
        if not await tasks.can_read(db, current_user.id, task.investigation):
            continue
        found = await tasks.runs(db, task)
        active = any(r.status in tasks.ACTIVE for r in found)
        good = next((r for r in found if r.status == "done"), None)
        out = read_output(await db.get(Blob, good.output_hash)) if good else None
        items.append(
            {
                "id": str(task.id),
                "question": task.spec.get("question"),
                "enabled": task.enabled,
                "state": "checking"
                if active
                else "paused"
                if task.paused_reason or not task.enabled
                else "watching",
                "reason": task.paused_reason
                or ("You stopped this check." if not task.enabled else None),
                "last_checked": task.last_success_at.isoformat()
                if task.last_success_at
                else None,
                "matches": out["outcome"]["proposed"] if out else None,
            }
        )
    return _json({"items": items, "off": bool(missing())})


@bp_agents.post("/tasks/<uuid:tid>/run")
async def check_task(tid):
    task = await _owned_task(tid, lock=True)
    db = g.db_session
    run = await db.get(Run, task.last_run_id) if task.last_run_id else None
    if run and run.status in tasks.ACTIVE:
        return _json({"message": "This is already being checked."}, 409)
    if not task.enabled or task.paused_reason:
        return _json({"message": "Resume this before checking it."}, 409)
    if missing():
        return _json(
            {"message": "Agents are off until the local model is set up."},
            409,
        )
    await tasks.wake(db, task.id, f"manual:{uuid.uuid4()}", "manual", {})
    task.next_at = datetime.now(UTC)
    await db.commit()
    return _json({"state": "checking"}, 202)


@bp_agents.patch("/tasks/<uuid:tid>")
async def toggle_task(tid):
    task = await _owned_task(tid, lock=True)
    enabled = (await _item()).get("enabled")
    if type(enabled) is not bool:
        abort(400, description="Choose whether this keeps checking.")
    task.enabled = enabled
    if enabled:
        task.paused_reason, task.failures = None, 0
        task.next_at = datetime.now(UTC)
    await g.db_session.commit()
    return _json({"enabled": task.enabled})


@bp_agents.get("/tasks/<uuid:tid>")
async def read_task(tid):
    if (await _owned_task(tid)).kind != "standing":
        abort(404)
    return _json(await standing.show(g.db_session, current_user, tid))
