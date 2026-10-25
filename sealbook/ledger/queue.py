"""The run table is the job queue: claim with SKIP LOCKED, honour available_at,
and reclaim runs whose worker died (lease expired). No Celery, Redis or DBOS."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sealbook.ledger.models import Run
from sealbook.settings import Config

# A model call must end inside its run's lease, or another worker reclaims the run
# and calls again.
LEASE = timedelta(seconds=max(300, Config.ROUTE_LOCAL_TIMEOUT + 60))

_CLAIM = text(
    """
    update run set status = 'running', attempts = attempts + 1,
                   lease_until = now() + make_interval(secs => :lease)
    where id = (
        select id from run
        where ((status = 'queued' and available_at <= now())
            or (status = 'running' and lease_until < now()))
          and (cardinality(cast(:tools as text[])) = 0 or tool = any(cast(:tools as text[])))
        order by available_at
        for update skip locked
        limit 1
    )
    returning id
    """
)


async def claim(session: AsyncSession, tools: list[str] | None = None) -> Run | None:
    """Claim the next due run, or None. Commit promptly so the lease is visible."""
    params = {"lease": LEASE.total_seconds(), "tools": tools or []}
    run_id = (await session.execute(_CLAIM, params)).scalar()
    return await session.get(Run, run_id) if run_id else None


async def fail(session: AsyncSession, run: Run, error: str) -> None:
    """A failed run stays failed and visible. No retry onto another route."""
    run.status = "failed"
    run.error = error
    run.lease_until = None
    await session.flush()
