"""A case's Overview: where it stands, which sources wait for a model, and what each
agent found since the person last left. Every number is the one the screen that owns
it shows, read through that screen's own function; nothing here decides or writes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from sealbook.agents import contradictions, read
from sealbook.agents import overview as agents
from sealbook.anchor.models import Batch
from sealbook.cases import conclusions
from sealbook.desk.data import since
from sealbook.discovery.models import FindingKey
from sealbook.discovery.service import available
from sealbook.guide import film
from sealbook.ingest import batch
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Acquisition, Payload, Statement, StatementTier
from sealbook.monitor import filter as mfilter
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
        # Search indexing is a side step: a source read while its indexing waits
        # is read, as its own page says.
        elif batch.attention(found) or any(
            s["state"] in LIVE and not s.get("side") for s in found
        ):
            working += 1
    return {
        "total": len(shas),
        "read": len(steps) - len(waiting) - working,
        "working": working,
        "waiting": waiting,
    }


async def holds(db, user, investigation: str) -> dict:
    """What the case holds by kind and tier, counted as a conclusion may cite it
    (`conclusions.citeable`), so the accepted numbers include whatever a
    conclusion rests on: claims from its available sources, about people or
    documents; same-person links between its person records; its findings; and
    copy matches between its items. Rejected, erased and superseded ones are left
    out. Open findings are those the reader's Discover lists."""
    live = (
        StatementTier.tier != Tier.REJECTED,
        write.current_clause(),
        Payload.value.is_not(None),
    )
    case = mfilter.statement_clause({"investigation": investigation, "time": None})

    async def count(*where, tiers=(Tier.ACCEPTED, Tier.UNCONFIRMED)) -> dict:
        found = dict(
            (
                await db.execute(
                    select(StatementTier.tier, func.count())
                    .select_from(Statement)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .where(*live, *where)
                    .group_by(StatementTier.tier)
                )
            )
            .tuples()
            .all()
        )
        return {str(t): found.get(t, 0) for t in tiers}

    accepted_findings = await db.scalar(
        select(func.count())
        .select_from(FindingKey)
        .join(StatementTier, StatementTier.statement_id == FindingKey.statement_id)
        .where(
            FindingKey.investigation == investigation,
            StatementTier.tier == Tier.ACCEPTED,
        )
    )
    return {
        "claims": await count(
            Statement.prop.in_([str(p) for p in conclusions.CLAIMS]),
            case,
            available(mfilter.source(mfilter.statement_blob())),
            tiers=(Tier.ACCEPTED, Tier.CORROBORATED, Tier.UNCONFIRMED),
        ),
        "links": await count(
            Statement.prop.in_(conclusions.LINKS),
            Statement.subject.in_(conclusions._persons(investigation)),
        ),
        "matches": await count(Statement.prop == Prop.MEDIA_MATCH, case),
        "findings": {
            "accepted": accepted_findings,
            "unconfirmed": len(await findings.queue(db, user, investigation)),
        },
    }


async def stands(db, user, investigation: str) -> dict:
    case = await read.case(db, investigation)
    # The disagreements the contradiction hunter starts from: the person page's
    # "Sources disagree", across two sources or more, one person at a time.
    forks = [c for c in contradictions.candidates(case) if not c.get("link")]
    anchored = await db.scalar(select(func.max(Batch.at)))
    items = await holds(db, user, investigation)
    return {
        "items": items,
        "sources": await sources(db, investigation),
        "disagreements": len(forks),
        "findings": items["findings"]["unconfirmed"],
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
        "film": film.for_case(investigation),
    }
