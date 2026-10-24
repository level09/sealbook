"""Cited disagreements and draft questions. Only the reading enters the ledger."""

from __future__ import annotations

import asyncio
import time
from itertools import combinations

from sqlalchemy import select, text

from stk.agents import brief, linker, read
from stk.discovery.evidence import granted
from stk.ledger.contracts import Tier
from stk.ledger.models import Blob, Run
from stk.ledger.queue import LEASE
from stk.monitor.data import LOCATED, _apart, map_layer
from stk.people import data as people

TOOL = "contradictions"
AGENT = "Contradiction hunter"
PROMPT_VERSION = "contradictions-1"
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
    "of one candidate. Use handles only in claims, never in why or question. "
    "Do not add facts or names absent from the cited claims."
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


def candidates(case) -> list[dict]:
    out = []
    timeless = set(brief.SINGLE) | set(linker.CONTRADICTS)
    for claims in case.by_person().values():
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
                "subject": c.person,
                "subject_label": "track",
                "when": read.when(c.lo, c.hi),
                "actor": {"agent": True},
            }
            for c in claims
            if c.prop in timeless | LOCATED
        ]
        facts = {}
        for row in rows:
            facts.setdefault(row["prop"], []).append(row)
        layer = map_layer(rows, case.places)
        points = {p["id"]: p for p in layer["points"]}
        steps = next(iter(layer["tracks"].values()), {}).get("steps", [])
        forks = people.disagreements(
            facts,
            steps,
            case.places,
            {},
            timeless=timeless,
            same_value=read.same_value,
        )
        by_id = {str(c.id): c for c in claims}
        for fork in forks:
            sides = [[by_id[s["id"]]] for s in fork["sides"]]
            if len({c.source for side in sides for c in side}) < 2:
                continue
            cand = {"sides": sides}
            if fork["kind"] == "position":
                cand["_pairs"] = [
                    {a.id, b.id}
                    for a, b in combinations([s[0] for s in sides], 2)
                    if _apart(points[str(a.id)], points[str(b.id)])
                ]
            out.append(cand)
    out.sort(
        key=lambda x: (
            -sum(c.tier == Tier.ACCEPTED for s in x["sides"] for c in s),
            -len({c.source for s in x["sides"] for c in s}),
            sorted(str(c.id) for s in x["sides"] for c in s),
        )
    )
    return out


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
            for cand in cands
        ]
    }


def fixed_answer(p: dict) -> dict:
    return {
        "items": [
            {
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
        if len({by_id[i].source for i in ids}) < 2 or not any(
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
    investigation = c.run.inputs["investigation"]
    agent = await read.agent_for(c.s, AGENT, c.run.inputs["for_user"])
    if agent.id != c.run.principal_id or c.run.route != "local":
        raise ValueError(
            "contradictions must act for the named person on the local route"
        )
    case = await read.case(c.s, investigation)
    every = candidates(case)
    seen = await reported(c.s, investigation)
    pending = [x for x in every if fresh([b.id for s in x["sides"] for b in s], seen)]
    await read.log_read(
        c, f"investigation:{investigation}", [x.id for x in case.claims]
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
                    c, TOOL, SYSTEM, payload(part, h, case.docs), SCHEMA, fixed_answer
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
        route="local",
        model_requested=model,
        inputs={"investigation": investigation, "for_user": user_id},
    )
    db.add(run)
    await db.flush()
    return run


async def show(db, user, investigation: str) -> dict:
    found = await runs(db, investigation)
    pending = next((r for r in found if r.status in ("queued", "running")), None)
    run = next((r for r in found if r.status == "done" and r.output_hash), None)
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
    out = read.read_output(await db.get(Blob, run.output_hash))
    case = await read.case(db, investigation)
    by_id = {str(c.id): c for c in case.claims}
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
        result["items"].append(
            item
            | {
                "locked": [],
                "claims": [
                    {
                        "field": c.prop.replace("_", " "),
                        "value": c.text,
                        "when": read.when(c.lo, c.hi),
                        "source": case.docs[c.source]["title"],
                        "source_date": case.docs[c.source].get("date"),
                        "decided": c.tier == Tier.ACCEPTED,
                        "href": f"/evidence/{c.source}",
                    }
                    for c in claims
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
