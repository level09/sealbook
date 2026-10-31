"""To do: everything waiting on this person in one list, biggest decisions first. Each
row opens the screen where it is decided; nothing is decided here."""

from sqlalchemy import Text, func, select
from sqlalchemy.orm import aliased

from sealbook import method
from sealbook.discovery.service import available
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Payload, Principal, Statement, StatementTier
from sealbook.monitor import filter as mfilter
from sealbook.review import events, findings, queue

LIMIT = 50


async def asked(db, user) -> list[dict]:
    """Open review requests addressed to this person, oldest first."""
    target = aliased(StatementTier)
    rows = (
        await db.execute(
            select(Statement, Payload.value, Principal.name)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(Principal, Principal.id == Statement.author_id)
            .join(
                target,
                func.concat("statement:", target.statement_id.cast(Text))
                == Statement.subject,
            )
            .where(
                Statement.prop == Prop.REVIEW_REQUEST,
                Payload.value["reviewer"].as_integer() == user.id,
                target.tier == Tier.UNCONFIRMED,
            )
            .order_by(Statement.recorded_at)
            .limit(LIMIT)
        )
    ).all()
    return [
        {
            "request": str(st.id),
            "statement": st.subject.removeprefix("statement:"),
            "from": name,
            "reason": v["reason"],
            "at": st.recorded_at.isoformat(),
        }
        for st, v, name in rows
    ]


async def triage(db, investigation: str | None) -> list[dict]:
    """Items whose first pass waits, high priority first."""
    title = aliased(Statement)
    title_value = aliased(Payload)
    rows = (
        await db.execute(
            select(Statement.subject, Payload.value, title_value.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .outerjoin(
                title,
                (title.subject == Statement.subject) & (title.prop == Prop.TITLE),
            )
            .outerjoin(title_value, title_value.commitment == title.commitment)
            .where(
                Statement.prop == Prop.TRIAGE,
                StatementTier.tier == Tier.UNCONFIRMED,
                available(Statement.span_blob),
                mfilter.statement_clause(
                    {"investigation": investigation, "time": None}
                ),
            )
            .order_by(Statement.recorded_at)
        )
    ).all()
    seen, out = set(), []
    for subject, v, t in rows:
        if subject in seen or v is None:
            continue
        seen.add(subject)
        out.append(
            {
                "sha": subject.removeprefix("blob:"),
                "title": t["title"] if t else None,
                "triage": method.words(Prop.TRIAGE, v),
                "priority": method.priority(v["content"], v["reliability"]),
            }
        )
    out.sort(key=lambda r: r["priority"] != "high")
    return out[:LIMIT]


async def todo(db, user, investigation: str | None) -> dict:
    found = await findings.queue(db, user, investigation)
    groups = await events.groups(db, user, investigation)
    claims = len(await queue.ids(db, user, "open", investigation))
    mine = await asked(db, user)
    read = await triage(db, investigation)
    return {
        # The page heading and the rail badge both show this number.
        "total": len(mine) + len(read) + len(groups) + len(found) + claims,
        "asked": mine,
        "triage": read,
        "groups": [
            {
                "id": g["id"],
                "posts": g["count"]["posts"],
                "links": g["count"]["links"],
                "summary": g["summary"],
            }
            for g in groups
        ],
        "findings": [
            {
                "id": f["id"],
                "kind": f["kind"],
                "label": f["label"],
                "reason": f["reason"],
                "band": f["band"],
            }
            for f in found
        ],
        "claims": claims,
        # Review lists at most queue.LIMIT, so the count stops there.
        "claims_more": claims >= queue.LIMIT,
    }
