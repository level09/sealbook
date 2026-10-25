"""Case map: who was where, when, in one case. Rows of people over time and their
moves on a map, the bands where two or more were at one place at once, the silences,
the shortest path between two people or places, and the case's news.

A projection over the story view (`stories.data.story`, with its masking and
erasure rules), discovery's shared presence, the gap finder's rules and the agent
reader's case. Nothing here writes a claim or decides one."""

from __future__ import annotations

import uuid
from collections import deque
from datetime import date
from itertools import combinations
from types import SimpleNamespace

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from sealbook.agents import gaps, read
from sealbook.desk.data import _ref, files
from sealbook.discovery import findings
from sealbook.discovery.intervals import interval
from sealbook.discovery.models import FindingKey
from sealbook.discovery.service import available
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Principal,
    Run,
    Statement,
    StatementTier,
)
from sealbook.ledger.write import current_clause
from sealbook.monitor import filter as mfilter
from sealbook.monitor.data import Context
from sealbook.people import data as people
from sealbook.places.models import Place
from sealbook.review.findings import KINDS as FINDING_WORDS
from sealbook.review.findings import month_label
from sealbook.stories import data as stories

# A place known to within this many metres is drawn as a point; coarser ones as areas.
PRECISE_M = 250
PATH_DEPTH = 8
NEWS_LIMIT = 30
HELD = {Prop.DETAINED_AT, Prop.DETENTION_BRANCH, Prop.SEEN_AT, Prop.TRANSFERRED_TO}
AGENT_TOOLS = ("brief", "link", "standing", "contradictions", "gaps")
# The strongest kind first, so of two equally short paths the better one is found.
EDGE_ORDER = {"with": 0, "at": 1, "transfer": 1, "family": 2, "source": 3, "same": 4}
# How a place step reads, most specific first.
VERBS = (
    (Prop.DETAINED_AT, "was held at"),
    (Prop.DETENTION_BRANCH, "was held by"),
    (Prop.TRANSFERRED_TO, "was transferred to"),
    (Prop.ARRESTED_AT, "was arrested at"),
    (Prop.SEEN_AT, "was seen at"),
    (Prop.LAST_SEEN, "was last seen at"),
)


def _day(s: str) -> date:
    return date.fromisoformat(s)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _apart(days: int) -> str:
    months = round(days / 30.44)
    if months < 2:
        return _plural(days, "day")
    if months < 24:
        return f"about {months} months"
    return f"about {round(days / 365.25)} years"


# --- the map and timeline ----------------------------------------------------------


def bands(points: list[dict], near=None) -> list[dict]:
    """Two or more people at one resolved place (or two places within reach of each
    other) on a shared day: discovery's shared presence over the points this reader
    sees, every tier included, so a band can rest on unconfirmed claims. Pure."""
    shown = [p for p in points if p["place_id"]]
    by_id = {p["id"]: p for p in shown}
    pts = [
        {
            "id": p["id"],
            "person": p["subject"],
            "place": f"place:{p['place_id']}",
            "interval": interval(_day(p["from"]), _day(p["to"]), Prop(p["prop"])),
            "source": p.get("source"),
        }
        for p in shown
    ]
    out = []
    for c in findings.shared_presence(pts, near):
        held = all(by_id[i]["prop"] in HELD for i in c["members"])
        out.append(
            {
                "people": c["people"],
                "places": [p.removeprefix("place:") for p in c["places"]],
                "from": c["window"][0].isoformat(),
                "to": c["window"][1].isoformat(),
                "month": c["month"],
                "members": c["members"],
                "label": f"{'held together' if held else 'together'}, "
                f"{month_label(c['month'])}",
            }
        )
    out.sort(key=lambda b: (b["from"], b["places"], b["people"]))
    return [b | {"id": f"band-{i + 1}"} for i, b in enumerate(out)]


def open_ends(points: list[dict], events=(), kinds=None, today=None) -> list[dict]:
    """ "No record after": the gap finder's open ends (`gaps.detect`) over the points
    on the timeline, with release and death events closing a silence. Kept only when
    the last account is itself on the timeline, so a masked claim never sets a date
    the reader cannot see. Pure."""
    seen = {p["id"] for p in points}
    claims = [
        SimpleNamespace(
            id=p["id"],
            person=p["subject"],
            prop=Prop(p["prop"]),
            lo=_day(p["from"]),
            hi=_day(p["to"]),
        )
        for p in points
    ] + list(events)
    out = []
    for g in gaps.detect(claims, kinds or {}, today):
        last = g["bounds"][-1]
        if g["window"][1] is None and str(last.id) in seen:
            out.append(
                {
                    "person": g["person"],
                    "after": g["window"][0],
                    "claim": str(last.id),
                    "label": gaps.window_words(g["window"]),
                }
            )
    return sorted(out, key=lambda o: (o["person"], o["after"]))


