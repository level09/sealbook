"""Cited disagreements and draft questions. Only the reading enters the ledger."""

from __future__ import annotations

import asyncio
import time
import uuid

from sqlalchemy import select, text

from sealbook.agents import brief, linker, read, tasks
from sealbook.discovery.evidence import granted
from sealbook.ledger.contracts import Tier
from sealbook.ledger.models import Blob, Run
from sealbook.ledger.queue import LEASE
from sealbook.monitor.data import LOCATED, Context, apart_why, clock, map_layer
from sealbook.people import data as people
from sealbook.settings import Config

TOOL = "contradictions"
AGENT = "Contradiction hunter"
PROMPT_VERSION = "contradictions-5"
CAP = 24
PER_CALL = 4
BUDGET_SECONDS = LEASE.total_seconds() - 90
KINDS = (
    "spelling or transliteration",
    "date precision",
    "possibly two people",
    "sources conflict",
    "other",
)
SYSTEM = (
    "You help an analyst examine disagreements. Each candidate contains sides, "
    "with claim handles, source titles, dates and human decisions. Report only "
    "these candidates. Explain in one or two sentences why the sources might "
    "disagree, without deciding which is true, and draft a short question for a "
    "colleague to settle it. Every item must cite claims from at least two sides "
    "of one candidate. A candidate with proposed_link compares two person "
    "records that a proposal, not yet reviewed, calls one person; there the "
    "values may show they are two people. A candidate with raised says why the "
    "rules put two places side by side: times that overlap, too little time to "
    "travel between them, or the same day with no times stated; a place claim "
    "may carry the time of day the source gives. A candidate with device names "
    "files whose own metadata puts their device at one side's place and time: "
    "it says where the device was, not who held it. "
    "Use handles only in claims. In why and question, refer to records by "
    "their source title or as the first and second record; an item whose why "
    "or question contains a handle such as c1 is discarded. "
    "Do not add facts or names absent from the cited claims. " + read.DATA_ONLY
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claims", "why", "question", "kind"],
                "properties": {
                    "claims": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string", "minLength": 1, "maxLength": 600},
                    "question": {"type": "string", "minLength": 1, "maxLength": 400},
                    "kind": {"enum": list(KINDS)},
                },
            },
        }
    },
}


def forks(claims, places, timeless) -> list[dict]:
    """The person projection's disagreements over these claims, as cited sides."""
    rows = [
        {
            "id": str(c.id),
            "prop": c.prop,
            "display": c.text,
            "from": c.lo.isoformat() if c.lo else None,
            "to": c.hi.isoformat() if c.hi else None,
            "inherited": False,
            "masked": False,
            "tier": c.tier,
            "subject": "track",
            "subject_label": "track",
            "when": read.when(c.lo, c.hi),
            "time": c.time,
            "actor": {"agent": True},
        }
        for c in claims
        if c.prop in timeless | LOCATED
    ]
    facts = {}
    for row in rows:
        facts.setdefault(row["prop"], []).append(row)
    layer = map_layer(rows, places)
    steps = next(iter(layer["tracks"].values()), {}).get("steps", [])
    by_id = {str(c.id): c for c in claims}
    out = []
    for fork in people.disagreements(
        facts, steps, places, {}, timeless=timeless, same_value=read.same_value
    ):
        cand = {"sides": [[by_id[s["id"]]] for s in fork["sides"]]}
        if fork.get("why"):
            # Two places on one day: only the pairs that cannot both hold, and why.
            cand["_pairs"] = [{uuid.UUID(i) for i in p["ids"]} for p in fork["why"]]
            cand["raised"] = sorted({p["why"] for p in fork["why"]})
        out.append(cand)
    return out


def candidates(case) -> list[dict]:
    """Disagreements within one person across sources, and between two records an
    open same-person proposal joins (possibly two people, even in one source)."""
    out = []
    timeless = set(brief.SINGLE) | set(linker.CONTRADICTS)
    persons = case.by_person()
    for claims in persons.values():
        out += [
            cand
            for cand in forks(claims, case.places, timeless)
            if len({c.source for side in cand["sides"] for c in side}) >= 2
        ]
    for a, b in case.links:
        if a in persons and b in persons:
            out += [
                cand | {"link": True}
                for cand in forks(persons[a] + persons[b], case.places, timeless)
                if len({c.person for side in cand["sides"] for c in side}) == 2
            ]
    for cand in out:
        if cand.get("raised") and (device := beside(cand, case)):
            cand["device"] = device
    out.sort(
        key=lambda x: (
            -sum(c.tier == Tier.ACCEPTED for s in x["sides"] for c in s),
            -len({c.source for s in x["sides"] for c in s}),
            sorted(str(c.id) for s in x["sides"] for c in s),
        )
    )
    return out


