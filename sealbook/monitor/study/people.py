"""Who is who. `clusters`: the records each desk had already joined, as accepted
same-person links (and each sheet row joined to its record). `links`: the case
analyst accepts the linker's proposals that join records the case plants as one
person, in a planted cluster or a truth `same_person` pair, and no other."""

from __future__ import annotations

from collections import defaultdict

from sealbook.ledger.contracts import EvidenceKind as E
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.write import append
from sealbook.monitor.seed_study import pending, person
from sealbook.monitor.study import step
from sealbook.people import data as people


def _names(st) -> dict:
    return {
        c["record"]: st.written[c["key"]]
        for c in st.spec["claims"]
        if c["prop"] == "name" and c["key"] in st.written
    }


@step("clusters")
async def clusters(st, act, args) -> None:
    s, inv, records = st.s, st.inv, st.records
    names = _names(st)
    joiner = await s.principal("case-linker")
    # `{"do": "clusters", "only": "rows"}` joins each sheet row to its record and
    # leaves the planted clusters to the analyst's own `join`.
    desks = [] if args.get("only") == "rows" else st.spec.get("clusters") or []
    for cluster in desks:
        first, *rest = cluster
        if tuple(cluster) in st.joined or not all(r in names for r in cluster):
            continue
        run = await s.run(joiner, "join", [records[first]["source"]])
        for other in rest:
            basis = [f"statement:{names[r].id}" for r in (other, first)]
            link = await s.say(
                run,
                records[other]["source"],
                person(inv, other),
                Prop.SAME_AS,
                {"person": person(inv, first).removeprefix("person:"), "basis": basis},
                evidence=[E.CORROBORATING_FIELD],
            )
            await s.review(st.analyst, link, "accept", "joined earlier by this desk")
        await s.done(run, len(rest))
        st.joined.add(tuple(cluster))
    by_record = defaultdict(list)
    for (record, subject), found in st.rows.items():
        if (record, subject) not in st.joined:
            by_record[record].append((subject, found))
    for record, mentions in by_record.items():
        run = await s.run(joiner, "join", [records[record]["source"]])
        for subject, found in mentions:
            basis = [f"statement:{x.id}" for x in (found, names[record])]
            link = await s.say(
                run,
                records[record]["source"],
                subject,
                Prop.SAME_AS,
                {"person": person(inv, record).removeprefix("person:"), "basis": basis},
                evidence=[E.CORROBORATING_FIELD],
            )
            await s.review(st.analyst, link, "accept", "one name in one log")
            st.joined.add((record, subject))
        await s.done(run, len(mentions))


async def planted_pairs(st) -> list:
    """The linker's open proposals that join two records the case plants as one
    person, read before any is accepted: a proposal with a namesake is left."""
    groups, _ = await people.identity(st.session)

    def cluster(subject: str) -> str:
        p = people.pid(subject)
        return min(groups.get(p, {p}))

    def subject(record: str) -> str | None:
        return st.subjects.get(record)

    true = set()
    for pair in st.spec["truth"].get("same_person") or []:
        if all(subject(r) for r in pair):
            true.add(frozenset(cluster(subject(r)) for r in pair))
    for group in st.spec.get("clusters") or []:
        known = [subject(r) for r in group if subject(r)]
        for a in known:
            for b in known:
                if cluster(a) != cluster(b):
                    true.add(frozenset({cluster(a), cluster(b)}))
    subjects = sorted(set(st.subjects.values()))
    return [
        found
        for found, v in await pending(st.session, Prop.SAME_AS, subjects)
        if frozenset({cluster(found.subject), cluster(f"person:{v['person']}")}) in true
    ]


@step("links")
async def links(st, act, args) -> None:
    for found in await planted_pairs(st):
        await st.s.review(
            st.analyst, found, "accept", args.get("why") or "the same person"
        )
        st.accepted.append(found)


@step("join")
async def join(st, act, args) -> None:
    """The case analyst joins the people written for `records` as one person, her
    own signed decision with `why`, for a link no agent proposed. Each record's
    person comes from what was written for it (planted, or read and matched).
    `{"clusters": "planted"}` joins every planted cluster instead; a record with no
    person written for it is left out."""
    sets = (
        st.spec.get("clusters") or []
        if args.get("clusters") == "planted"
        else [args["records"]]
    )
    for records in sets:
        known = [r for r in records if r in st.subjects]
        if len(known) > 1:
            await _join(st, known, args["why"])


async def _join(st, records, why) -> None:
    first, *rest = records
    anchor = st.subjects[first]
    names = _names(st)
    groups, _ = await people.identity(st.session)

    def cluster(subject: str) -> str:
        p = people.pid(subject)
        return min(groups.get(p, {p}))

    for record in rest:
        subject = st.subjects[record]
        if cluster(subject) == cluster(anchor):
            continue  # already one person
        basis = [f"statement:{names[r].id}" for r in (record, first) if r in names]
        await append(
            st.session,
            StatementIn(
                subject=subject,
                prop=Prop.SAME_AS,
                value={
                    "person": anchor.removeprefix("person:"),
                    "basis": basis,
                    "why": why,
                },
                author_id=st.analyst.id,
            ),
            sign=st.s.assertion,
        )