async def places(db, ids) -> list[dict]:
    """Each resolved place once, with how precisely it is known."""
    found = await db.scalars(
        select(Place).where(Place.id.in_([uuid.UUID(i) for i in ids]))
    )
    return [
        {
            "id": str(p.id),
            "en": p.name_en,
            "ar": p.name_ar,
            "lonlat": [float(p.lon), float(p.lat)],
            "precision_m": p.precision_m,
            # No precision means an area (a district, a governorate), never a point.
            "approximate": p.precision_m is None or p.precision_m > PRECISE_M,
        }
        for p in found
    ]


async def build(db, user, investigation: str) -> tuple[dict, read.Case]:
    """The case map for one investigation and the agent reader's view of the case
    behind it (sources and open same-person proposals), used by the path search."""
    f = {"investigation": investigation, "time": None}
    s = await stories.story(db, user, "filter", None, f)
    case = await read.case(db, investigation)
    rows = {r["key"] for r in s["rows"]}
    ids = sorted({p["place_id"] for p in s["points"] if p["place_id"]})
    events = [
        c
        for c in case.claims
        if c.prop == Prop.EVENT and c.person in rows and not c.sensitive
    ]
    pairs = [list(p) for p in case.links if set(p) <= rows]
    # Two records an open proposal may join are not two people together.
    found = [
        b
        for b in bands(
            s["points"], await findings.near(db, [f"place:{i}" for i in ids])
        )
        if len(b["people"]) > 2 or sorted(b["people"]) not in pairs
    ]
    # A band discovery already proposed opens its finding.
    for b in found:
        b["finding"] = next(
            (
                c["id"]
                for c in s["suggestions"]
                if c["kind"] == findings.SHARED
                and set(c["people"]) == set(b["people"])
                and c["window"][0][:7] == b["month"]
            ),
            None,
        )
    view = {
        "investigation": investigation,
        "rows": s["rows"],
        "points": s["points"],
        "places": await places(db, ids),
        "bands": found,
        "open_ends": open_ends(s["points"], events, await gaps.event_kinds(db, events)),
        "pairs": pairs,
        "unresolved": s["unresolved"],
        "attribution": s["attribution"],
        "truncated": s["truncated"],
    }
    return view, case


# --- how two people or places connect -----------------------------------------------


def edges(view: dict, named=(), family=()) -> list[dict]:
    """Every link the path search may walk. Held at and transferred to join a person
    and a place; held with joins two people of one band; named in joins a person and
    a source; family and an open same-person proposal join two people. `named` is
    (person, source sha) pairs; `family` is relation rows {id, a, b, tier}. Pure."""
    out = []
    at: dict[tuple, list] = {}
    for p in view["points"]:
        if p["place_id"]:
            at.setdefault((p["subject"], f"place:{p['place_id']}"), []).append(p)
    for (person, place), pts in sorted(at.items()):
        transfer = all(p["prop"] == Prop.TRANSFERRED_TO for p in pts)
        out.append(
            {
                "a": person,
                "b": place,
                "kind": "transfer" if transfer else "at",
                "claims": [p["id"] for p in pts],
                "from": min(p["from"] for p in pts),
                "to": max(p["to"] for p in pts),
            }
        )
    pts = {p["id"]: p for p in view["points"]}
    for b in view["bands"]:
        for x, y in combinations(sorted(b["people"]), 2):
            out.append(
                {
                    "a": x,
                    "b": y,
                    "kind": "with",
                    "band": b["id"],
                    "claims": [i for i in b["members"] if pts[i]["subject"] in (x, y)],
                    "from": b["from"],
                    "to": b["to"],
                }
            )
    rows = {r["key"] for r in view["rows"]}
    for person, sha in sorted(set(named)):
        if person in rows:
            out.append({"a": person, "b": f"blob:{sha}", "kind": "source"})
    for r in family:
        if {r["a"], r["b"]} <= rows and r["a"] != r["b"]:
            out.append(
                {
                    "a": r["a"],
                    "b": r["b"],
                    "kind": "family",
                    "statement": r["id"],
                    "tier": r["tier"],
                }
            )
    for x, y in view["pairs"]:
        out.append({"a": x, "b": y, "kind": "same"})
    return out


