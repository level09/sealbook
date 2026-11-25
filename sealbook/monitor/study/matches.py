"""The app's match proposals decided by a person, and the desk searching the pile.

- `matches`: counts the match proposals written so far (`out["match proposals"]`).
  The fingerprint tool writes them at arrival; nothing is decided here.
- `confirm copies`: the case analyst confirms, signed, each open strong match
  proposal that joins two clips of the planted family (`truth.original` and
  `truth.copies`), as the relationship `relationship` (default `same_recording`)
  with `why`, as the evidence page's Confirm does after the two are opened side by
  side. Weak proposals, and any outside the family, stay for a person.
- `search`: the desk searches the case for `query` as the search page does, keyword
  only (no embedding route is recorded), with the access logged as the page logs
  it. `out["search"][query]` lists the source keys found, in order.

The planted family stands for what a person sees side by side: the same footage.
The app's proposals are never accepted on the app's word.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from sealbook.discovery.evidence import copy_reason
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop, StatementIn, Tier
from sealbook.ledger.models import Payload, Statement, StatementTier
from sealbook.monitor import filter as mfilter
from sealbook.monitor.seed_study import copy_family
from sealbook.monitor.study import step
from sealbook.search import find

RELATIONSHIPS = ("same_recording", "contains", "contained_in", "related")


async def _open(st) -> list[tuple]:
    """Open match proposals between the case's clips: (statement, value, the two
    source keys)."""
    key_of = {f"blob:{sha}": k for k, sha in st.s.blobs.items() if k in st.arrived}
    rows = await st.session.execute(
        select(Statement, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.prop == Prop.MEDIA_MATCH,
            Statement.subject.in_(list(key_of)),
            StatementTier.tier == Tier.UNCONFIRMED,
        )
        .order_by(Statement.recorded_at)
    )
    return [
        (found, value, {key_of[found.subject], key_of.get(f"blob:{value['blob']}")})
        for found, value in rows.all()
        if f"blob:{value['blob']}" in key_of
    ]


async def _review(st, target, verdict: str, reason: str, **extra) -> None:
    """A signed review, as the evidence page writes it: `at` makes each gesture its
    own statement, and an accepted match names its relationship."""
    await write.append(
        st.session,
        StatementIn(
            subject=f"statement:{target.id}",
            prop=Prop.REVIEW,
            value={
                "verdict": verdict,
                "reason": reason,
                "at": datetime.now(UTC).isoformat(),
            }
            | extra,
            author_id=st.analyst.id,
        ),
        sign=st.s.assertion,
    )


@step("matches")
async def matches(st, act, args) -> None:
    st.out["match proposals"] = await st.session.scalar(
        select(func.count())
        .select_from(Statement)
        .where(
            Statement.prop == Prop.MEDIA_MATCH,
            Statement.subject.in_([f"blob:{sha}" for sha in st.s.blobs.values()]),
        )
    )


@step("confirm copies")
async def confirm_copies(st, act, args) -> None:
    relationship = args.get("relationship", "same_recording")
    if relationship not in RELATIONSHIPS:
        raise ValueError(f"no relationship {relationship!r}")
    family = copy_family(st.spec)
    done = 0
    for found, value, pair in await _open(st):
        if pair <= family and copy_reason(value)[2] == "strong":
            await _review(st, found, "accept", args["why"], relationship=relationship)
            st.accepted.append(found)
            done += 1
    st.out["copies confirmed"] = st.out.get("copies confirmed", 0) + done


@step("search")
async def search(st, act, args) -> None:
    query = args["query"]
    f = mfilter.parse(None) | {"investigation": st.inv}
    found = await find.find(
        st.session, query, f, types=("sources", "passages", "media"), limit=50
    )
    key_of = {sha: k for k, sha in st.s.blobs.items()}
    keys: list[str] = []
    for group in ("media", "passages", "sources"):
        for item in found["groups"][group]["items"]:
            k = key_of.get(item["sha256"])
            if k and k not in keys:
                keys.append(k)
    who = await write.principal_of(st.session, st.owner)
    await write.append(
        st.session,
        StatementIn(
            subject="search:evidence",
            prop=Prop.ACCESS,
            value={
                "action": "search",
                "query": query,
                "mode": "keyword",
                "filter": mfilter.dump(f),
                "at": datetime.now(UTC).isoformat(),
            },
            author_id=who.id,
        ),
    )
    st.out.setdefault("search", {})[query] = keys
