"""Who is who, as a person settles it.

- `decline`: the case analyst declines, signed with her reason `why`, each open
  same-person proposal that joins a record to one the case plants as someone else
  (truth `twins`), as the Review page's reject writes it. Nothing else is
  declined, and it is the person's decision: an act boundary before it leaves the
  proposal open for a reader.
- `recognise`: `{"record", "as"}`. Reading matches a planted record to the person
  extraction wrote by the exact name; a paper that prints the name another way
  (a passport's "KASSAR / ADRIAN") is matched here instead, by the written form the
  case names. It writes nothing to the ledger: it tells the seeder which written
  person stands for the record, then matches that record's planted claims to what
  extraction wrote about that person, as reading does, and counts them in
  `out["read"]`."""

from __future__ import annotations

from datetime import date

from sealbook.ledger.contracts import Prop, Tier
from sealbook.monitor.seed_study import pending
from sealbook.monitor.study import reading, step
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


@step("recognise")
async def recognise(st, act, args) -> None:
    record = args["record"]
    source = st.records[record]["source"]
    if source not in st.arrived or record in st.subjects:
        return
    got = [g for g in await reading._written(st, [source]) if g[2] != Tier.REJECTED]
    named = [
        f
        for f, v, _, _ in got
        if f.prop == Prop.NAME
        and reading._norm(reading._shown(v)) == reading._norm(args["as"])
    ]
    if not named:
        return
    st.subjects[record] = subject = named[0].subject
    total = st.out.setdefault("read", {"planted": 0, "recovered": 0, "written": 0})
    for c in st.claims[source]:
        if c["record"] != record or c["key"] in st.written:
            continue
        want = date.fromisoformat(c["from"]) if c["from"] else None
        same = [
            f
            for f, v, _, _ in got
            if f.subject == subject
            and f.prop == c["prop"]
            and (
                c["prop"] == Prop.NAME
                or reading.same_value(c["prop"], reading._shown(v), c["value"])
            )
            and (want is None or f.valid_from == want)
        ]
        if same:
            st.written[c["key"]] = same[0]
            if c["key"] in st.missed:
                st.missed.remove(c["key"])
            total["recovered"] += 1
    st.out["recognised"] = st.out.get("recognised", 0) + 1
