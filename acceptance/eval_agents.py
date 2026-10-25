"""Score the proactive agents against the planted truth in acceptance/agents/case.json.

  uv run python acceptance/eval_agents.py                 # fixed answer, no model
  ROUTE_LOCAL_BASE_URL=http://127.0.0.1:11434/v1 AGENT_MODEL=qwen3.5:9b \
    ROUTE_LOCAL_TIMEOUT=900 uv run python acceptance/eval_agents.py --live

Fixed mode runs the agents on their fixed answer (`read.FIXED`), which proposes
every candidate, so its numbers are the rule-built candidate layer. Live mode asks
the local model. Both run the real run functions (`linker.link`, `gaps.gaps`,
`contradictions.contradictions`, `brief.brief`) on the case held in memory: their
database reads return the planted case and their writes are captured, nothing
else is patched. Prints one JSON report. See docs/agent-eval.md.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import uuid
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sealbook.agents import brief, contradictions, gaps, linker, read, tasks
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop, Route
from sealbook.monitor.data import when as doc_when
from sealbook.runs import routes
from sealbook.settings import Config

CASE = Path(__file__).parent / "agents" / "case.json"
NS = uuid.UUID("5ea1b00c-0000-4000-8000-a6e47e7a1000")
PRINCIPAL = uuid.uuid5(NS, "principal")
TARGETS = {
    "linker_recall": 0.8,
    "linker_false_merges": 0,
    "gap_recall": 1.0,
    "contradiction_precision": 0.8,
}


def _id(key: str) -> uuid.UUID:
    return uuid.uuid5(NS, key)


def _day(text):
    return date.fromisoformat(text) if text else None


def load(path=CASE):
    """The planted case as `read.case` would return it, plus the event kinds the
    gap finder reads and maps from ids back to the planted keys."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    sha = {
        k: hashlib.sha256(f"agent-eval/{k}".encode()).hexdigest()
        for k in spec["sources"]
    }
    docs = {
        sha[k]: {
            "sha256": sha[k],
            "file": k,
            "title": s["title"],
            "doc_type": s["doc_type"],
            "sensitive": s["sensitivity"] == "sensitive",
            "date": doc_when(_day(s["date"]), None),
            "mime": "text/plain",
        }
        for k, s in spec["sources"].items()
    }
    subject = {r: f"person:{_id(r)}" for r in spec["records"]}
    groups = {}
    for cluster in spec["clusters"]:
        members = frozenset(str(_id(r)) for r in cluster)
        groups |= dict.fromkeys(members, members)
    places = {
        k: SimpleNamespace(id=_id(f"place/{k}"), **p) for k, p in spec["places"].items()
    }
    claims, located, events = [], {}, {}
    for c in spec["claims"]:
        record = spec["records"][c["record"]]
        cid = _id(c["key"])
        claims.append(
            read.Claim(
                id=cid,
                subject=subject[c["record"]],
                person=read.cluster_of(groups, subject[c["record"]]),
                prop=c["prop"],
                text=c["value"],
                lo=_day(c["from"]),
                hi=_day(c["to"]),
                source=sha[record["source"]],
                span_blob=None,
                span=None,
                tier=c["tier"],
                sensitive=docs[sha[record["source"]]]["sensitive"],
            )
        )
        if c["place"]:
            located[cid] = places[c["place"]]
        if c.get("event"):
            events[cid] = c["event"]
    case = read.Case(spec["investigation"], claims, docs, located, groups)
    keys = {
        "claim": {str(_id(c["key"])): c["key"] for c in spec["claims"]},
        "record": {str(_id(r)): r for r in spec["records"]},
        "cluster": {read.cluster_of(groups, s): r for r, s in subject.items()},
        "sha": {v: k for k, v in sha.items()},
    }
    return spec, case, events, keys


class Ctx:
    """What the worker hands a run: the run row, events, and the model route."""

    def __init__(self, model: str, inputs: dict):
        self.s = None
        self.sign = None
        self.events = []
        self.run = SimpleNamespace(
            id=uuid.uuid4(),
            principal_id=PRINCIPAL,
            route="local",
            model_requested=model,
            inputs=inputs,
            endpoint=None,
            model_served=None,
            provider_served=None,
            raw_output_ref=None,
            prompt_hash=None,
        )

    async def emit(self, stage, drop_reason=None, **counts):
        self.events.append({"stage": stage, "drop_reason": drop_reason} | counts)

    async def call(self, body: dict) -> routes.Served:
        served = await routes.chat(
            Route.LOCAL, {"model": self.run.model_requested, **body}
        )
        self.run.model_served = served.model
        self.run.raw_output_ref = hashlib.sha256(served.raw).hexdigest()
        return served


