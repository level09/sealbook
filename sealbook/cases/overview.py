"""A case's Overview: where it stands, which sources wait for a model, and what each
agent found since the person last left. Every number is the one the screen that owns
it shows, read through that screen's own function; nothing here decides or writes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from sealbook.agents import contradictions, read
from sealbook.agents import overview as agents
from sealbook.anchor.models import Batch
from sealbook.desk.data import since
from sealbook.ingest import batch
from sealbook.ledger.models import Acquisition
from sealbook.people import data as people
from sealbook.review import findings

LIVE = ("queued", "running")


def _waits(steps: list[dict]) -> dict | None:
    """What a source waits on that a model or a retry can move: the step and how."""
    s = batch.attention(steps)
    if s is None:
        return None
    if s["state"] == "failed":
        return {"state": "failed", "retry": s.get("retry") or [], "words": s["words"]}
    if s["state"] == "model" or s.get("action") == "read":
        return {
            "state": "model",
            "read": s.get("action") == "read",
            "words": s["words"],
        }
    return None


async def sources(db, investigation: str) -> dict:
    """The case's sources, as Sources counts them: read, waiting, still working."""
    shas = list(
        await db.scalars(
            select(Acquisition.blob_sha256)
            .where(Acquisition.context["investigation"].astext == investigation)
            .distinct()
        )
    )
    # ponytail: steps for every source in the case on each load; page or cache this
    # when a case holds thousands of sources.
    steps = await batch.source_steps(db, {s: [s] for s in shas})
    waiting, working = [], 0
    for sha, found in steps.items():
        if w := _waits(found):
            waiting.append({"sha256": sha} | w)
        elif batch.attention(found) or any(s["state"] in LIVE for s in found):
            working += 1
    return {
        "total": len(shas),
        "read": len(steps) - len(waiting) - working,
        "working": working,
        "waiting": waiting,
    }


async def stands(db, user, investigation: str) -> dict:
    case = await read.case(db, investigation)
    # The disagreements the contradiction hunter starts from: the person page's
    # "Sources disagree", across two sources or more, one person at a time.
    forks = [c for c in contradictions.candidates(case) if not c.get("link")]
    anchored = await db.scalar(select(func.max(Batch.at)))
    return {
        "claims": await people.tiers(db, investigation),
        "sources": await sources(db, investigation),
        "disagreements": len(forks),
        "findings": len(await findings.queue(db, user, investigation)),
        "anchored": anchored.isoformat() if anchored else None,
    }


async def away(db, user, investigation: str) -> dict:
    """Each agent's last outcome in this case when it ran after the person left."""
    at, basis = await since(db, user)
    found = await agents.overview(db, user, investigation, [])
    rows = [
        {
            "key": a["key"],
            "name": a["name"],
            "text": a["last"]["text"],
            "failed": a["last"]["failed"],
            "at": a["last"]["at"],
            "does": a["does"],
            "href": a["href"] or "/agents/",
            "where": a["where"],
        }
        for a in found["agents"]
        if a["last"]
    ]
    new = [r for r in rows if datetime.fromisoformat(r["at"]) > at]
    return {
        "since": at.isoformat(),
        "basis": basis,
        "agents": new,
        "earlier": [] if new else rows,
    }


async def overview(db, user, investigation: str) -> dict:
    return {
        "investigation": investigation,
        "stands": await stands(db, user, investigation),
        "away": await away(db, user, investigation),
    }
