"""The linker: a run that reads across a case's sources and proposes the links the
rules miss, each as an unconfirmed statement in the normal review path.

Candidates are built here, so the model only ever judges pairs that already share
something checkable: two records whose names are cross-script or partial matches
and that agree on at least one other field (same person), a person placed at an
incident's place and time (involved), two sources that name the same people
(document relation), and two people named together who were held at one place at
one time or share a family name and a parent (relation). The model says which hold,
which claims carry each, and why; it may cite only the handles it was given.
Nothing here accepts anything. An open, accepted or blocked link is never proposed
again, and a rejected one only when its basis holds a claim the rejection did not
see."""

from __future__ import annotations

import dataclasses
import time
from itertools import combinations

from sqlalchemy import select

from sealbook.agents import read
from sealbook.agents.read import Claim, Handles, claim_view, place_name, when
from sealbook.ledger import write
from sealbook.ledger.contracts import (
    DOCUMENT_RELATIONS,
    PERSON_RELATIONS,
    EvidenceKind,
    Prop,
    Refused,
    StatementIn,
    Tier,
)
from sealbook.ledger.models import Blob, Payload, Run, Statement, StatementTier
from sealbook.ledger.queue import LEASE
from sealbook.monitor.data import LOCATED
from sealbook.places import normalise
from sealbook.search.arabic import candidates as name_match
from sealbook.settings import Config

TOOL = "link"
AGENT = "Linker"
PROMPT_VERSION = "linker-3"
CAP = 12  # candidates of each kind per run, strongest first
PER_CALL = 4
BUDGET_SECONDS = LEASE.total_seconds() - 90
COMPARED = (
    Prop.BIRTH_DATE,
    Prop.FATHER_NAME,
    Prop.MOTHER_NAME,
    Prop.PLACE_OF_BIRTH,
    Prop.DETENTION_BRANCH,
    Prop.SEX,
)
# A difference here rules a same-person link out, whatever else agrees.
CONTRADICTS = (Prop.BIRTH_DATE, Prop.FATHER_NAME, Prop.MOTHER_NAME)
# Where a person was held at a time; branch membership and transfers do not count.
HELD = (Prop.DETAINED_AT, Prop.SEEN_AT)
PARENTS = (Prop.FATHER_NAME, Prop.MOTHER_NAME)
KINDS = ("same_person", "involved", *DOCUMENT_RELATIONS, *PERSON_RELATIONS)

SYSTEM = (
    "You check possible links in a human rights case for an analyst, who decides "
    "every one. Each candidate has a handle and the claims behind it, each claim "
    "with its own handle (c...). Candidates: m = maybe the same person, v = a "
    "person maybe involved in an incident, r = two documents maybe related, x = two "
    "people maybe related, n = the same full name, held by no other record in the "
    "case, where one record has nothing else to compare. Rules: outside n, a shared "
    "name alone never makes the same person; for n, propose only when nothing given "
    "argues against it and say in why that only the name matches; "
    "names in Arabic and Latin script, or a shorter form of a name, can be the same "
    "name; a difference in birth date or a parent's name rules out the same person. "
    "For each candidate first write why in one sentence naming what agrees and what "
    "differs and list in basis the claim handles that carry it, then set kind "
    "(same_person for m and n, involved for v, contains or contained_in or related for "
    "r, family or cellmate or command for x) and set propose to true only when its "
    f"claims support the link. Use only the data given. {read.DATA_ONLY}"
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["links"],
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["h", "why", "basis", "kind", "propose"],
                "properties": {
                    "h": {"type": "string"},
                    "why": {"type": "string"},
                    "basis": {"type": "array", "items": {"type": "string"}},
                    "kind": {"enum": list(KINDS)},
                    "propose": {"type": "boolean"},
                },
            },
        }
    },
}


def _ref(i) -> str:
    return f"statement:{i}"


# --- what is already known ----------------------------------------------------------


async def known(db, groups: dict) -> tuple[set, dict]:
    """Keys of links that exist (open, accepted, or blocked by a not-same) and, for
    rejected ones, every basis statement a rejection saw."""
    rows = await db.execute(
        select(Statement.subject, Statement.prop, Payload.value, StatementTier.tier)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.prop.in_(
                [Prop.SAME_AS, Prop.NOT_SAME, Prop.INVOLVED, Prop.RELATION]
            )
        )
    )
    exists, rejected = set(), {}
    for subject, prop, value, tier in rows:
        k = key(prop, subject, value, groups)
        if k is None:
            continue
        if tier == Tier.REJECTED:
            rejected.setdefault(k, set()).update(value.get("basis") or [])
        else:
            exists.add(k)
    return exists, rejected