def path(links: list[dict], start: str, goal: str, depth: int = PATH_DEPTH):
    """The shortest chain of links from `start` to `goal`, at most `depth` long, as
    [(link, from node, to node)]; None when there is none within reach. Breadth
    first, so the search is bounded by the case's links. Pure."""
    if start == goal:
        return None
    near: dict[str, list] = {}
    for e in links:
        near.setdefault(e["a"], []).append((e, e["b"]))
        near.setdefault(e["b"], []).append((e, e["a"]))
    for v in near.values():
        v.sort(key=lambda x: (EDGE_ORDER[x[0]["kind"]], x[1]))
    came: dict[str, tuple | None] = {start: None}
    queue = deque([(start, 0)])
    while queue:
        node, d = queue.popleft()
        if node == goal:
            out = []
            while came[node] is not None:
                e, prev = came[node]
                out.append((e, prev, node))
                node = prev
            return out[::-1]
        if d == depth:
            continue
        for e, other in near.get(node, ()):
            if other not in came:
                came[other] = (e, node)
                queue.append((other, d + 1))
    return None


def steps(chain, view: dict, titles: dict) -> list[dict]:
    """Each link of a path as a sentence with its sources. A step through a place
    whose two stays do not overlap is weak and says how far apart they were. Pure."""
    rows = {r["key"]: r["label"] for r in view["rows"]}
    names = {
        # One spelling: two scripts in one sentence reorder under bidi.
        f"place:{p['id']}": p["en"] or p["ar"]
        for p in view["places"]
    }
    pts = {p["id"]: p for p in view["points"]}
    bands = {b["id"]: b for b in view["bands"]}

    def name(node):
        if node.startswith("blob:"):
            return titles.get(node[5:], "a source")
        return rows.get(node) or names.get(node) or "an unnamed record"

    def cited(ids):
        out = {}
        for i in ids:
            p = pts[i]
            if p.get("href"):
                out.setdefault(p["href"], {"title": p["file"], "href": p["href"]})
        return list(out.values())

    out = []
    for n, (e, x, y) in enumerate(chain):
        kind, claims = e["kind"], e.get("claims", [])
        if kind in ("at", "transfer"):
            person, place = (x, y) if x.startswith("person:") else (y, x)
            props = {pts[i]["prop"] for i in claims}
            verb = next(v for p, v in VERBS if p in props)
            when = read.when(_day(e["from"]), _day(e["to"]))
            text = f"{name(person)} {verb} {name(place)}, {when}"
            sources = cited(claims)
        elif kind == "with":
            b = bands[e["band"]]
            place = " and ".join(name(f"place:{p}") for p in b["places"])
            verb = "were held together" if b["label"].startswith("held") else "were"
            text = (
                f"{name(x)} and {name(y)} {verb} at {place}, {month_label(b['month'])}"
            )
            sources = cited(claims)
        elif kind == "source":
            person, blob = (x, y) if x.startswith("person:") else (y, x)
            text = f"{name(person)} is named in {name(blob)}"
            sources = [{"title": name(blob), "href": f"/evidence/{blob[5:]}"}]
        elif kind == "family":
            text = f"{name(x)} and {name(y)} are family"
            if e["tier"] != Tier.ACCEPTED:
                text += ", a link proposed and not yet reviewed"
            sources = [
                {
                    "title": "The family link",
                    "href": f"/review?statement={e['statement']}",
                }
            ]
        else:
            text = (
                f"{name(x)} and {name(y)} may be one person: a same-person "
                "proposal not yet reviewed"
            )
            sources = [
                {
                    "title": "The proposal",
                    "href": f"/people/{x.removeprefix('person:')}",
                }
            ]
        step = {
            "text": text,
            "kind": kind,
            "nodes": [x, y],
            "claims": claims,
            "sources": sources,
            "unconfirmed": any(pts[i]["tier"] == Tier.UNCONFIRMED for i in claims)
            or kind == "same"
            or (kind == "family" and e["tier"] != Tier.ACCEPTED),
            "weak": None,
        }
        before = chain[n - 1][0] if n else None
        if (
            before
            and kind in ("at", "transfer")
            and before["kind"] in ("at", "transfer")
            and x.startswith("place:")
        ):
            lo = max(_day(before["from"]), _day(e["from"]))
            hi = min(_day(before["to"]), _day(e["to"]))
            if lo > hi:
                step["weak"] = (
                    f"Not at the same time: {_apart((lo - hi).days)} apart, so this "
                    "is a shared place, not a shared cell"
                )
        out.append(step)
    return out


