"""Analyst search battery against the local demo database (both demo cases seeded,
`stk enrich` and `stk worker` run). Read-only: every query runs inside one
transaction that is rolled back, so no access statements or spaces are kept.

  uv run python acceptance/eval_search.py            # table
  uv run python acceptance/eval_search.py --json     # one JSON report

Needs the local Ollama server from docs/models.md for the semantic half. Ranks
are per source, 1-based; "-" means not returned. Latency is the second of two
passes, so statement caches and the model are warm, as in a running server. Results describe these synthetic cases only. See docs/search-eval.md.
"""

import asyncio
import json
import sys
import time

from sqlalchemy import select

import stk.extensions as ext
from stk.app import create_app
from stk.discovery import embeddings, service
from stk.ledger.contracts import Prop
from stk.ledger.models import Acquisition, Payload, Statement
from stk.settings import Config

C1, C2 = "case-demo-01", "case-demo-02"
# (query, case or None for all cases, expected source files, expected person names, why)
BATTERY = [
    (
        "لينا عمر النجار",
        C2,
        ["list", "appeal"],
        ["لينا عمر النجار"],
        "full Arabic name",
    ),
    (
        "لينا النجار",
        C2,
        ["testimony", "list", "appeal"],
        ["لينا عمر النجار"],
        "name without middle name",
    ),
    (
        "Lina Al-Najjar",
        C2,
        ["note"],
        ["لينا عمر النجار"],
        "English spelling, no middle name",
    ),
    ("Lina Najjar", C2, ["note"], ["لينا عمر النجار"], "transliteration, no article"),
    ("لينا النجّار", C2, ["testimony"], ["لينا عمر النجار"], "with shadda"),
    ("محمد احمد الخطيب", C2, ["list"], ["محمد أحمد الخطيب"], "no hamza on alef"),
    ("Mohamad al Khatib", C2, ["note"], ["محمد أحمد الخطيب"], "loose transliteration"),
    ("يوسف خالد علي", C2, ["transfer"], ["يوسف خالد علي"], "PDF page text"),
    (
        "Yousef Khaled Al-Ali",
        None,
        ["post", "post_v2"],
        ["يوسف خالد العلي"],
        "English name in Arabic page",
    ),
    ("حرستا", None, ["register_page.png"], [], "place, case 1 register"),
    ("Harasta", None, ["register_page.png"], [], "place, other script"),
    ("حي الجديدة", C2, ["list", "appeal"], [], "neighbourhood"),
    ("Jdeideh", C2, ["note"], [], "neighbourhood, English"),
    ("الفرع 999", C2, ["list", "testimony", "transfer"], [], "facility"),
    ("Branch 999", C2, ["note", "list", "testimony"], [], "facility, English"),
    ("999", C2, ["list", "testimony", "transfer", "note"], [], "bare identifier"),
    ("4471", None, ["transfer", "transfer_order.pdf"], [], "order number"),
    ("أمر نقل رقم 4471", C2, ["transfer"], [], "phrase in a PDF page"),
    ("2014-02-11", C2, ["list", "appeal"], [], "ISO date"),
    ("11 February 2014", C2, ["note"], [], "English date"),
    ("سجن ك المركزي", C2, ["transfer", "testimony"], [], "prison name"),
    ("آخر مره شوهدت", C2, ["appeal"], [], "ta marbuta written as ha"),
    ("اخيها", C2, ["appeal"], [], "alef without hamza"),
    ("سمعنا إنو نقلوها", C2, ["testimony"], [], "Syrian dialect"),
    (
        "who was moved to the central prison",
        C2,
        ["transfer", "testimony"],
        [],
        "English question, Arabic sources",
    ),
    (
        "woman held at the branch in March 2014",
        C2,
        ["testimony"],
        [],
        "English question about a testimony",
    ),
    (
        "checkpoint",
        C2,
        ["checkpoint", "checkpoint_forward", "note"],
        [],
        "visual and text",
    ),
    (
        "حاجز عسكري",
        C2,
        ["checkpoint", "checkpoint_forward", "appeal"],
        [],
        "visual, Arabic",
    ),
    ("prison building with barred windows", C2, ["photo"], [], "visual"),
    ("destroyed street", C2, [], [], "no relevant item in the local seed"),
    ("market stalls", C2, ["market"], [], "visual"),
    ("رحلة عالساحل بالباص", None, ["speech", "sy_bus"], [], "audio content, dialect"),
    ("handwritten register of detainees", C2, ["register", "scan", "list"], [], "scan"),
    ("{transfer_sha}", None, ["transfer"], [], "full sha256"),
    ("{transfer_prefix}", None, ["transfer"], [], "sha256 prefix"),
]