def key(prop: str, subject: str, value: dict, groups: dict):
    c = lambda s: read.cluster_of(groups, s)  # noqa: E731
    if prop in (Prop.SAME_AS, Prop.NOT_SAME):
        return ("person", frozenset((c(subject), c(value["person"]))))
    if prop == Prop.INVOLVED:
        return ("involved", subject, c(value["person"]))
    if prop == Prop.RELATION and "person" in value:
        return ("relation", frozenset((c(subject), c(value["person"]))))
    if prop == Prop.RELATION and "blob" in value:
        return ("document", frozenset((subject.removeprefix("blob:"), value["blob"])))
    return None


def fresh(k, basis: list[str], exists: set, rejected: dict) -> bool:
    """New, or rejected before but now resting on a claim the rejection did not see."""
    if k in exists:
        return False
    return k not in rejected or not set(basis) <= rejected[k]


# --- candidates ---------------------------------------------------------------------


def _same_place(case, a: Claim, b: Claim) -> bool:
    pa, pb = case.places.get(a.id), case.places.get(b.id)
    if pa is not None and pb is not None:
        return pa.id == pb.id
    return read.same_value(a.prop, a.text, b.text)


def candidates(
    case, incidents: list[dict], focus: set | None, h: Handles, keep=None
) -> list:
    """Every candidate, strongest first within its kind, at most CAP of each.
    `focus` is a set of claim ids a candidate must touch (None: the whole case).
    `keep` filters before the cap, so known links never fill a run's places."""
    docs = case.docs
    top = lambda xs: [x for x in xs if keep is None or keep(x)][:CAP]  # noqa: E731
    view = lambda c: claim_view(h, c, docs)  # noqa: E731
    people = case.by_person()
    names = {p: [c for c in cs if c.prop == Prop.NAME] for p, cs in people.items()}
    touches = lambda cs: focus is None or any(c.id in focus for c in cs)  # noqa: E731
    out = []

    same = []
    for a, b in combinations(sorted(people), 2):
        pairs = [
            (x, y) for x in names[a] for y in names[b] if name_match(x.text, y.text)
        ]
        if not pairs or not touches(people[a] + people[b]):
            continue
        agree, differ = [], []
        for prop in COMPARED:
            xs = [c for c in people[a] if c.prop == prop]
            ys = [c for c in people[b] if c.prop == prop]
            if not (xs and ys):
                continue
            hit = next(
                (
                    (x, y)
                    for x in xs
                    for y in ys
                    if read.same_value(prop, x.text, y.text)
                ),
                None,
            )
            if hit:
                agree.append(hit)
            else:
                differ.append((xs[0], ys[0]))
        for prop in ("last_seen", "arrested_at", "detained_at", "seen_at"):
            xs = [c for c in people[a] if c.prop == prop]
            ys = [c for c in people[b] if c.prop == prop]
            hit = next(
                (
                    (x, y)
                    for x in xs
                    for y in ys
                    if read.overlap(x, y) and _same_place(case, x, y)
                ),
                None,
            )
            if hit:
                agree.append(hit)
        if not agree or any(x.prop in CONTRADICTS for x, _ in differ):
            continue
        x, y = pairs[0]
        same.append(
            (
                -len(agree),
                {
                    "h": h("m", (a, b)),
                    "a": {"names": [view(x)]},
                    "b": {"names": [view(y)]},
                    "agree": [
                        {"field": p.prop.replace("_", " "), "a": view(p), "b": view(q)}
                        for p, q in agree
                    ],
                    "differ": [
                        {"field": p.prop.replace("_", " "), "a": view(p), "b": view(q)}
                        for p, q in differ
                    ],
                    "_sides": (a, b),
                    "_basis": [x, y, *(c for pair in agree for c in pair)],
                },
            )
        )
    out += top(c for _, c in sorted(same, key=lambda t: t[0]))

    # A record that is only a name (a mention in a document) can agree on nothing
    # else, so it never becomes an m. On analyst-linked staging data a full name
    # held by exactly two records, one of them bare, was the same person in every
    # pair, and partial names never were; so only the full, unshared name goes on.
    holders: dict[str, set[str]] = {}
    for p, ns in names.items():
        for n in ns:
            if len((key := normalise(n.text)).split()) >= 2:
                holders.setdefault(key, set()).add(p)
    named = []
    for key, ps in holders.items():
        if len(ps) != 2:
            continue
        a, b = sorted(ps)
        bare = [p for p in (a, b) if all(c.prop == Prop.NAME for c in people[p])]
        if not bare or not touches(people[a] + people[b]):
            continue
        x = next(c for c in names[a] if normalise(c.text) == key)
        y = next(c for c in names[b] if normalise(c.text) == key)
        named.append(
            (
                -len(key.split()),
                {
                    "h": h("n", (a, b)),
                    "a": {"names": [view(x)], "other": _other(people[a], view)},
                    "b": {"names": [view(y)], "other": _other(people[b], view)},
                    "_sides": (a, b),
                    "_basis": [x, y],
                },
            )
        )
    out += top(c for _, c in sorted(named, key=lambda t: t[0]))

    involved = []
    for inc in incidents:
        for person, cs in sorted(people.items()):
            if person in inc["people"]:
                continue
            at = [
                c
                for c in cs
                if c.prop in LOCATED
                and (place := case.places.get(c.id)) is not None
                and place.id in inc["places"]
                and c.lo
                and c.lo <= inc["places"][place.id][1]
                and inc["places"][place.id][0] <= c.hi
            ]
            if not at or not touches(cs):
                continue
            lo, hi = inc["places"][case.places[at[0].id].id]
            involved.append(
                {
                    "h": h("v", (inc["id"], person)),
                    "incident": inc["title"],
                    "place": place_name(case.places[at[0].id]),
                    "window": when(lo, hi),
                    "person": [view(c) for c in names[person][:2]],
                    "claims": [view(c) for c in at[:3]],
                    "_incident": inc["id"],
                    "_person": person,
                    "_basis": [*names[person][:1], *at[:3]],
                }
            )
    out += top(involved)

    by_source: dict[str, dict[str, list[Claim]]] = {}
    for p, cs in people.items():
        for c in cs:
            by_source.setdefault(c.source, {}).setdefault(p, []).append(c)
    documents = []
    for a, b in combinations(sorted(by_source), 2):
        shared = sorted(set(by_source[a]) & set(by_source[b]))
        if len(shared) < 2:
            continue
        if not touches([c for p in shared for c in by_source[a][p] + by_source[b][p]]):
            continue
        rows = []
        basis = []
        for p in shared:
            x = next(
                (c for c in by_source[a][p] if c.prop == Prop.NAME), by_source[a][p][0]
            )
            y = next(
                (c for c in by_source[b][p] if c.prop == Prop.NAME), by_source[b][p][0]
            )
            rows.append({"a": view(x), "b": view(y)})
            basis += [x, y]
        documents.append(
            (
                -len(shared),
                {
                    "h": h("r", (a, b)),
                    "a": {"title": docs[a]["title"], "type": docs[a]["doc_type"]},
                    "b": {"title": docs[b]["title"], "type": docs[b]["doc_type"]},
                    "people_in_both": rows,
                    "_docs": (a, b),
                    "_basis": basis,
                },
            )
        )
    out += top(c for _, c in sorted(documents, key=lambda t: t[0]))

    related = []
    together = {p: {c.source for c in cs} for p, cs in people.items()}
    same_pairs = {c["_sides"] for c in out if "_sides" in c}
    for a, b in combinations(sorted(people), 2):
        if (a, b) in same_pairs:
            continue
        hint, basis, rows = None, [], []
        shared_source = together[a] & together[b]
        held = [
            (x, y)
            for x in people[a]
            if x.prop in HELD
            for y in people[b]
            if y.prop in HELD and read.overlap(x, y) and _same_place(case, x, y)
        ]
        if shared_source and held:
            hint, (x, y) = "cellmate", held[0]
            rows.append({"field": "held", "a": view(x), "b": view(y)})
            basis += [x, y]
        else:
            last = lambda cs: {  # noqa: E731
                normalise(c.text).split(" ")[-1] for c in cs if c.prop == Prop.NAME
            }
            parent = [
                (x, y)
                for x in people[a]
                if x.prop in PARENTS
                for y in people[b]
                if y.prop == x.prop and read.same_value(x.prop, x.text, y.text)
            ]
            if last(people[a]) & last(people[b]) and parent:
                hint, (x, y) = "family", parent[0]
                rows.append(
                    {"field": x.prop.replace("_", " "), "a": view(x), "b": view(y)}
                )
                basis += [x, y]
        if hint is None or not touches(people[a] + people[b]):
            continue
        na, nb = names[a][:1], names[b][:1]
        related.append(
            {
                "h": h("x", (a, b)),
                "hint": hint,
                "a": {"names": [view(c) for c in na]},
                "b": {"names": [view(c) for c in nb]},
                "together": rows,
                "named_together_in": sorted(docs[s]["title"] for s in shared_source),
                "_sides": (a, b),
                "_basis": [*na, *nb, *basis],
            }
        )
    out += top(related)
    return out


