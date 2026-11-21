"""Discovery: the app proposes who was at one place at one time, and a person
decides.

`discover` runs the real `discover` tool (local, no model) over the case's
accepted or corroborated, dated, placed claims, so an act first accepts the claims
it should see (`accept`) and their places are confirmed (`places`). Then the case
analyst decides the open findings of `month`, through the review plan and one
passkey gesture each, as the Review page does:

- `incident`: `{"title", "keep": [claim keys], "roles": {record: role}}` accepts
  the candidate incident, keeping only the members `keep` names (all members when
  it is left out), each person in the role `roles` gives (method.ROLES).
- `reject`: a reason; the shared presence over the same month is rejected with it.

`out["discovery"]` counts what discovery proposed, by kind, and what was kept.
"""

from __future__ import annotations

from sqlalchemy import func, select
from stk.user.models import User

from sealbook.discovery import findings as discovery
from sealbook.discovery.models import FindingKey
from sealbook.ledger.contracts import Prop
from sealbook.monitor import seed_flows as flows
from sealbook.monitor.study import step
from sealbook.people import data as people
from sealbook.review import findings


@step("discover")
async def discover(st, act, args) -> None:
    s, month = st.s, args["month"]
    await flows.run_discover(s)
    proposed = dict(
        (
            await st.session.execute(
                select(FindingKey.kind, func.count())
                .where(FindingKey.investigation == st.inv, FindingKey.state == "open")
                .group_by(FindingKey.kind)
            )
        ).all()
    )
    report = {"proposed": proposed}
    user = await st.session.get(User, st.analyst.user_id)
    if spec := args.get("incident"):
        found = await flows.open_finding(s, discovery.INCIDENT, month)
        members = set(found.members or ())
        keep = spec.get("keep")
        kept = (
            [str(st.written[k].id) for k in keep if k in st.written]
            if keep
            else sorted(members)
        )
        if missing := [k for k in kept if k not in members]:
            raise ValueError(f"{len(missing)} kept claims are not in the finding")
        groups, _ = await people.identity(st.session)

        def cluster(subject: str) -> str:
            pid = people.pid(subject)
            return f"person:{min(groups.get(pid, {pid}))}"

        roles = {
            cluster(st.subjects[record]): role
            for record, role in (spec.get("roles") or {}).items()
            if record in st.subjects
        }
        _, planned = await findings.plan(
            st.session,
            user,
            found.key,
            kept,
            roles,
            spec["title"],
            None,
            st.analyst.id,
        )
        written = await flows.gesture(s, user, planned)
        st.others["incident"] = next(w for w in written if w.prop == Prop.INCIDENT)
        report |= {
            "incident members": len(members),
            "kept": len(kept),
            "people kept": sum(w.prop == Prop.INVOLVED for w in written),
        }
    if why := args.get("reject"):
        found = await flows.open_finding(s, discovery.SHARED, month)
        _, planned = await findings.reject(
            st.session, user, found.key, why, st.analyst.id
        )
        await flows.gesture(s, user, planned)
        report["rejected"] = 1
    st.out["discovery"] = report