async def family(db, rows: set) -> list[dict]:
    """Family links between people on the map, proposed or accepted, not rejected."""
    found = await db.execute(
        select(Statement.id, Statement.subject, Payload.value, StatementTier.tier)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.prop == Prop.RELATION,
            Statement.subject.startswith("person:"),
            Payload.value["kind"].astext == "family",
            StatementTier.tier != Tier.REJECTED,
            current_clause(),
        )
    )
    groups, _ = await people.identity(db)
    out = []
    for sid, subject, value, tier in found:
        a = read.cluster_of(groups, subject)
        b = read.cluster_of(groups, value["person"])
        if {a, b} <= rows:
            out.append({"id": str(sid), "a": a, "b": b, "tier": tier})
    return out


async def connect(db, user, investigation: str, start: str, goal: str) -> dict:
    """How `start` connects to `goal` (each a person row key or place:<id>)."""
    view, case = await build(db, user, investigation)
    rows = {r["key"] for r in view["rows"]}
    named = {(c.person, c.source) for c in case.claims if c.person}
    links = edges(view, named, await family(db, rows))
    nodes = rows | {f"place:{p['id']}" for p in view["places"]}
    if start not in nodes or goal not in nodes:
        raise LookupError("pick two people or places on this map")
    chain = path(links, start, goal)
    titles = {sha: d["title"] for sha, d in case.docs.items()}
    return {
        "view": view,
        "steps": steps(chain, view, titles) if chain else [],
        "found": chain is not None,
        "depth": PATH_DEPTH,
    }


# --- case news ----------------------------------------------------------------------


def _titles(names: list[str]) -> str:
    # Titles hold commas ("Roster, July 2013"), so a semicolon parts them.
    shown = "; ".join(names[:2])
    return shown + (f" and {len(names) - 2} more" if len(names) > 2 else "")


async def arrivals(db, investigation: str) -> list[dict]:
    """One item per intake batch (or per collector and day for sources added one by
    one): who added how many sources, and how many people they name, counted from
    the claims, never named, so nothing here needs masking."""
    acqs = (
        await db.execute(
            select(
                Acquisition.blob_sha256,
                Acquisition.at,
                Acquisition.context["batch"].astext,
                Principal.name,
            )
            .join(Principal, Principal.id == Acquisition.collector_id)
            .where(
                Acquisition.context["investigation"].astext == investigation,
                available(Acquisition.blob_sha256),
            )
            .order_by(Acquisition.at)
        )
    ).all()
    first: dict[str, object] = {}
    groups: dict[tuple, dict] = {}
    for sha, at, batch, who in acqs:
        first.setdefault(sha, at)
        g = groups.setdefault(
            ("batch", batch) if batch else ("day", who, at.date()),
            {"shas": [], "who": who, "at": at, "start": at, "batch": batch},
        )
        if sha not in g["shas"]:
            g["shas"].append(sha)
        g["at"] = max(g["at"], at)
    clusters, _ = await people.identity(db)
    src = mfilter.source(mfilter.statement_blob())
    named: dict[str, set] = {}
    for subject, sha in await db.execute(
        select(Statement.subject, src)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.subject.startswith("person:"),
            Statement.prop.not_in(people.HIDDEN),
            StatementTier.tier != Tier.REJECTED,
            current_clause(),
            mfilter.statement_clause({"investigation": investigation, "time": None}),
        )
        .distinct()
    ):
        named.setdefault(sha, set()).add(read.cluster_of(clusters, subject))
    titles = await files(db, [sha for g in groups.values() for sha in g["shas"]])
    out = []
    for g in groups.values():
        mine = set().union(*(named.get(s, set()) for s in g["shas"]))
        known = {
            p
            for sha, ps in named.items()
            if sha not in g["shas"] and first.get(sha, g["start"]) < g["start"]
            for p in ps & mine
        }
        text = f"Added {_plural(len(g['shas']), 'source')}: " + _titles(
            [titles[s] for s in g["shas"] if s in titles]
        )
        if mine:
            text += f", naming {_plural(len(mine), 'person', 'people')}"
            text += (
                f", {len(known)} already in the case"
                if known and len(known) < len(mine)
                else ", all already in the case"
                if known
                else ", all new to the case"
            )
        out.append(
            {
                "kind": "arrival",
                "at": g["at"].isoformat(),
                "who": g["who"],
                "agent": False,
                "text": text,
                "href": f"/sources?batch={g['batch']}" if g["batch"] else "/sources",
            }
        )
    return out