def _other(claims: list[Claim], view) -> list[dict]:
    """What else a record says, so the model can see what argues against a name."""
    return [view(c) for c in claims if c.prop != Prop.NAME][:4]


def payload(cands: list[dict]) -> dict:
    return {
        "candidates": [
            {k: v for k, v in c.items() if not k.startswith("_")} for c in cands
        ]
    }


def fixed_answer(p: dict) -> dict:
    """The demo and test stand-in: propose every candidate on all of its claims,
    with the kind its shape suggests and a templated reason."""
    out = []
    for c in p["candidates"]:
        prefix = c["h"][0]

        def hs(*groups):
            return [v["h"] for g in groups for v in g]

        if prefix == "m":
            agree = ", ".join(a["field"] for a in c["agree"])
            differ = ", ".join(d["field"] for d in c["differ"])
            why = f"The names match and {agree} agree" + (
                f"; {differ} differ." if differ else "; nothing differs."
            )
            basis = hs(c["a"]["names"], c["b"]["names"]) + [
                v["h"] for a in c["agree"] for v in (a["a"], a["b"])
            ]
            kind = "same_person"
        elif prefix == "n":
            why = "Only the full name matches; no other record in the case has it."
            basis, kind = hs(c["a"]["names"], c["b"]["names"]), "same_person"
        elif prefix == "v":
            why = f"Placed at {c['place']} in {c['window']}, the incident's place and time."
            basis, kind = hs(c["person"], c["claims"]), "involved"
        elif prefix == "r":
            why = f"Both name the same {len(c['people_in_both'])} people."
            basis = [v["h"] for r in c["people_in_both"] for v in (r["a"], r["b"])]
            kind = "related"
        else:
            why = (
                "Named together and held at one place at one time."
                if c["hint"] == "cellmate"
                else "Same family name and the same parent's name."
            )
            basis = hs(c["a"]["names"], c["b"]["names"]) + [
                v["h"] for r in c["together"] for v in (r["a"], r["b"])
            ]
            kind = c["hint"]
        out.append(
            {
                "h": c["h"],
                "propose": True,
                "kind": kind,
                "why": why,
                "basis": list(dict.fromkeys(basis)),
            }
        )
    return {"links": out}