async def names(db, subject):
    rows = await db.execute(
        select(Payload.value)
        .join(Statement, Statement.commitment == Payload.commitment)
        .where(Statement.subject == subject, Statement.prop == Prop.NAME)
    )
    out = []
    for (value,) in rows:
        text = value.get("text") if isinstance(value, dict) else value
        if text and text not in out:
            out.append(text)
    return out


async def run():
    create_app()
    report = []
    async with ext.async_session_factory() as db:
        files = {}
        for acq in (await db.execute(select(Acquisition))).scalars():
            files.setdefault(acq.blob_sha256, acq.context.get("file") or "")
        transfer = next(
            sha
            for sha, name in files.items()
            if name == "transfer" and sha.startswith("9")
        )
        # Pass 1 warms statement caches and the model; pass 2 is reported.
        for query, case, expect, people_expect, why in BATTERY * 2:
            query = query.format(transfer_sha=transfer, transfer_prefix=transfer[:12])
            f = {"investigation": case, "time": None}
            await db.rollback()
            try:
                started = time.perf_counter()
                embedding, error = None, None
                if Config.EMBEDDING_MODEL:
                    try:
                        embedding = await embeddings.query(
                            db, query, Config.EMBEDDING_MODEL
                        )
                    except Exception as exc:  # recorded, the battery continues
                        error = f"{type(exc).__name__}: {exc}"
                embedded = time.perf_counter()
                hits = await service.search(
                    db,
                    query,
                    "hybrid" if embedding else "keyword",
                    f,
                    query_embedding=embedding,
                )
                searched = time.perf_counter()
                people = await service.actor_search(
                    db, query, f, query_embedding=embedding
                )
                done = time.perf_counter()
                sources = []
                for hit in hits:
                    name = files.get(hit["sha256"], hit["sha256"][:8])
                    if all(s["file"] != name for s in sources):
                        sources.append(
                            {
                                "file": name,
                                "group": hit["group"],
                                "signals": hit["signals"],
                                "score": hit.get("score"),
                                "location": hit.get("location"),
                            }
                        )
                ranks = {
                    e: next(
                        (i + 1 for i, s in enumerate(sources) if s["file"] == e), None
                    )
                    for e in expect
                }
                found_people = []
                for row in people[:5]:
                    found_people.append(
                        {
                            "names": (await names(db, row["subject"]))[:2],
                            "signals": row["signals"],
                        }
                    )
                person_hit = (
                    next(
                        (
                            i + 1
                            for i, p in enumerate(found_people)
                            if any(n in p["names"] for n in people_expect)
                        ),
                        None,
                    )
                    if people_expect
                    else None
                )
                report.append(
                    {
                        "query": query,
                        "case": case,
                        "why": why,
                        "ranks": ranks,
                        "best": min((r for r in ranks.values() if r), default=None),
                        "person_rank": person_hit,
                        "person_expected": bool(people_expect),
                        "top": sources[:3],
                        "results": len(sources),
                        "people": found_people[:3],
                        "embed_ms": round((embedded - started) * 1000),
                        "search_ms": round((searched - embedded) * 1000),
                        "people_ms": round((done - searched) * 1000),
                        "error": error,
                    }
                )
            finally:
                await db.rollback()
    await ext.engine.dispose()
    return report[len(BATTERY) :]


