"""Who is who, as a person settles it.

- `decline`: the case analyst declines, signed with her reason `why`, each open
  same-person proposal that joins a record to one the case plants as someone else
  (truth `twins`), as the Review page's reject writes it. Nothing else is
  declined, and it is the person's decision: an act boundary before it leaves the
  proposal open for a reader."""

from __future__ import annotations

from sealbook.ledger.contracts import Prop
from sealbook.monitor.seed_study import pending
from sealbook.monitor.study import step
from sealbook.people import data as people


async def namesakes(st) -> list:
    """The open same-person proposals that join two records the truth plants as
    different people."""
    groups, _ = await people.identity(st.session)

    def cluster(subject: str) -> str:
        p = people.pid(subject)
        return min(groups.get(p, {p}))

    apart = {
        frozenset(cluster(st.subjects[r]) for r in pair)
        for pair in st.spec["truth"].get("twins") or []
        if all(r in st.subjects for r in pair)
    }
    subjects = sorted(set(st.subjects.values()))
    return [
        found
        for found, v in await pending(st.session, Prop.SAME_AS, subjects)
        if frozenset({cluster(found.subject), cluster(f"person:{v['person']}")})
        in apart
    ]


@step("decline")
async def decline(st, act, args) -> None:
    for found in await namesakes(st):
        await st.s.review(st.analyst, found, "reject", args["why"])
        st.out["declined"] = st.out.get("declined", 0) + 1