ALLOWED = {
    "m": ("same_person",),
    "n": ("same_person",),
    "v": ("involved",),
    "r": DOCUMENT_RELATIONS,
    "x": PERSON_RELATIONS,
}


def validate(answer: dict | None, cands: list[dict], h: Handles, claims) -> tuple:
    """The links that may be written, and what was dropped and why."""
    dropped = {"declined": 0, "unknown": 0, "uncited": 0, "weak": 0, "kind": 0}
    if answer is None:
        return [], dropped
    by = {c["h"]: c for c in cands}
    out, seen = [], set()
    for link in answer["links"]:
        cand = by.get(link["h"])
        if cand is None or link["h"] in seen:
            dropped["unknown"] += 1
            continue
        seen.add(link["h"])
        if not link["propose"]:
            dropped["declined"] += 1
            continue
        if link["kind"] not in ALLOWED[link["h"][0]]:
            dropped["kind"] += 1
            continue
        allowed = {c.id: c for c in cand["_basis"]}
        ids = [h.ref.get(x) for x in link["basis"]]
        if not ids:
            dropped["uncited"] += 1
            continue
        if any(i not in allowed for i in ids):
            dropped["unknown"] += 1
            continue
        basis = [allowed[i] for i in dict.fromkeys(ids)]
        if not _strong(link["h"][0], cand, basis):
            dropped["weak"] += 1
            continue
        why = link["why"].strip()[:300]
        if read.leaks(why, {c.id for c in basis}, claims):
            why = ""
        out.append({"cand": cand, "kind": link["kind"], "basis": basis, "why": why})
    return out, dropped


