"""Stories: a person, a group, an incident or a place as dated rows on a timeline and
resolved points on a map. A projection over the statements the monitor and person
pages read, with their masking, erasure and access rules; nothing here writes."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from stk.discovery.models import Segment
from stk.discovery.service import available
from stk.ledger.contracts import Prop, Tier
from stk.ledger.models import Blob, Payload, Statement, StatementTier
from stk.ledger.write import current_clause
from stk.monitor import data
from stk.monitor import filter as mfilter
from stk.people import data as people
from stk.places import resolve
from stk.places.models import Place
from stk.review import findings

LENSES = ("person", "group", "incident", "place", "filter")
LIMIT = 5000
INCIDENT_PROPS = (Prop.INCIDENT, Prop.OCCURRED_AT, Prop.INVOLVED, Prop.SUPPORTED_BY)


def _members(groups: dict, ref) -> frozenset:
    p = people.pid(ref)
    return groups.get(p, frozenset({p}))


def _canon(groups: dict, subject: str) -> str:
    return f"person:{min(_members(groups, subject))}"


def _subjects(groups: dict, refs) -> list[str]:
    return sorted({f"person:{m}" for r in refs for m in _members(groups, r)})


def gaps(points: list[dict], days: int = people.GAP_DAYS) -> list[dict]:
    """Silences longer than `days` between one row's dated points, measured from the
    latest end so far to the next start. Pure."""
    out, end = [], None
    for p in sorted(points, key=lambda p: (p["from"], p["id"])):
        start = date.fromisoformat(p["from"])
        if end and (start - end).days > days:
            out.append(
                {
                    "from": end.isoformat(),
                    "to": p["from"],
                    "days": (start - end).days,
                }
            )
        end = max(end or start, date.fromisoformat(p["to"] or p["from"]))
    return out


async def incidents(db, groups: dict, ids: list[str] | None = None) -> list[dict]:
    """Current incidents built from accepted statements. One stands only while some
    support it names is visible (value kept, source available)."""
    # ponytail: reads every current incident when `ids` is None; filter by involved
    # person in SQL when incidents pass a few thousand.
    q = (
        select(
            Statement.subject,
            Statement.prop,
            Payload.value,
            Statement.valid_from,
            Statement.valid_to,
        )
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.subject.startswith("incident:"),
            Statement.prop.in_(INCIDENT_PROPS),
            StatementTier.tier == Tier.ACCEPTED,
            current_clause(),
        )
        .order_by(Statement.recorded_at)
    )
    if ids is not None:
        q = q.where(Statement.subject.in_(ids))
    found: dict[str, dict] = {}
    for subject, prop, value, lo, hi in (await db.execute(q)).all():
        inc = found.setdefault(
            subject,
            {"id": subject, "title": None, "when": [], "places": [], "people": []}
            | {"support": []},
        )
        if prop == Prop.INCIDENT:
            inc["title"] = data.display(value.get("title"))
        elif prop == Prop.OCCURRED_AT:
            inc["places"].append(value["place"])
            if lo:
                inc["when"].append((lo, hi or lo))
        elif prop == Prop.INVOLVED:
            inc["people"].append(_canon(groups, value["person"]))
        else:
            inc["support"].append(value["statement"])
    support = [uuid.UUID(s) for i in found.values() for s in i["support"]]
    visible = {
        str(sid): src
        for sid, src in (
            await db.execute(
                select(Statement.id, mfilter.source(Statement.span_blob))
                .join(Payload, Payload.commitment == Statement.commitment)
                .where(Statement.id.in_(support), available(Statement.span_blob))
            )
        ).all()
    }
    refs = {p for i in found.values() for p in i["places"]}
    places = {
        f"place:{p.id}": {
            "id": str(p.id),
            "en": p.name_en,
            "ar": p.name_ar,
            "lonlat": [float(p.lon), float(p.lat)],
        }
        for p in (
            await db.execute(
                select(Place).where(
                    Place.id.in_([uuid.UUID(r.removeprefix("place:")) for r in refs])
                )
            )
        ).scalars()
    }
    out = []
    for inc in found.values():
        shown = [s for s in inc["support"] if s in visible]
        if inc["title"] is None or not shown:
            continue
        out.append(
            {
                "id": inc["id"],
                "title": inc["title"],
                "window": [
                    min(lo for lo, _ in inc["when"]).isoformat(),
                    max(hi for _, hi in inc["when"]).isoformat(),
                ]
                if inc["when"]
                else None,
                "places": [
                    places[p] for p in dict.fromkeys(inc["places"]) if p in places
                ],
                "sources": len({visible[s] for s in shown}),
                "people": sorted(set(inc["people"])),
                "statements": shown,
            }
        )
    return out


async def _names(db, groups: dict, subjects: list[str]) -> dict[str, str]:
    """A shown name per canonical person subject (see people.names)."""
    found = await people.names(db, [people.pid(x) for x in subjects], groups)
    return {_canon(groups, f"person:{p}"): name for p, name in found.items()}


async def links(db, rows: list[dict]) -> dict[str, dict]:
    """Per located row: its acquired source, mime, and the evidence link that opens
    the exact page, or the indexed segment holding its characters (line or second)."""
    spans = {r["doc"]["sha256"] for r in rows if r["doc"]}
    if not spans:
        return {}
    source = dict(
        (
            await db.execute(
                select(Blob.sha256, mfilter.source(Blob.sha256)).where(
                    Blob.sha256.in_(spans)
                )
            )
        ).all()
    )
    mime = dict(
        (
            await db.execute(
                select(Blob.sha256, Blob.mime).where(
                    Blob.sha256.in_(set(source.values()))
                )
            )
        ).all()
    )
    segs: dict[str, list] = {}
    for seg in (
        await db.execute(select(Segment).where(Segment.text_sha256.in_(spans)))
    ).scalars():
        segs.setdefault(seg.text_sha256, []).append(seg)
    ctx = await data.Context.load(db, blobs=set(source.values()))
    out = {}
    for r in rows:
        if not r["doc"]:
            continue
        sha, span = r["doc"]["sha256"], r["span"] or {}
        src = source.get(sha, sha)
        link = {
            "source": src,
            "mime": mime.get(src),
            "file": ctx.doc(src)["title"],
            "sensitive_source": ctx.doc(src)["sensitive"],
            "href": f"/evidence/{src}",
            "segment": None,
        }
        if "page" in span:
            link["href"] += f"?page={span['page']}"
        elif "start" in span:
            hit = next(
                (
                    s
                    for s in segs.get(sha, [])
                    if s.start <= span["start"] < (s.end or 0)
                ),
                None,
            )
            if hit:
                end = min(span["end"], hit.end)
                link["segment"] = {
                    "id": str(hit.id),
                    "start": span["start"],
                    "end": end,
                }
                link["href"] += f"?segment={hit.id}&start={span['start']}&end={end}"
        out[r["id"]] = link
    return out


async def story(db, user, lens: str, ref: str | None, f: dict) -> dict:
    """The story for one lens under the monitor filter `f`. ValueError for an unknown
    lens or a malformed ref, LookupError for a ref that names nothing."""
    if lens not in LENSES:
        raise ValueError(f"lens must be one of {', '.join(LENSES)}")
    groups, _ = await people.identity(db)
    where = [
        Statement.prop.in_(data.LOCATED),
        Statement.subject.startswith("person:"),
        StatementTier.tier != Tier.REJECTED,
        current_clause(),
        mfilter.statement_clause(f),
        available(Statement.span_blob),
    ]
    involved, story_incidents, place, title = [], [], None, None
    if lens == "person" or (lens == "group" and ref):
        refs = [r for r in (ref or "").split(",") if r.strip()]
        if not refs:
            raise ValueError("a person story needs a person id")
        try:
            subjects = _subjects(groups, refs)
        except ValueError:
            raise ValueError("person ids must be UUIDs") from None
        where.append(Statement.subject.in_(subjects))
    elif lens == "incident":
        if not ref or not ref.startswith("incident:"):
            raise ValueError("an incident story needs incident:<uuid>")
        story_incidents = await incidents(db, groups, [ref])
        if not story_incidents:
            raise LookupError("no current incident with that id that you may see")
        involved = story_incidents[0]["people"]
        title = story_incidents[0]["title"]
        where.append(Statement.subject.in_(_subjects(groups, involved)))
    elif lens == "place":
        try:
            place = await db.get(Place, uuid.UUID(ref or ""))
        except ValueError:
            raise ValueError("a place story needs a place id") from None
        if place is None:
            raise LookupError("no such place")
        title = place.name_en or place.name_ar

    rows = await data.statement_rows(
        db, *where, limit=LIMIT, order=Statement.valid_from
    )
    truncated = len(rows) >= LIMIT
    # An erased value is gone from every view; a masked one is never a point.
    rows = [r for r in rows if not r["erased"] and not r["masked"] and r["display"]]
    resolved = await resolve.resolved(
        db, [uuid.UUID(r["id"]) for r in rows], f["investigation"]
    )
    if place is not None:
        rows = [
            r
            for r in rows
            if (p := resolved[uuid.UUID(r["id"])]) is not None and p.id == place.id
        ]
    rows = [r | {"subject": _canon(groups, r["subject"])} for r in rows]
    layer = data.map_layer(rows, resolved)
    on_map = {p["id"]: p for p in layer["points"]}
    hrefs = await links(db, rows)
    points = []
    for r in rows:
        if not r["from"]:
            continue  # undated claims never appear on a story
        mapped = on_map.get(r["id"])
        resolved_place = resolved[uuid.UUID(r["id"])]
        points.append(
            {
                "id": r["id"],
                "subject": r["subject"],
                "prop": r["prop"],
                "place": mapped["place"] if mapped else r["display"],
                "place_id": str(resolved_place.id) if mapped else None,
                "lonlat": mapped["lonlat"] if mapped else None,
                "from": r["from"],
                "to": r["to"] or r["from"],
                "when": r["when"],
                "inherited": r["inherited"],
                "tier": r["tier"],
                "agent": r["actor"]["agent"],
                "actor": r["actor"]["label"],
                "sensitive": bool(r["doc"] and r["doc"]["sensitive"]),
            }
            | hrefs.get(r["id"], {})
        )

    by_row = data.tracks(points)
    mine: dict[str, list] = {}
    for p in points:
        mine.setdefault(p["subject"], []).append(p)
    keys = list(by_row) + [k for k in involved if k not in by_row]
    keys.sort(key=lambda k: (by_row[k]["steps"][0]["date"] if k in by_row else "~", k))
    names = await _names(db, groups, _subjects(groups, keys))
    # People whose only names are masked get a neutral label, numbered in row order.
    unnamed = [k for k in keys if k not in names]
    story_rows = [
        {
            "key": k,
            "label": names.get(k) or f"Unnamed person {unnamed.index(k) + 1}",
            "named": k in names,
            "steps": by_row[k]["steps"] if k in by_row else [],
            "gaps": gaps(mine.get(k, [])),
        }
        for k in keys
    ]
    ids = {p["id"] for p in points}
    if lens != "incident":
        story_incidents = [
            i for i in await incidents(db, groups) if set(i["people"]) & set(keys)
        ]
    suggestions = [
        c
        for c in await findings.queue(db, user, f["investigation"])
        if set(c["people"]) & set(keys) or {m["statement"] for m in c["members"]} & ids
    ]
    if title is None:
        if lens == "person":
            title = story_rows[0]["label"] if story_rows else "Person"
        elif lens == "group" and ref:
            title = f"{len(story_rows)} people"
        else:
            title = f["investigation"] or "All investigations"
    return {
        "lens": lens,
        "ref": ref,
        "filter": mfilter.dump(f),
        "title": title,
        "rows": story_rows,
        "points": points,
        "incidents": story_incidents,
        "suggestions": suggestions,
        "unresolved": layer["unresolved"],
        "attribution": layer["attribution"],
        "truncated": truncated,
    }
