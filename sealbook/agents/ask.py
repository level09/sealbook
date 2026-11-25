"""Ask a case a question now. The answer is a few sentences, each citing the claims
it rests on: something to read and check, never a fact. Nothing is written to the
ledger but the reading.

Two passes, as standing questions: a screen over every claim in the case keeps what
might help, then one call answers from what was kept. The model sees handles, never
ids, and a sentence that cites nothing, cites a claim it was not given, names a
handle or shows a sensitive value it does not cite is dropped."""

from __future__ import annotations

import re
import time

from sqlalchemy import select, text

from sealbook.agents import brief, read, standing, tasks
from sealbook.discovery.evidence import granted
from sealbook.ledger.contracts import Tier
from sealbook.ledger.models import Blob, Run
from sealbook.people import data as people
from sealbook.settings import Config

TOOL = "ask"
AGENT = "Case questions"
VERSION = "ask-1"
# The answer call reads at most this many screened claims, about 16k tokens; the rest
# are counted. The screen keeps about two in three (254 to 166 on a local case,
# 2026-11-25), so a cap of 40 cut the claims that answered.
ANSWER_CAP = 200
SENTENCES = 6
# The answer call thinks a little: with no reasoning the default fast model returned
# no sentences in 3 of 4 tries on a question its claims answered, with "low" in
# none of 4 (local synthetic case, 2026-11-25).
ANSWER_REASONING = "low"
KEEP = 5
# Standing questions' screen keeps nearly everything (223 of 254 claims on a local
# case, 2026-11-25), and a fast model handed that much answered nothing.
SCREEN = (
    "Screen these claims for a question an analyst asked about the case. Return "
    "the handles of claims that bear on it: about the people, places, dates or "
    "events it asks about, or claims an answer would cite. Leave out claims that "
    "bear on nothing in the question. When unsure, keep it. Use only the "
    "supplied handles. " + read.DATA_ONLY
)
ANSWER = (
    "Answer the analyst's question from these claims only. Each claim names whom "
    "it is about. Write two to six short plain sentences, and cite in claims "
    "every handle a sentence rests on. Say what the sources say, not what is "
    "true: 'Two sources place Dale Corrin at the Crown and Anchor on 14 March.' "
    "Where claims disagree, say so and cite both sides. Answer every part you "
    "can; for a part the claims do not answer, say so and cite the closest "
    "claims you checked. Return no sentences only when no claim bears on the "
    "question at all. Do not add names, dates or places that no cited claim "
    "gives. Never put handles in text. " + read.DATA_ONLY
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sentences"],
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "claims"],
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 400},
                    "claims": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


def validate(answer, claims, h, case_claims) -> tuple[list, dict]:
    dropped = {"uncited": 0, "unknown": 0, "text": 0, "leak": 0}
    allowed = {c.id for c in claims}
    out = []
    for item in (answer or {}).get("sentences", [])[:SENTENCES]:
        ids = [h.ref.get(x) for x in item["claims"]]
        if not ids:
            dropped["uncited"] += 1
            continue
        if any(i not in allowed for i in ids):
            dropped["unknown"] += 1
            continue
        said = item["text"].strip()
        if not said or brief.HANDLE.search(said) or re.search(r"[–—]", said):
            dropped["text"] += 1
            continue
        if read.leaks(said, set(ids), case_claims):
            dropped["leak"] += 1
            continue
        out.append({"text": said, "claims": sorted(str(i) for i in dict.fromkeys(ids))})
    return out, dropped


def fixed_answer(p: dict) -> dict:
    """The demo stand-in: one sentence per claim the standing question's demo rule
    matches, saying only that the source gives that field."""
    found = standing.fixed_answer(p)["matches"]
    fields = {c["h"]: c for c in p["claims"]}
    return {
        "sentences": [
            {
                "text": f"Demo answer: {fields[m['claims'][0]]['source']} gives "
                f"a {fields[m['claims'][0]]['field']}.",
                "claims": m["claims"],
            }
            for m in found
        ]
    }


