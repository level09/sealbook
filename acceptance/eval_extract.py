"""Score text extraction against the planted truth in acceptance/extract/posts.json:
people a model invents (outlets, headlines, hashtags, unnamed people), people it
misses, events it reports where a post reports none, and reported events whose
place names the planted town. What is scored is what the worker would write
(`worker.facts`), so the not-a-person guard and the place check apply.

  uv run python acceptance/eval_extract.py                      # MODEL_CLOUD_ZDR on cloud-zdr
  uv run python acceptance/eval_extract.py --route anthropic --model claude-haiku-5-5
  uv run python acceptance/eval_extract.py --trials 3 --out instance/extract.json

An invented person is the failure that matters most: it reaches review, search and
the linker as someone who does not exist. Target: zero in every trial.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sealbook.ledger.contracts import Prop, Route
from sealbook.places import normalise
from sealbook.runs import routes
from sealbook.runs.worker import EXTRACT_SCHEMA, EXTRACT_SYSTEM, facts
from sealbook.settings import Config

POSTS = Path(__file__).parent / "extract" / "posts.json"


def same(a: str, b: str) -> bool:
    a, b = normalise(a), normalise(b)
    return bool(a and b) and (a == b or a in b or b in a)


async def read_post(route: Route, model: str, post: dict) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": post["text"]},
        ],
        "response_format": {"type": "json_schema", "json_schema": EXTRACT_SCHEMA},
        "temperature": 0,
    }
    try:
        served = await routes.chat(route, body)
        items, _ = facts(json.loads(routes.content(served)), post["text"])
    except Exception as exc:  # noqa: BLE001  a failed call is scored as a failure
        return {"id": post["id"], "error": repr(exc)[:300]}
    names = [i["value"]["text"] for i in items if i["prop"] == Prop.NAME]
    reports = [i["value"] for i in items if i["prop"] == Prop.REPORTS]
    places = [r.get(k, "") for r in reports for k in ("town", "place")]
    return {
        "id": post["id"],
        "invented": [n for n in names if not any(same(n, p) for p in post["people"])],
        "missed": [p for p in post["people"] if not any(same(n, p) for n in names)],
        "invented_events": len(reports) if not post["towns"] else 0,
        "towns_found": sum(any(same(t, x) for x in places) for t in post["towns"]),
    }


async def trial(route: Route, model: str, posts: list[dict]) -> dict:
    rows = await asyncio.gather(*(read_post(route, model, p) for p in posts))
    ok = [r for r in rows if "error" not in r]
    people = sum(len(p["people"]) for p in posts)
    towns = sum(len(p["towns"]) for p in posts)
    return {
        "errors": [r for r in rows if "error" in r],
        "invented_people": sum(len(r["invented"]) for r in ok),
        "people_recall": round(1 - sum(len(r["missed"]) for r in ok) / people, 3),
        "invented_events": sum(r["invented_events"] for r in ok),
        "town_recall": round(sum(r["towns_found"] for r in ok) / towns, 3),
        "rows": ok,
    }


async def main(args):
    posts = json.loads(POSTS.read_text())["posts"]
    route = Route(args.route)
    model = args.model or Config.RUN_MODELS.get(route)
    if not model:
        raise SystemExit(f"no model for route {route}: pass --model")
    trials = [await trial(route, model, posts) for _ in range(args.trials)]
    report = {
        "route": str(route),
        "model": model,
        "posts": len(posts),
        "invented_people": [t["invented_people"] for t in trials],
        "people_recall": [t["people_recall"] for t in trials],
        "invented_events": [t["invented_events"] for t in trials],
        "town_recall": [t["town_recall"] for t in trials],
        "errors": sum(len(t["errors"]) for t in trials),
        "invented": sorted(
            {n for t in trials for r in t["rows"] for n in r["invented"]}
        ),
        "passed": all(t["invented_people"] == 0 and not t["errors"] for t in trials),
    }
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(
            json.dumps(report | {"trials": trials}, ensure_ascii=False, indent=1)
        )
    print(text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--route", default="cloud-zdr", choices=[str(r) for r in Route])
    ap.add_argument("--model", help="default: the route's configured extraction model")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--out")
    asyncio.run(main(ap.parse_args()))