def beside(cand, case) -> list[dict]:
    """Files whose own metadata puts their device at one side's place on its day,
    inside that side's time when it gives one. Context for a person, never a side:
    it says where the device was, not who held it."""
    out = []
    for side in cand["sides"]:
        c, place = side[0], case.places.get(side[0].id)
        if place is None or place.lon is None or c.lo is None or c.lo != c.hi:
            continue
        span = clock(c.time) if c.time else None
        for cap in case.captures:
            at = clock(cap["time"])
            if cap["day"] != c.lo or (span and not span[0] <= at[0] <= span[1]):
                continue
            here = {
                "place_id": None,
                "place": "device",
                "prop": c.prop,
                "lonlat": cap["lonlat"],
                "precision_m": cap["precision_m"] or 1,
            }
            there = here | {
                "place": "claim",
                "lonlat": [float(place.lon), float(place.lat)],
                "precision_m": place.precision_m or 1,
            }
            if apart_why(here, there) is None:
                out.append(cap | {"side": c.id})
    return out


DEVICE_NOTE = (
    "the file's own metadata, about the file and the device: where and when the "
    "device was, not who held it, and not an accepted fact about the person"
)


def payload(cands, h, docs) -> dict:
    return {
        "candidates": [
            {
                "sides": [
                    [
                        read.claim_view(h, c, docs)
                        | {"source_date": docs[c.source].get("date")}
                        for c in side
                    ]
                    for side in cand["sides"]
                ]
            }
            | ({"proposed_link": True} if cand.get("link") else {})
            | ({"raised": cand["raised"]} if cand.get("raised") else {})
            | (
                {
                    "device": [
                        {
                            "file": docs.get(d["source"], {}).get("title", "a file"),
                            "beside": h("c", d["side"]),
                            "time": d["time"],
                            "note": DEVICE_NOTE,
                        }
                        for d in cand["device"]
                    ]
                }
                if cand.get("device")
                else {}
            )
            for cand in cands
        ]
    }


def fixed_answer(p: dict) -> dict:
    return {
        "items": [
            {
                "claims": [s[0]["h"] for s in cand["sides"]],
                "kind": "possibly two people",
                "why": "Two records proposed as one person give different values here. They may be two people.",
                "question": "Could you check these passages and say whether they describe the same person?",
            }
            if cand.get("proposed_link")
            else {
                "claims": [s[0]["h"] for s in cand["sides"]],
                "kind": "sources conflict",
                "why": "The sources give different accounts. A colleague should check the original passages.",
                "question": "Could you check these passages and confirm which account the sources support?",
            }
            for cand in p["candidates"]
        ]
    }


def fresh(ids, seen: set) -> bool:
    return frozenset(str(i) for i in ids) not in seen


def validate(answer, cands, h, claims) -> tuple[list, dict]:
    dropped = dict.fromkeys(
        ("unknown", "one_sided", "leak", "handle", "empty", "duplicate"), 0
    )
    items, seen = [], set()
    by_id = {c.id: c for c in claims}
    allowed = [
        ({frozenset(c.id for c in s) for s in cand["sides"]}, cand.get("_pairs"))
        for cand in cands
    ]
    for item in (answer or {}).get("items", []):
        refs = item["claims"]
        if any(r not in h.ref or not r.startswith("c") for r in refs):
            dropped["unknown"] += 1
            continue
        ids = {h.ref[r] for r in refs}
        two = {by_id[i].source for i in ids}, {by_id[i].person for i in ids}
        if max(len(x) for x in two) < 2 or not any(
            ids <= set().union(*sides)
            and sum(bool(ids & s) for s in sides) >= 2
            and (pairs is None or any(pair <= ids for pair in pairs))
            for sides, pairs in allowed
        ):
            dropped["one_sided"] += 1
            continue
        why, question = item["why"].strip(), item["question"].strip()
        if not why or not question:
            dropped["empty"] += 1
        elif brief.HANDLE.search(why) or brief.HANDLE.search(question):
            dropped["handle"] += 1
        elif read.leaks(why, ids, claims) or read.leaks(question, ids, claims):
            dropped["leak"] += 1
        elif not fresh(ids, seen):
            dropped["duplicate"] += 1
        else:
            seen.add(frozenset(str(i) for i in ids))
            items.append(
                {
                    "kind": item["kind"],
                    "why": why,
                    "question": question,
                    "claims": sorted(str(i) for i in ids),
                }
            )
    return items, dropped


