"""Position claims to place records. A person confirms a place once per name variant
(an accepted located_at); the variant then resolves automatically, but only inside
the same investigation and governorate, and never when the name has several
candidate places."""

import uuid
from collections import defaultdict
from dataclasses import replace

from sqlalchemy import func, select
from sqlalchemy.orm import aliased, selectinload

from sealbook.ledger.contracts import Prop, StatementIn, Tier
from sealbook.ledger.models import Acquisition, Payload, Statement, StatementTier
from sealbook.ledger.write import append, current_clause, subject_head
from sealbook.monitor import filter as mfilter
from sealbook.monitor.data import LOCATED, display
from sealbook.places import normalise
from sealbook.places.models import Place, PlaceName

SUBJECT = "statement:"


def confirmation(claim: uuid.UUID, place: uuid.UUID, name: str, author_id):
    """A person's choice of place for one position claim, to be signed. Shared by the
    confirm endpoint and the demo seeds so both write the same statement."""
    return StatementIn(
        subject=f"{SUBJECT}{claim}",
        prop=Prop.LOCATED_AT,
        value={"place": f"place:{place}", "name": normalise(name)},
        author_id=author_id,
    )


def scope(place: Place) -> uuid.UUID | None:
    """The governorate a name resolves within: the boundary polygon covering the
    place once boundaries are imported, else the gazetteer's own governorate."""
    return place.admin1_id or place.governorate_id


def _chunks(items, n=5000):
    """Slices that keep an IN list under the driver's bind-parameter limit."""
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _ref(value: dict) -> uuid.UUID:
    return uuid.UUID(value["place"].removeprefix("place:"))


async def candidates_many(db, norms) -> dict[str, list[Place]]:
    """Candidate places per normalised name, in one query per batch of names."""
    found = defaultdict(list)
    for part in _chunks(set(norms)):
        q = (
            select(PlaceName.norm, Place)
            .join(Place, Place.id == PlaceName.place_id)
            .where(PlaceName.norm.in_(part))
            .options(selectinload(Place.governorate))
            .order_by(Place.name_ar, Place.id)
        )
        for norm, place in (await db.execute(q)).all():
            found[norm].append(place)
    return found


async def candidates(db, name: str) -> list[Place]:
    norm = normalise(name)
    return (await candidates_many(db, [norm])).get(norm, [])


async def investigations_of(db, ids) -> dict[uuid.UUID, str | None]:
    """The investigation of the document each statement hangs off."""
    blob = mfilter.source(mfilter.statement_blob())
    inv = (
        select(Acquisition.context["investigation"].astext)
        .where(Acquisition.blob_sha256 == blob)
        .limit(1)
        .scalar_subquery()
    )
    out = {}
    for part in _chunks(ids):
        rows = await db.execute(select(Statement.id, inv).where(Statement.id.in_(part)))
        out |= dict(rows.all())
    return out


async def _accepted_links(db, *where) -> list[tuple[uuid.UUID, dict]]:
    """(claim id, located_at value) for every accepted link, oldest first."""
    q = (
        select(Statement.subject, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.prop == Prop.LOCATED_AT,
            StatementTier.tier == Tier.ACCEPTED,
            current_clause(),
            *where,
        )
        .order_by(Statement.recorded_at, Statement.id)
    )
    return [
        (uuid.UUID(s.removeprefix(SUBJECT)), v) for s, v in (await db.execute(q)).all()
    ]


async def _places(db, ids) -> dict[uuid.UUID, Place]:
    out = {}
    for part in _chunks(set(ids)):
        q = (
            select(Place)
            .where(Place.id.in_(part))
            .options(selectinload(Place.governorate))
        )
        out |= {p.id: p for p in (await db.execute(q)).scalars()}
    return out


