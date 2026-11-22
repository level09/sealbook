"""Persons: a union-find view over accepted same_as statements, two projections of
what the ledger says about one, name search, and erasure."""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from functools import partial

from sqlalchemy import (
    Text,
    cast,
    delete,
    func,
    null,
    or_,
    select,
    type_coerce,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, array_agg
from sqlalchemy.ext.asyncio import AsyncSession

from sealbook.ledger.contracts import EvidenceKind, Prop, StatementIn, Tier
from sealbook.ledger.models import Payload, Statement, StatementTier
from sealbook.ledger.write import append, current_clause, lock_chain
from sealbook.monitor import filter as mfilter
from sealbook.monitor.data import (
    LOCATED,
    META,
    Context,
    apart_why,
    display,
    map_layer,
    statement_rows,
)
from sealbook.monitor.filter import statement_clause
from sealbook.packages.models import Package, Sentence
from sealbook.places import normalise, resolve
from sealbook.search.arabic import candidates, natural, same_keys

# Silence between two dated points longer than this is shown as a gap.
GAP_DAYS = 30
IDENTITY = {Prop.SAME_AS, Prop.NOT_SAME}
HIDDEN = META | IDENTITY | {Prop.ERASURE}
PROJECTIONS = {
    "draft": (Tier.UNCONFIRMED, Tier.CORROBORATED, Tier.ACCEPTED),
    "factual": (Tier.CORROBORATED, Tier.ACCEPTED),
}
# Fields that corroborate or contradict a name match between two persons.
CORROBORATING = (
    Prop.BIRTH_DATE,
    Prop.FATHER_NAME,
    Prop.MOTHER_NAME,
    Prop.PLACE_OF_BIRTH,
    Prop.DETENTION_BRANCH,
)
# What the linker compares between two persons, and Review's card with it.
COMPARED = (*CORROBORATING, Prop.SEX)
# Every field the linker weighs, the name included: compared by `agreement`.
WEIGHED = frozenset({Prop.NAME, *COMPARED})


def pid(ref) -> str:
    """A person id from "person:<uuid>" or a bare uuid; anything else raises."""
    return str(uuid.UUID(str(ref).removeprefix("person:")))


def _subjects(members) -> list[str]:
    return [f"person:{m}" for m in sorted(members)]


async def identity(session: AsyncSession) -> tuple[dict[str, frozenset], list[dict]]:
    """Every person id that takes part in identity, mapped to its cluster, plus the
    same_as edges an accepted not_same blocked. Names never merge; an edge that would
    join two persons someone said are different is refused, not resolved."""
    # ponytail: rebuilds the whole graph per call; materialise it when identity
    # statements reach the tens of thousands.
    rows = await session.execute(
        select(Statement.id, Statement.subject, Statement.prop, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.prop.in_(IDENTITY),
            Statement.subject.startswith("person:"),
            StatementTier.tier == Tier.ACCEPTED,
        )
        .order_by(Statement.recorded_at, Statement.id)
    )
    groups: dict[str, set] = {}
    blocks, edges = set(), []
    for sid, subject, prop, value in rows:
        a, b = pid(subject), pid(value["person"])
        for x in (a, b):
            groups.setdefault(x, {x})
        if prop == Prop.NOT_SAME:
            blocks.add(frozenset((a, b)))
        else:
            edges.append((sid, a, b))
    conflicts = []
    for sid, a, b in edges:
        ga, gb = groups[a], groups[b]
        if ga is gb:
            continue
        blocked = [
            sorted(pair) for pair in blocks if len(pair & ga) == 1 and pair <= ga | gb
        ]
        if blocked:
            conflicts.append(
                {"statement": str(sid), "join": [a, b], "not_same": blocked}
            )
            continue
        ga |= gb
        for x in gb:
            groups[x] = ga
    return {x: frozenset(g) for x, g in groups.items()}, conflicts


def _cluster(groups: dict, ref) -> frozenset:
    p = pid(ref)
    return groups.get(p, frozenset({p}))


async def _person_values(session: AsyncSession, props) -> list:
    """(statement id, person id, prop, value, value_key) of every non-rejected
    statement with these props about a person."""
    # ponytail: scans every name and corroborating payload per search; add a
    # rebuildable name-key projection table when this passes ~100k names.
    rows = await session.execute(
        select(
            Statement.id,
            Statement.subject,
            Statement.prop,
            Payload.value,
            Payload.value_key,
        )
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.prop.in_(props),
            Statement.subject.startswith("person:"),
            StatementTier.tier != Tier.REJECTED,
            current_clause(),
        )
    )
    return [(str(sid), pid(s), prop, v, k) for sid, s, prop, v, k in rows]