async def runs(db, investigation: str) -> list[Run]:
    return list(
        await db.scalars(
            select(Run)
            .where(
                Run.tool == TOOL, Run.inputs["investigation"].astext == investigation
            )
            .order_by(Run.created_at.desc(), Run.id)
        )
    )


async def reported(db, investigation: str) -> set:
    seen = set()
    for run in await runs(db, investigation):
        if run.status == "done" and run.output_hash:
            out = read.read_output(await db.get(Blob, run.output_hash))
            seen.update(frozenset(item["claims"]) for item in out["items"])
    return seen


async def contradictions(c) -> str:
    await tasks.check_access(c.s, c.run)
    investigation = c.run.inputs["investigation"]
    agent = await read.agent_for(c.s, AGENT, c.run.inputs["for_user"])
    if agent.id != c.run.principal_id or c.run.route != Config.AGENT_ROUTE:
        raise ValueError(
            "contradictions must act for the named person on the local route"
        )
    case = await read.case(c.s, investigation)
    case.captures = await read.captures(c.s, investigation)
    every = candidates(case)
    seen = await reported(c.s, investigation)
    pending = [x for x in every if fresh([b.id for s in x["sides"] for b in s], seen)]
    await read.log_read(
        c,
        f"investigation:{investigation}",
        [x.id for x in case.claims] + [x["id"] for x in case.captures],
    )
    await c.emit("input_read", claims=len(case.claims), candidates=len(every))
    c.run.prompt_hash = read.prompt_hash(PROMPT_VERSION, SYSTEM, SCHEMA)
    if c.run.model_requested == read.FIXED:
        c.run.inputs = {**c.run.inputs, "demo": read.note("contradiction report")}
    h = read.Handles()
    items, dropped, raw = [], {}, []
    cost = {"seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0}
    deferred = max(0, len(pending) - CAP)
    started = time.monotonic()
    for i in range(0, min(len(pending), CAP), PER_CALL):
        part = pending[i : min(i + PER_CALL, CAP)]
        if time.monotonic() - started > BUDGET_SECONDS:
            deferred += len(part)
            continue
        try:
            got = await asyncio.wait_for(
                read.ask(
                    c,
                    TOOL,
                    SYSTEM,
                    payload(part, h, case.docs),
                    SCHEMA,
                    fixed_answer,
                    lambda a, part=part: validate(a, part, h, case.claims)[1],
                ),
                BUDGET_SECONDS - (time.monotonic() - started),
            )
        except TimeoutError:
            deferred += len(part)
            dropped["timeout"] = dropped.get("timeout", 0) + 1
            continue
        raw.append(got["raw"])
        for k in cost:
            cost[k] += got[k] or 0
        found, why = validate(got["answer"], part, h, case.claims)
        for item in found:
            # Why the rules raised it, and any device beside it, in the report
            # beside the model's reading.
            ids = set(item["claims"])
            cand = next(
                x for x in part if ids <= {str(b.id) for s in x["sides"] for b in s}
            )
            if cand.get("raised"):
                item["raised"] = cand["raised"]
            if cand.get("device"):
                item["device"] = [
                    {"source": d["source"], "time": d["time"], "side": str(d["side"])}
                    for d in cand["device"]
                ]
            if fresh(item["claims"], seen):
                items.append(item)
                seen.add(frozenset(item["claims"]))
            else:
                why["duplicate"] += 1
        for k, n in why.items():
            dropped[k] = dropped.get(k, 0) + n
    gone = await brief.invisible(c.s, {"items": items})
    kept = [x for x in items if not set(x["claims"]) & gone]
    dropped["invisible"] = len(items) - len(kept)
    reason = None
    if not kept:
        reason = (
            "No two sources disagree in this case."
            if not every
            else "Every disagreement here was already reported."
            if not pending or dropped.get("duplicate")
            else "The remaining disagreements will be read in a later run."
            if deferred
            else "No cited explanation passed the checks."
        )
    return await read.store(
        c,
        {
            "version": PROMPT_VERSION,
            "investigation": investigation,
            "items": kept,
            "candidates": len(every),
            "deferred": deferred,
            "dropped": dropped,
            "outcome": {"proposed": len(kept), "reason": reason},
            "cost": cost,
            "raw": raw,
        },
    )


async def queue(db, investigation: str, user_id: int, model: str) -> Run:
    await db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"contradictions:{investigation}"},
    )
    if any(r.status in ("queued", "running") for r in await runs(db, investigation)):
        raise ValueError(
            "A colleague is already checking this case for contradictions."
        )
    agent = await read.agent_for(db, AGENT, user_id)
    run = Run(
        principal_id=agent.id,
        tool=TOOL,
        route=Config.AGENT_ROUTE,
        model_requested=model,
        inputs={"investigation": investigation, "for_user": user_id},
    )
    db.add(run)
    await db.flush()
    return run


