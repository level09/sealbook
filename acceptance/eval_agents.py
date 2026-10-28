"""Score the proactive agents against the planted truth in acceptance/agents/case.json.

  uv run python acceptance/eval_agents.py                 # fixed answer, no model
  uv run python acceptance/eval_agents.py --live --trials 3 --out instance/eval.json
  ROUTE_LOCAL_BASE_URL=http://127.0.0.1:11434/v1 AGENT_MODEL=qwen3.5:9b \
    ROUTE_LOCAL_TIMEOUT=900 uv run python acceptance/eval_agents.py --live --route local
  uv run python acceptance/eval_agents.py --live --model anthropic/claude-sonnet-5.5 \
    --reasoning low

Fixed mode runs the agents on their fixed answer (`read.FIXED`), which proposes
every candidate, so its numbers are the rule-built candidate layer. Live mode asks
`AGENT_MODEL` on `--route` (default `AGENT_ROUTE`) through `routes.chat`, as the
worker does. Both run the real run functions (`linker.link`, `gaps.gaps`,
`contradictions.contradictions`, `brief.brief`) on the case held in memory: their
database reads return the planted case and their writes are captured, nothing
else is patched. Prints one JSON report; with --out, each call's prompt and raw
response and every drop event also go to a JSONL file beside it. See
docs/agent-eval.md.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import uuid
from collections import Counter
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sealbook.agents import brief, contradictions, gaps, linker, read, tasks
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop, Route
from sealbook.ledger.queue import LEASE
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
# Contradiction kinds that say the values only look different, and those that say
# the sources really disagree.
BENIGN = ("spelling or transliteration", "date precision")
CONFLICT = ("sources conflict", "possibly two people")


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

    def __init__(self, model: str, inputs: dict, route: Route, transport=None):
        self.s = None
        self.sign = None
        self.events = []
        self.calls = []
        self.transport = transport
        self.run = SimpleNamespace(
            id=uuid.uuid4(),
            principal_id=PRINCIPAL,
            route=str(route),
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
        body = {"model": self.run.model_requested, **body}
        served = await routes.chat(Route(self.run.route), body, self.transport)
        self.run.endpoint = served.endpoint
        self.run.model_served = served.model
        self.run.provider_served = served.provider
        self.run.raw_output_ref = hashlib.sha256(served.raw).hexdigest()
        self.calls.append(
            {
                "route": self.run.route,
                "endpoint": served.endpoint,
                "model_served": served.model,
                "provider": served.provider,
                "body": body,
                "raw": served.raw.decode("utf-8", "replace"),
            }
        )
        return served


def harness(case, events, g, written, route: Route):
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
        # Gaps and contradictions refuse a run off the configured agent route.
        (Config, "AGENT_ROUTE", str(route)),
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
    hard = {frozenset(p["records"]) for p in spec["truth"]["hard_same_person"]}
    traps = {frozenset(p["records"]) for p in spec["truth"]["traps"]}
    planted = truth | twins | hard | traps

    def pairs(cands):
        return {
            frozenset(keys["cluster"][s] for s in c["_sides"])
            for c in cands
            if c["h"][0] == "m"
        }

    every = linker.candidates(case, [], None, read.Handles())
    built = pairs(every)
    with mock.patch.object(linker, "CAP", len(case.claims) ** 2):
        uncapped = pairs(linker.candidates(case, [], None, read.Handles()))
    # Beyond CAP a pair is never built; past the time budget the run skips the
    # tail of its candidates. Either way a later run reads it, not a miss.
    deferred = (uncapped - built) | pairs(every[len(every) - out["deferred"] :])
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

    def rows(planted):
        return [
            {
                "records": sorted(p),
                "names": [name[f"person:{_id(r)}"] for r in sorted(p)],
                "candidate": p in built,
                "deferred": p in deferred,
                "proposed": p in proposed,
            }
            for p in sorted(planted, key=sorted)
        ]

    return {
        "recall": ratio(len(proposed & truth), len(truth)),
        "false_merges": len(proposed & twins),
        "other_false_proposals": len(proposed - planted),
        "hard_recall": ratio(len(proposed & hard), len(hard)),
        "trap_avoidance": ratio(len(traps - proposed), len(traps)),
        "canary_declined": frozenset(spec["truth"]["canary"]["pair"]) not in proposed,
        "dropped": out["dropped"],
        "refused": out["refused"],
        "planted_deferred": len(deferred & planted),
        "rule_candidates": {
            "recall": ratio(len(built & truth), len(truth)),
            "twins": len(built & twins),
            "hard_same_person": len(built & hard),
            "traps": len(built & traps),
            "other": len(built - planted),
        },
        "same_person": rows(truth),
        "twins": rows(twins),
        "hard_same_person": rows(hard),
        "traps": rows(traps),
        "relations_written": sum(st.prop != Prop.SAME_AS for st in written),
        "candidates": out["candidates"],
        "deferred": out["deferred"],
        "valid": out["valid"],
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
    # The run ranks gaps to its own today, so match a gap by person and start.
    ranked = gaps.detect(case.claims, events)
    deferred = {
        (g["person"], g["window"][0]) for g in ranked[len(ranked) - out["deferred"] :]
    }
    return {
        "recall": ratio(len(found & planted.keys()), len(planted)),
        "dropped": out["dropped"],
        "planted_deferred": len({k[:2] for k in planted} & deferred),
        "false_gaps_on_planted_non_gaps": len(found & absent.keys()),
        "extra_in_planted_people": len(
            {f for f in found if f[0] in scope} - planted.keys()
        ),
        "detected_elsewhere": len({f for f in found if f[0] not in scope}),
        "planted": [
            g
            | {
                "detected": k in found,
                "reported_this_run": k in reported,
                "deferred": k[:2] in deferred,
            }
            for k, g in planted.items()
        ],
        "not_gaps": [g | {"detected": k in found} for k, g in absent.items()],
        "detected": out["gaps"],
        "reported": len(out["items"]),
        "deferred": out["deferred"],
        "cost": cost(out, len(out["items"])),
    }


def score_contradictions(spec, case, out, keys):
    truth = spec["truth"]
    true = [frozenset(t["claims"]) for t in truth["contradictions"]]
    false = [frozenset(t["claims"]) for t in truth["not_contradictions"]]
    hard = [frozenset(t["claims"]) for t in truth["hard_contradictions"]]
    traps = [frozenset(t["claims"]) for t in truth["trap_contradictions"]]

    def label(ids):
        cited = {keys["claim"][str(i)] for i in ids}
        return {
            "claims": sorted(cited),
            "true": any(t <= cited for t in true),
            "planted_non_contradiction": any(f <= cited for f in false),
            "judgment": any(j <= cited for j in hard + traps),
        }

    offered = [
        label(c.id for s in x["sides"] for c in s)
        for x in contradictions.candidates(case)
    ]
    built = [i for i in offered if not i["judgment"]]
    every = [label(i["claims"]) | {"kind": i["kind"]} for i in out["items"]]
    # Precision and recall read the first planted set only, as they always have.
    items = [i for i in every if not i["judgment"]]
    serious = [i for i in items if i["kind"] not in BENIGN]

    def covered(found, planted=true):
        return sum(any(t <= set(i["claims"]) for i in found) for t in planted)

    # A judgment item counts as reported only under a conflict kind. The hunter
    # explains every candidate and has no "consistent" kind; models that read two
    # values as one pick spelling, date precision or other.
    conflicts = [i for i in every if i["kind"] in CONFLICT]
    canary = frozenset(truth["canary"]["contradiction"])

    # Past CAP or the time budget the run skips the tail of its candidates.
    tail = offered[len(offered) - out["deferred"] :]
    return {
        "precision": ratio(sum(i["true"] for i in items), len(items)),
        "precision_without_spelling_or_precision_kinds": ratio(
            sum(i["true"] for i in serious), len(serious)
        ),
        "recall": ratio(covered(items), len(true)),
        "hard_recall": ratio(covered(conflicts, hard), len(hard)),
        "trap_avoidance": ratio(len(traps) - covered(conflicts, traps), len(traps)),
        "canary_reported": any(canary <= set(i["claims"]) for i in conflicts),
        "dropped": out["dropped"],
        "planted_deferred": sum(
            i["true"] or i["planted_non_contradiction"] or i["judgment"] for i in tail
        ),
        "rule_candidates": {
            "precision": ratio(sum(i["true"] for i in built), len(built)),
            "recall": ratio(covered(built), len(true)),
            "judgment": ratio(covered(offered, hard + traps), len(hard + traps)),
            "candidates": offered,
        },
        "items": every,
        "truth": truth["contradictions"],
        "not_contradictions": truth["not_contradictions"],
        "hard_contradictions": truth["hard_contradictions"],
        "trap_contradictions": truth["trap_contradictions"],
        "deferred": out["deferred"],
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


async def trial(spec, case, events, keys, model, route, log, n, transport=None):
    inputs = {"investigation": case.investigation, "for_user": 1}
    ctx = {
        a: Ctx(model, inputs, route, transport)
        for a in ("linker", "gaps", "contradictions")
    }
    ctx["brief"] = Ctx(model, {"batch": "agent-eval", "for_user": 1}, route, transport)
    written = []
    with harness(case, events, batch(spec, case), written, route):
        link_out = await linker.link(ctx["linker"])
        gap_out = await gaps.gaps(ctx["gaps"])
        contra_out = await contradictions.contradictions(ctx["contradictions"])
        brief_out = await brief.brief(ctx["brief"])
    for a, c in ctx.items():
        log += [{"trial": n, "agent": a, "call": x} for x in c.calls]
        log += [
            {"trial": n, "agent": a, "drop": e} for e in c.events if e["drop_reason"]
        ]
    return {
        "served": {
            a: {
                "endpoint": c.run.endpoint,
                "model_served": c.run.model_served,
                "provider": c.run.provider_served,
            }
            for a, c in ctx.items()
        },
        "linker": score_linker(spec, case, link_out, written, keys),
        "gaps": score_gaps(spec, case, events, gap_out, keys),
        "contradictions": score_contradictions(spec, case, contra_out, keys),
        "brief": score_brief(spec, brief_out, keys),
    }


def spread(values):
    seen = [v for v in values if v is not None]
    return {
        "mean": ratio(sum(seen), len(seen)),
        "min": min(seen, default=None),
        "max": max(seen, default=None),
        "trials": values,
    }


async def evaluate(
    live=False,
    route=None,
    trials=1,
    path=CASE,
    log=None,
    transport=None,
    model=None,
    reasoning=None,
) -> dict:
    """`log`, a list, receives each model call and drop event."""
    route = Route(route or Config.AGENT_ROUTE)
    model = (model or Config.AGENT_MODEL) if live else read.FIXED
    reasoning = reasoning or Config.AGENT_REASONING
    need = "ROUTE_LOCAL_BASE_URL" if route is Route.LOCAL else "OPENROUTER_API_KEY"
    if live and not (os.environ.get(need) and model):
        return {
            "mode": "live",
            "route": route,
            "skipped": f"{need} and AGENT_MODEL must both be set for live mode on {route}",
        }
    spec, case, events, keys = load(path)
    log = [] if log is None else log
    with mock.patch.object(Config, "AGENT_REASONING", reasoning):
        runs = [
            await trial(spec, case, events, keys, model, route, log, n, transport)
            for n in range(trials)
        ]
    metrics = {
        name: spread([r[agent][field] for r in runs])
        for name, (agent, field) in (
            ("linker_recall", ("linker", "recall")),
            ("linker_false_merges", ("linker", "false_merges")),
            ("gap_recall", ("gaps", "recall")),
            ("contradiction_precision", ("contradictions", "precision")),
            ("contradiction_recall", ("contradictions", "recall")),
            ("linker_hard_recall", ("linker", "hard_recall")),
            ("linker_trap_avoidance", ("linker", "trap_avoidance")),
            ("contradiction_hard_recall", ("contradictions", "hard_recall")),
            ("contradiction_trap_avoidance", ("contradictions", "trap_avoidance")),
        )
    }
    # Injected text changed nothing: the canary pair declined and the canary
    # disagreement still reported, in the same trial.
    metrics["canary_held"] = spread(
        [
            int(
                r["linker"]["canary_declined"]
                and r["contradictions"]["canary_reported"]
            )
            for r in runs
        ]
    )
    agents = ("linker", "gaps", "contradictions")
    planted_deferred = {a: sum(r[a]["planted_deferred"] for r in runs) for a in agents}
    return {
        "mode": "live" if live else "fixed",
        "route": route,
        "model": model,
        # The fixed answer sends nothing to a model, so no effort applies.
        "reasoning": reasoning if live else None,
        "served": [
            {"model_served": m, "provider": p}
            for m, p in sorted(
                {
                    (s["model_served"], s["provider"])
                    for r in runs
                    for s in r["served"].values()
                    if s["model_served"]
                }
            )
        ],
        "case": spec["investigation"],
        "claims": len(case.claims),
        "trials": trials,
        "limits": {
            "timeout_seconds": Config.ROUTE_LOCAL_TIMEOUT
            if route is Route.LOCAL
            else routes.TIMEOUT.read,
            "lease_seconds": LEASE.total_seconds(),
            **{
                a: {
                    "cap": m.CAP,
                    "per_call": m.PER_CALL,
                    "budget_seconds": m.BUDGET_SECONDS,
                }
                for a, m in zip(agents, (linker, gaps, contradictions), strict=True)
            },
        },
        # A planted linker pair or contradiction left for a later run is not scored
        # as found or missed by this one. Gap recall scores detection, which no
        # limit cuts; the gap finder's deferrals are counted for reading only.
        "complete": not (
            planted_deferred["linker"] or planted_deferred["contradictions"]
        ),
        "planted_deferred": planted_deferred,
        "deferred": {a: [r[a]["deferred"] for r in runs] for a in agents},
        "metrics": metrics,
        "dropped": {
            a: dict(sum((Counter(r[a]["dropped"]) for r in runs), Counter()))
            for a in (*agents, "brief")
        },
        "targets": {
            "linker_recall >= 0.8, mean": (metrics["linker_recall"]["mean"] or 0)
            >= TARGETS["linker_recall"],
            "linker_false_merges == 0 in every trial (pass^k)": metrics[
                "linker_false_merges"
            ]["max"]
            == TARGETS["linker_false_merges"],
            "gap_recall == 1.0 in every trial": metrics["gap_recall"]["min"]
            == TARGETS["gap_recall"],
            "contradiction_precision >= 0.8, mean": (
                metrics["contradiction_precision"]["mean"] or 0
            )
            >= TARGETS["contradiction_precision"],
        },
        "runs": runs,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--live", action="store_true", help="ask AGENT_MODEL")
    p.add_argument(
        "--route",
        choices=[r.value for r in Route],
        default=Config.AGENT_ROUTE,
        help="model route for live mode (default AGENT_ROUTE)",
    )
    p.add_argument("--trials", type=int, default=1, help="runs of every agent")
    p.add_argument(
        "--out", type=Path, help="also write the report here, calls to .jsonl"
    )
    p.add_argument("--model", help="live model (default AGENT_MODEL)")
    p.add_argument(
        "--reasoning",
        choices=Config.REASONING_EFFORTS,
        default=Config.AGENT_REASONING,
        help="reasoning_effort sent on each call (default AGENT_REASONING)",
    )
    args = p.parse_args()
    if args.trials < 1:
        p.error("--trials must be at least 1")
    log = []
    report = asyncio.run(
        evaluate(
            args.live,
            args.route,
            args.trials,
            log=log,
            model=args.model,
            reasoning=args.reasoning,
        )
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        args.out.with_suffix(".jsonl").write_text(
            "".join(json.dumps(x, ensure_ascii=False, default=str) + "\n" for x in log),
            encoding="utf-8",
        )
    print(text)


if __name__ == "__main__":
    main()
