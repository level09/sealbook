"""Coalesce evidence and schedule wakes into local runs for one named person."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from stk.user.models import User

from sealbook.agents import read
from sealbook.agents.models import AgentTask, AgentWake
from sealbook.cases.models import Case
from sealbook.collaboration import notifications
from sealbook.ledger.contracts import EXTRACTED_PROPS, Event
from sealbook.ledger.models import Acquisition, Blob, Run, RunEvent, Statement
from sealbook.ledger.write import emit
from sealbook.monitor import filter as mfilter
from sealbook.monitor.views import ROLES
from sealbook.settings import Config

log = logging.getLogger(__name__)
# kind: (tool, agent name, wakes on evidence, notice when it proposes, page)
KINDS = {
    "standing": (
        "standing",
        "Standing questions",
        True,
        "A standing question has new matches.",
        "/desk",
    ),
    "contradictions": (
        "contradictions",
        "Contradiction hunter",
        True,
        "The contradiction hunter found sources that disagree.",
        "/people",
    ),
    "gaps": (
        "gaps",
        "Gap finder",
        True,
        "The gap finder found silences worth a look.",
        "/people",
    ),
    "noticer": (
        "notice",
        "Noticer",
        True,
        "The noticer found evidence that resembles other evidence.",
        "/agents/",
    ),
}
# Runs that make a source comparable by meaning or by picture and sound without
# writing claims; they wake only the noticer.
MEDIA_TOOLS = ("embed", "fingerprint")
ACTIVE = ("queued", "running")


class AccessLost(PermissionError):
    pass


async def can_read(db, user_id, investigation):
    user = await db.get(User, user_id)
    return bool(
        user
        and user.active
        and any(user.has_role(r) for r in ROLES)
        and await db.scalar(select(Case.id).where(Case.slug == investigation))
    )


async def check_access(db, run):
    if not run.inputs.get("task"):
        return
    task = await db.get(AgentTask, uuid.UUID(run.inputs["task"]))
    if task is None or not await can_read(db, task.user_id, task.investigation):
        raise AccessLost("The owner of this check no longer has access to this case.")
    principal = await read.agent_for(db, KINDS[task.kind][1], task.user_id)
    if (
        run.inputs.get("for_user") != task.user_id
        or run.inputs.get("investigation") != task.investigation
        or run.principal_id != principal.id
    ):
        raise AccessLost("The owner of this check no longer has access to this case.")


def backoff(seconds, empty_streak):
    return timedelta(seconds=seconds * 2 ** min(empty_streak, 3))


def failure_reason(error):
    kind = (error or "").partition(":")[0]
    if kind == "AccessLost":
        return "The owner of this check no longer has access to this case."
    if "Timeout" in kind:
        return "The local model did not answer in time."
    if kind == "ConnectError":
        return "The local model could not be reached."
    if kind == "RouteError":
        return "The local model route could not complete the check."
    if kind == "LookupError":
        return "The check could not load its case or owner."
    return "The check could not finish."


async def wake(db, task_id, key, reason, payload):
    await db.execute(
        insert(AgentWake)
        .values(task_id=task_id, key=key, reason=reason, payload=payload)
        .on_conflict_do_nothing(index_elements=["task_id", "key"])
    )


async def queue(db, task, wakes, model=None):
    principal = await read.agent_for(db, KINDS[task.kind][1], task.user_id)
    run = Run(
        principal_id=principal.id,
        tool=KINDS[task.kind][0],
        route=Config.AGENT_ROUTE,
        model_requested=model or Config.AGENT_MODEL,
        inputs={
            "investigation": task.investigation,
            "for_user": task.user_id,
            "task": str(task.id),
            "wakes": [str(w.id) for w in wakes],
        },
    )
    db.add(run)
    await db.flush()
    for w in wakes:
        w.run_id = run.id
    task.last_attempt_at = datetime.now(UTC)
    task.last_run_id = run.id
    return run


async def runs(db, task):
    return list(
        await db.scalars(
            select(Run)
            .where(
                Run.tool == KINDS[task.kind][0],
                Run.inputs["task"].astext == str(task.id),
            )
            .order_by(Run.created_at.desc(), Run.id)
        )
    )


async def tick(Session):  # noqa: N803
    from sealbook.agents.views import missing

    if missing():
        return 0
    # Recover a worker that stopped after sealing but before settling its task.
    async with Session() as db:
        unfinished = list(
            await db.scalars(
                select(Run.id)
                .join(AgentTask, AgentTask.last_run_id == Run.id)
                .where(
                    Run.status.in_(("done", "failed")),
                    ~exists().where(
                        RunEvent.run_id == Run.id, RunEvent.stage == "task_settled"
                    ),
                )
            )
        )
    for run_id in unfinished:
        await settle(Session, run_id)
    async with Session() as db, db.begin():
        now = datetime.now(UTC)
        due = list(
            await db.scalars(
                select(AgentTask)
                .where(
                    AgentTask.enabled.is_(True),
                    AgentTask.paused_reason.is_(None),
                    AgentTask.kind.in_(KINDS),
                    ~exists().where(
                        Run.id == AgentTask.last_run_id, Run.status.in_(ACTIVE)
                    ),
                    or_(
                        AgentTask.last_run_id.is_(None),
                        exists().where(
                            RunEvent.run_id == AgentTask.last_run_id,
                            RunEvent.stage == "task_settled",
                        ),
                    ),
                    or_(
                        AgentTask.next_at <= now,
                        exists().where(
                            AgentWake.task_id == AgentTask.id,
                            AgentWake.run_id.is_(None),
                        ),
                    ),
                    # A failed run's pending wakes must respect its retry delay.
                    or_(AgentTask.failures == 0, AgentTask.next_at <= now),
                )
                .order_by(AgentTask.created_at, AgentTask.id)
                .with_for_update(skip_locked=True)
            )
        )
        for task in due:
            pending = list(
                await db.scalars(
                    select(AgentWake)
                    .where(AgentWake.task_id == task.id, AgentWake.run_id.is_(None))
                    .order_by(AgentWake.created_at, AgentWake.id)
                )
            )
            await queue(db, task, pending)
        return len(due)


async def evidence(db, run):
    """Only sealed runs that actually wrote extracted claims wake a case, and media
    indexing wakes the noticer."""
    if run.status != "done":
        return
    kinds = [kind for kind, k in KINDS.items() if k[2]]
    if not await db.scalar(
        select(Statement.id)
        .where(Statement.run_id == run.id, Statement.prop.in_(EXTRACTED_PROPS))
        .limit(1)
    ):
        if run.tool not in MEDIA_TOOLS or not run.inputs.get("acquisition"):
            return
        kinds = ["noticer"]
    if ref := run.inputs.get("acquisition"):
        acq = await db.get(Acquisition, uuid.UUID(ref))
        investigations = [acq.context.get("investigation")]
    else:
        investigations = list(
            await db.scalars(
                select(Acquisition.context["investigation"].astext)
                .join(
                    Statement,
                    Acquisition.blob_sha256 == mfilter.source(mfilter.statement_blob()),
                )
                .where(Statement.run_id == run.id, Statement.prop.in_(EXTRACTED_PROPS))
                .distinct()
            )
        )
    for task in await db.scalars(
        select(AgentTask).where(
            AgentTask.enabled.is_(True),
            AgentTask.investigation.in_(investigations),
            AgentTask.kind.in_(kinds),
        )
    ):
        await wake(db, task.id, f"evidence:{run.id}", "evidence", {"run": str(run.id)})


async def settle(Session, run_id):  # noqa: N803
    """A task or notification failure must not stop the worker."""
    try:
        await _settle(Session, run_id)
    except Exception:
        log.exception("task settlement for run %s failed", run_id)


async def _settle(Session, run_id):  # noqa: N803
    notice = None
    async with Session() as db:
        run = await db.get(Run, run_id)
        if not run.inputs.get("task") or run.status not in ("done", "failed"):
            return
        task = await db.scalar(
            select(AgentTask)
            .where(AgentTask.id == uuid.UUID(run.inputs["task"]))
            .with_for_update()
        )
        if task.last_run_id != run.id or await db.scalar(
            select(RunEvent.id).where(
                RunEvent.run_id == run.id, RunEvent.stage == "task_settled"
            )
        ):
            return
        now = datetime.now(UTC)
        if run.status == "done":
            out = read.read_output(await db.get(Blob, run.output_hash))
            proposed = out["outcome"]["proposed"]
            task.last_success_at, task.failures = now, 0
            task.empty_streak = 0 if proposed > 0 else task.empty_streak + 1
            task.next_at = (
                now + backoff(task.every_seconds, task.empty_streak)
                if task.every_seconds
                else None
            )
            if proposed:
                notice = (
                    "standing_match" if task.kind == "standing" else "agent_found",
                    KINDS[task.kind][3],
                )
        else:
            task.failures += 1
            await db.execute(
                update(AgentWake).where(AgentWake.run_id == run.id).values(run_id=None)
            )
            task.next_at = now + timedelta(minutes=5)
            if (run.error or "").startswith("AccessLost:"):
                task.paused_reason = (
                    "The owner of this check no longer has access to this case."
                )
            elif task.failures >= 3:
                # Errors can contain model output; never copy values into notices.
                task.paused_reason = (
                    "Checking stopped after three failures. "
                    + failure_reason(run.error)
                )
            if task.paused_reason:
                notice = ("agent_paused", task.paused_reason)
        seq = await db.scalar(
            select(func.max(RunEvent.seq)).where(RunEvent.run_id == run.id)
        )
        await emit(
            db,
            Event(run_id=run.id, seq=(seq or 0) + 1, stage="task_settled", counts={}),
        )
        if notice:
            await notifications.notify(
                db,
                task.user_id,
                notice[0],
                KINDS[task.kind][1],
                notice[1],
                f"{KINDS[task.kind][4]}?investigation={quote(task.investigation)}",
                f"task:{task.id}",
            )
        else:
            await db.commit()
