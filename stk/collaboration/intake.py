"""Tell the collector once when every run of an acquisition batch has finished.
A batch is the acquisitions sharing context["batch"] (set at upload)."""

import logging
import uuid
from urllib.parse import quote

from sqlalchemy import Text, cast, func, select, text

from stk.collaboration.models import Notification
from stk.collaboration.notifications import notify
from stk.ledger.models import Acquisition, Principal, Run

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
