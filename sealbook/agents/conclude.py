"""Candidate conclusions for a case, each argued against. Proposals only: a draft is
stored as the run's report, nothing is written to the ledger but the reading, and a
person writes and signs any conclusion themselves.

Rules build the candidates from what the case has accepted: a person whose records
accepted same-person links join across two sources or more, and each accepted
finding. Rules also find what does not fit: a record an open proposal would join to
that person whose identity details differ, and sources within the person that
disagree. The model only words the conclusion and says why each misfit matters; it
sees handles, never ids, and every sentence it writes must cite or is dropped."""

from __future__ import annotations

from sealbook.agents import brief, contradictions, linker, read, tasks
from sealbook.cases import conclusions
from sealbook.discovery.evidence import granted
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Blob, Run
from sealbook.people import data as people
from sealbook.settings import Config

TOOL = "conclude"
AGENT = "Conclusion drafter"
PROMPT_VERSION = "conclude-1"
CAP = 8
AGAINST_CAP = 6
IDENTITY = (Prop.BIRTH_DATE, Prop.PLACE_OF_BIRTH, *linker.PARENTS)
SYSTEM = (
    "You help an analyst state what a case shows. Each candidate is a conclusion "
    "the case's accepted links or findings support, with the claims that may not "
    "fit it. For each candidate you keep, write the conclusion as one plain "
    "sentence, cite in basis the accepted items it rests on, and for each group "
    "of claims that may not fit say in one sentence why it matters. You do not "
    "decide; a person reviews and signs any conclusion. Use handles only in basis "
    "and claims, never in text; an item whose text contains a handle such as c1 "
    "is discarded. Do not add facts or names absent from the candidate. "
    + read.DATA_ONLY
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
                "required": ["candidate", "basis", "conclusion", "against"],
                "properties": {
                    "candidate": {"type": "string"},
                    "basis": {"type": "array", "items": {"type": "string"}},
                    "conclusion": {"type": "string", "minLength": 1, "maxLength": 400},
                    "against": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["claims", "why"],
                            "properties": {
                                "claims": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "why": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 400,
                                },
                            },
                        },
                    },
                },
            },
        }
    },
}


def _names(names: list[str]) -> str:
    names = list(dict.fromkeys(n for n in names if n))
    if len(names) < 2:
        return names[0] if names else "These records"
    return ", ".join(names[:-1]) + " and " + names[-1]


async def candidates(db, case: read.Case) -> list[dict]:
    """Rule-built candidates: {"key", "kind", "what", "names", "basis": [citeable
    items], "claims": [the person's claims], "against": [[Claim, ...], ...]}."""
    found = await conclusions.citeable(db, case.investigation)
    persons = case.by_person()
    out = []
    for cluster, claims in persons.items():
        records = {c.subject for c in claims}
        if len(records) < 2 or len({c.source for c in claims}) < 2:
            continue
        basis = [
            link
            for link in found["links"]
            if link["same"] and all(p in records for p in link["people"])
        ]
        if not basis:
            continue
        recs = await conclusions.records(db, records)
        names = sorted({r["name"] for r in recs.values() if r.get("name")})
        out.append(
            {
                "kind": "one person",
                "what": f"{_names(names)} are one person",
                "names": names,
                "basis": basis,
                "claims": claims,
                "against": against(case, cluster, claims),
            }
        )
    for f in found["findings"]:
        out.append(
            {
                "kind": "finding",
                "what": f["text"],
                "names": [],
                "basis": [f],
                "claims": [],
                "against": [],
            }
        )
    for i, cand in enumerate(out[:CAP]):
        cand["key"] = f"k{i + 1}"
    return out[:CAP]


def against(case: read.Case, cluster: str, claims: list) -> list[list]:
    """Groups of claims that do not fit `cluster` being one person: another record
    an open proposal would join, whose identity details differ from the person's;
    then two sources about the person that disagree."""
    out = []
    mine = {}
    for c in claims:
        mine.setdefault(c.prop, []).append(c)
    persons = case.by_person()
    for a, b in case.links:
        if cluster not in (a, b):
            continue
        other = b if a == cluster else a
        for c in persons.get(other, []):
            if c.prop not in IDENTITY or c.prop not in mine:
                continue
            # A value the person's records do not all give: a year-only entry that
            # agrees does not hide a full date that differs.
            differ = [
                m for m in mine[c.prop] if not read.same_value(c.prop, c.text, m.text)
            ]
            if not differ:
                continue
            ref = sorted(differ, key=lambda m: (m.tier != Tier.ACCEPTED, m.id))
            out.append([c, ref[0]])
    timeless = set(brief.SINGLE) | set(linker.CONTRADICTS)
    for cand in contradictions.forks(claims, case.places, timeless):
        side = [s[0] for s in cand["sides"]]
        if len({c.source for c in side}) >= 2:
            out.append(side)
    seen, kept = set(), []
    for group in out:
        key = frozenset(c.id for c in group)
        if key not in seen:
            seen.add(key)
            kept.append(group)
    return kept[:AGAINST_CAP]


