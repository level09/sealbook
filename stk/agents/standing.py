"""A question watches a case. Matches are cited reports, never ledger facts."""

import re
import time
import uuid

from sqlalchemy import select

from stk.agents import brief, read, tasks
from stk.agents.models import AgentTask, AgentWake
from stk.agents.read import Handles, claim_view
from stk.discovery.evidence import granted
from stk.ledger.models import Blob, Run, Statement
from stk.ledger.queue import LEASE
from stk.people import data as people
from stk.settings import Config

TOOL = "standing"
PER_CALL = 12
BUDGET_SECONDS = LEASE.total_seconds() - max(90, Config.ROUTE_LOCAL_TIMEOUT + 15)
VERSION = "standing-2"
SCREEN = (
    "Screen these claims for a standing question. Prefer false positives. Return "
    "every claim handle that might help answer it, including context. This is only "
    "a screen, not a finding. Use only the supplied handles."
)
JUDGE = (
    "Read these claims against the standing question. Propose only matches the "
    "claims support. A match meets every condition in the question: place, "
    "time and person or role. A date in the same year does not meet 'after' "
    "that year, a record at another place does not match, and leaving a place "
    "is not being held there. When unsure, leave it out; the screen already "
    "kept everything that might help. Each match "
    "must cite at least one supplied claim handle in claims. Write why as one "
    "short plain sentence using only cited values. Do not put handles in why. "
    "A match is a proposal for human review, never an accepted fact."
)
SCREEN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["maybe"],
    "properties": {"maybe": {"type": "array", "items": {"type": "string"}}},
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["matches"],
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claims", "why"],
                "properties": {
                    "claims": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string"},
                },
            },
        }
    },
}


def match_key(ids):
    return frozenset(str(i) for i in ids)


def validate(answer, claims, h, case_claims, seen):
    dropped = {"uncited": 0, "unknown": 0, "leak": 0, "repeat": 0, "text": 0}
    out = []
    allowed = {c.id for c in claims}
    for item in (answer or {}).get("matches", []):
        ids = [h.ref.get(handle) for handle in item["claims"]]
        if not ids:
            dropped["uncited"] += 1
            continue
        if any(i not in allowed for i in ids):
            dropped["unknown"] += 1
            continue
        key = match_key(ids)
        if key in seen:
            dropped["repeat"] += 1
            continue
        why = item["why"].strip()[:400]
        if not why or brief.HANDLE.search(why) or re.search(r"[\u2013\u2014]", why):
            dropped["text"] += 1
            continue
        if read.leaks(why, set(ids), case_claims):
            dropped["leak"] += 1
            continue
        seen.add(key)
        out.append({"why": why, "claims": sorted(key)})
    return out, dropped


def fixed_answer(payload):
    """A demo stand-in: a claim matches when its value contains a name or number
    from the question, and why says only that."""
    first, *rest = re.findall(r"\w+", payload["question"])
    keys = {w.casefold(): w for w in rest if w[0].isupper() or any(map(str.isdigit, w))}
    if any(map(str.isdigit, first)):
        keys[first.casefold()] = first
    matches = []
    for c in payload["claims"]:
        value = set(re.findall(r"\w+", c["value"].casefold()))
        shared = [keys[k] for k in sorted(keys) if k in value]
        if shared:
            matches.append(
                {
                    "claims": [c["h"]],
                    "why": "Demo match: the value and the question both name "
                    f"{' and '.join(shared)}.",
                }
            )
    return {"matches": matches}


async def runs(db, task_id):
    return list(
        await db.scalars(
            select(Run)
            .where(Run.tool == TOOL, Run.inputs["task"].astext == str(task_id))
            .order_by(Run.created_at.desc(), Run.id)
        )
    )


