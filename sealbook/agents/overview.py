"""The Agents page: every agent working for one person in a case, in plain words,
and their recent runs. Reads run rows and stored outcomes; changes nothing."""

from collections import Counter

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import aliased

from sealbook.agents import (
    approximate,
    brief,
    contradictions,
    gaps,
    linker,
    noticer,
    read,
    standing,
    tasks,
)
from sealbook.agents.models import AgentTask
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Acquisition, Blob, Run, Statement, StatementTier

# (key, tool, name, what it does, when it runs, where results show, link words)
AGENTS = (
    (
        "brief",
        brief.TOOL,
        brief.AGENT,
        "Writes what a batch brought in: who, where, when, what it connects to "
        "and what to do next.",
        "When a batch you sent finishes.",
        "/inbox",
        "Briefs in the Inbox",
    ),
    (
        "linker",
        linker.TOOL,
        linker.AGENT,
        "Proposes same-person, incident and document links the rules miss, with "
        "the passages behind each.",
        "When a batch you sent finishes, and when you press Find links.",
        "/review",
        "Its links in Review",
    ),
    (
        "standing",
        standing.TOOL,
        "Standing questions",
        "Watches the case for answers to questions you asked once.",
        "When evidence arrives, and daily.",
        "/desk",
        "Questions on the Desk",
    ),
    (
        "contradictions",
        contradictions.TOOL,
        contradictions.AGENT,
        "Finds sources that disagree and drafts a question to settle each.",
        "When you ask, and when evidence arrives if it keeps checking.",
        "/people",
        "Sources disagree, on each person's page",
    ),
    (
        "gaps",
        gaps.TOOL,
        gaps.AGENT,
        "Finds silences in a person's record and suggests where to look.",
        "When you ask, and when evidence arrives if it keeps checking.",
        "/people",
        "Gaps on People",
    ),
    (
        "approximate",
        approximate.TOOL,
        approximate.AGENT,
        "Suggests an approximate place for a name the gazetteer does not have, "
        "from the words around it.",
        "When you ask on Places, and when evidence arrives if it keeps checking.",
        "/places",
        "Suggestions under Unresolved names",
    ),
    (
        "noticer",
        noticer.TOOL,
        noticer.AGENT,
        "Notices evidence that resembles other evidence you can read: same "
        "picture, same wording, a name spelled another way, the same person or "
        "place in another case.",
        "When evidence arrives if it keeps checking, and whenever you ask.",
        None,
        "Also on each evidence page",
    ),
)
TOOLS = {a[1]: a for a in AGENTS} | {noticer.LINK_TOOL: AGENTS[-1]}
# What each agent proposes, one and many.
NOUN = {
    standing.TOOL: ("match", "matches"),
    contradictions.TOOL: ("disagreement", "disagreements"),
    gaps.TOOL: ("place to look", "places to look"),
    noticer.TOOL: ("resemblance", "resemblances"),
    approximate.TOOL: ("approximate place", "approximate places"),
}
SETUP = {
    "ROUTE_LOCAL_BASE_URL": "ROUTE_LOCAL_BASE_URL to the local model server",
    "OPENROUTER_API_KEY": "OPENROUTER_API_KEY for the cloud route",
    "AGENT_MODEL": "AGENT_MODEL to the model it serves",
}


# Agents whose proposals a person accepts or rejects: the linker's links and the
# links the Noticer proposes on a click. The others advise; nothing is decided.
DECIDABLE = (linker.TOOL, noticer.LINK_TOOL)
VERDICT = {"human_accept": "accepted", "human_reject": "rejected"}
OPEN = (Tier.UNCONFIRMED, Tier.CORROBORATED)


def plural(n: int, one: str, many: str) -> str:
    return f"{'One' if n == 1 else n} {one if n == 1 else many}"


def setup_line(missing: list[str]) -> str | None:
    if not missing:
        return None
    return (
        "Agents are off. An administrator sets "
        + " and ".join(SETUP[m] for m in missing)
        + ", then restarts the app and the worker."
    )