async def ask(c) -> str:
    started = time.monotonic()
    investigation, question = c.run.inputs["investigation"], c.run.inputs["question"]
    agent = await read.agent_for(c.s, AGENT, c.run.inputs["for_user"])
    if agent.id != c.run.principal_id or c.run.route != Config.AGENT_ROUTE:
        raise ValueError(
            "the question must run for the named person on the agent route"
        )
    if not await tasks.can_read(c.s, c.run.inputs["for_user"], investigation):
        raise tasks.AccessLost(
            "The person who asked no longer has access to this case."
        )
    case = await read.case(c.s, investigation)
    c.run.prompt_hash = read.prompt_hash(
        VERSION, SCREEN, ANSWER, standing.SCREEN_SCHEMA, SCHEMA
    )
    if c.run.model_requested == read.FIXED:
        c.run.inputs = {**c.run.inputs, "demo": read.note("an answer")}
    h = read.Handles()
    raw, kept, dropped = [], [], {}
    cost = {"seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0}
    unread = 0

    async def call(name, system, claims, schema, fixed, check, reasoning=None):
        p = {
            "question": question,
            # Whom each claim is about: "seen at Branch 999" alone answers nothing.
            "claims": [
                read.claim_view(h, x, case.docs)
                | ({"about": names[x.person]} if names.get(x.person) else {})
                for x in claims
            ],
        }
        got = await read.ask(c, name, system, p, schema, fixed, check, reasoning)
        raw.append(got["raw"])
        for k in cost:
            cost[k] += got[k] or 0
        if not got["valid"]:
            dropped["invalid"] = dropped.get("invalid", 0) + 1
        return got["answer"]

    names = {p: case.name(p) for p in {x.person for x in case.claims if x.person}}
    groups = {}
    for claim in case.claims:
        groups.setdefault(claim.person or claim.subject, []).append(claim)
    parts = [
        g[i : i + standing.PER_CALL]
        for g in groups.values()
        for i in range(0, len(g), standing.PER_CALL)
    ]
    for part in parts:
        if time.monotonic() - started > standing.BUDGET_SECONDS / 2:
            unread += len(part)
            continue
        allowed = {h("c", x.id) for x in part}
        got = await call(
            "ask_screen",
            SCREEN,
            part,
            standing.SCREEN_SCHEMA,
            lambda p: {"maybe": [x["h"] for x in p["claims"]]},
            lambda a, ok=allowed: {"unknown": len(set(a["maybe"]) - ok)},
        )
        if got:
            kept += [x for x in part if h("c", x.id) in got["maybe"]]
    await read.log_read(
        c, f"investigation:{investigation}", [x.id for x in case.claims]
    )
    over = max(0, len(kept) - ANSWER_CAP)
    kept = kept[:ANSWER_CAP]
    sentences = []
    if kept:
        got = await call(
            "ask_answer",
            ANSWER,
            kept,
            SCHEMA,
            fixed_answer,
            lambda a: validate(a, kept, h, case.claims)[1],
            ANSWER_REASONING,
        )
        sentences, counts = validate(got, kept, h, case.claims)
        for k, n in counts.items():
            dropped[k] = dropped.get(k, 0) + n
    reason = None
    if not sentences:
        reason = (
            "The case has no claims yet."
            if not case.claims
            else "The model did not return a usable answer."
            if dropped.get("invalid")
            else "The case's claims do not answer this question."
        )
    return await read.store(
        c,
        {
            "version": VERSION,
            "investigation": investigation,
            "question": question,
            "sentences": sentences,
            "screened": len(case.claims) - unread,
            "kept": len(kept),
            "unread": unread,
            "over": over,
            "dropped": dropped,
            "outcome": {"proposed": len(sentences), "reason": reason},
            "cost": cost,
            "raw": raw,
        },
    )


async def runs(db, investigation: str, user_id: int) -> list[Run]:
    return list(
        await db.scalars(
            select(Run)
            .where(
                Run.tool == TOOL,
                Run.inputs["investigation"].astext == investigation,
                Run.inputs["for_user"].as_integer() == user_id,
            )
            .order_by(Run.created_at.desc(), Run.id)
            .limit(KEEP)
        )
    )


async def queue(db, investigation: str, user_id: int, question: str, model: str) -> Run:
    await db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"ask:{investigation}:{user_id}"},
    )
    if any(
        r.status in ("queued", "running")
        for r in await runs(db, investigation, user_id)
    ):
        raise ValueError("Your last question is still being answered.")
    agent = await read.agent_for(db, AGENT, user_id)
    run = Run(
        principal_id=agent.id,
        tool=TOOL,
        route=Config.AGENT_ROUTE,
        model_requested=model,
        inputs={
            "investigation": investigation,
            "for_user": user_id,
            "question": question,
        },
    )
    db.add(run)
    await db.flush()
    return run


async def show(db, user, investigation: str) -> dict:
    """The person's last questions in this case, newest first, each answer with its
    sentences and the claims they cite in words. A sentence resting on a sensitive
    source the reader has not revealed is locked; one whose claims are gone since is
    left out."""
    case = None
    items = []
    for run in await runs(db, investigation, user.id):
        item = {
            "id": str(run.id),
            "question": run.inputs["question"],
            "status": run.status,
            "at": run.created_at.isoformat(),
            "sentences": [],
            "outcome": None,
            "demo": False,
        }
        items.append(item)
        if run.status != "done" or not run.output_hash:
            continue
        out = read.read_output(await db.get(Blob, run.output_hash))
        case = case or await read.case(db, investigation)
        by_id = {str(x.id): x for x in case.claims}
        shown = []
        for s in out["sentences"]:
            claims = [by_id[i] for i in s["claims"] if i in by_id]
            if len(claims) != len(s["claims"]):
                continue
            hidden = sorted(
                {x.source for x in claims if x.sensitive and not granted(x.source)}
            )
            if hidden:
                item["sentences"].append(
                    {
                        "text": None,
                        "locked": [
                            {"href": f"/evidence/{x}", "title": case.docs[x]["title"]}
                            for x in hidden
                        ],
                        "claims": [],
                    }
                )
                continue
            shown += s["claims"]
            item["sentences"].append(
                {
                    "text": s["text"],
                    "locked": [],
                    "claims": [
                        {
                            "id": str(x.id),
                            "person": people.pid(x.subject) if x.person else None,
                            "name": case.name(x.person) if x.person else None,
                            "field": read.label(x.prop),
                            "value": x.text,
                            "when": read.when(x.lo, x.hi),
                            "source": case.docs[x.source]["title"],
                            "decided": x.tier == Tier.ACCEPTED,
                            "href": f"/evidence/{x.source}",
                        }
                        for x in claims
                    ],
                }
            )
        item["outcome"] = out["outcome"]
        item["demo"] = run.model_served == read.FIXED
        item["unread"], item["over"] = out.get("unread", 0), out.get("over", 0)
        if out["sentences"] and not item["sentences"]:
            item["outcome"] = {
                "proposed": 0,
                "reason": "The claims this answer cited are gone since.",
            }
        if shown:
            await brief.log_view(db, user, run, sorted(set(shown)))
    return {
        "items": items,
        "busy": any(i["status"] in ("queued", "running") for i in items),
    }