def summary(report):
    report = [r for r in report if r["ranks"]]
    best = [r["best"] for r in report]
    people = [r for r in report if r["person_expected"]]
    return {
        "queries": len(report),
        "hit_at_1": sum(1 for b in best if b == 1) / len(best),
        "hit_at_3": sum(1 for b in best if b and b <= 3) / len(best),
        "missed": sum(1 for b in best if b is None),
        "person_at_1": sum(1 for r in people if r["person_rank"] == 1) / len(people),
        "median_total_ms": sorted(
            r["embed_ms"] + r["search_ms"] + r["people_ms"] for r in report
        )[len(report) // 2],
        "errors": sum(1 for r in report if r["error"]),
    }


# (query, case, group, expected title or file) for the grouped /api/search answer.
ENTITIES = [
    ("Lina Najjar", C2, "people", "لينا عمر النجار"),
    ("Mohamad al Khatib", C2, "people", "محمد أحمد الخطيب"),
    ("Yousef Khaled Al-Ali", None, "people", "Yousef Khaled Al-Ali"),
    ("Harasta", None, "places", "حرستا"),
    ("حرستا", None, "places", "حرستا"),
    ("Branch 999", None, "places", "الفرع 999"),
    ("حي الجديدة", None, "places", "حي الجديدة"),
    ("Jdeideh", None, "places", "الجديدة"),
    ("94057a751318", None, "sources", "Transfer order"),
    ("checkpoint", C2, "sources", "checkpoint"),
    ("حرستا", None, "claims", "حرستا"),
    ("الفرع 999", C2, "claims", "الفرع 999"),
    ("Lina Najjar", C2, "passages", "Documentation note"),
    ("أمر نقل رقم 4471", C2, "passages", "Transfer order"),
    ("checkpoint", C2, "media", "checkpoint"),
    ("prison building with barred windows", C2, "media", "Damaged building"),
    ("رحلة عالساحل بالباص", C2, "media", "Voice note, coastal"),
    ("لينا عمر النجار", C2, "findings", None),
    ("لينا عمر النجار", C2, "incidents", None),
]


async def entities():
    """Grouped answers through stk.search.find, the function behind /api/search."""
    from stk.search import find

    create_app()
    out, times = [], []
    async with ext.async_session_factory() as db:
        for query, case, group, expect in ENTITIES * 2:
            await db.rollback()
            f = {"investigation": case, "time": None}
            started = time.perf_counter()
            embedding = None
            if Config.EMBEDDING_MODEL and not service.SHA.fullmatch(query.lower()):
                embedding = await embeddings.query(db, query, Config.EMBEDDING_MODEL)
            found = await find.find(db, query, f, embedding=embedding)
            times.append(round((time.perf_counter() - started) * 1000))
            items = found["groups"][group]["items"]
            rank = next(
                (
                    i + 1
                    for i, item in enumerate(items)
                    if expect
                    and expect in (item.get("title") or "")
                    or expect in item.get("alt", [])
                ),
                None,
            )
            out.append(
                {
                    "query": query,
                    "case": case,
                    "group": group,
                    "expect": expect,
                    "rank": rank,
                    "exact": items[rank - 1]["exact"] if rank else None,
                    "total": found["groups"][group]["total"],
                    "top": [i.get("title") for i in items[:3]],
                    "groups": {k: v["total"] for k, v in found["groups"].items()},
                }
            )
        await db.rollback()
    await ext.engine.dispose()
    warm = sorted(times[len(ENTITIES) :])
    return out[len(ENTITIES) :], {
        "p50_ms": warm[len(warm) // 2],
        "p95_ms": warm[min(len(warm) - 1, round(len(warm) * 0.95))],
    }


def main():
    report = asyncio.run(run())
    grouped, latency = asyncio.run(entities())
    if "--json" in sys.argv:
        print(
            json.dumps(
                {
                    "summary": summary(report),
                    "queries": report,
                    "entities": grouped,
                    "entity_latency": latency,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return
    for r in report:
        top = ", ".join(f"{s['file']}({'+'.join(s['signals'])})" for s in r["top"])
        ranks = " ".join(f"{k}={v or '-'}" for k, v in r["ranks"].items())
        person = r["person_rank"] if r["person_expected"] else "n/a"
        print(
            f"{r['query'][:38]:<38} | {ranks:<40} | person={person} | "
            f"{r['embed_ms']}+{r['search_ms']}+{r['people_ms']}ms | {top}"
            + (f" | ERROR {r['error']}" if r["error"] else "")
        )
    print(json.dumps(summary(report)))
    for r in grouped:
        print(
            f"{r['query'][:30]:<30} | {r['group']:<9} | rank={r['rank'] or '-'} "
            f"exact={r['exact']} total={r['total']} | {r['top']}"
        )
    print(json.dumps(latency))


if __name__ == "__main__":
    main()