def _texts(value) -> list[str]:
    if isinstance(value, dict):
        return [value[k] for k in ("text", "norm") if value.get(k)]
    return [value] if isinstance(value, str) else []


async def search(
    session: AsyncSession, query: str, investigation: str | None = None
) -> list[dict]:
    """Persons with a name matching `query` in any spelling or script. A hit is a
    proposal to look, never a merge: exact-name twins come back as two persons.
    With an investigation, only persons with facts from its sources."""
    groups, _ = await identity(session)
    hits: dict[str, dict] = {}
    for sid, person, _prop, value, _key in await _person_values(session, [Prop.NAME]):
        if any(candidates(query, t) for t in _texts(value)):
            members = groups.get(person, frozenset({person}))
            hit = hits.setdefault(
                min(members), {"members": len(members), "statements": []}
            )
            hit["statements"].append(sid)
    summary = await roster(session, investigation)
    return [
        {"person": p} | summary.get(p, {}) | h
        for p, h in sorted(hits.items())
        if not investigation or p in summary
    ]


async def hints(
    session: AsyncSession, items: list[dict], investigation: str | None = None
) -> dict[str, str]:
    """For people whose rows read the same (one name, one dated range, both shown
    on every list), the first fact that sets each apart: another case they appear
    in than the one the list is filtered to, then their first source. The id is the
    last resort. `items` are roster rows with "person", "name", "from" and "to"."""
    same: dict = {}
    for i in items:
        same.setdefault((i["name"], i.get("from"), i.get("to")), []).append(i)
    clash = [g for g in same.values() if len(g) > 1]
    if not clash:
        return {}
    groups, _ = await identity(session)
    owner = {
        f"person:{m}": i["person"]
        for g in clash
        for i in g
        for m in groups.get(i["person"], {i["person"]})
    }
    src = mfilter.source(mfilter.statement_blob())
    rows = await session.execute(
        select(Statement.subject, src, func.min(Statement.recorded_at))
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.subject.in_(owner),
            Statement.prop.not_in(META),
            StatementTier.tier != Tier.REJECTED,
        )
        .group_by(Statement.subject, src)
    )
    seen: dict[str, list] = {}
    for subject, sha, at in rows:
        if sha:
            seen.setdefault(owner[subject], []).append((at, sha))
    ctx = await Context.load(
        session, blobs={sha for v in seen.values() for _, sha in v}
    )

    def case(i):
        found = {
            a.context.get("investigation")
            for _, sha in seen.get(i["person"], ())
            for a in ctx.acqs.get(sha, ())
        } - {None, investigation}
        if not found:
            return None
        return f"{'also ' if investigation else ''}in {', '.join(sorted(found))}"

    def first(i):
        found = sorted(seen.get(i["person"], ()))
        return f"first in {ctx.doc(found[0][1])['title']}" if found else None

    out = {}
    for g in clash:
        for tell in (case, first):
            told = {i["person"]: tell(i) for i in g if i["person"] not in out}
            count = Counter(told.values())
            out |= {p: t for p, t in told.items() if t and count[t] == 1}
        out |= {
            i["person"]: f"record {i['person'][:8]}"
            for i in g
            if i["person"] not in out
        }
    return out


def _listed(investigation: str | None) -> tuple:
    """A fact as the person page lists it: about a person, current, not rejected,
    no identity links; with an investigation, only from its sources."""
    return (
        Statement.subject.startswith("person:"),
        Statement.prop.not_in(HIDDEN),
        StatementTier.tier != Tier.REJECTED,
        current_clause(),
        statement_clause({"investigation": investigation, "time": None}),
    )