async def standing(c):
    started = time.monotonic()
    await tasks.check_access(c.s, c.run)
    task = await c.s.get(AgentTask, uuid.UUID(c.run.inputs["task"]))
    case = await read.case(c.s, task.investigation)
    wakes = list(
        await c.s.scalars(
            select(AgentWake).where(
                AgentWake.id.in_([uuid.UUID(i) for i in c.run.inputs.get("wakes", [])]),
                AgentWake.task_id == task.id,
                AgentWake.run_id == c.run.id,
            )
        )
    )
    selected = case.claims
    if wakes and not any(w.reason == "manual" for w in wakes):
        run_ids = [uuid.UUID(w.payload["run"]) for w in wakes if "run" in w.payload]
        ids = set(
            await c.s.scalars(select(Statement.id).where(Statement.run_id.in_(run_ids)))
        )
        ids.update(uuid.UUID(i) for w in wakes for i in w.payload.get("claims", []))
        selected = [claim for claim in case.claims if claim.id in ids]
    seen = set()
    for previous in await runs(c.s, task.id):
        if previous.status == "done":
            old = read.read_output(await c.s.get(Blob, previous.output_hash))
            seen.update(match_key(m["claims"]) for m in old["matches"])
    groups = {}
    for claim in selected:
        groups.setdefault(claim.person or claim.subject, []).append(claim)
    parts = [
        group[i : i + PER_CALL]
        for group in groups.values()
        for i in range(0, len(group), PER_CALL)
    ]
    h = Handles()
    c.run.prompt_hash = read.prompt_hash(VERSION, SCREEN, JUDGE, SCREEN_SCHEMA, SCHEMA)
    if c.run.model_requested == read.FIXED:
        c.run.inputs = {**c.run.inputs, "demo": read.note("standing question matches")}
    raw, matches, deferred = [], [], []
    screened = maybe = 0
    dropped = {}
    cost = {"seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0}

    async def ask(name, system, claims, schema, fixed):
        p = {
            "question": task.spec["question"],
            "claims": [claim_view(h, claim, case.docs) for claim in claims],
        }
        got = await read.ask(c, name, system, p, schema, fixed)
        raw.append(got["raw"])
        for key in cost:
            cost[key] += got[key] or 0
        if not got["valid"]:
            dropped["invalid"] = dropped.get("invalid", 0) + 1
        return got["answer"]

    for part in parts:
        if time.monotonic() - started > BUDGET_SECONDS:
            deferred.extend(part)
            continue
        answer = await ask(
            "standing_screen",
            SCREEN,
            part,
            SCREEN_SCHEMA,
            lambda p: {"maybe": [claim["h"] for claim in p["claims"]]},
        )
        screened += len(part)
        if answer is None:
            continue
        allowed = {h("c", claim.id) for claim in part}
        unknown = set(answer["maybe"]) - allowed
        dropped["unknown"] = dropped.get("unknown", 0) + len(unknown)
        survivors = [claim for claim in part if h("c", claim.id) in answer["maybe"]]
        maybe += len(survivors)
        if not survivors:
            continue
        if time.monotonic() - started > BUDGET_SECONDS:
            deferred.extend(survivors)
            continue
        answer = await ask("standing_matches", JUDGE, survivors, SCHEMA, fixed_answer)
        if answer is None:
            continue
        found, counts = validate(answer, survivors, h, case.claims, seen)
        matches.extend(found)
        for key, count in counts.items():
            dropped[key] = dropped.get(key, 0) + count
    await read.log_read(
        c,
        f"investigation:{task.investigation}",
        sorted(claim.id for claim in case.claims),
    )
    if deferred:
        await tasks.wake(
            c.s,
            task.id,
            f"deferred:{c.run.id}",
            "deferred",
            {"claims": sorted({str(claim.id) for claim in deferred})},
        )
    reason = (
        None
        if matches
        else (
            "Some claims still need checking."
            if deferred
            else "The local model did not return a usable answer."
            if dropped.get("invalid")
            else "No new claims matched this question."
        )
    )
    return await read.store(
        c,
        {
            "version": VERSION,
            "task": str(task.id),
            "question": task.spec["question"],
            "matches": matches,
            "screened": screened,
            "maybe": maybe,
            "deferred": len(deferred),
            "dropped": dropped,
            "outcome": {"proposed": len(matches), "reason": reason},
            "cost": cost,
            "raw": raw,
        },
    )


async def show(db, user, task_id):
    task = await db.get(AgentTask, task_id)
    if task is None or not await tasks.can_read(db, user.id, task.investigation):
        raise LookupError("This question is not available.")
    found = await runs(db, task.id)
    run = next((r for r in found if r.status == "done"), None)
    result = {
        "question": task.spec["question"],
        "matches": [],
        "outcome": None,
        "history": [
            {
                "at": (r.finished_at or r.created_at).isoformat(),
                "state": "checking"
                if r.status in tasks.ACTIVE
                else "checked"
                if r.status == "done"
                else "failed",
                "reason": tasks.failure_reason(r.error)
                if r.status == "failed"
                else None,
            }
            for r in found
        ],
    }
    if run is None:
        return result
    out = read.read_output(await db.get(Blob, run.output_hash))
    gone = await brief.invisible(db, {"matches": out["matches"]})
    case = await read.case(db, task.investigation)
    by_id = {str(c.id): c for c in case.claims}
    groups, locked, shown = {}, {}, set()
    for match in out["matches"]:
        if set(match["claims"]) & gone or not set(match["claims"]) <= by_id.keys():
            continue
        claims = [by_id[i] for i in match["claims"]]
        hidden = sorted(
            {c.source for c in claims if c.sensitive and not granted(c.source)}
        )
        if hidden:
            # One line per sensitive source, never under a person: the person a
            # masked match is about is part of what it would reveal.
            locked[hidden[0]] = locked.get(hidden[0], 0) + 1
            continue
        shown.update(match["claims"])
        who = claims[0].person
        groups.setdefault(who, []).append(
            {
                "why": match["why"],
                "claims": [
                    {
                        "field": read.label(c.prop),
                        "value": c.text,
                        "when": read.when(c.lo, c.hi),
                        "title": case.docs[c.source]["title"],
                        "href": f"/evidence/{c.source}?statement={c.id}",
                    }
                    for c in claims
                ],
            }
        )
    named = await people.names(db, [people.pid(p) for p in groups if p], case.groups)
    result["matches"] = [
        {
            "locked": False,
            "person": p and p.removeprefix("person:"),
            "name": p and named.get(people.pid(p)),
            "matches": found,
        }
        for p, found in groups.items()
    ] + [
        {
            "locked": True,
            "count": n,
            "title": case.docs[sha]["title"],
            "href": f"/evidence/{sha}",
        }
        for sha, n in locked.items()
    ]
    if shown:
        await brief.log_view(db, user, run, sorted(shown))
    result["outcome"] = out["outcome"]
    result["deferred"] = out["deferred"]
    result["fixed"] = run.model_requested == read.FIXED
    return result