def summary(run: Run, out: dict | None) -> str:
    """A run's outcome as one sentence."""
    if run.status in tasks.ACTIVE:
        return "Checking now."
    if run.status == "failed":
        return tasks.failure_reason(run.error)
    out = out or {}
    if run.tool == brief.TOOL:
        n = sum(len(v) for v in out.get("sections", {}).values())
        text = f"Wrote a brief of {plural(n, 'line', 'lines').lower()}."
    elif run.tool == linker.TOOL:
        n = len(out.get("written", []))
        text = (
            f"Proposed {plural(n, 'link', 'links').lower()} for review."
            if n
            else "Proposed no new links."
        )
    elif run.tool == noticer.LINK_TOOL:
        text = "Proposed a link you chose for review."
    else:
        outcome = out.get("outcome") or {}
        n = outcome.get("proposed", 0)
        text = (
            f"Found {plural(n, *NOUN[run.tool]).lower()}."
            if n
            else outcome.get("reason") or "Found nothing new."
        )
    return text + (" Demo output." if run.model_requested == read.FIXED else "")


async def case_of(db, run: Run) -> str | None:
    if run.inputs.get("investigation"):
        return run.inputs["investigation"]
    if batch := run.inputs.get("batch"):
        return await db.scalar(
            select(Acquisition.context["investigation"].astext)
            .where(Acquisition.context["batch"].astext == batch)
            .limit(1)
        )
    return None


async def recent(db, user_id: int, limit: int = 60) -> list[tuple[Run, str]]:
    """This person's agent runs, newest first, with the case each read, only in
    cases they can still open."""
    rows = await db.scalars(
        select(Run)
        .where(
            Run.tool.in_(list(TOOLS)),
            Run.inputs["for_user"].as_integer() == user_id,
        )
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(limit)
    )
    out, ok = [], {}
    for run in rows:
        slug = await case_of(db, run)
        if slug is None:
            continue
        if slug not in ok:
            ok[slug] = await tasks.can_read(db, user_id, slug)
        if ok[slug]:
            out.append((run, slug))
    return out


async def _outcome(db, run: Run) -> dict | None:
    # A link the Noticer proposes seals its result's hash; no blob holds it.
    if run.status == "done" and run.output_hash and run.tool != noticer.LINK_TOOL:
        return read.read_output(await db.get(Blob, run.output_hash))
    return None


def _row(run: Run, slug: str, out) -> dict:
    return {
        "agent": TOOLS[run.tool][2],
        "case": slug,
        "at": (run.finished_at or run.created_at).isoformat(),
        "text": summary(run, out),
        "failed": run.status == "failed",
    }


async def overview(db, user, investigation: str, missing: list[str]) -> dict:
    runs = await recent(db, user.id)
    outs = {run.id: await _outcome(db, run) for run, _ in runs[:12]}
    here = [run for run, slug in runs if slug == investigation]
    my_tasks = list(
        await db.scalars(
            select(AgentTask)
            .where(
                AgentTask.user_id == user.id, AgentTask.investigation == investigation
            )
            .order_by(AgentTask.created_at, AgentTask.id)
        )
    )
    agree = await agreement(db, investigation)
    cards = []
    for key, tool, name, does, when, href, where in AGENTS:
        mine = [r for r in here if TOOLS[r.tool][0] == key]
        last = next((r for r in mine if r.tool == tool), None)
        if last is not None and last.id not in outs:
            outs[last.id] = await _outcome(db, last)
        state, note = _state(
            key, mine, [t for t in my_tasks if t.kind == key], bool(missing)
        )
        cards.append(
            {
                "key": key,
                "name": name,
                "does": does,
                "when": when,
                "state": state,
                "note": note,
                "last": _row(last, investigation, outs[last.id]) if last else None,
                "href": href,
                "where": where,
                "keep": key if key in tasks.KINDS and key != "standing" else None,
                "agreement": agreed([x for x in agree if x["agent"] == key]),
            }
        )
    return {
        "investigation": investigation,
        "off": setup_line(missing),
        "agents": cards,
        "recent": _runs([_row(run, slug, outs[run.id]) for run, slug in runs[:12]]),
    }


def _runs(rows: list[dict]) -> list[dict]:
    """Back-to-back runs that say the same thing read as one row with a count."""
    out: list[dict] = []
    for r in rows:
        last = out[-1] if out else None
        if last and all(last[k] == r[k] for k in ("agent", "case", "text", "failed")):
            last["times"] += 1
        else:
            out.append(r | {"times": 1})
    return out