def harness(case, events, g, written):
    """Replace each run's database reads with the planted case and capture its
    writes. The agents' candidate, model and validation code runs unchanged."""

    async def put(s, raw, mime, root):
        return SimpleNamespace(sha256=hashlib.sha256(raw).hexdigest())

    async def append(s, st, sign=None):
        written.append(st)
        return SimpleNamespace(id=uuid.uuid4(), prop=st.prop)

    async def store(c, output):
        return output

    nothing = mock.AsyncMock(return_value=None)
    stack = ExitStack()
    for target, attr, new in (
        (read, "case", mock.AsyncMock(return_value=case)),
        (read, "incidents", mock.AsyncMock(return_value=[])),
        (read, "log_read", nothing),
        (read, "agent_for", mock.AsyncMock(return_value=SimpleNamespace(id=PRINCIPAL))),
        (read, "store", store),
        (read.blobs, "put", put),
        (tasks, "check_access", nothing),
        (brief, "invisible", mock.AsyncMock(return_value=set())),
        (brief, "gather", mock.AsyncMock(return_value=g)),
        (linker, "known", mock.AsyncMock(return_value=(set(), {}))),
        (write, "subject_head", nothing),
        (write, "append", append),
        (gaps, "event_kinds", mock.AsyncMock(return_value=events)),
        (gaps, "reported", mock.AsyncMock(return_value=set())),
        (gaps, "colleagues", mock.AsyncMock(return_value={})),
        (contradictions, "reported", mock.AsyncMock(return_value=set())),
        # Runs here are built on the local route, whatever .env routes agents to.
        (Config, "AGENT_ROUTE", "local"),
    ):
        stack.enter_context(mock.patch.object(target, attr, new))
    return stack


def batch(spec, case):
    """`brief.gather` for the planted batch: no findings, assignments or incidents."""
    shas = [s for s in case.docs if case.docs[s]["file"] in spec["batch"]]
    return {
        "batch": "agent-eval",
        "investigation": case.investigation,
        "shas": shas,
        "docs": case.docs,
        "case": case,
        "mine": [c for c in case.claims if c.source in shas],
        "incidents": [],
        "findings": [],
        "finding_values": {},
        "assignments": [],
        "links": [],
        "blocked": False,
        "candidates": {},
    }


def ratio(n, d):
    return round(n / d, 3) if d else None


def cost(out, findings):
    tokens = (out["cost"]["prompt_tokens"] or 0) + (
        out["cost"]["completion_tokens"] or 0
    )
    return out["cost"] | {
        "findings": findings,
        "seconds_per_finding": ratio(out["cost"]["seconds"], findings),
        "tokens_per_finding": ratio(tokens, findings),
    }


def score_linker(spec, case, out, written, keys):
    truth = {frozenset(p) for p in spec["truth"]["same_person"]}
    twins = {frozenset(p) for p in spec["truth"]["twins"]}
    h = read.Handles()
    built = {
        frozenset(keys["cluster"][s] for s in c["_sides"])
        for c in linker.candidates(case, [], None, h)
        if c["h"][0] == "m"
    }
    proposed = {
        frozenset(
            (
                keys["record"][st.subject.removeprefix("person:")],
                keys["record"][st.value["person"]],
            )
        )
        for st in written
        if st.prop == Prop.SAME_AS
    }
    name = {c.subject: c.text for c in case.claims if c.prop == Prop.NAME}

    def rows(pairs):
        return [
            {
                "records": sorted(p),
                "names": [name[f"person:{_id(r)}"] for r in sorted(p)],
                "candidate": p in built,
                "proposed": p in proposed,
            }
            for p in sorted(pairs, key=sorted)
        ]

    return {
        "recall": ratio(len(proposed & truth), len(truth)),
        "false_merges": len(proposed & twins),
        "other_false_proposals": len(proposed - truth - twins),
        "rule_candidates": {
            "recall": ratio(len(built & truth), len(truth)),
            "twins": len(built & twins),
            "other": len(built - truth - twins),
        },
        "same_person": rows(truth),
        "twins": rows(twins),
        "relations_written": sum(st.prop != Prop.SAME_AS for st in written),
        "candidates": out["candidates"],
        "deferred": out["deferred"],
        "valid": out["valid"],
        "dropped": out["dropped"],
        "refused": out["refused"],
        "cost": cost(out, len(out["written"])),
    }


def score_gaps(spec, case, events, out, keys):
    def cluster(record):
        return read.cluster_of(case.groups, f"person:{_id(record)}")

    def key(g):
        return (cluster(g["record"]), g["from"], g["to"])

    planted = {key(g): g for g in spec["truth"]["gaps"]}
    absent = {key(g): g for g in spec["truth"]["not_gaps"]}
    scope = {k[0] for k in planted | absent}
    found = {
        (g["person"], *g["window"])
        for g in gaps.detect(case.claims, events, date.fromisoformat(spec["today"]))
    }
    reported = {(i["person"], *i["window"]) for i in out["items"]}
    return {
        "recall": ratio(len(found & planted.keys()), len(planted)),
        "false_gaps_on_planted_non_gaps": len(found & absent.keys()),
        "extra_in_planted_people": len(
            {f for f in found if f[0] in scope} - planted.keys()
        ),
        "detected_elsewhere": len({f for f in found if f[0] not in scope}),
        "planted": [
            g | {"detected": k in found, "reported_this_run": k in reported}
            for k, g in planted.items()
        ],
        "not_gaps": [g | {"detected": k in found} for k, g in absent.items()],
        "detected": out["gaps"],
        "reported": len(out["items"]),
        "deferred": out["deferred"],
        "dropped": out["dropped"],
        "cost": cost(out, len(out["items"])),
    }