def payload(cands: list[dict], h: read.Handles, docs: dict) -> dict:
    return {
        "candidates": [
            {
                "candidate": cand["key"],
                "kind": cand["kind"],
                "what": cand["what"],
                "basis": [
                    {"h": h("c", b["id"]), "item": b["text"]} for b in cand["basis"]
                ],
                "against": [
                    {
                        "found": "another record an open proposal would join to "
                        "this person, with a different value"
                        if len({c.person for c in group}) > 1
                        else "two sources about this person that disagree",
                        "claims": [read.claim_view(h, c, docs) for c in group],
                    }
                    for group in cand["against"]
                ],
            }
            for cand in cands
        ]
    }


def rule_why(group: list) -> str:
    field = read.label(group[0].prop).lower()
    if len({c.person for c in group}) > 1:
        return (
            f"A record proposed as this person gives a different {field}; "
            "it may be someone else."
        )
    return f"The sources about this person disagree on the {field}."


def fixed_answer(p: dict) -> dict:
    """The rule candidate as the conclusion; what does not fit is left to the rules'
    own words, which validate() adds for every group an answer leaves out."""
    return {
        "items": [
            {
                "candidate": cand["candidate"],
                "basis": [b["h"] for b in cand["basis"]],
                "conclusion": cand["what"] + ".",
                "against": [],
            }
            for cand in p["candidates"]
        ]
    }


def validate(answer, cands, h, claims) -> tuple[list, dict]:
    """Items whose conclusion cites only its candidate's accepted items and whose
    reasons cite only the claims rules found against it. A sentence that cites
    nothing, names a handle or leaks a masked value is dropped and counted."""
    dropped = dict.fromkeys(
        ("unknown", "empty", "handle", "leak", "duplicate", "against"), 0
    )
    by_key = {c["key"]: c for c in cands}
    items, seen = [], set()
    for item in (answer or {}).get("items", []):
        cand = by_key.get(item.get("candidate"))
        if cand is None:
            dropped["unknown"] += 1
            continue
        allowed = {h.ref.get(r) for r in item["basis"]} if item["basis"] else set()
        ids = {b["id"] for b in cand["basis"]}
        if (
            not item["basis"]
            or any(r not in h.ref for r in item["basis"])
            or not (allowed <= ids)
        ):
            dropped["unknown"] += 1
            continue
        about = {c.id for c in cand["claims"]}
        sentence = item["conclusion"].strip()
        if not sentence:
            dropped["empty"] += 1
            continue
        if brief.HANDLE.search(sentence):
            dropped["handle"] += 1
            continue
        if read.leaks(sentence, about, claims):
            dropped["leak"] += 1
            continue
        if cand["key"] in seen:
            dropped["duplicate"] += 1
            continue
        groups = [frozenset(c.id for c in g) for g in cand["against"]]
        kept = []
        for a in item["against"]:
            refs = a["claims"]
            why = a["why"].strip()
            got = {h.ref.get(r) for r in refs}
            if (
                not refs
                or any(r not in h.ref or not r.startswith("c") for r in refs)
                or not any(got <= g for g in groups)
                or not why
                or brief.HANDLE.search(why)
                or read.leaks(why, got, claims)
            ):
                dropped["against"] += 1
                continue
            kept.append({"claims": sorted(str(i) for i in got), "why": why})
        # Arguing against is not the model's choice: a group the rules found and the
        # answer left out is kept with the rules' own words.
        covered = [frozenset(a["claims"]) for a in kept]
        for g in cand["against"]:
            ids_ = frozenset(str(c.id) for c in g)
            if not any(ids_ <= c for c in covered):
                dropped["against_kept_by_rules"] = (
                    dropped.get("against_kept_by_rules", 0) + 1
                )
                kept.append({"claims": sorted(ids_), "why": rule_why(g), "by": "rules"})
        seen.add(cand["key"])
        items.append(
            {
                "candidate": cand["key"],
                "kind": cand["kind"],
                "conclusion": sentence,
                "basis": sorted(str(i) for i in allowed),
                "against": kept,
            }
        )
    return items, dropped


async def runs(db, investigation: str) -> list[Run]:
    return await read.runs_of(db, TOOL, investigation=investigation)


