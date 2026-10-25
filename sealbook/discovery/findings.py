"""Bounded discovery: shared presence and candidate incidents from accepted or
corroborated position claims at resolved places. Shared presence also joins two
places whose centroids lie within the larger of their two precisions. Deterministic
and explainable:
each finding names its member statements and the features that put it there, and
only a person decides. Nothing here accepts anything."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from sqlalchemy import func, select, text

from sealbook.discovery.intervals import interval, overlap
from sealbook.discovery.models import FindingKey, FindingSalt
from sealbook.ledger import chain, write
from sealbook.ledger.contracts import Prop, Refused, StatementIn, Tier
from sealbook.ledger.models import Statement, StatementTier
from sealbook.monitor import filter as mfilter
from sealbook.monitor.data import LOCATED
from sealbook.people import data as people
from sealbook.places import resolve
from sealbook.settings import Config

SHARED = "shared_presence"
INCIDENT = "candidate_incident"
KINDS = (SHARED, INCIDENT)


_SALTS: dict[str, bytes] = {}


async def salt(db) -> bytes:
    """The deployment's finding-key salt, stored once by its migration and cached per
    database. Independent of SECRET_KEY, so rotating that keeps every key."""
    where = str(db.bind.url)
    if where not in _SALTS:
        _SALTS[where] = await db.scalar(
            select(FindingSalt.salt).where(FindingSalt.id == 1)
        )
    return _SALTS[where]


def key(
    salt: bytes,
    kind: str,
    investigation: str,
    people: list[str],
    places: list[str],
    month: str,
) -> str:
    """Suppression key over the resolved set, its investigation and its calendar
    month. Salted, so it names no one."""
    return chain.commitment(
        salt,
        {
            "kind": kind,
            "investigation": investigation,
            "people": sorted(people),
            "places": sorted(places),
            "month": month,
        },
    )


def _by_place(points: list[dict]) -> dict[str, list[dict]]:
    out = defaultdict(list)
    for p in points:
        out[p["place"]].append(p)
    return out


async def near(db, places) -> dict[tuple[str, str], dict]:
    """Pairs of distinct places whose centroids lie within the larger of their two
    precisions, as {(a, b): {"m": metres, "precision_m": [a's, b's]}} with a < b.
    A place without a precision (an admin area) is never near anything."""
    ids = [p.removeprefix("place:") for p in places]
    rows = await db.execute(
        text(
            """
            SELECT a.id::text, b.id::text, ST_Distance(a.centroid, b.centroid),
                   a.precision_m, b.precision_m
            FROM place a JOIN place b ON a.id::text < b.id::text
             AND ST_DWithin(a.centroid, b.centroid,
                            greatest(a.precision_m, b.precision_m))
            WHERE a.id::text = ANY(:ids) AND b.id::text = ANY(:ids)
              AND a.precision_m IS NOT NULL AND b.precision_m IS NOT NULL
            """
        ),
        {"ids": ids},
    )
    return {
        (f"place:{a}", f"place:{b}"): {"m": round(m), "precision_m": [pa, pb]}
        for a, b, m, pa, pb in rows.all()
    }


def _month(day) -> str:
    return day.strftime("%Y-%m")


def _candidate(
    kind: str, people: list[str], place: str, month: str, members, **features
) -> dict:
    return {
        "kind": kind,
        "people": people,
        "places": [place],
        "month": month,
        "members": sorted(p["id"] for p in members),
        "window": (
            min(p["interval"][0] for p in members),
            max(p["interval"][1] for p in members),
        ),
        "features": {**features, "sources": len({p["source"] for p in members})},
    }


def _pair(a: list[dict], b: list[dict]) -> tuple[float, int]:
    """Strongest overlap between two people's points, with its length in days."""
    return max(
        (
            overlap(x["interval"], y["interval"]),
            (
                min(x["interval"][1], y["interval"][1])
                - max(x["interval"][0], y["interval"][0])
            ).days
            + 1,
        )
        for x in a
        for y in b
    )