async def claim_names(db, ids) -> dict[uuid.UUID, str]:
    out = {}
    for part in _chunks(ids):
        q = (
            select(Statement.id, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(Statement.id.in_(part), Statement.prop.in_(LOCATED))
        )
        out |= {i: n for i, v in (await db.execute(q)).all() if (n := display(v))}
    return out


async def resolved(
    db, statement_ids: list[uuid.UUID], investigation: str | None
) -> dict[uuid.UUID, Place | None]:
    """A claim resolves through its own accepted link, or through an accepted link on
    the same name variant in its investigation and governorate. With `investigation`
    set, claims from other investigations stay unresolved."""
    out: dict[uuid.UUID, Place | None] = dict.fromkeys(statement_ids)
    names = await claim_names(db, statement_ids)
    own = await investigations_of(db, list(names))
    if investigation:
        names = {i: n for i, n in names.items() if own[i] == investigation}

    direct = {}
    for part in _chunks(SUBJECT + str(i) for i in names):
        for claim, value in await _accepted_links(db, Statement.subject.in_(part)):
            direct[claim] = _ref(value)
    by_id = await _places(db, direct.values())
    for claim, place_id in direct.items():
        out[claim] = by_id.get(place_id)

    norms = {i: normalise(n) for i, n in names.items() if out[i] is None}
    if not norms:
        return out
    confirmed = defaultdict(list)  # norm -> [(claim id, place id)]
    for part in _chunks(set(norms.values())):
        for claim, value in await _accepted_links(
            db, Payload.value["name"].astext.in_(part)
        ):
            confirmed[value["name"]].append((claim, _ref(value)))
    where = await investigations_of(
        db, [c for pairs in confirmed.values() for c, _ in pairs]
    )
    linked = await _places(db, [p for pairs in confirmed.values() for _, p in pairs])
    found = await candidates_many(db, norms.values())
    for claim, norm in norms.items():
        if len(found[norm]) != 1 or own[claim] is None:
            continue
        gov = scope(found[norm][0])
        # The person's own choice for this variant, in this investigation and the
        # candidate's governorate; two different choices there need a person.
        chosen = {
            p
            for c, p in confirmed[norm]
            if where.get(c) == own[claim] and scope(linked[p]) == gov
        }
        if gov is not None and len(chosen) == 1:
            out[claim] = linked[chosen.pop()]
    return out


async def propose(db, run_id, author_id, sign, only=None) -> int:
    """Agent located_at proposals for unresolved position claims that have exactly one
    candidate place, limited to the claim ids in `only` when given. Zero or several
    candidates need a person, so nothing is written."""
    claimed = {
        s.removeprefix(SUBJECT)
        for s in (
            await db.execute(
                select(Statement.subject).where(
                    Statement.prop == Prop.LOCATED_AT,
                    Statement.subject.like(SUBJECT + "%"),
                )
            )
        ).scalars()
    }
    live = (
        select(Statement.id)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.prop.in_(LOCATED),
            StatementTier.tier != Tier.REJECTED,
            current_clause(),
        )
    )
    if only is not None:
        live = live.where(Statement.id.in_(list(only)))
    ids = [i for i in (await db.execute(live)).scalars() if str(i) not in claimed]
    places = await resolved(db, ids, None)
    names = await claim_names(db, ids)
    found = await candidates_many(db, [normalise(n) for n in names.values()])
    written = 0
    for claim in ids:
        options = found[normalise(names[claim])] if claim in names else []
        if places[claim] is not None or len(options) != 1:
            continue
        proposal = confirmation(claim, options[0].id, names[claim], author_id)
        await append(
            db,
            replace(
                proposal,
                run_id=run_id,
                based_on=await subject_head(db, proposal.subject),
            ),
            sign=sign,
        )
        written += 1
    return written


async def link_counts(db, investigation: str | None) -> dict[uuid.UUID, int]:
    """Claims per place that a person or agent link accepted, not rejected claims,
    inside one investigation when given.
    ponytail: claims that resolve through a confirmed name variant are not counted;
    count them too if the places list needs it, at the cost of resolving every claim."""
    target = aliased(Statement)
    target_tier = aliased(StatementTier)
    place = Payload.value["place"].astext
    q = (
        select(place, func.count(target.id.distinct()))
        .select_from(Statement)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .join(target, func.concat(SUBJECT, target.id) == Statement.subject)
        .join(target_tier, target_tier.statement_id == target.id)
        .where(
            Statement.prop == Prop.LOCATED_AT,
            StatementTier.tier == Tier.ACCEPTED,
            current_clause(),
            target.prop.in_(LOCATED),
            target_tier.tier != Tier.REJECTED,
            mfilter.statement_clause(
                {"investigation": investigation, "time": None}, target
            ),
        )
        .group_by(place)
    )
    return {
        uuid.UUID(ref.removeprefix("place:")): n
        for ref, n in (await db.execute(q)).all()
    }
