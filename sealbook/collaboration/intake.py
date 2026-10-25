"""Tell the collector once when every run of an acquisition batch has finished, and
queue the linker and the intake brief for them. A batch is the acquisitions sharing
context["batch"] (set at upload)."""

import logging
import uuid
from urllib.parse import quote

from sqlalchemy import Text, cast, func, select, text

from sealbook.collaboration.models import Notification
from sealbook.collaboration.notifications import notify
from sealbook.ledger.models import Acquisition, Principal, Run

log = logging.getLogger(__name__)


async def finished(Session, run_id):  # noqa: N803
    """A notification problem must not stop the worker loop."""
    try:
        await _finished(Session, run_id)
    except Exception:
        log.exception("intake notification for run %s failed", run_id)


async def _finished(Session, run_id):  # noqa: N803
    async with Session() as s:
        run = await s.get(Run, run_id)
        if not run.inputs.get("acquisition"):
            return
        acq = await s.get(Acquisition, uuid.UUID(run.inputs["acquisition"]))
        batch = acq.context.get("batch")
        if not batch:
            return
        owner = await s.scalar(
            select(Principal.user_id).where(Principal.id == acq.collector_id)
        )
        if owner is None:
            return
        ids = select(cast(Acquisition.id, Text)).where(
            Acquisition.context["batch"].astext == batch
        )
        counts = dict(
            (
                await s.execute(
                    select(Run.status, func.count())
                    .where(Run.inputs["acquisition"].astext.in_(ids))
                    .group_by(Run.status)
                )
            ).all()
        )
        if counts.get("queued") or counts.get("running"):
            return
        subject = f"batch:{batch}"
        # One notification per batch even when its last two runs finish together.
        await s.execute(
            text("select pg_advisory_xact_lock(hashtext(:b))"), {"b": subject}
        )
        if await s.scalar(
            select(Notification.id).where(
                Notification.user_id == owner,
                Notification.kind == "intake_done",
                Notification.subject == subject,
            )
        ):
            return
        failed = counts.get("failed", 0)
        await notify(
            s,
            owner,
            "intake_done",
            "Intake finished",
            f"{sum(counts.values())} runs done"
            + (f", {failed} failed" if failed else ""),
            f"/sources?batch={quote(batch)}",
            subject,
        )
        await agents(s, batch, acq.context.get("investigation"), owner)


async def agents(s, batch, investigation, owner):
    """The linker first, so the brief can name the links it proposed. Off until the
    local route and an agent model are set; a batch then simply has no brief."""
    from sealbook.agents import brief, linker
    from sealbook.agents.views import missing
    from sealbook.settings import Config

    if missing() or not investigation or await brief.runs(s, batch):
        return
    await linker.queue(s, investigation, owner, Config.AGENT_MODEL, f"batch:{batch}")
    await brief.queue(s, batch, owner, Config.AGENT_MODEL)
    await s.commit()