async def agent_outputs(db, investigation: str) -> list[dict]:
    """One item per finished agent run in the case: what it proposed or found,
    counted, with the screen where a person reads and decides it."""
    batches = select(Acquisition.context["batch"].astext).where(
        Acquisition.context["investigation"].astext == investigation
    )
    found = list(
        await db.scalars(
            select(Run)
            .where(
                Run.tool.in_(AGENT_TOOLS),
                Run.status == "done",
                or_(
                    Run.inputs["investigation"].astext == investigation,
                    Run.inputs["batch"].astext.in_(batches),
                ),
            )
            .order_by(Run.finished_at.desc())
            .limit(NEWS_LIMIT)
        )
    )
    if not found:
        return []
    ctx = await Context.load(db, runs=[r.id for r in found])
    proposed = dict(
        (
            await db.execute(
                select(Statement.run_id, func.count())
                .where(
                    Statement.run_id.in_([r.id for r in found]),
                    Statement.prop != Prop.ACCESS,
                )
                .group_by(Statement.run_id)
            )
        ).all()
    )
    out = []
    for run in found:
        if run.tool in ("contradictions", "gaps", "standing"):
            n = len(read.read_output(await db.get(Blob, run.output_hash))["items"])
        else:
            n = proposed.get(run.id, 0)
        text, href = {
            "brief": ("Wrote the intake brief for a new batch", "/inbox"),
            "link": (
                f"Proposed {_plural(n, 'link')} for review"
                if n
                else "Looked for links and proposed none",
                "/review",
            ),
            "contradictions": (
                f"Found {_plural(n, 'place')} where sources disagree"
                if n
                else "Found no new disagreements",
                "/people",
            ),
            "gaps": (
                f"Found {_plural(n, 'silence')} worth a search"
                if n
                else "Found no new silences",
                "/people",
            ),
            "standing": (
                f"Found {_plural(n, 'new match', 'new matches')} for a standing question"
                if n
                else "No new matches for a standing question",
                "/desk",
            ),
        }[run.tool]
        out.append(
            {
                "kind": "agent",
                "at": (run.finished_at or run.created_at).isoformat(),
                "who": ctx.actor(run.principal_id, run.id)["label"],
                "agent": True,
                "text": text,
                "href": href,
            }
        )
    return out


async def decisions(db, investigation: str) -> list[dict]:
    """Human reviews of the case's claims and findings, one item per gesture (or per
    person, verdict and minute), saying what was decided by kind, never by value."""
    review, about = Statement, aliased(Statement)
    rows = (
        await db.execute(
            select(
                review.id,
                review.recorded_at,
                Principal.name,
                Payload.value["verdict"].astext,
                Payload.value["gesture"].astext,
                about.id,
                about.prop,
                FindingKey.kind,
            )
            .join(Payload, Payload.commitment == review.commitment)
            .join(Principal, Principal.id == review.author_id)
            .join(about, about.id == _ref(review))
            .outerjoin(FindingKey, FindingKey.statement_id == about.id)
            .where(
                review.prop == Prop.REVIEW,
                Principal.kind == "human",
                or_(
                    FindingKey.investigation == investigation,
                    mfilter.statement_clause(
                        {"investigation": investigation, "time": None}, about
                    ),
                ),
            )
            .order_by(review.recorded_at.desc())
            .limit(500)
        )
    ).all()
    sittings: dict[tuple, dict] = {}
    for _, at, who, verdict, gesture, sid, prop, kind in rows:
        key = (who, verdict, gesture or at.strftime("%Y%m%d%H%M"))
        s = sittings.setdefault(
            key, {"at": at, "who": who, "verdict": verdict, "claims": [], "kinds": []}
        )
        if kind:
            s["kinds"].append(kind)
        else:
            s["claims"].append((sid, prop))
    out = []
    for s in sittings.values():
        verb = "Accepted" if s["verdict"] == "accept" else "Rejected"
        if s["kinds"]:
            what = FINDING_WORDS.get(s["kinds"][0], "finding").lower()
            text, href = f"{verb} a finding: {what}", "/discover"
        else:
            fields = list(dict.fromkeys(read.label(p).lower() for _, p in s["claims"]))
            text = f"{verb} {_plural(len(s['claims']), 'claim')}: " + ", ".join(
                fields[:3]
            )
            href = f"/review?statement={s['claims'][0][0]}"
        out.append(
            {
                "kind": "decision",
                "at": s["at"].isoformat(),
                "who": s["who"],
                "agent": False,
                "text": text,
                "href": href,
            }
        )
    return out


async def news(db, investigation: str, limit: int = NEWS_LIMIT) -> list[dict]:
    """What arrived, what agents proposed and what people decided, newest first."""
    items = (
        await arrivals(db, investigation)
        + await agent_outputs(db, investigation)
        + await decisions(db, investigation)
    )
    return sorted(items, key=lambda i: i["at"], reverse=True)[:limit]