def shared_presence(points: list[dict], near=None) -> list[dict]:
    """One candidate per place set, month and set of two or more people whose
    claims share a day there. The sets are the maximal groups of claims active on
    one day (the cliques of an interval graph), bucketed by the calendar month their
    overlap starts in; strength is the weakest pair's best overlap. Each place is a
    group, and so is each pair in `near` (see `near()`); a near pair's candidate
    must use both places and names their distance and precisions."""
    found: dict[tuple, dict] = {}
    at = _by_place(points)
    groups = [((place,), pts, None) for place, pts in at.items()]
    groups += [
        ((a, b), at[a] + at[b], gap)
        for (a, b), gap in sorted((near or {}).items())
        if a in at and b in at
    ]
    for _, pts, gap in groups:
        # ponytail: quadratic in the claims at one place; sweep sorted ends if a
        # single place passes a few thousand claims.
        cliques = {
            frozenset(
                p["id"] for p in pts if p["interval"][0] <= day <= p["interval"][1]
            )
            for day in {p["interval"][0] for p in pts}
        }
        by_id = {p["id"]: p for p in pts}
        for clique in cliques:
            if any(clique < other for other in cliques):
                continue
            places = tuple(sorted({by_id[i]["place"] for i in clique}))
            if gap is not None and len(places) < 2:
                continue  # the same-place group already has it
            mine = defaultdict(list)
            for i in clique:
                mine[by_id[i]["person"]].append(by_id[i])
            if len(mine) < 2:
                continue
            strength = min(_pair(mine[a], mine[b]) for a, b in combinations(mine, 2))
            month = _month(max(by_id[i]["interval"][0] for i in clique))
            entry = found.setdefault(
                (places, tuple(sorted(mine)), month),
                {"members": {}, "strength": strength, "gap": gap},
            )
            entry["members"].update({i: by_id[i] for i in clique})
            entry["strength"] = max(entry["strength"], strength)
    out = []
    for (places, names, month), e in found.items():
        extra = {}
        if e["gap"] is not None:
            extra = {
                "distance_m": e["gap"]["m"],
                "precision_m": e["gap"]["precision_m"],
            }
        c = _candidate(
            SHARED,
            list(names),
            places[0],
            month,
            e["members"].values(),
            overlap=e["strength"][0],
            days=e["strength"][1],
            **extra,
        )
        c["places"] = list(places)
        if len(places) > 1:
            c["at"] = {i: p["place"] for i, p in e["members"].items()}
        out.append(c)
    return out


def candidate_incidents(
    points: list[dict], window_days=14, min_people=3, min_sources=2
) -> list[dict]:
    """Dense clusters at one place: claims starting within `window_days` of the
    cluster's first, naming at least `min_people` people from `min_sources`
    documents. The calendar month the cluster starts in is part of its key, so the
    same people at the same place in another month are a new candidate; clusters
    in one month join."""
    found: dict[tuple, dict] = {}
    for place, pts in _by_place(points).items():
        clusters, current = [], []
        for p in sorted(pts, key=lambda p: (p["interval"][0], p["id"])):
            if (
                current
                and (p["interval"][0] - current[0]["interval"][0]).days > window_days
            ):
                clusters.append(current)
                current = []
            current.append(p)
        clusters.append(current)
        for cluster in clusters:
            names = tuple(sorted({p["person"] for p in cluster}))
            if (
                len(names) >= min_people
                and len({p["source"] for p in cluster}) >= min_sources
            ):
                month = _month(cluster[0]["interval"][0])
                found.setdefault((place, names, month), {}).update(
                    {p["id"]: p for p in cluster}
                )
    out = []
    for (place, names, month), members in found.items():
        c = _candidate(INCIDENT, list(names), place, month, members.values())
        start, end = c["window"]
        c["features"] = {
            "people": len(names),
            "sources": c["features"]["sources"],
            "window": window_days,
            "span_days": (end - start).days + 1,
        }
        out.append(c)
    return out


def rank(c: dict) -> int:
    """Evidence strength: independent documents weigh most. Ties go to the earliest
    window (see `_order`)."""
    f = c["features"]
    return f["sources"] * 10 + len(c["people"]) * 5 + round(f.get("overlap", 0) * 10)


def _order(c: dict) -> tuple:
    return (-rank(c), c["window"][0], c["key"])