async def conclude(c) -> str:
    await tasks.check_access(c.s, c.run)
    investigation = c.run.inputs["investigation"]
    agent = await read.agent_for(c.s, AGENT, c.run.inputs["for_user"])
    if agent.id != c.run.principal_id or c.run.route != Config.AGENT_ROUTE:
        raise ValueError("the drafter must act for the named person on the agent route")
    case = await read.case(c.s, investigation)
    cands = await candidates(c.s, case)
    await read.log_read(
        c, f"investigation:{investigation}", [x.id for x in case.claims]
    )
    await c.emit("input_read", claims=len(case.claims), candidates=len(cands))
    c.run.prompt_hash = read.prompt_hash(PROMPT_VERSION, SYSTEM, SCHEMA)
    if c.run.model_requested == read.FIXED:
        c.run.inputs = {**c.run.inputs, "demo": read.note("conclusion drafts")}
    h = read.Handles()
    items, dropped, raw = [], {}, []
    cost = {"seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0}
    if cands:
        got = await read.ask(
            c,
            TOOL,
            SYSTEM,
            payload(cands, h, case.docs),
            SCHEMA,
            fixed_answer,
            lambda a: validate(a, cands, h, case.claims)[1],
        )
        raw.append(got["raw"])
        for k in cost:
            cost[k] += got[k] or 0
        items, dropped = validate(got["answer"], cands, h, case.claims)
    reason = None
    if not items:
        reason = (
            "The case has no accepted links or findings to conclude from yet."
            if not cands
            else "No drafted conclusion passed the checks."
        )
    return await read.store(
        c,
        {
            "version": PROMPT_VERSION,
            "investigation": investigation,
            "items": items,
            "candidates": len(cands),
            "dropped": dropped,
            "outcome": {"proposed": len(items), "reason": reason},
            "cost": cost,
            "raw": raw,
        },
    )


async def queue(db, investigation: str, user_id: int, model: str) -> Run:
    return await read.new_run(
        db,
        TOOL,
        AGENT,
        user_id,
        model,
        {"investigation": investigation},
        busy="Conclusions for this case are already being drafted.",
    )


def _value(c) -> str:
    """A birth date in words ("2 March 1974"); any other value as read."""
    if c.prop != Prop.BIRTH_DATE:
        return c.text
    try:
        return read.date_words(c.text)
    except (ValueError, IndexError):
        return c.text


async def show(db, user, investigation: str) -> dict:
    """The newest drafts, each with what it rests on and the claims against it in
    words. A draft whose accepted items no longer stand, or whose claims are gone,
    is left out; one resting on an unrevealed sensitive source is locked."""
    found = await runs(db, investigation)
    pending = next((r for r in found if r.status in read.ACTIVE), None)
    result = {
        "items": [],
        "outcome": None,
        "pending": str(pending.id)
        if pending and pending.inputs["for_user"] == user.id
        else None,
        "busy": pending is not None,
        "model": None,
    }
    done = [r for r in found if r.status == "done" and r.output_hash]
    if not done:
        return result
    run = done[0]
    out = read.read_output(await db.get(Blob, run.output_hash))
    result["model"] = {
        "served": run.model_served,
        "demo": run.model_served == read.FIXED,
        "at": run.finished_at.isoformat() if run.finished_at else None,
    }
    case = await read.case(db, investigation)
    by_id = {str(c.id): c for c in case.claims}
    items = conclusions._flat(await conclusions.citeable(db, investigation))
    shown = set()
    for item in out["items"]:
        if any(b not in items for b in item["basis"]):
            continue
        against_ = []
        for a in item["against"]:
            claims = [by_id[i] for i in a["claims"] if i in by_id]
            if len(claims) != len(a["claims"]):
                continue
            hidden = sorted(
                {c.source for c in claims if c.sensitive and not granted(c.source)}
            )
            if hidden:
                against_.append(
                    {
                        "why": None,
                        "locked": [f"/evidence/{x}" for x in hidden],
                        "claims": [],
                    }
                )
                continue
            shown.update(a["claims"])
            named = await conclusions.records(db, {c.subject for c in claims})
            against_.append(
                {
                    "why": a["why"],
                    "by": a.get("by", "model"),
                    "locked": [],
                    "claims": [
                        {
                            "id": str(c.id),
                            "record": (named.get(c.subject) or {}).get("name"),
                            "person": people.pid(c.subject),
                            "field": read.label(c.prop),
                            "value": _value(c),
                            "when": read.when(c.lo, c.hi),
                            "source": case.docs[c.source]["title"],
                            "decided": c.tier == Tier.ACCEPTED,
                            "href": f"/evidence/{c.source}",
                        }
                        for c in claims
                    ],
                }
            )
        result["items"].append(
            {
                "kind": item["kind"],
                "conclusion": item["conclusion"],
                "basis": [items[b] for b in item["basis"]],
                "against": against_,
            }
        )
    if shown:
        await brief.log_view(db, user, run, sorted(shown))
    result["outcome"] = out["outcome"]
    if out["items"] and not result["items"]:
        result["outcome"] = {
            "proposed": 0,
            "reason": "What these drafts rested on no longer stands.",
        }
    return result