def _strong(prefix: str, cand: dict, basis: list[Claim]) -> bool:
    """The fail-closed shape each kind needs from its cited claims."""
    if prefix in ("m", "n", "x"):
        sides = {c.person for c in basis}
        if prefix == "n":
            return set(cand["_sides"]) <= sides
        return set(cand["_sides"]) <= sides and any(c.prop != Prop.NAME for c in basis)
    if prefix == "v":
        return any(c.prop in LOCATED for c in basis)
    a, b = cand["_docs"]
    return {a, b} <= {c.source for c in basis}


# --- the run ------------------------------------------------------------------------


async def focus_of(db, case, ref: str | None) -> set | None:
    """Claim ids a run must touch: a batch's, a source's or a person's."""
    if not ref:
        return None
    kind, _, rest = ref.partition(":")
    if kind == "batch":
        from sealbook.ledger.models import Acquisition

        shas = set(
            await db.scalars(
                select(Acquisition.blob_sha256).where(
                    Acquisition.context["batch"].astext == rest
                )
            )
        )
        return {c.id for c in case.claims if c.source in shas}
    if kind == "blob":
        return {c.id for c in case.claims if c.source == rest}
    if kind == "person":
        cluster = read.cluster_of(case.groups, ref)
        return {c.id for c in case.claims if c.person == cluster}
    raise ValueError(f"cannot focus on {ref}")


def _statement(c, link: dict) -> StatementIn:
    cand, kind, basis = link["cand"], link["kind"], link["basis"]
    refs = sorted(_ref(b.id) for b in basis)
    why = {"why": link["why"]} if link["why"] else {}
    prefix = cand["h"][0]
    anchor = basis[0]
    if prefix in ("m", "n", "x"):
        a, b = cand["_sides"]
        here = next(x for x in basis if x.person == a)
        there = next(x for x in basis if x.person == b)
        value = {"person": there.subject.removeprefix("person:"), "basis": refs} | why
        if prefix == "x":
            value["kind"] = kind
        return StatementIn(
            subject=here.subject,
            prop=Prop.RELATION if prefix == "x" else Prop.SAME_AS,
            value=value,
            author_id=c.run.principal_id,
            run_id=c.run.id,
            span_blob=here.span_blob,
            span=here.span,
            evidence=(EvidenceKind.CORROBORATING_FIELD,),
        )
    if prefix == "v":
        anchor = next(x for x in basis if x.prop in LOCATED)
        return StatementIn(
            subject=cand["_incident"],
            prop=Prop.INVOLVED,
            value={
                "person": anchor.subject,
                "role": "unspecified",
                "basis": refs,
            }
            | why,
            author_id=c.run.principal_id,
            run_id=c.run.id,
            span_blob=anchor.span_blob,
            span=anchor.span,
            evidence=(EvidenceKind.CORROBORATING_FIELD,),
        )
    a, b = cand["_docs"]
    return StatementIn(
        subject=f"blob:{a}",
        prop=Prop.RELATION,
        value={"blob": b, "kind": kind, "basis": refs} | why,
        author_id=c.run.principal_id,
        run_id=c.run.id,
        evidence=(EvidenceKind.CORROBORATING_FIELD,),
    )


def _key(cand: dict):
    prefix = cand["h"][0]
    if prefix in ("m", "n"):
        return ("person", frozenset(cand["_sides"]))
    if prefix == "x":
        return ("relation", frozenset(cand["_sides"]))
    if prefix == "v":
        return ("involved", cand["_incident"], cand["_person"])
    return ("document", frozenset(cand["_docs"]))


def _wire(k) -> list:
    return [k[0], *(sorted(p) if isinstance(p, frozenset) else p for p in k[1:])]


def _unwire(w: list):
    return (w[0], *(frozenset(p) if isinstance(p, list) else p for p in w[1:]))


async def declined(db, investigation: str, model: str) -> list:
    """What this model already declined in this case under this prompt, with the
    claims it saw: like a rejection, asked again only when a new claim joins.
    A new model or prompt version reads everything afresh."""
    # ponytail: reads every past run's output; keep declines in a table if a case
    # gathers thousands of linker runs.
    runs = await db.scalars(
        select(Run).where(
            Run.tool == TOOL,
            Run.status == "done",
            Run.model_requested == model,
            Run.inputs["investigation"].astext == investigation,
            Run.output_hash.is_not(None),
        )
    )
    out = []
    for run in runs:
        o = read.read_output(await db.get(Blob, run.output_hash))
        if o.get("version") == PROMPT_VERSION:
            out += [(_unwire(k), set(basis)) for k, basis in o.get("declined", [])]
    return out