async def roster(
    session: AsyncSession, investigation: str | None = None, groups=None
) -> dict[str, dict]:
    """Every person with facts on record, by canonical id: how much is known, from
    how many documents, over which dates. No values, so nothing needs masking.
    With an investigation, only facts whose source belongs to it count. A fact is
    what the person page lists: current, not rejected, no identity links."""
    if groups is None:
        groups, _ = await identity(session)
    rows = await session.execute(
        select(
            Statement.subject,
            func.count(),
            array_agg(func.distinct(Statement.span_blob)),
            func.min(Statement.valid_from),
            func.max(func.coalesce(Statement.valid_to, Statement.valid_from)),
        )
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(*_listed(investigation))
        .group_by(Statement.subject)
    )
    out: dict[str, dict] = {}
    seen: dict[str, set] = {}
    for subject, facts, docs, first, last in rows:
        person = pid(subject)
        members = groups.get(person, frozenset({person}))
        p = out.setdefault(
            min(members),
            {
                "members": len(members),
                "facts": 0,
                "documents": 0,
                "from": None,
                "to": None,
            },
        )
        p["facts"] += facts
        # A document naming two linked records is still one document.
        blobs = seen.setdefault(min(members), set())
        blobs.update(d for d in docs if d)
        p["documents"] = len(blobs)
        for key, value, pick in (("from", first, min), ("to", last, max)):
            if value:
                day = value.isoformat()
                p[key] = pick(p[key], day) if p[key] else day
    return out


NAME_ORDER = {Tier.ACCEPTED: 0, Tier.CORROBORATED: 1, Tier.UNCONFIRMED: 2}


NAMES = {Prop.NAME, Prop.FATHER_NAME, Prop.MOTHER_NAME}
ARABIC = re.compile(r"[\u0600-\u06ff]")


def same_value(prop: str, a: str, b: str) -> bool:
    """One field's two values name the same thing, as the linker counts it."""
    if prop in NAMES and bool(ARABIC.search(a)) != bool(ARABIC.search(b)):
        # Across scripts only consonant keys can compare (خالد, Khaled). Within one
        # script the text decides, so Hasan and Husayn stay different names.
        # ponytail: keys drop vowels, so حسن and Husayn match; needs a vowelled key.
        return same_keys(a, b)
    if prop in NAMES:
        a, b = natural(a), natural(b)
    a, b = normalise(a), normalise(b)
    if a == b:
        return True
    return _year_of(prop, a, b)


def _year_of(prop: str, a: str, b: str) -> bool:
    # A year-only entry equals a full date at the coarser precision.
    short, long = sorted((a, b), key=len)
    return prop == Prop.BIRTH_DATE and bool(short) and long.startswith(short + "-")


def agreement(prop: str, xs: list, ys: list) -> tuple[str, str | None]:
    """Two people's values of one field, the one comparison the linker, Review's
    card and Candidate duplicates use: ("agree", None); ("agree", "year only") when
    the only match is a year against a full date in it; ("differ", None), also when
    both sides state a full date and no full dates match, whatever the years say;
    or ("unknown", None) when a side states nothing, shown as "cannot tell". A name
    agrees when the linker's name match holds ("A. Qassar", "Adrian Kassar")."""
    xs, ys = [x for x in xs if x], [y for y in ys if y]
    if not (xs and ys):
        return "unknown", None
    same = candidates if prop == Prop.NAME else partial(same_value, prop)
    hits = [(x, y) for x in xs for y in ys if same(x, y)]
    if not hits:
        return "differ", None
    if not all(_year_of(prop, normalise(x), normalise(y)) for x, y in hits):
        return "agree", None
    full = [[v for v in side if len(normalise(v)) > 4] for side in (xs, ys)]
    return ("differ", None) if all(full) else ("agree", "year only")


def _heard(row: dict) -> bool:
    """A name heard in a recording, not written: a weaker basis than a written one."""
    return EvidenceKind.SPOKEN in (row.get("evidence") or ())