async def show(db, user, investigation: str) -> dict:
    found = await runs(db, investigation)
    pending = next((r for r in found if r.status in ("queued", "running")), None)
    run, out = await read.report(db, found)
    result = {
        "items": [],
        "outcome": None,
        "pending": str(pending.id)
        if pending and pending.inputs["for_user"] == user.id
        else None,
        "busy": pending is not None,
    }
    if run is None:
        return result
    case = await read.case(db, investigation)
    by_id = {str(c.id): c for c in case.claims}
    files = await Context.load(
        db, blobs=[d["source"] for i in out["items"] for d in i.get("device", [])]
    )
    shown = set()
    for item in out["items"]:
        if any(i not in by_id for i in item["claims"]):
            continue
        claims = [by_id[i] for i in item["claims"]]
        hidden = sorted(
            {c.source for c in claims if c.sensitive and not granted(c.source)}
        )
        if hidden:
            result["items"].append(
                {
                    "locked": [
                        {"title": case.docs[x]["title"], "href": f"/evidence/{x}"}
                        for x in hidden
                    ],
                    "kind": None,
                    "why": None,
                    "question": None,
                    "claims": [],
                }
            )
            continue
        shown.update(item["claims"])
        # Two records under a proposed link: say which record each side is from.
        records = (
            {c.person for c in claims} if len({c.person for c in claims}) > 1 else set()
        )
        named = await people.names(db, [people.pid(p) for p in records], case.groups)

        def dated(sha):
            words = read.date_words(case.docs[sha].get("date"))
            return None if words and case.docs[sha]["title"].endswith(words) else words

        result["items"].append(
            item
            | {
                "locked": [],
                # Who the item is about, so a person's page can keep only theirs.
                "people": sorted({c.person.removeprefix("person:") for c in claims}),
                "claims": [
                    {
                        "person": c.person.removeprefix("person:")
                        if c.person in records
                        else None,
                        "name": named.get(people.pid(c.person))
                        if c.person in records
                        else None,
                        "field": read.label(c.prop),
                        "value": c.text,
                        "when": read.when(c.lo, c.hi),
                        "time": c.time.replace("-", " to ") if c.time else None,
                        "source": case.docs[c.source]["title"],
                        "source_date": dated(c.source),
                        "decided": c.tier == Tier.ACCEPTED,
                        "href": f"/evidence/{c.source}",
                    }
                    for c in claims
                ],
                "device": [
                    {
                        "title": files.doc(d["source"])["title"],
                        "href": f"/evidence/{d['source']}",
                        "time": d["time"],
                        "place": by_id[d["side"]].text if d["side"] in by_id else None,
                    }
                    for d in item.get("device", [])
                ],
            }
        )
    if shown:
        await brief.log_view(db, user, run, sorted(shown))
    result["outcome"] = out["outcome"]
    if out["items"] and not result["items"]:
        result["outcome"] = {
            "proposed": 0,
            "reason": "The claims behind this report are no longer available.",
        }
    return result
