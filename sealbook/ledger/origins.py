"""Only a human-confirmed same-recording relationship collapses source origins."""

from sqlalchemy import or_, select

from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Acquisition, Payload, Statement, StatementTier
from sealbook.monitor.filter import source


async def relationship(db, sid):
    from sealbook.ledger.write import _attested

    reviews = (
        await db.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(
                Statement.subject == f"statement:{sid}", Statement.prop == Prop.REVIEW
            )
            .order_by(Statement.recorded_at.desc())
        )
    ).all()
    for review, value in reviews:
        if not await _attested(db, review):
            continue
        undoes = (
            await db.execute(
                select(Statement).where(
                    Statement.subject == f"statement:{review.id}",
                    Statement.prop == Prop.RETRACTION,
                )
            )
        ).scalars()
        if any([await _attested(db, undo) for undo in undoes]):
            continue
        return value.get("relationship") if value.get("verdict") == "accept" else None
    return None


async def groups(db, shas):
    parents = {sha: sha for sha in shas}

    def root(sha):
        while parents[sha] != sha:
            sha = parents[sha]
        return sha

    frontier, seen = set(shas), set()
    while frontier:
        rows = (
            await db.execute(
                select(Statement, Payload.value)
                .join(Payload, Payload.commitment == Statement.commitment)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .where(
                    Statement.prop == Prop.MEDIA_MATCH,
                    StatementTier.tier == Tier.ACCEPTED,
                    or_(
                        Statement.subject.in_([f"blob:{sha}" for sha in frontier]),
                        Payload.value["blob"].astext.in_(frontier),
                    ),
                )
            )
        ).all()
        frontier = set()
        for st, value in rows:
            if st.id in seen:
                continue
            seen.add(st.id)
            if await relationship(db, st.id) != "same_recording":
                continue
            a, b = st.subject.removeprefix("blob:"), value["blob"]
            for sha in (a, b):
                if sha not in parents:
                    parents[sha] = sha
                    frontier.add(sha)
            ra, rb = root(a), root(b)
            parents[max(ra, rb)] = min(ra, rb)
    return {sha: root(sha) for sha in parents}


async def count(db, st):
    from sealbook.ledger.write import current_clause

    value_key = (
        select(Payload.value_key)
        .where(Payload.commitment == st.commitment)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(source(Statement.span_blob), Acquisition.origin)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(Acquisition, Acquisition.blob_sha256 == source(Statement.span_blob))
            .where(
                Statement.subject == st.subject,
                Statement.prop == st.prop,
                Payload.value_key == value_key,
                current_clause(),
            )
        )
    ).all()
    if not rows:
        return 0
    components = await groups(db, {sha for sha, _ in rows})
    return min(
        len({components[sha] for sha, _ in rows}), len({origin for _, origin in rows})
    )