def _heard_value(value) -> bool:
    return isinstance(value, dict) and bool(value.get("heard"))


def label_rank(name: str) -> tuple:
    """How well one spelling labels a person, best first: the most names longer
    than an initial ("Aron Vesk" before "A. Vesk"), then written in natural order
    ("Vesk, Aron" after), then the most words ("N. Harrow" before "Harrow"), then
    not all capitals ("ARON VESK" after)."""
    words = re.findall(r"[^\W\d_]+", name)
    full = sum(len(w) > 1 for w in words)
    return (-full, natural(name) != name, -len(words), name.isupper())


async def names(session: AsyncSession, people, groups=None) -> dict[str, str]:
    """The label for each person id: the best name the reader may see from any source
    of any linked record. Across every member of the person, accepted before
    corroborated before unconfirmed, then the fullest natural-order spelling
    (`label_rank`), then the earliest recorded. A written name wins over one heard in
    a recording; a person known only from a heard name reads "heard as Joe". Masked
    (sensitive source) and erased names are never labels, so a person with only those
    is absent and pages say "Unnamed person"."""
    if groups is None:
        groups, _ = await identity(session)
    members = {p: groups.get(p, frozenset({p})) for p in people}
    subjects = sorted({f"person:{m}" for ms in members.values() for m in ms})
    if not subjects:
        return {}
    rows = await statement_rows(
        session,
        Statement.subject.in_(subjects),
        Statement.prop == Prop.NAME,
        StatementTier.tier != Tier.REJECTED,
        current_clause(),
        limit=5000,
    )
    # Earliest recorded breaks ties, so a label does not change between rebuilds of
    # the same evidence (ids are random).
    order = lambda r: (  # noqa: E731
        NAME_ORDER.get(r["tier"], 3),
        _heard(r),
        label_rank(r["display"]),
        r["recorded_at"],
        r["id"],
    )
    best: dict[str, tuple] = {}
    for r in rows:
        if r["display"]:
            m = pid(r["subject"])
            # A person known only from a recording says so.
            shown = f"heard as {r['display']}" if _heard(r) else r["display"]
            if m not in best or order(r) < best[m][0]:
                best[m] = (order(r), shown)
    return {
        p: min(found)[1]
        for p, ms in members.items()
        if (found := [best[m] for m in ms if m in best])
    }


def _same_day_apart(a: dict, b: dict, places: dict) -> str | None:
    """Why two place claims on one exact day cannot both hold (`apart_why`, by
    their resolved places and times of day), or None."""
    if not (a["from"] and a["from"] == a["to"] == b["from"] == b["to"]):
        return None

    def point(r):
        p = places.get(uuid.UUID(r["id"]))
        return {
            "place_id": str(p.id) if p else None,
            "place": r["display"],
            "prop": r["prop"],
            "lonlat": [float(p.lon), float(p.lat)] if p and p.lon is not None else None,
            "precision_m": p.precision_m if p else None,
            "time": r.get("time"),
        }

    return apart_why(point(a), point(b))


def _overlap(a: dict, b: dict) -> bool:
    return a["from"] <= b["to"] and b["from"] <= a["to"]