async def agreement(db, investigation: str | None = None) -> list[dict]:
    """Analyst decisions on agent proposals, all time, counted by case, agent,
    prompt version and served model. Counts only, never values. A link the Noticer
    proposes counts under the check that showed it."""
    shown = aliased(Run)
    q = (
        select(
            Run.inputs["investigation"].astext,
            Run.tool,
            shown.prompt_hash,
            shown.model_served,
            Run.prompt_hash,
            Run.model_served,
            StatementTier.tier,
            StatementTier.reason,
        )
        .select_from(Statement)
        .join(Run, Run.id == Statement.run_id)
        .outerjoin(shown, shown.id == cast(Run.inputs["report"].astext, UUID))
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(Run.tool.in_(DECIDABLE), Statement.prop != Prop.ACCESS)
    )
    if investigation:
        q = q.where(Run.inputs["investigation"].astext == investigation)
    # ponytail: counted in Python, one row per proposal; group in SQL if a case
    # holds enough agent proposals for this to be slow.
    counts = Counter()
    for case, tool, sh, sm, h, model, tier, reason in (await db.execute(q)).all():
        verdict = VERDICT.get(reason) or ("open" if tier in OPEN else None)
        if verdict:
            counts[case, TOOLS[tool][0], sh or h, sm or model, verdict] += 1
    versions = {h: await _version(db, h) for h in {k[2] for k in counts}}
    table = {}
    for (case, key, h, model, verdict), n in counts.items():
        row = table.setdefault(
            (case, key, versions[h], model),
            {"case": case, "agent": key, "version": versions[h], "model": model}
            | dict.fromkeys(("accepted", "rejected", "open"), 0),
        )
        row[verdict] += n
    return [table[k] for k in sorted(table)]


async def _version(db, prompt_hash: str) -> str:
    """The readable prompt version a finished run with this prompt stored."""
    out = await db.scalar(
        select(Run.output_hash)
        .where(
            Run.tool.in_([linker.TOOL, noticer.TOOL]),
            Run.prompt_hash == prompt_hash,
            Run.output_hash.is_not(None),
        )
        .limit(1)
    )
    return read.read_output(await db.get(Blob, out))["version"]


def agreed(rows: list[dict]) -> dict | None:
    """The card's sentence, and a line per prompt version and model."""
    if not rows:
        return None
    a, r, o = (sum(x[k] for x in rows) for k in ("accepted", "rejected", "open"))
    if a + r:
        text = f"Analysts accepted {a} of {a + r} decided " + (
            "proposal" if a + r == 1 else "proposals"
        )
        text += f" ({o} open)." if o else "."
    else:
        text = f"{plural(o, 'proposal waits', 'proposals wait')} for a decision."
    return {
        "text": text,
        "rows": [
            {
                "label": f"Version {x['version'].rsplit('-', 1)[-1]}, "
                + (
                    "the demo answer with no model"
                    if x["model"] == read.FIXED
                    else x["model"]
                ),
                "text": f"{x['accepted']} accepted, {x['rejected']} rejected, "
                f"{x['open']} open",
            }
            for x in rows
        ],
    }


def _state(key: str, runs: list[Run], mine: list[AgentTask], off: bool):
    """(state, the sentence beside it): off, checking, paused, watching or ready."""
    if off:
        return "off", "Off until the agent model is set up."
    if any(r.status in tasks.ACTIVE for r in runs):
        return "checking", "Checking now."
    if key in ("brief", "linker"):
        return "watching", "Watching for batches you send."
    if not mine:
        if key == "standing":
            return "ready", "No questions yet. Ask one on the Desk."
        return (
            "ready",
            "Runs when you ask. Turn on keep checking to watch evidence as it arrives.",
        )
    live = [t for t in mine if t.enabled and not t.paused_reason]
    if live:
        if key == "standing":
            return (
                "watching",
                f"Watching {plural(len(live), 'question', 'questions').lower()}.",
            )
        return "watching", "Watching evidence as it arrives."
    task = mine[0]
    return "paused", task.paused_reason or "You stopped this check."
