"""The place finder: an approximate place for a name the gazetteer does not have,
near a place the same passage names, with a radius and the quoted words. It only
suggests; a person adds the place and confirms it. Only the reading enters the
ledger."""

from __future__ import annotations

import asyncio
import time
import uuid

from sqlalchemy import select, text

from sealbook.agents import brief, read, tasks
from sealbook.discovery.evidence import granted
from sealbook.ledger.models import Blob, Run
from sealbook.ledger.queue import LEASE
from sealbook.monitor.data import LOCATED, Context
from sealbook.places import normalise, resolve, suggest
from sealbook.places.models import Place, PlaceName
from sealbook.settings import Config

TOOL = "approximate"
AGENT = "Place finder"
PROMPT_VERSION = "approximate-1"
CAP = 24
PER_CALL = 4
PASSAGES = 3  # claims read per name
PLACES = 6  # known places offered per name
RADIUS_KM = (0.1, 50.0)
MIN_QUOTE = 8
BUDGET_SECONDS = LEASE.total_seconds() - 90
SYSTEM = (
    "You help an analyst place names that the gazetteer does not have. Each "
    "candidate gives a name handle, the spelling found in the sources, passages "
    "around it with claim handles, and known places those passages name, with "
    "place handles. Answer for a candidate only when one passage says where the "
    "named place is relative to one of the known places. Give the name handle, the "
    "claim handle of that passage, the place handle, a radius in kilometres that "
    "covers where the named place could be, and quote the exact words of that "
    "passage that say so, copied character for character and naming the known "
    "place. Leave out a candidate the passages do not locate. Do not add places or "
    "facts absent from the passages."
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
                "required": ["name", "claims", "place", "radius_km", "quote"],
                "properties": {
                    "name": {"type": "string"},
                    "claims": {"type": "array", "items": {"type": "string"}},
                    "place": {"type": "string"},
                    "radius_km": {"type": "number"},
                    "quote": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
        }
    },
}


async def candidates(db, case) -> list[dict]:
    """Unresolved spellings in the case with no gazetteer candidate, each with the
    passages around its claims and the known places those passages name."""
    by: dict[str, list] = {}
    for c in case.claims:
        if c.prop in LOCATED and c.text and case.places.get(c.id) is None:
            by.setdefault(normalise(c.text), []).append(c)
    found = await resolve.candidates_many(db, by)
    by = {n: cs for n, cs in by.items() if not found.get(n)}
    texts = await suggest.passages(db, [c.id for cs in by.values() for c in cs])
    out = []
    for norm, claims in sorted(by.items(), key=lambda x: (-len(x[1]), x[0])):
        read_ = [c for c in claims if c.id in texts][:PASSAGES]
        places, seen = [], set()
        for c in read_:
            for name, p in await suggest.named_in(db, texts[c.id], skip=norm):
                if p.id not in seen:
                    seen.add(p.id)
                    places.append((name, p))
        if read_ and places:
            out.append(
                {
                    "norm": norm,
                    "name": claims[0].text,
                    "claims": read_,
                    "passages": {c.id: texts[c.id] for c in read_},
                    "places": places[:PLACES],
                }
            )
    return out


def payload(cands, h, docs) -> dict:
    return {
        "candidates": [
            {
                "name": h("n", cand["norm"]),
                "spelling": cand["name"],
                "passages": [
                    {
                        "h": h("c", c.id),
                        "source": docs[c.source]["title"],
                        "text": cand["passages"][c.id],
                    }
                    for c in cand["claims"]
                ],
                "places": [
                    {
                        "h": h("p", p.id),
                        "named": name,
                        "name": read.place_name(p),
                        "kind": p.kind,
                    }
                    for name, p in cand["places"]
                ],
            }
            for cand in cands
        ]
    }