def disagreements(
    facts: dict,
    steps: list[dict],
    places: dict,
    links: dict,
    *,
    timeless=(),
    same_value=None,
) -> list[dict]:
    """Where sources disagree: different values for one prop over overlapping time,
    and days on which the person stands in two places at once. Places compare by
    resolved id and other values by normalised text, so two spellings of one place
    or name never disagree. A field the linker weighs compares as it does
    (`agreement`: a year inside a full date, a name across scripts or in a shorter
    form); `same_value` compares any other. Each disagreement is listed once, with
    every side's value and source; none is ever resolved."""
    rows = {r["id"]: r for rs in facts.values() for r in rs}

    def agrees(prop, a, b) -> bool:
        if prop in WEIGHED:
            return agreement(prop, [a], [b])[0] == "agree"
        return bool(same_value and same_value(prop, a, b))

    def side(r):
        link = links.get(r["id"], {})
        return {
            "id": r["id"],
            "prop": r["prop"],
            "value": r["display"],
            "time": r.get("time"),
            "file": link.get("file"),
            "href": link.get("href"),
        }

    def key(r):
        place = places.get(uuid.UUID(r["id"])) if r["prop"] in LOCATED else None
        return place.id if place else normalise(r["display"])

    forks = []
    for s in steps:
        if not s["fork"]:
            continue
        # Only the claims in a pair that cannot both hold: a place that fits the
        # others' times is not a side.
        paired = {i for p in s["pairs"] for i in p["ids"]}
        forks.append(
            {
                "kind": "position",
                "prop": "position",
                "from": s["date"],
                "to": s["date"],
                "sides": [side(rows[i]) for i in s["ids"] if i in paired],
                "why": s["pairs"],
            }
        )
    shown = [{x["id"] for x in f["sides"]} for f in forks]
    for prop, rs in facts.items():
        dated = [
            r
            for r in rs
            if (r["from"] or prop in timeless) and r["display"] is not None
        ]
        for i, a in enumerate(dated):
            for b in dated[i + 1 :]:
                if key(a) == key(b) or (
                    prop not in LOCATED and agrees(prop, a["display"], b["display"])
                ):
                    continue
                if a["from"] and b["from"] and not _overlap(a, b):
                    continue
                # Places at overlapping dates (a month and a day in it) can both be
                # true: only one exact day at two places too far apart, at times
                # no journey joins, is a disagreement, and a quarter and its city
                # never are.
                why = _same_day_apart(a, b, places) if prop in LOCATED else None
                if prop in LOCATED and not why:
                    continue
                if any({a["id"], b["id"]} <= ids for ids in shown):
                    continue
                forks.append(
                    {
                        "kind": "value",
                        "prop": prop,
                        "from": max(a["from"], b["from"])
                        if a["from"] and b["from"]
                        else None,
                        "to": min(a["to"], b["to"]) if a["to"] and b["to"] else None,
                        "sides": [side(a), side(b)],
                    }
                    | (
                        {"why": [{"ids": [a["id"], b["id"]], "why": why}]}
                        if why
                        else {}
                    )
                )
    return forks


def _forks(facts: dict, steps: list[dict], places: dict, links: dict) -> list[dict]:
    return disagreements(facts, steps, places, links)


async def _duplicates(
    session: AsyncSession, groups: dict, members: frozenset, blocked: set
) -> list[dict]:
    """Other persons whose names match this one's, with the fields that agree or
    differ, most agreement first. Proposals for a human; nothing here merges
    anything. A candidate that only differs is left out, and so is one whose every
    matching name was heard in a recording with no field agreeing: a heard name is a
    weaker basis than a written one."""
    values = await _person_values(session, [Prop.NAME, *CORROBORATING])
    mine = [v for v in values if v[1] in members]
    heard = {
        sid
        for sid, _, prop, val, _ in values
        if prop == Prop.NAME and _heard_value(val)
    }
    names = [
        (sid, t)
        for sid, _, prop, val, _ in mine
        if prop == Prop.NAME
        for t in _texts(val)
    ]
    found: dict[str, dict] = {}
    for sid, person, prop, value, _ in values:
        if prop != Prop.NAME or person in members:
            continue
        matched = [
            n for n, text in names if any(candidates(text, t) for t in _texts(value))
        ]
        if not matched:
            continue
        other = groups.get(person, frozenset({person}))
        c = found.setdefault(
            min(other),
            {"members": other, "names": [], "not_same": False, "written": False},
        )
        c["names"].append(sid)
        c["written"] = c["written"] or any(
            n not in heard for n in matched if sid not in heard
        )
        c["not_same"] = c["not_same"] or any(
            frozenset((a, b)) in blocked for a in members for b in other
        )
    # Each matching name of theirs once, as the reader may see it (masked stays None).
    shown = {
        r["id"]: r["display"]
        for r in await statement_rows(
            session,
            Statement.id.in_([sid for c in found.values() for sid in c["names"]]),
            limit=5000,
        )
    }
    out = []
    for person, c in sorted(found.items()):
        agree, differ, year_only = [], [], []
        for prop in CORROBORATING:
            # The linker's rule: a year agrees with a full date in that year.
            state, note = agreement(
                prop,
                [display(v, pr) for _, _, pr, v, _ in mine if pr == prop],
                [
                    display(v, pr)
                    for _, p, pr, v, _ in values
                    if pr == prop and p in c["members"]
                ],
            )
            if state == "agree":
                agree.append(str(prop))
                if note:
                    year_only.append(str(prop))
            elif state == "differ":
                differ.append(str(prop))
        if not c["written"] and not agree:
            continue
        out.append(
            {
                "person": person,
                "names": _by_name(c["names"], shown),
                # Every matching name was heard in a recording, not written.
                "heard": not c["written"],
                "agree": agree,
                "differ": differ,
                "year_only": year_only,
                "not_same": c["not_same"],
                # A name match every other compared field contradicts reads as two
                # people; it stays visible so twins are kept apart knowingly.
                "distinct": bool(differ) and not agree,
            }
        )
    # Most agreement first, then fewest differences; likely-distinct and decided last.
    return sorted(
        out,
        key=lambda d: (
            d["not_same"],
            d["distinct"],
            -len(d["agree"]),
            len(d["differ"]),
        ),
    )