async def link(c) -> str:
    """Worker tool `link`: inputs {investigation, for_user, focus}."""
    investigation = c.run.inputs["investigation"]
    case = await read.case(c.s, investigation)
    focus = await focus_of(c.s, case, c.run.inputs.get("focus"))
    incidents = await read.incidents(c.s, investigation, case.groups)
    exists, rejected = await known(c.s, case.groups)
    for k, basis in await declined(c.s, investigation, c.run.model_requested):
        rejected.setdefault(k, set()).update(basis)
    h = Handles()
    skipped = []

    def keep(x):
        new = fresh(_key(x), [_ref(b.id) for b in x["_basis"]], exists, rejected)
        if not new:
            skipped.append(x)
        return new

    cands = candidates(case, incidents, focus, h, keep)
    await c.emit(
        "input_read",
        claims=len(case.claims),
        candidates=len(cands) + len(skipped),
        already_known=len(skipped),
    )
    await read.log_read(
        c,
        c.run.inputs.get("focus") or f"investigation:{investigation}",
        sorted({b.id for x in cands for b in x["_basis"]}),
    )
    c.run.prompt_hash = read.prompt_hash(PROMPT_VERSION, SYSTEM, SCHEMA)
    if c.run.model_requested == read.FIXED:
        c.run.inputs = {**c.run.inputs, "demo": read.note("linker proposals")}
    # Small calls keep each answer inside the route's timeout, and the run stops
    # asking before its lease ends; what is left is read again by the next run.
    asked = {
        "valid": True,
        "raw": [],
        "seconds": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    links, dropped, deferred, declines = [], {}, [], []
    started = time.monotonic()
    for i in range(0, len(cands), PER_CALL):
        part = cands[i : i + PER_CALL]
        if time.monotonic() - started > BUDGET_SECONDS:
            deferred += part
            continue
        got = await read.ask(
            c,
            "links",
            SYSTEM,
            payload(part),
            SCHEMA,
            fixed_answer,
            lambda a, part=part: validate(a, part, h, case.claims)[1],
        )
        asked["valid"] &= got["valid"]
        asked["raw"].append(got["raw"])
        asked["seconds"] = round(asked["seconds"] + got["seconds"], 1)
        for k in ("prompt_tokens", "completion_tokens"):
            asked[k] += got[k] or 0
        found, why = validate(got["answer"], part, h, case.claims)
        links += found
        by = {x["h"]: x for x in part}
        for a in (got["answer"] or {}).get("links", []):
            if not a["propose"] and a["h"] in by:
                x = by[a["h"]]
                declines.append(
                    [_wire(_key(x)), sorted(_ref(b.id) for b in x["_basis"])]
                )
        for k, n in why.items():
            dropped[k] = dropped.get(k, 0) + n
    written, refused = [], []
    for link_ in links:
        k = _key(link_["cand"])
        if not fresh(k, [_ref(b.id) for b in link_["basis"]], exists, rejected):
            refused.append("already known")
            continue
        s = _statement(c, link_)
        s = dataclasses.replace(s, based_on=await write.subject_head(c.s, s.subject))
        try:
            st = await write.append(c.s, s, sign=c.sign)
        except Refused as exc:
            refused.append(exc.reason)
            await c.emit("dropped", drop_reason=f"refused: {exc.reason}")
            continue
        exists.add(k)
        written.append(
            {"statement": str(st.id), "prop": st.prop, "kind": link_["kind"]}
        )
    await c.emit(
        "statements_written",
        candidates=len(cands),
        written=len(written),
        dropped=sum(dropped.values()) + len(refused),
        valid=asked["valid"],
        seconds=asked["seconds"],
    )
    return await read.store(
        c,
        {
            "version": PROMPT_VERSION,
            "investigation": investigation,
            "focus": c.run.inputs.get("focus"),
            "candidates": len(cands),
            "already_known": len(skipped),
            "deferred": len(deferred),
            "declined": declines,
            "written": written,
            "dropped": dropped,
            "refused": refused,
            "valid": asked["valid"],
            "cost": {
                k: asked[k] for k in ("seconds", "prompt_tokens", "completion_tokens")
            },
            "raw": asked["raw"],
        },
    )


async def queue(db, investigation: str, user_id: int, model: str, focus=None) -> Run:
    agent = await read.agent_for(db, AGENT, user_id)
    run = Run(
        principal_id=agent.id,
        tool=TOOL,
        route=Config.AGENT_ROUTE,
        model_requested=model,
        inputs={"investigation": investigation, "for_user": user_id}
        | ({"focus": focus} if focus else {}),
    )
    db.add(run)
    await db.flush()
    return run
