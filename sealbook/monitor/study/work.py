"""People working the case together, through the collaboration service.

- `assign`: the case owner assigns `sources` to the colleague, due in `days`.
- `ask`: the owner asks the colleague to check the claim `claim` (a planted key
  the app has written), with `why`; a review request, unsigned, as the UI sends.
- `reply`: the colleague answers that request with `text`.
- `mark`: the Bayanat work step on the assigned `sources`: `"status": "done"`
  (Updated, marked by the colleague) or `"returned"` (Returned, the owner sends it
  back to the colleague for changes).
- `first pass`: the case analyst accepts, with a signature, the first pass the app
  proposed on `sources` (triage, title, description, labels).

The colleague is an active analyst (COLLEAGUE), as assignment and review need; the
signing analyst's account is a demo passkey holder and cannot be assigned work.
"""

from __future__ import annotations

from sqlalchemy import select
from stk.user.models import User

from sealbook.collaboration import assignments
from sealbook.collaboration.models import Assignment
from sealbook.ledger.contracts import ASSESSED_PROPS, Tier
from sealbook.ledger.models import Statement, StatementTier
from sealbook.monitor import seed_flows as flows
from sealbook.monitor.seed_full import users
from sealbook.monitor.study import step

COLLEAGUE = ("noor.adeyemi", "Noor Adeyemi")


async def colleague(st) -> User:
    if "colleague" not in st.others:
        _, user = await users(st.session, st.app, (COLLEAGUE[0],))
        user.name = COLLEAGUE[1]
        await st.session.flush()
        st.others["colleague"] = user
    return st.others["colleague"]


def _subjects(st, keys) -> list[str]:
    return [f"blob:{st.s.blobs[k]}" for k in keys]


@step("assign")
async def assign(st, act, args) -> None:
    to = await colleague(st)
    await flows.assign(
        st.s,
        st.owner,
        to,
        _subjects(st, args["sources"]),
        args.get("days", 2),
        args.get("priority", "normal"),
    )


@step("mark")
async def mark(st, act, args) -> None:
    status = args["status"]
    by = await colleague(st) if status == "done" else st.owner
    found = await st.session.scalars(
        select(Assignment.id).where(
            Assignment.subject.in_(_subjects(st, args["sources"])),
            Assignment.status == "open",
        )
    )
    for aid in list(found):
        await assignments.update(st.session, by, aid, {"status": status})


@step("ask")
async def ask(st, act, args) -> None:
    to = await colleague(st)
    claim = st.written.get(args["claim"])
    if claim is None:
        raise ValueError(f"no claim {args['claim']!r} has been written to ask about")
    row = await flows.request(st.s, claim, st.owner, to, args["why"])
    st.others[f"request:{args['claim']}"] = row.id


@step("reply")
async def reply(st, act, args) -> None:
    by = await colleague(st)
    await flows.reply(st.s, st.others[f"request:{args['claim']}"], by, args["text"])


@step("first pass")
async def first_pass(st, act, args) -> None:
    found = await st.session.scalars(
        select(Statement)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.subject.in_(_subjects(st, args["sources"])),
            Statement.prop.in_([str(p) for p in ASSESSED_PROPS]),
            StatementTier.tier == Tier.UNCONFIRMED,
        )
        .order_by(Statement.recorded_at)
    )
    for proposal in list(found):
        await st.s.review(
            st.analyst, proposal, "accept", args.get("why") or "the first pass holds"
        )