def _by_name(sids, shown: dict) -> list[dict]:
    """One entry per shown name with the first statement behind it and how many;
    masked names (None) share one entry, after the readable ones."""
    out: dict = {}
    for sid in dict.fromkeys(sids):
        name = shown.get(str(sid))
        e = out.setdefault(name, {"statement": str(sid), "name": name, "count": 0})
        e["count"] += 1
    return sorted(out.values(), key=lambda e: e["name"] is None)


async def _not_same_pairs(session: AsyncSession) -> set:
    rows = await session.execute(
        select(Statement.subject, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(Statement.prop == Prop.NOT_SAME, StatementTier.tier == Tier.ACCEPTED)
    )
    return {frozenset((pid(s), pid(v["person"]))) for s, v in rows}


async def person(session: AsyncSession, ref, projection: str = "draft") -> dict | None:
    """One person assembled from every statement about any member of its cluster.
    draft: every non-rejected statement, carrying its tier. factual: accepted and
    corroborated only. None when the ledger holds nothing about `ref`."""
    # Stories builds on people, so its evidence links are imported here.
    from sealbook.stories.data import links

    if projection not in PROJECTIONS:
        raise ValueError(f"projection must be one of {sorted(PROJECTIONS)}")
    groups, conflicts = await identity(session)
    members = _cluster(groups, ref)
    subjects = _subjects(members)
    present = await session.execute(
        select(Statement.subject, func.count())
        .where(Statement.subject.in_(subjects))
        .group_by(Statement.subject)
    )
    if not present.all():
        return None
    canonical = f"person:{min(members)}"
    rows = await statement_rows(
        session,
        Statement.subject.in_(subjects),
        Statement.prop.not_in(HIDDEN),
        StatementTier.tier.in_(PROJECTIONS[projection]),
        current_clause(),
        limit=5000,
        order=Statement.recorded_at,
    )
    linked = await links(session, rows)
    others = await names(
        session, {pid(r["other"]) for r in rows if "other" in r}, groups
    )
    facts: dict[str, list] = {}
    for r in rows:
        other = r.pop("other", None)
        if other and r["display"] and pid(other) in others:
            r["display"] = r["display"].replace("another person", others[pid(other)])
        # The source a fact came from, opened at the claim; the actor is detail.
        lk = linked.get(r["id"])
        r["source"] = {"title": lk["file"], "href": lk["href"]} if lk else None
        facts.setdefault(r["prop"], []).append(r)
    places = await resolve.resolved(
        session, [uuid.UUID(r["id"]) for r in rows if r["prop"] in LOCATED], None
    )
    layer = map_layer(
        [r | {"subject": canonical, "subject_label": "track"} for r in rows], places
    )
    track = layer["tracks"].get(canonical, {"steps": [], "gap_after": None})
    steps = track["steps"]
    gaps = [
        {"from": a["date"], "to": b["date"], "days": b["gap_days"]}
        for a, b in zip(steps, steps[1:], strict=False)
        if b["gap_days"] > GAP_DAYS
    ]
    erasures = await session.execute(
        select(Statement.id, Statement.recorded_at).where(
            Statement.subject.in_(subjects), Statement.prop == Prop.ERASURE
        )
    )
    mine = [c for c in conflicts if set(c["join"]) & members]
    dups = await _duplicates(session, groups, members, await _not_same_pairs(session))
    return {
        "person": canonical.removeprefix("person:"),
        "members": sorted(members),
        "projection": projection,
        "facts": facts,
        "track": track,
        "points": layer["points"],
        "places": layer["places"],
        "schematic": layer["schematic"],
        "forks": disagreements(facts, steps, places, linked),
        "gaps": gaps,
        "gap_days": GAP_DAYS,
        "conflicts": mine,
        "duplicates": dups,
        # Labels for the other persons named above, by the one label rule.
        "names": await names(
            session,
            {x for c in mine for x in c["join"]} | {d["person"] for d in dups},
            groups,
        ),
        "erasures": [{"id": str(i), "at": at.isoformat()} for i, at in erasures.all()],
    }


async def log_access(
    session: AsyncSession, person: str, action: str, ids: list[str], author_id
) -> Statement:
    """One access statement on the person listing the statements the use touched;
    those ids are what move statements ahead in the review queue."""
    return await append(
        session,
        StatementIn(
            subject=f"person:{person}",
            prop=Prop.ACCESS,
            value={
                "action": action,
                "at": datetime.now(UTC).isoformat(),
                "statements": ids,
            },
            author_id=author_id,
        ),
    )


async def log_view(session: AsyncSession, view: dict, author_id) -> Statement:
    ids = [i for step in view["track"]["steps"] for i in step["ids"]]
    return await log_access(session, view["person"], "view", ids, author_id)


async def values(session: AsyncSession, ids: list[str]) -> dict[str, str]:
    """Display values for statement ids; erased ones are absent."""
    rows = await session.execute(
        select(Statement.id, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(Statement.id.in_([uuid.UUID(i) for i in ids]))
    )
    return {str(sid): display(value) for sid, value in rows}


async def erase(session: AsyncSession, ref, by, reason: str) -> dict:
    """Delete the values of every statement about the person's cluster, statements
    about those statements (reviews carry reasons), and identity links pointing at
    it, then write an ERASURE tombstone. Statements and the chain stay: the chain
    hashes the salted commitment, so it still verifies. Earlier tombstones keep
    their reason. Copies go too: brief sentences citing an erased statement are
    deleted (working drafts) and packages disclosing one are revoked, which stops
    their download; their PACKAGE statements stay. Findings and incident links
    naming an erased statement or person lose their values too, with statements
    about them, and finding keys keep only their key and state; so do conclusions
    naming the person or resting on an erased statement. Caller commits."""
    if not reason.strip():
        raise ValueError("erasure needs a reason")
    # Chain lock first, the order finalize takes it in, so the two never deadlock.
    await lock_chain(session)
    groups, _ = await identity(session)
    members = _cluster(groups, ref)
    subjects = _subjects(members)
    about = select(Statement.subject).where(Statement.subject.in_(subjects)).exists()
    if not (await session.execute(select(about))).scalar():
        raise LookupError(f"nothing recorded about person:{pid(ref)}")
    descendants = (
        select(Statement.id)
        .where(Statement.subject.in_(subjects))
        .cte("descendants", recursive=True)
    )
    descendants = descendants.union(
        select(Statement.id).join(
            descendants,
            Statement.subject
            == func.concat("statement:", cast(descendants.c.id, Text)),
        )
    )
    doomed = union(
        select(Statement.commitment).where(
            Statement.id.in_(select(descendants.c.id)),
            Statement.prop != Prop.ERASURE,
        ),
        select(Payload.commitment).where(
            Payload.value["person"].astext.in_(list(members))
        ),
    )
    erased = list(
        (
            await session.execute(
                select(Statement.id).where(Statement.commitment.in_(doomed))
            )
        ).scalars()
    )
    refs = cast([f"person:{m}" for m in members], ARRAY(Text))
    ids = cast([str(i) for i in erased], ARRAY(Text))
    linked = (
        select(Statement.id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            # ACCESS: research queries name the people they were built from. A
            # conclusion names the people it is about and the claims it rests on.
            Statement.prop.in_(
                [
                    Prop.FINDING,
                    Prop.INVOLVED,
                    Prop.SUPPORTED_BY,
                    Prop.ACCESS,
                    Prop.CONCLUSION,
                ]
            ),
            or_(
                Payload.value["person"].astext == func.any(refs),
                Payload.value["statement"].astext == func.any(ids),
                Payload.value["members"].has_any(ids),
                Payload.value["people"].has_any(refs),
                Payload.value["rests_on"].has_any(ids),
                Payload.value["contradicts"].has_any(ids),
            ),
        )
        .cte("linked", recursive=True)
    )
    linked = linked.union(
        select(Statement.id).join(
            linked,
            Statement.subject == func.concat("statement:", cast(linked.c.id, Text)),
        )
    )
    seen = set(erased)
    erased += [
        i
        for i in (await session.execute(select(linked.c.id))).scalars()
        if i not in seen
    ]
    doomed = select(Statement.commitment).where(Statement.id.in_(erased))
    sentences = await session.execute(
        delete(Sentence).where(Sentence.statement_ids.overlap(erased))
    )
    packages = await session.execute(
        update(Package)
        .where(
            # A package with no recorded statements cannot be shown clean: revoke it.
            or_(
                Package.statement_ids.overlap(erased),
                func.cardinality(Package.statement_ids) == 0,
            ),
            Package.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    # ponytail: blob bytes (scans, OCR text, the stored source files) still hold the
    # values; crypto-shredding per-blob keys is the upgrade once blobs are encrypted.
    deleted = await session.execute(
        delete(Payload).where(Payload.commitment.in_(doomed))
    )
    from sealbook.discovery.models import (
        AudioLandmark,
        EntityEmbedding,
        FindingKey,
        IndexProgress,
        MediaFrame,
        SearchChunk,
        Segment,
        SegmentEmbedding,
    )
    from sealbook.discovery.service import available

    for projection in (MediaFrame, SearchChunk, AudioLandmark):
        await session.execute(
            delete(projection)
            .where(~available(projection.blob_sha256))
            .execution_options(synchronize_session=False)
        )
    await session.execute(
        delete(SegmentEmbedding)
        .where(
            SegmentEmbedding.segment_id.in_(
                select(Segment.id).where(
                    Segment.text_sha256.is_not(None), ~available(Segment.text_sha256)
                )
            )
        )
        .execution_options(synchronize_session=False)
    )
    for projection in (Segment, IndexProgress):
        await session.execute(
            delete(projection)
            .where(~available(projection.source_sha256))
            .execution_options(synchronize_session=False)
        )
    await session.execute(
        delete(EntityEmbedding).where(EntityEmbedding.subject.in_(subjects))
    )
    all_ids = cast([str(i) for i in erased], ARRAY(Text))
    await session.execute(
        update(FindingKey)
        .where(
            or_(
                type_coerce(FindingKey.members, JSONB).has_any(all_ids),
                type_coerce(FindingKey.notes, JSONB).has_any(all_ids),
                FindingKey.statement_id.in_(erased),
            )
        )
        .values(members=null(), features=null(), notes=[])
        .execution_options(synchronize_session=False)
    )
    tomb = await append(
        session,
        StatementIn(
            subject=f"person:{min(members)}",
            prop=Prop.ERASURE,
            value={
                "reason": reason,
                "commitments": deleted.rowcount,
                "sentences": sentences.rowcount,
                "packages": packages.rowcount,
            },
            author_id=by,
        ),
    )
    return {
        "person": min(members),
        "members": sorted(members),
        "commitments": deleted.rowcount,
        "sentences": sentences.rowcount,
        "packages": packages.rowcount,
        "tombstone": str(tomb.id),
    }
