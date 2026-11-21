"""The case's signed conclusion (`sealbook/cases/conclusions.py`). The analyst first
accepts exactly what it rests on: claims by key, the linker's proposals for the
planted same-person pairs ("same_person"), the strong matches inside the planted
copies ("copies"); "links" cites every accepted same-person link. A match proposal
cannot be cited, so "copies" is accepted and not cited. With `peer_review` a second
analyst signs agreement."""

from __future__ import annotations

from sealbook.cases import conclusions
from sealbook.monitor.seed import Seeder
from sealbook.monitor.seed_study import REVIEWER, colleague, copies
from sealbook.monitor.study import step
from sealbook.monitor.study.people import planted_pairs


@step("conclusion")
async def conclusion(st, act, args) -> None:
    c = st.spec.get("conclusion")
    if not c:
        return
    cite, accept = [], []
    for item in c["rests_on"]:
        if item == "links":
            cite.append(item)
        elif item == "same_person":
            accept += await planted_pairs(st)
        elif item == "copies":
            accept += await copies(st.s, st.spec)
        else:
            found = st.written.get(item)
            if found is None:
                raise ValueError(
                    f"the conclusion rests on {item!r}, which no source has given"
                )
            cite.append(str(found.id))
            if found not in st.accepted:
                accept.append(found)
    for found in accept:
        await st.s.review(
            st.analyst, found, "accept", "what the case's conclusion rests on"
        )
        st.accepted.append(found)
    await st.session.commit()
    form = {k: c[k] for k in ("text", "standard", "alternatives", "not_established")}
    rows = await conclusions.seed(
        st.session, st.inv, form | {"rests_on": cite}, st.analyst.id, st.s.assertion
    )
    if c.get("peer_review"):
        # A second Seeder: each holds the passkey of the analyst it made.
        other = Seeder(st.session)
        reviewer = await colleague(other, st.inv, *REVIEWER)
        await conclusions.seed_peer_review(
            st.session, rows[0], st.analyst.id, reviewer.id, other.assertion
        )
    await st.session.commit()
    st.out["signed conclusion"] = 1