def fixed_answer(p: dict) -> dict:
    """The demo answer: the first known place a passage names, 1 km, quoting the
    words around it."""
    items = []
    for cand in p["candidates"]:
        for place in cand["places"]:
            passage = next(
                (x for x in cand["passages"] if place["named"] in x["text"]), None
            )
            if passage:
                items.append(
                    {
                        "name": cand["name"],
                        "claims": [passage["h"]],
                        "place": place["h"],
                        "radius_km": 1,
                        "quote": suggest.quote(passage["text"], place["named"]),
                    }
                )
                break
    return {"items": items}


def validate(answer, cands, h, norms_of) -> tuple[list, dict]:
    """Keep a suggestion only when it names a candidate, cites that candidate's
    claims, picks a place offered for it, gives a sane radius, and quotes words
    that occur in a cited passage and name the chosen place. `norms_of` maps a
    place id to the normalised names it is known by."""
    dropped = dict.fromkeys(
        ("unknown", "place", "radius", "quote", "unsupported", "duplicate"), 0
    )
    by_norm = {c["norm"]: c for c in cands}
    items, seen = [], set()
    for item in (answer or {}).get("items", []):
        cand = by_norm.get(h.ref.get(item["name"])) if item["name"][:1] == "n" else None
        refs = item["claims"]
        mine = {c.id: c for c in cand["claims"]} if cand else {}
        if (
            cand is None
            or not refs
            or any(not r.startswith("c") or h.ref.get(r) not in mine for r in refs)
        ):
            dropped["unknown"] += 1
            continue
        offered = {p.id: p for _, p in cand["places"]}
        place = h.ref.get(item["place"]) if item["place"][:1] == "p" else None
        if place not in offered:
            dropped["place"] += 1
            continue
        radius = item["radius_km"]
        if not RADIUS_KM[0] <= radius <= RADIUS_KM[1]:
            dropped["radius"] += 1
            continue
        words = item["quote"].strip()
        folded = normalise(words)
        cited = [h.ref[r] for r in refs]
        if len(folded) < MIN_QUOTE or not any(
            folded in normalise(cand["passages"][i]) for i in cited
        ):
            dropped["quote"] += 1
            continue
        if not any(n in folded for n in norms_of.get(place, ())):
            dropped["unsupported"] += 1
            continue
        if cand["norm"] in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(cand["norm"])
        items.append(
            {
                "norm": cand["norm"],
                "name": cand["name"],
                "claims": sorted(str(i) for i in cited),
                "source": mine[cited[0]].source,
                "place": str(place),
                "radius_km": round(float(radius), 1),
                "quote": words,
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


async def outputs(db, investigation: str) -> list[tuple[Run, dict]]:
    found = []
    for run in await runs(db, investigation):
        if run.status == "done" and run.output_hash:
            found.append((run, read.read_output(await db.get(Blob, run.output_hash))))
    return found


async def approximate(c) -> str:
    await tasks.check_access(c.s, c.run)
    investigation = c.run.inputs["investigation"]
    agent = await read.agent_for(c.s, AGENT, c.run.inputs["for_user"])
    if agent.id != c.run.principal_id or c.run.route != Config.AGENT_ROUTE:
        raise ValueError("the place finder must act for the named person")
    case = await read.case(c.s, investigation)
    every = await candidates(c.s, case)
    seen = {
        i["norm"] for _, out in await outputs(c.s, investigation) for i in out["items"]
    }
    pending = [x for x in every if x["norm"] not in seen]
    await read.log_read(
        c,
        f"investigation:{investigation}",
        [x.id for cand in every for x in cand["claims"]],
    )
    await c.emit("input_read", claims=len(case.claims), candidates=len(every))
    c.run.prompt_hash = read.prompt_hash(PROMPT_VERSION, SYSTEM, SCHEMA)
    if c.run.model_requested == read.FIXED:
        c.run.inputs = {**c.run.inputs, "demo": read.note("approximate places")}
    places = {p.id for cand in pending for _, p in cand["places"]}
    norms_of: dict[uuid.UUID, set] = {}
    for pid, norm in (
        await c.s.execute(
            select(PlaceName.place_id, PlaceName.norm).where(
                PlaceName.place_id.in_(list(places))
            )
        )
    ).all():
        norms_of.setdefault(pid, set()).add(norm)
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
        found, why = validate(got["answer"], part, h, norms_of)
        items += found
        for k, n in why.items():
            dropped[k] = dropped.get(k, 0) + n
    gone = await brief.invisible(c.s, {"items": items})
    kept = [x for x in items if not set(x["claims"]) & gone]
    dropped["invisible"] = len(items) - len(kept)
    reason = None
    if not kept:
        reason = (
            "No unplaced name here has a passage that names a known place."
            if not every
            else "Every such name already has a suggestion."
            if not pending
            else "The remaining names will be read in a later run."
            if deferred
            else "No quoted suggestion passed the checks."
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
        {"k": f"approximate:{investigation}"},
    )
    if any(r.status in ("queued", "running") for r in await runs(db, investigation)):
        raise ValueError("The place finder is already reading this case.")
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


async def suggestions(db, user, items, investigation: str | None) -> dict[str, dict]:
    """The newest suggestion per spelling among these unresolved names, from cases
    this person may open, as the Places page shows it. A suggestion resting on a
    sensitive source this session has not revealed is locked; one whose claim is
    gone or whose place was removed is left out. Showing one is logged."""
    from sealbook.cases.models import Case
    from sealbook.entities.data import _place_rows

    wanted = {u["norm"]: u for u in items}
    slugs = (
        [investigation]
        if investigation
        else list(await db.scalars(select(Case.slug).order_by(Case.slug)))
    )
    picked: dict[str, tuple[Run, dict]] = {}
    for slug in slugs:
        if not await tasks.can_read(db, user.id, slug):
            continue
        for run, out in await outputs(db, slug):
            for item in out["items"]:
                if item["norm"] in wanted and item["norm"] not in picked:
                    picked[item["norm"]] = (run, item)
    if not picked:
        return {}
    gone = await brief.invisible(db, {"items": [i for _, i in picked.values()]})
    picked = {n: x for n, x in picked.items() if not set(x[1]["claims"]) & gone}
    ids = {uuid.UUID(i["place"]) for _, i in picked.values()}
    places = await _place_rows(db, ids)
    # Where an approximate place would go: the known place's point.
    for pid, lon, lat in await db.execute(
        select(Place.id, Place.lon, Place.lat).where(Place.id.in_(list(ids)))
    ):
        places[pid] |= {"lon": float(lon), "lat": float(lat)}
    ctx = await Context.load(db, blobs={i["source"] for _, i in picked.values()})
    out, shown = {}, {}
    for norm, (run, item) in picked.items():
        doc = ctx.doc(item["source"])
        near = places.get(uuid.UUID(item["place"]))
        if near is None:
            continue
        hrefs = {c["id"]: c["href"] for c in wanted[norm]["claims"]}
        cite = {
            "file": doc["file"],
            "href": next(
                (hrefs[i] for i in item["claims"] if hrefs.get(i)),
                f"/evidence/{doc['source_sha256']}",
            ),
        }
        if doc["sensitive"] and not granted(doc["source_sha256"]):
            out[norm] = {
                "kind": "locked",
                "places": [],
                "cite": cite,
                "agent": AGENT,
                "why": "The place finder has a suggestion from a sensitive source. "
                "Reveal the source to read it.",
            }
            continue
        shown.setdefault(run.id, (run, []))[1].extend(item["claims"])
        out[norm] = {
            "kind": "approximate",
            "places": [],
            "near": near,
            "radius_km": item["radius_km"],
            "quote": item["quote"],
            "cite": cite,
            "agent": AGENT,
            "demo": run.model_requested == read.FIXED,
            "why": "Not in the gazetteer. Placed from the quoted words.",
        }
    for run, ids in shown.values():
        await brief.log_view(db, user, run, sorted(set(ids)))
    return out