def score_contradictions(spec, case, out, keys):
    true = [frozenset(t["claims"]) for t in spec["truth"]["contradictions"]]
    false = [frozenset(t["claims"]) for t in spec["truth"]["not_contradictions"]]

    def label(ids):
        cited = {keys["claim"][str(i)] for i in ids}
        return {
            "claims": sorted(cited),
            "true": any(t <= cited for t in true),
            "planted_non_contradiction": any(f <= cited for f in false),
        }

    built = [
        label(c.id for s in x["sides"] for c in s)
        for x in contradictions.candidates(case)
    ]
    items = [label(i["claims"]) | {"kind": i["kind"]} for i in out["items"]]
    serious = [
        i
        for i in items
        if i["kind"] not in ("spelling or transliteration", "date precision")
    ]

    def covered(found):
        return sum(any(t <= set(i["claims"]) for i in found) for t in true)

    return {
        "precision": ratio(sum(i["true"] for i in items), len(items)),
        "precision_without_spelling_or_precision_kinds": ratio(
            sum(i["true"] for i in serious), len(serious)
        ),
        "recall": ratio(covered(items), len(true)),
        "rule_candidates": {
            "precision": ratio(sum(i["true"] for i in built), len(built)),
            "recall": ratio(covered(built), len(true)),
            "candidates": built,
        },
        "items": items,
        "truth": spec["truth"]["contradictions"],
        "not_contradictions": spec["truth"]["not_contradictions"],
        "deferred": out["deferred"],
        "dropped": out["dropped"],
        "cost": cost(out, len(out["items"])),
    }


def score_brief(spec, out, keys):
    true = [frozenset(t["claims"]) for t in spec["truth"]["contradictions"]]
    lines = out["sections"]
    flagged = [
        {
            "claims": sorted(keys["claim"][i] for i in line["claims"]),
            "true": any(t <= {keys["claim"][i] for i in line["claims"]} for t in true),
        }
        for line in lines["disagrees"]
    ]
    written = sum(len(lines[s]) for s in ("connects", "disagrees", "next"))
    return {
        "lines": {s: len(v) for s, v in lines.items()},
        "disagreements": flagged,
        "valid": out["valid"],
        "dropped": out["dropped"],
        "cost": cost(out, written),
    }


async def evaluate(live: bool = False, path=CASE) -> dict:
    model = Config.AGENT_MODEL if live else read.FIXED
    if live and not (os.environ.get("ROUTE_LOCAL_BASE_URL") and model):
        return {
            "mode": "live",
            "skipped": "ROUTE_LOCAL_BASE_URL and AGENT_MODEL must both be set for live mode",
        }
    spec, case, events, keys = load(path)
    inputs = {"investigation": case.investigation, "for_user": 1}
    written = []
    with harness(case, events, batch(spec, case), written):
        link_out = await linker.link(Ctx(model, inputs))
        gap_out = await gaps.gaps(Ctx(model, inputs))
        contra_out = await contradictions.contradictions(Ctx(model, inputs))
        brief_out = await brief.brief(
            Ctx(model, {"batch": "agent-eval", "for_user": 1})
        )
    report = {
        "mode": "live" if live else "fixed",
        "model": model,
        "case": spec["investigation"],
        "claims": len(case.claims),
        "linker": score_linker(spec, case, link_out, written, keys),
        "gaps": score_gaps(spec, case, events, gap_out, keys),
        "contradictions": score_contradictions(spec, case, contra_out, keys),
        "brief": score_brief(spec, brief_out, keys),
    }
    report["targets"] = {
        "linker_recall >= 0.8": (report["linker"]["recall"] or 0)
        >= TARGETS["linker_recall"],
        "linker_false_merges == 0": report["linker"]["false_merges"]
        == TARGETS["linker_false_merges"],
        "gap_recall == 1.0": report["gaps"]["recall"] == TARGETS["gap_recall"],
        "contradiction_precision >= 0.8": (report["contradictions"]["precision"] or 0)
        >= TARGETS["contradiction_precision"],
    }
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--live", action="store_true", help="ask the local model")
    p.add_argument("--out", type=Path, help="also write the report here")
    args = p.parse_args()
    report = asyncio.run(evaluate(args.live))
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
