"""Watchlist matcher: a deterministic run that proposes same_as between a new person
mention and a watched person. Names alone never match: a name variant needs at
least one equal corroborating field."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sealbook.ingest.service import system_agent
from sealbook.ledger import write
from sealbook.ledger.contracts import (
    EvidenceKind,
    Prop,
    Refused,
    StatementIn,
    Tier,
)
from sealbook.ledger.models import Payload, Principal, Run, Statement, StatementTier
from sealbook.monitor.data import display
from sealbook.people import data as people
from sealbook.search.arabic import candidates, normalize
from sealbook.watch.models import Watch

AGENT_NAME = "matcher"
TOOL = "match"
CORROBORATING = (Prop.BIRTH_DATE, Prop.FATHER_NAME, Prop.DETENTION_BRANCH)


async def matcher_agent(s: AsyncSession) -> Principal:
    return await system_agent(s, AGENT_NAME)


def match_run(agent: Principal, acquisition: str, blob: str, names: list) -> Run:
    """A queued match run over new NAME statements. Local route: it reads only
    ledger rows, never bytes, but runs under the source document's policy."""
    return Run(
        principal_id=agent.id,
        tool=TOOL,
        route="local",
        inputs={
            "blobs": [blob],
            "acquisition": acquisition,
            "statements": [str(i) for i in names],
        },
    )


async def cluster(s: AsyncSession, person: str) -> set[str]:
    """The watched mention plus every mention the person view joins to it."""
    groups, _ = await people.identity(s)
    return {f"person:{m}" for m in groups.get(people.pid(person), {people.pid(person)})}


def value_text(value) -> str:
    # Dates may be stored as {"date": ...} (demo seed) as well as {"text": ...}.
    return display(value)


async def facts(s: AsyncSession, subjects: set[str]) -> dict[str, list[str]]:
    """Names and corroborating values of these mentions in the draft projection
    (every non-rejected statement)."""
    rows = (
        await s.execute(
            select(Statement.prop, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.subject.in_(subjects),
                Statement.prop.in_([Prop.NAME, *CORROBORATING]),
                StatementTier.tier != Tier.REJECTED,
            )
            # Oldest first, so the evidence shown for a match does not vary by run.
            .order_by(Statement.recorded_at, Statement.id)
        )
    ).all()
    out: dict[str, list[str]] = {}
    for prop, value in rows:
        out.setdefault(prop, []).append(value_text(value))
    return out


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", normalize(text)).strip()


def _same(prop: str, a: str, b: str) -> bool:
    a, b = _key(a), _key(b)
    if a == b:
        return True
    # A year-only register entry equals a full date at the coarser precision.
    short, long = sorted((a, b), key=len)
    return prop == Prop.BIRTH_DATE and bool(short) and long.startswith(short + "-")


def corroborate(new: dict, watched: dict) -> dict | None:
    """Pure: the name variant and equal fields that tie a new mention to a watched
    person, or None. A name without a corroborating field is never a match."""
    name = next(
        (
            [a, b]
            for a in new.get(Prop.NAME, [])
            for b in watched.get(Prop.NAME, [])
            if candidates(a, b)
        ),
        None,
    )
    if name is None:
        return None
    fields = [
        {"prop": str(p), "value": a}
        for p in CORROBORATING
        for a in dict.fromkeys(new.get(p, []))
        if any(_same(p, a, b) for b in watched.get(p, []))
    ]
    return {"name": name, "fields": fields} if fields else None


async def basis(s: AsyncSession, subjects: set[str], evidence: dict) -> list[str]:
    """The statements a match rests on: both names and every agreeing field. The
    proposal names them so review can check they still stand."""
    rows = (
        await s.execute(
            select(Statement.id, Statement.prop, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.subject.in_(subjects),
                Statement.prop.in_([Prop.NAME, *CORROBORATING]),
                StatementTier.tier != Tier.REJECTED,
            )
            .order_by(Statement.id)
        )
    ).all()
    names = {_key(n) for n in evidence["name"]}
    return [
        f"statement:{sid}"
        for sid, prop, value in rows
        if (prop == Prop.NAME and _key(value_text(value)) in names)
        or any(
            f["prop"] == prop and _same(prop, value_text(value), f["value"])
            for f in evidence["fields"]
        )
    ]


async def match(
    s: AsyncSession, statement_ids: list[uuid.UUID], run: Run, sign
) -> dict:
    """Propose same_as for each new NAME statement that matches a watched person.
    Re-running writes nothing new: the write path keys proposals on the author."""
    names = (
        await s.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.id.in_(statement_ids),
                Statement.prop == Prop.NAME,
                StatementTier.tier != Tier.REJECTED,
            )
        )
    ).all()
    persons = (await s.execute(select(Watch.person).distinct())).scalars().all()
    watched = {}
    for p in persons:
        members = await cluster(s, p)
        watched[p] = (members, await facts(s, members))
    written, refused = [], []
    for st, value in names:
        new = await facts(s, {st.subject}) | {Prop.NAME: [value_text(value)]}
        for person, (members, known) in watched.items():
            evidence = None if st.subject in members else corroborate(new, known)
            if evidence is None:
                continue
            try:
                out = await write.append(
                    s,
                    StatementIn(
                        subject=st.subject,
                        prop=Prop.SAME_AS,
                        value={
                            "person": person.removeprefix("person:"),
                            "basis": await basis(s, members | {st.subject}, evidence),
                        },
                        author_id=run.principal_id,
                        run_id=run.id,
                        span_blob=st.span_blob,
                        span=st.span,
                        evidence=(EvidenceKind.CORROBORATING_FIELD,),
                        based_on=await write.subject_head(s, st.subject),
                    ),
                    sign=sign,
                )
            except Refused as exc:
                refused.append(exc.reason)
                continue
            written.append(str(out.id))
    return {"written": written, "refused": refused}
