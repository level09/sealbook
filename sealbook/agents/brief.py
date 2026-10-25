"""The intake brief: when a batch finishes, one run reads the batch against the case
and writes what arrived, who, where and when, what it connects to, what disagrees,
and up to five next steps, each a link that opens the thing to do.

What arrived and who, where and when are counted from the claims. The model writes
the connections, disagreements and the order of next steps from candidates built
here, and may cite only the handles it was given. Every line cites claims or
sources; a line without one, or citing anything unknown, is dropped. The stored
brief is the run's sealed output. A reader sees it through masking: a line resting
on a sensitive source they have not revealed is locked, a line resting on an erased
or rejected claim is gone."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from stk.user.models import User

from sealbook.agents import read
from sealbook.agents.read import Claim, Handles, claim_view, place_name, when
from sealbook.collaboration.models import Assignment
from sealbook.discovery.evidence import granted, kind_of, view_session
from sealbook.discovery.models import FindingKey
from sealbook.discovery.service import available
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop, StatementIn, Tier
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Run,
    Statement,
    StatementTier,
)
from sealbook.monitor import filter as mfilter
from sealbook.monitor.data import LOCATED, Context
from sealbook.places import normalise, resolve
from sealbook.research.service import blocked
from sealbook.review.findings import month_label
from sealbook.settings import Config

TOOL = "brief"
AGENT = "Intake brief"
PROMPT_VERSION = "intake-brief-1"
MAX_NEXT = 5
MAX_TEXT = 400
SINGLE = (
    Prop.BIRTH_DATE,
    Prop.FATHER_NAME,
    Prop.MOTHER_NAME,
    Prop.PLACE_OF_BIRTH,
    Prop.SEX,
)
# A handle in a sentence means the model wrote for itself, not for the analyst.
HANDLE = re.compile(r"\b[a-z]\d+\b")
SECTIONS = ("arrived", "who", "where", "when", "connects", "disagrees", "next")

SYSTEM = (
    "You help a human rights analyst read a batch of new evidence against their case. "
    "You get the batch's people, connections between the batch and the case, "
    "disagreements between the batch and decided claims, and possible next steps, "
    "each with a handle. Write one short plain sentence for each connection and each "
    "disagreement worth the analyst's attention, and say only what the data says: "
    "no guesses, no probabilities, no names or values that are not in the data. "
    "Cite in `cite` the claim handles (c...) the sentence rests on, and put the "
    "connection or disagreement handle in `about`. A disagreement cites both sides. "
    "Then choose at most five next steps from `actions`, most useful first, each by "
    "its handle with a short imperative sentence. Skip anything that adds nothing. "
    "Write for the analyst: name people, places, dates and source titles in words; "
    "handles go only in `about`, `cite` and `action`, never in `text`."
)
SENTENCE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["about", "text", "cite"],
    "properties": {
        "about": {"type": "string"},
        "text": {"type": "string"},
        "cite": {"type": "array", "items": {"type": "string"}},
    },
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["connects", "disagrees", "next"],
    "properties": {
        "connects": {"type": "array", "items": SENTENCE},
        "disagrees": {"type": "array", "items": SENTENCE},
        "next": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "text"],
                "properties": {
                    "action": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
        },
    },
}


def _line(text, claims=(), sources=(), href=None, tag=None) -> dict:
    return {
        "text": text,
        "claims": sorted({str(c) for c in claims}),
        "sources": sorted(set(sources)),
        "href": href,
        "tag": tag,
    }


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _words(token: str | None) -> str:
    return (token or "").replace("_", " ")


# --- reading the batch ------------------------------------------------------------


async def gather(db, batch: str, user_id: int) -> dict:
    acqs = (
        (
            await db.execute(
                select(Acquisition)
                .where(Acquisition.context["batch"].astext == batch)
                .order_by(Acquisition.at, Acquisition.id)
            )
        )
        .scalars()
        .all()
    )
    if not acqs:
        raise LookupError(f"no acquisitions in batch {batch}")
    investigation = acqs[0].context.get("investigation")
    shas = list(dict.fromkeys(a.blob_sha256 for a in acqs))
    kept = set(
        await db.scalars(
            select(Blob.sha256).where(Blob.sha256.in_(shas), available(Blob.sha256))
        )
    )
    shas = [s for s in shas if s in kept]
    case = await read.case(db, investigation)
    ctx = await Context.load(db, blobs=shas)
    mimes = dict(
        (await db.execute(select(Blob.sha256, Blob.mime).where(Blob.sha256.in_(shas))))
        .tuples()
        .all()
    )
    docs = {s: ctx.doc(s) | {"mime": mimes.get(s, "")} for s in shas} | case.docs
    mine = [c for c in case.claims if c.source in kept]
    ids = {c.id for c in mine}
    findings = [
        fk
        for fk in await db.scalars(
            select(FindingKey)
            .where(
                FindingKey.investigation == investigation, FindingKey.state == "open"
            )
            .order_by(FindingKey.rank.desc(), FindingKey.key)
        )
        if ids & {uuid.UUID(m) for m in fk.members or ()}
    ]
    finding_values = {}
    if findings:
        finding_values = dict(
            (
                await db.execute(
                    select(FindingKey.key, Payload.value)
                    .join(Statement, Statement.id == FindingKey.statement_id)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .where(FindingKey.key.in_([f.key for f in findings]))
                )
            ).all()
        )
    # A shared presence and a candidate incident over the same people, places and
    # month are one set read twice: keep the stronger, name the other.
    sets, kept = {}, []
    for fk in findings:
        v = finding_values.get(fk.key) or {}
        k = (
            tuple(sorted(v.get("people", ()))),
            tuple(sorted(v.get("places", ()))),
            v.get("month"),
        )
        if k in sets:
            sets[k].also = fk.kind
            continue
        fk.also = None
        sets[k] = fk
        kept.append(fk)
    findings = kept
    assignments = (
        (
            await db.execute(
                select(Assignment, User)
                .join(User, User.id == Assignment.assignee_id)
                .where(
                    Assignment.investigation == investigation,
                    Assignment.status == "open",
                    Assignment.assignee_id != user_id,
                )
                .order_by(Assignment.due.asc().nulls_last(), Assignment.id)
            )
        )
        .tuples()
        .all()
    )
    pending_links = (
        (
            await db.execute(
                select(Statement.id, Statement.subject, Payload.value)
                .join(Payload, Payload.commitment == Statement.commitment)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .join(Run, Run.id == Statement.run_id)
                .where(
                    Run.tool == "link",
                    StatementTier.tier == Tier.UNCONFIRMED,
                )
                .order_by(Statement.recorded_at, Statement.id)
            )
        )
        .tuples()
        .all()
    )
    return {
        "batch": batch,
        "investigation": investigation,
        "shas": shas,
        "docs": docs,
        "case": case,
        "mine": mine,
        "incidents": await read.incidents(db, investigation, case.groups),
        "findings": findings,
        "finding_values": finding_values,
        "assignments": assignments,
        "links": [
            (sid, subject, value)
            for sid, subject, value in pending_links
            if {uuid.UUID(b.removeprefix("statement:")) for b in value.get("basis", [])}
            & ids
        ],
        "blocked": investigation in await blocked(db),
        "candidates": {
            name: [p.id for p in found]
            for name, found in (
                await resolve.candidates_many(
                    db,
                    {
                        normalise(c.text)
                        for c in mine
                        if c.prop in LOCATED and case.places.get(c.id) is None
                    },
                )
            ).items()
        },
    }


# --- what the counting says -------------------------------------------------------


def facts(g: dict) -> dict[str, list[dict]]:
    """What arrived, who, where and when: counted, not written by a model."""
    case, mine, docs = g["case"], g["mine"], g["docs"]
    others = [c for c in case.claims if c.source not in set(g["shas"])]
    arrived = []
    for sha in g["shas"]:
        d, n = docs[sha], sum(c.source == sha for c in mine)
        kind = ", ".join(x for x in (_words(d["doc_type"]), kind_of(d["mime"])) if x)
        read_ = _plural(n, "claim") + " read" if n else "no claims read yet"
        arrived.append(
            _line(
                f"{d['title']}: {kind}, {read_}.",
                sources=[sha],
                href=f"/evidence/{sha}",
            )
        )
    who = []
    by_person: dict[str, list[Claim]] = {}
    for c in mine:
        if c.person:
            by_person.setdefault(c.person, []).append(c)
    for person, claims in by_person.items():
        name_claims = [c for c in claims if c.prop == Prop.NAME]
        name_claims.sort(
            key=lambda c: (c.sensitive, c.tier != Tier.ACCEPTED, str(c.id))
        )
        cite = name_claims[:1] or claims[:1]
        name = name_claims[0].text if name_claims else "A person without a name"
        seen = sorted({c.source for c in others if c.person == person})
        href = f"/people/{claims[0].subject.removeprefix('person:')}"
        if seen:
            text = f"{name}, already in the case from {_plural(len(seen), 'other source')}."
            who.append(_line(text, [c.id for c in cite], href=href, tag="known"))
        else:
            who.append(
                _line(
                    f"{name}, new to the case.",
                    [c.id for c in cite],
                    href=href,
                    tag="new",
                )
            )
    where, unresolved = [], {}
    places: dict = {}
    for c in mine:
        if c.prop not in LOCATED:
            continue
        place = case.places.get(c.id)
        if place is None:
            unresolved.setdefault(normalise(c.text), []).append(c)
        else:
            places.setdefault(place.id, (place, []))[1].append(c)
    for place, claims in places.values():
        known = any(
            case.places.get(o.id) is not None and case.places[o.id].id == place.id
            for o in others
            if o.prop in LOCATED
        )
        where.append(
            _line(
                f"{place_name(place)}, {'known to the case' if known else 'new to the case'}.",
                [c.id for c in claims[:3]],
                href=f"/places/{place.id}",
                tag="known" if known else "new",
            )
        )
    for claims in unresolved.values():
        where.append(
            _line(
                f"{claims[0].text}, not yet matched to a place.",
                [c.id for c in claims[:3]],
                href="/places?tab=unresolved",
                tag="open",
            )
        )
    dated = sorted((c for c in mine if c.lo), key=lambda c: (c.lo, str(c.id)))
    when_ = []
    if dated:
        first = dated[0]
        last = max(dated, key=lambda c: (c.hi, str(c.id)))
        a, b = when(first.lo, first.hi), when(last.lo, last.hi)
        text = f"Dated {a}." if a == b else f"Dated from {a} to {b}."
        when_.append(_line(text, {first.id, last.id}))
    return {"arrived": arrived, "who": who, "where": where, "when": when_}


# --- what the model is asked ------------------------------------------------------


def candidates(g: dict, h: Handles) -> dict:
    """Connections, disagreements and next steps the model may write about, each with
    a handle, in a fixed order so the same batch always reads the same."""
    case, mine, docs = g["case"], g["mine"], g["docs"]
    batch = set(g["shas"])
    others = [c for c in case.claims if c.source not in batch]
    view = lambda c: claim_view(h, c, docs)  # noqa: E731
    by_person: dict[str, list[Claim]] = {}
    for c in mine:
        if c.person:
            by_person.setdefault(c.person, []).append(c)
    name = {p: case.name(p, among=cs) or case.name(p) for p, cs in by_person.items()}

    connects = []
    for person, claims in by_person.items():
        theirs = [o for o in others if o.person == person]
        if not theirs:
            continue
        per_source: dict[str, Claim] = {}
        for o in sorted(theirs, key=lambda o: (o.tier != Tier.ACCEPTED, str(o.id))):
            per_source.setdefault(o.source, o)
        shown = sorted(per_source.values(), key=lambda o: docs[o.source]["title"])[:3]
        connects.append(
            {
                "h": h("k", ("person", person)),
                "kind": "same person in other sources",
                "person": name[person],
                "batch": [view(c) for c in claims[:3]],
                "case": [view(o) for o in shown],
                "other_sources": len(per_source),
                "href": f"/people/{claims[0].subject.removeprefix('person:')}",
            }
        )
    for inc in g["incidents"]:
        for person, claims in by_person.items():
            at = [
                c
                for c in claims
                if c.prop in LOCATED
                and (place := case.places.get(c.id)) is not None
                and place.id in inc["places"]
                and c.lo
                and c.lo <= inc["places"][place.id][1]
                and inc["places"][place.id][0] <= c.hi
            ]
            involved = person in inc["people"]
            if not (involved or at):
                continue
            connects.append(
                {
                    "h": h("k", ("incident", inc["id"], person)),
                    "kind": "involved in an incident"
                    if involved
                    else "at an incident's place and time",
                    "person": name[person],
                    "incident": inc["title"],
                    "batch": [view(c) for c in (at or claims)[:3]],
                    "href": f"/incidents/{inc['id'].removeprefix('incident:')}",
                }
            )
    for fk in g["findings"]:
        members = {uuid.UUID(m) for m in fk.members or ()}
        here = [c for c in mine if c.id in members]
        there = [c for c in others if c.id in members]
        value = g["finding_values"].get(fk.key) or {}
        places = [
            place_name(p)
            for p in {
                case.places[c.id].id: case.places[c.id]
                for c in here + there
                if case.places.get(c.id) is not None
            }.values()
        ]
        connects.append(
            {
                "h": h("k", ("finding", fk.key)),
                "kind": _words(fk.kind) + " finding",
                "also_proposed_as": _words(fk.also) if fk.also else None,
                "places": places,
                "month": month_label(value["month"]) if value.get("month") else None,
                "batch": [view(c) for c in here[:4]],
                "case": [view(c) for c in there[:4]],
                "href": f"/discover?f={fk.key}",
            }
        )

    disagrees = []
    for c in mine:
        if c.prop not in SINGLE or not c.person:
            continue
        for o in case.claims:
            if (
                o.person == c.person
                and o.prop == c.prop
                and o.tier == Tier.ACCEPTED
                and o.id != c.id
                and not read.same_value(c.prop, c.text, o.text)
            ):
                disagrees.append(
                    {
                        "h": h("d", (c.id, o.id)),
                        "person": name.get(c.person),
                        "field": c.prop.replace("_", " "),
                        "batch": view(c),
                        "accepted": view(o),
                    }
                )

    actions = []
    pending: dict[str, list[Claim]] = {}
    for c in mine:
        if c.tier == Tier.UNCONFIRMED:
            pending.setdefault(c.source, []).append(c)
    for sha, claims in pending.items():
        actions.append(
            {
                "h": h("a", ("review", sha)),
                "does": (
                    "Review the claim"
                    if len(claims) == 1
                    else f"Review the {len(claims)} claims"
                )
                + f" read from {docs[sha]['title']}",
                "href": f"/review?box=open&statement={claims[0].id}",
                "sources": [sha],
            }
        )
    if g["links"]:
        actions.append(
            {
                "h": h("a", ("links",)),
                "does": (
                    "Decide the link"
                    if len(g["links"]) == 1
                    else f"Decide the {len(g['links'])} links"
                )
                + " the linker proposed for this batch",
                "href": f"/review?box=open&statement={g['links'][0][0]}",
                "sources": sorted(batch)[:1],
            }
        )
    seen_names = set()
    for c in mine:
        key = normalise(c.text)
        if (
            c.prop in LOCATED
            and case.places.get(c.id) is None
            and key not in seen_names
        ):
            seen_names.add(key)
            found = g["candidates"].get(key, [])
            actions.append(
                {
                    "h": h("a", ("place", key)),
                    "does": f"Confirm where {c.text} is",
                    "href": f"/places/{found[0]}"
                    if len(found) == 1
                    else "/places?tab=unresolved",
                    "claims": [c.id],
                }
            )
    for fk in g["findings"]:
        value = g["finding_values"].get(fk.key) or {}
        actions.append(
            {
                "h": h("a", ("finding", fk.key)),
                "does": f"Decide the {_words(fk.kind)} finding"
                + (f" for {month_label(value['month'])}" if value.get("month") else "")
                + (f" (also proposed as a {_words(fk.also)})" if fk.also else ""),
                "href": f"/discover?f={fk.key}",
                "sources": sorted(
                    {c.source for c in mine if str(c.id) in (fk.members or ())}
                ),
            }
        )
    persons = set(by_person)
    for a, user in g["assignments"]:
        kind, _, ref = a.subject.partition(":")
        if kind == "blob" and ref not in batch:
            shared = {c.person for c in case.claims if c.source == ref} & persons
            if not shared:
                continue
            actions.append(
                {
                    "h": h("a", ("ask", str(a.id))),
                    "does": f"Ask {user.name or user.email}, who is assigned "
                    f"{docs[ref]['title']}, about the people it shares with this batch",
                    "href": f"/evidence/{ref}",
                    "sources": [ref],
                }
            )
    if not g["blocked"]:
        for person, claims in by_person.items():
            ok = [
                c
                for c in claims
                if c.prop == Prop.NAME
                and not c.sensitive
                and c.tier in (Tier.ACCEPTED, Tier.CORROBORATED)
            ]
            if ok:
                actions.append(
                    {
                        "h": h("a", ("research", person)),
                        "does": f"Search the web for {ok[0].text}",
                        "href": f"/people/{ok[0].subject.removeprefix('person:')}?research=1",
                        "claims": [ok[0].id],
                    }
                )
    return {"connects": connects, "disagrees": disagrees, "actions": actions}


def payload(g: dict, cands: dict, lines: dict) -> dict:
    """What the model reads: the counted lines as context, then the candidates.
    Hrefs and ids stay here; the model sees handles."""

    def strip(d):
        return {k: v for k, v in d.items() if k not in ("href", "sources", "claims")}

    return {
        "batch": [line["text"] for line in lines["arrived"]],
        "people": [line["text"] for line in lines["who"]],
        "connections": [strip(c) for c in cands["connects"]],
        "disagreements": [strip(d) for d in cands["disagrees"]],
        "actions": [{"h": a["h"], "does": a["does"]} for a in cands["actions"]],
    }


def fixed_answer(p: dict) -> dict:
    """The demo and test stand-in for the model: one templated sentence per
    candidate, every citation it was given, the first five actions in order."""

    def cites(*groups):
        return [c["h"] for g in groups for c in g]

    connects = []
    for k in p["connections"]:
        if k["kind"] == "same person in other sources":
            titles = list(dict.fromkeys(c["source"] for c in k["case"]))
            more = k["other_sources"] - len(titles)
            listed = (
                ", ".join(titles[:-1]) + f" and {titles[-1]}"
                if len(titles) > 1
                else titles[0]
            )
            if more:
                listed = ", ".join(titles) + f" and {_plural(more, 'other source')}"
            text = f"{k['person']} also appears in {listed}."
        elif k["kind"].endswith("finding"):
            where = " and ".join(k["places"]) or "one place"
            month = f" in {k['month']}" if k["month"] else ""
            also = (
                f", also proposed as a {k['also_proposed_as']},"
                if k["also_proposed_as"]
                else ""
            )
            text = (
                f"An open {k['kind']} at {where}{month}{also} rests on "
                f"{_plural(len(k['batch']), 'claim')} from this batch."
            )
        elif k["kind"] == "involved in an incident":
            text = f"{k['person']} is involved in the incident {k['incident']}."
        else:
            text = f"{k['person']} is placed at the place and time of the incident {k['incident']}."
        connects.append(
            {
                "about": k["h"],
                "text": text,
                "cite": cites(k["batch"], k.get("case", [])),
            }
        )
    disagrees = [
        {
            "about": d["h"],
            "text": f"{d['batch']['source']} gives {d['field']} {d['batch']['value']} "
            f"for {d['person']}; the accepted claim from {d['accepted']['source']} "
            f"says {d['accepted']['value']}.",
            "cite": [d["batch"]["h"], d["accepted"]["h"]],
        }
        for d in p["disagreements"]
    ]
    nxt = [{"action": a["h"], "text": a["does"] + "."} for a in p["actions"][:MAX_NEXT]]
    return {"connects": connects, "disagrees": disagrees, "next": nxt}


def validate(
    answer: dict | None, cands: dict, h: Handles, claims: list[Claim]
) -> tuple:
    """The model's sentences that may be stored, and what was dropped and why. A
    sentence must cite, cite only handles it was given, cite both sides of a
    disagreement, and name no sensitive value whose claim it does not cite."""
    dropped = {
        "uncited": 0,
        "unknown": 0,
        "one_sided": 0,
        "leak": 0,
        "extra": 0,
        "handle": 0,
    }
    out = {"connects": [], "disagrees": [], "next": []}
    if answer is None:
        return out, dropped
    by = {c["h"]: c for c in cands["connects"]} | {
        d["h"]: d for d in cands["disagrees"]
    }
    acts = {a["h"]: a for a in cands["actions"]}
    claim_ids = {c.id for c in claims}

    def cited(handles):
        ids = [h.ref.get(x) for x in handles]
        if any(i not in claim_ids for i in ids):
            return None
        return set(ids)

    for section in ("connects", "disagrees"):
        for s in answer[section]:
            cand = by.get(s["about"])
            if cand is None or (section == "disagrees") != (s["about"][:1] == "d"):
                dropped["unknown"] += 1
                continue
            if not s["cite"]:
                dropped["uncited"] += 1
                continue
            ids = cited(s["cite"])
            if ids is None:
                dropped["unknown"] += 1
                continue
            text = s["text"].strip()[:MAX_TEXT]
            if not text:
                dropped["uncited"] += 1
                continue
            if HANDLE.search(text):
                dropped["handle"] += 1
                continue
            if section == "disagrees" and not (
                h.ref[cand["batch"]["h"]] in ids and h.ref[cand["accepted"]["h"]] in ids
            ):
                dropped["one_sided"] += 1
                continue
            if read.leaks(text, ids, claims):
                dropped["leak"] += 1
                continue
            out[section].append(_line(text, ids, href=cand.get("href")))
    used = set()
    for s in answer["next"]:
        a = acts.get(s["action"])
        if a is None or s["action"] in used:
            dropped["unknown"] += 1
            continue
        if len(used) == MAX_NEXT:
            dropped["extra"] += 1
            continue
        text = s["text"].strip()[:MAX_TEXT]
        if not text or HANDLE.search(text):
            text = a["does"]
        ids = set(a.get("claims", ()))
        if read.leaks(text, ids, claims):
            dropped["leak"] += 1
            continue
        if not ids and not a.get("sources"):
            dropped["uncited"] += 1
            continue
        used.add(s["action"])
        out["next"].append(_line(text, ids, a.get("sources", ()), href=a["href"]))
    return out, dropped


# --- the run ------------------------------------------------------------------------


async def brief(c) -> str:
    """Worker tool `brief`: inputs {batch, for_user}."""
    batch, user_id = c.run.inputs["batch"], c.run.inputs["for_user"]
    g = await gather(c.s, batch, user_id)
    lines = facts(g)
    h = Handles()
    cands = candidates(g, h)
    p = payload(g, cands, lines)
    await c.emit(
        "input_read",
        sources=len(g["shas"]),
        claims=len(g["mine"]),
        candidates=len(cands["connects"]) + len(cands["disagrees"]),
        actions=len(cands["actions"]),
    )
    await read.log_read(c, f"batch:{batch}", [x.id for x in g["mine"]])
    c.run.prompt_hash = read.prompt_hash(PROMPT_VERSION, SYSTEM, SCHEMA)
    if c.run.model_requested == read.FIXED:
        c.run.inputs = {**c.run.inputs, "demo": read.note("intake brief")}
    asked = await read.ask(c, "intake_brief", SYSTEM, p, SCHEMA, fixed_answer)
    written, dropped = validate(asked["answer"], cands, h, g["case"].claims)
    sections = lines | written
    # Recheck what every line cites against the ledger as it stands now.
    gone = await invisible(c.s, sections)
    for name in SECTIONS:
        keep = [s for s in sections[name] if not set(s["claims"]) & gone]
        dropped["invisible"] = (
            dropped.get("invisible", 0) + len(sections[name]) - len(keep)
        )
        sections[name] = keep
    output = {
        "version": PROMPT_VERSION,
        "batch": batch,
        "investigation": g["investigation"],
        "sections": sections,
        "dropped": dropped,
        "valid": asked["valid"],
        "cost": {
            k: asked[k] for k in ("seconds", "prompt_tokens", "completion_tokens")
        },
        "raw": asked["raw"],
    }
    await c.emit(
        "brief_written",
        lines=sum(len(v) for v in sections.values()),
        dropped=sum(dropped.values()),
        valid=asked["valid"],
        seconds=asked["seconds"],
    )
    return await read.store(c, output)


async def invisible(db, sections: dict) -> set[str]:
    """Cited claims that are gone, erased, rejected or from an unavailable source."""
    ids = {i for lines in sections.values() for s in lines for i in s["claims"]}
    if not ids:
        return set()
    ok = {
        str(i)
        for i in await db.scalars(
            select(Statement.id)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.id.in_([uuid.UUID(i) for i in ids]),
                StatementTier.tier != Tier.REJECTED,
                write.current_clause(),
                available(mfilter.source(mfilter.statement_blob())),
            )
        )
    }
    return ids - ok


# --- queue and read -----------------------------------------------------------------


async def queue(db, batch: str, user_id: int, model: str) -> Run:
    agent = await read.agent_for(db, AGENT, user_id)
    run = Run(
        principal_id=agent.id,
        tool=TOOL,
        route=Config.AGENT_ROUTE,
        model_requested=model,
        inputs={"batch": batch, "for_user": user_id},
    )
    db.add(run)
    await db.flush()
    return run


def _cost(run: Run, out: dict | None) -> dict:
    cost = (out or {}).get("cost") or {}
    return {
        "id": str(run.id),
        "status": run.status,
        "at": (run.finished_at or run.created_at).isoformat(),
        "model": run.model_served or run.model_requested,
        "route": run.route,
        "fixed": run.model_requested == read.FIXED,
        "seconds": cost.get("seconds"),
        "tokens": (cost.get("prompt_tokens") or 0)
        + (cost.get("completion_tokens") or 0)
        or None,
        "valid": (out or {}).get("valid"),
        "error": run.error,
    }


async def runs(db, batch: str) -> list[Run]:
    return list(
        await db.scalars(
            select(Run)
            .where(Run.tool == TOOL, Run.inputs["batch"].astext == batch)
            .order_by(Run.created_at.desc(), Run.id)
        )
    )


async def show(db, user, batch: str, version: str | None = None) -> dict:
    """The brief as this reader may see it, plus its versions and cost."""
    found = await runs(db, batch)
    done = [r for r in found if r.status == "done"]
    pending = next((r for r in found if r.status in ("queued", "running")), None)
    run = next((r for r in done if str(r.id) == version), done[0] if done else None)
    out = None
    if run is not None:
        out = read.read_output(await db.get(Blob, run.output_hash))
    versions = [_cost(r, None) for r in found]
    if run is None:
        return {
            "batch": batch,
            "brief": None,
            "pending": _cost(pending, None) if pending else None,
            "failed": next(
                (_cost(r, None) for r in found if r.status == "failed"), None
            ),
            "versions": versions,
        }
    sections = out["sections"]
    gone = await invisible(db, sections)
    cited = {i for lines in sections.values() for s in lines for i in s["claims"]}
    shas = (
        dict(
            (
                await db.execute(
                    select(
                        Statement.id, mfilter.source(mfilter.statement_blob())
                    ).where(Statement.id.in_([uuid.UUID(i) for i in cited - gone]))
                )
            ).all()
        )
        if cited - gone
        else {}
    )
    ctx = await Context.load(db, blobs=set(shas.values()))
    shown_claims, shaped = set(), {}
    for name in SECTIONS:
        shaped[name] = []
        for s in sections[name]:
            if set(s["claims"]) & gone:
                continue
            behind = {shas[uuid.UUID(i)] for i in s["claims"]}
            hidden = sorted(
                x for x in behind if ctx.doc(x)["sensitive"] and not granted(x)
            )
            if hidden:
                shaped[name].append(
                    {
                        "text": None,
                        "locked": [
                            {"sha256": x, "title": ctx.doc(x)["title"]} for x in hidden
                        ],
                        "href": f"/evidence/{hidden[0]}",
                        "tag": s["tag"],
                    }
                )
                continue
            shown_claims |= set(s["claims"])
            shaped[name].append(
                {"text": s["text"], "locked": [], "href": s["href"], "tag": s["tag"]}
            )
    if shown_claims:
        await log_view(db, user, run, sorted(shown_claims))
    return {
        "batch": batch,
        "investigation": out["investigation"],
        "brief": shaped,
        "version": _cost(run, out),
        "pending": _cost(pending, None) if pending else None,
        "versions": versions,
    }


async def log_view(db, user, run: Run, ids: list[str]) -> None:
    """One access record per reader, brief version and login session, naming the
    claims whose values the brief showed."""
    me = await write.principal_for_user(db, user.id, user.name or user.email)
    key = view_session()
    subject = f"brief:{run.id}"
    seen = await db.scalar(
        select(Statement.id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            Statement.subject == subject,
            Statement.prop == Prop.ACCESS,
            Statement.author_id == me.id,
            Payload.value["session"].astext == key,
        )
        .limit(1)
    )
    if seen:
        return
    await write.append(
        db,
        StatementIn(
            subject=subject,
            prop=Prop.ACCESS,
            value={
                "action": "view",
                "at": datetime.now(UTC).isoformat(),
                "session": key,
                "statements": ids,
            },
            author_id=me.id,
        ),
    )
    await db.commit()