async def points(db, investigation: str) -> list[dict]:
    """Accepted or corroborated, current, dated position claims in one
    investigation that resolve to a place. People are identity clusters, named by
    their smallest member id, so a merge changes future keys only."""
    rows = (
        await db.execute(
            select(
                Statement.id,
                Statement.subject,
                Statement.prop,
                Statement.valid_from,
                Statement.valid_to,
                mfilter.source(Statement.span_blob),
            )
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop.in_(LOCATED),
                Statement.subject.startswith("person:"),
                StatementTier.tier.in_((Tier.ACCEPTED, Tier.CORROBORATED)),
                write.current_clause(),
            )
        )
    ).all()
    # Scoped to the investigation: claims from any other one stay unresolved.
    places = await resolve.resolved(db, [r[0] for r in rows], investigation)
    groups, _ = await people.identity(db)
    out = []
    for sid, subject, prop, start, end, source in rows:
        span = interval(start, end, Prop(prop))
        if places[sid] is None or span is None:
            continue
        pid = people.pid(subject)
        out.append(
            {
                "id": str(sid),
                "person": f"person:{min(groups.get(pid, {pid}))}",
                "place": f"place:{places[sid].id}",
                "interval": span,
                "source": source,
            }
        )
    return out


def _value(c: dict) -> dict:
    """The FINDING payload. Floats are not canonical, so overlap is written as text."""
    return {
        "kind": c["kind"],
        "members": c["members"],
        "people": c["people"],
        "places": c["places"],
        "month": c["month"],
        "window": [d.isoformat() for d in c["window"]],
        "features": {
            k: f"{v:.2f}" if isinstance(v, float) else v
            for k, v in c["features"].items()
        },
        "key": c["key"],
    } | ({"at": c["at"]} if "at" in c else {})


async def discover(c) -> str | None:
    """Worker tool. Proposes findings for `inputs["investigation"]`: suppressed keys
    (open, accepted, rejected) never return, and new evidence for a rejected set is
    noted on its key. Each kind keeps at most FINDINGS_PER_KIND open in the
    investigation; deferred keys are released first, then new ones, by rank."""
    investigation = c.run.inputs["investigation"]
    if not investigation:
        raise ValueError("discover needs an investigation")
    pts = await points(c.s, investigation)
    await c.emit("points_read", points=len(pts))
    found = {}
    secret = await salt(c.s)
    close = await near(c.s, {p["place"] for p in pts})
    for cand in shared_presence(pts, close) + candidate_incidents(pts):
        cand["key"] = key(
            secret,
            cand["kind"],
            investigation,
            cand["people"],
            cand["places"],
            cand["month"],
        )
        found[cand["key"]] = cand
    known = {
        fk.key: fk
        for fk in (
            await c.s.execute(select(FindingKey).where(FindingKey.key.in_(found)))
        ).scalars()
    }
    noted = 0
    for k, fk in known.items():
        if fk.state == "rejected":
            seen = set(fk.members or ()) | set(fk.notes)
            new = [m for m in found[k]["members"] if m not in seen]
            fk.notes = fk.notes + new
            noted += len(new)
    still_open = dict(
        (
            await c.s.execute(
                select(FindingKey.kind, func.count())
                .where(
                    FindingKey.state == "open",
                    FindingKey.investigation == investigation,
                )
                .group_by(FindingKey.kind)
            )
        ).all()
    )
    written, deferred = [], []
    for kind in KINDS:
        budget = max(Config.FINDINGS_PER_KIND - still_open.get(kind, 0), 0)
        pending = sorted(
            (
                cand
                for k, cand in found.items()
                if cand["kind"] == kind
                and (k not in known or known[k].state == "deferred")
            ),
            key=lambda cand: (cand["key"] not in known, *_order(cand)),
        )
        for cand in pending:
            fk = known.get(cand["key"]) or FindingKey(
                key=cand["key"], kind=kind, investigation=investigation, notes=[]
            )
            fk.rank, fk.run_id = rank(cand), c.run.id
            fk.members, fk.features = cand["members"], cand["features"]
            fk.state = "deferred"
            if budget:
                try:
                    st = await write.append(
                        c.s,
                        StatementIn(
                            subject=f"finding:{cand['key']}",
                            prop=Prop.FINDING,
                            value=_value(cand),
                            author_id=c.run.principal_id,
                            run_id=c.run.id,
                        ),
                        sign=c.sign,
                    )
                except Refused as exc:
                    await c.emit("dropped", drop_reason=f"refused: {exc.reason}")
                else:
                    fk.state, fk.statement_id = "open", st.id
                    budget -= 1
                    written.append(str(st.id))
            if fk.state == "deferred":
                deferred.append(cand["key"])
            c.s.add(fk)
    await c.s.flush()
    await c.emit(
        "statements_written",
        candidates=len(found),
        written=len(written),
        deferred=len(deferred),
        noted=noted,
    )
    return chain.sha256(chain.canon({"open": written, "deferred": sorted(deferred)}))
