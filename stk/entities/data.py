"""Entity pages: one place or one incident with its story, relations, evidence,
threads and history. A projection over the statements the story, findings and
evidence pages read, with their masking, erasure and access rules; nothing here
writes except the access records the story view already leaves."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date

import orjson
from sqlalchemy import Text, and_, case, cast, func, or_, select

from stk.discovery.evidence import REVIEW_PROPS, granted, threads
from stk.discovery.models import FindingKey
from stk.discovery.service import available
from stk.ledger.contracts import Prop, Tier
from stk.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Principal,
    Statement,
    StatementTier,
)
from stk.ledger.write import current_clause
from stk.monitor import data
from stk.monitor import filter as mfilter
from stk.people import data as people
from stk.places import normalise, resolve
from stk.places.models import AdminArea, Place, PlaceKind, PlaceName
from stk.review import findings
from stk.stories import data as stories

TIERS = ("accepted", "corroborated", "unconfirmed")
EVIDENCE_SORTS = ("claims", "tier", "title")
PLACE_SORTS = ("name", "kind", "governorate", "claims", "people", "sources", "last")
NEARBY_M = 5000
SAMPLE = 20  # claims shown per unresolved name


def _place_ref(place_id) -> str:
    return f"place:{place_id}"


def _pid(ref: str) -> uuid.UUID:
    return uuid.UUID(ref.removeprefix("place:"))


async def located(db, investigation: str | None) -> list[tuple[dict, Place | None]]:
    """Every visible position claim about a person with its resolved place, under
    the story view's rules: not rejected, current, source available, value kept."""
    # ponytail: resolves up to stories.LIMIT claims per request; keep a resolved
    # place column per claim once cases pass that.
    rows = await data.statement_rows(
        db,
        Statement.prop.in_(data.LOCATED),
        Statement.subject.startswith("person:"),
        StatementTier.tier != Tier.REJECTED,
        current_clause(),
        mfilter.statement_clause({"investigation": investigation, "time": None}),
        available(Statement.span_blob),
        limit=stories.LIMIT,
        order=Statement.valid_from,
    )
    rows = [r for r in rows if not r["erased"] and not r["masked"] and r["display"]]
    places = await resolve.resolved(
        db, [uuid.UUID(r["id"]) for r in rows], investigation
    )
    return [(r, places[uuid.UUID(r["id"])]) for r in rows]


# --- evidence ------------------------------------------------------------------


async def evidence(
    db, ids: list[str], sort: str, desc: bool, page: int, per_page: int
) -> dict:
    """The sources behind these claims, one row per acquired source, paged in SQL:
    file, kind, how many of the claims it holds, the best tier among them and the
    first claim's span with the link that opens the source there."""
    if sort not in EVIDENCE_SORTS:
        raise ValueError(f"sort must be one of {', '.join(EVIDENCE_SORTS)}")
    if not ids:
        return {"items": [], "total": 0}
    src = mfilter.source(Statement.span_blob).label("src")
    rank = case(
        (StatementTier.tier == Tier.ACCEPTED, 0),
        (StatementTier.tier == Tier.CORROBORATED, 1),
        else_=2,
    )
    grouped = (
        select(
            src,
            func.count().label("claims"),
            func.min(rank).label("rank"),
            func.min(cast(Statement.id, Text)).label("first"),
        )
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.id.in_([uuid.UUID(i) for i in ids]),
            Statement.span_blob.is_not(None),
        )
        .group_by(src)
        .subquery()
    )
    title = (
        select(
            func.min(
                func.coalesce(
                    Acquisition.context["title"].astext,
                    Acquisition.context["file"].astext,
                    Acquisition.context["filename"].astext,
                )
            )
        )
        .where(Acquisition.blob_sha256 == grouped.c.src)
        .scalar_subquery()
    )
    key = {"claims": grouped.c.claims, "tier": grouped.c.rank, "title": title}[sort]
    # Tier sorts best first, as claims sort most first; desc flips either.
    descending = desc if sort == "title" else desc == (sort == "claims")
    total = await db.scalar(select(func.count()).select_from(grouped))
    rows = (
        await db.execute(
            select(grouped)
            .order_by(key.desc() if descending else key.asc(), grouped.c.src)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()
    shas = [r.src for r in rows]
    q = select(Blob.sha256, Blob.mime).where(Blob.sha256.in_(shas))
    mimes = dict((await db.execute(q)).all())
    ctx = await data.Context.load(db, blobs=shas)
    first = await data.statement_rows(
        db, Statement.id.in_([uuid.UUID(r.first) for r in rows]), limit=per_page
    )
    hrefs = await stories.links(db, first)
    spans = {r["id"]: r for r in first}
    items = []
    for r in rows:
        doc, claim = ctx.doc(r.src), spans.get(r.first)
        link = hrefs.get(r.first, {})
        items.append(
            {
                "sha256": r.src,
                "file": doc["title"],
                "doc_type": doc["doc_type"],
                "mime": mimes.get(r.src),
                "sensitive": doc["sensitive"],
                # As on Sources: without a held reveal the tile stays locked.
                "revealed": granted(r.src) or not doc["sensitive"],
                "claims": r.claims,
                "tier": TIERS[r.rank],
                "span": claim["span"] if claim else None,
                "prop": claim["prop"] if claim else None,
                "href": link.get("href") or f"/evidence/{r.src}",
            }
        )
    return {"items": items, "total": total}


# --- threads and history -------------------------------------------------------


async def history(db, ids: list[str], subject: str | None = None) -> list[dict]:
    """Decisions, confirmations and undos on these statements, plus the statements
    of `subject` itself (an incident), newest first, each with its gesture."""
    where = and_(
        Statement.prop.in_(REVIEW_PROPS),
        Statement.subject.in_([f"statement:{i}" for i in ids]),
    )
    rows = await data.statement_rows(
        db,
        where if subject is None else or_(where, Statement.subject == subject),
        limit=300,
    )
    refs = {
        i: rest
        for i, *rest in (
            await db.execute(
                select(
                    Statement.id,
                    Payload.value["gesture"].astext,
                    Payload.value["person"].astext,
                    Payload.value["place"].astext,
                )
                .join(Payload, Payload.commitment == Statement.commitment)
                .where(Statement.id.in_([uuid.UUID(r["id"]) for r in rows]))
            )
        ).all()
    }
    out = []
    for r in rows:
        gesture, person, place = refs.get(uuid.UUID(r["id"]), (None, None, None))
        out.append(r | {"gesture": gesture, "person": person, "place": place})
    return out


async def undoable(db, user, rows: list[dict]) -> list[str]:
    """Gestures in `rows` this reader signed and that still have an accepted row:
    the ones /api/review/undo will take from them."""
    mine = {
        str(p)
        for p in await db.scalars(
            select(Principal.id).where(Principal.user_id == user.id)
        )
    }
    return sorted(
        {
            r["gesture"]
            for r in rows
            if r["gesture"]
            and r["tier"] == Tier.ACCEPTED
            and r["prop"] != Prop.RETRACTION
            and r["actor"]["principal"] in mine
        }
    )


# --- shared pieces -------------------------------------------------------------


def _people(story: dict) -> list[dict]:
    """Story rows as related people: when they are placed in this story and how firmly."""
    pts: dict[str, list] = {}
    for p in story["points"]:
        pts.setdefault(p["subject"], []).append(p)
    out = []
    for row in story["rows"]:
        mine = pts.get(row["key"], [])
        best = min((TIERS.index(p["tier"]) for p in mine), default=None)
        out.append(
            {
                "person": row["key"].removeprefix("person:"),
                "label": row["label"],
                "named": row["named"],
                "points": len(mine),
                "from": min((p["from"] for p in mine), default=None),
                "to": max((p["to"] for p in mine), default=None),
                "tier": TIERS[best] if best is not None else None,
            }
        )
    return out


async def _place_rows(db, ids) -> dict[uuid.UUID, dict]:
    """Name, kind and governorate per place id for lists of related places."""
    rows = (
        (await db.execute(select(Place).where(Place.id.in_(list(ids))))).scalars().all()
    )
    areas = await _areas(db, {p.admin1_id for p in rows})
    return {
        p.id: {
            "id": str(p.id),
            "name_ar": p.name_ar,
            "name_en": p.name_en,
            "kind": p.kind,
            "governorate": areas.get(p.admin1_id),
        }
        for p in rows
    }


async def _areas(db, ids) -> dict:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = await db.execute(
        select(AdminArea.id, AdminArea.name_ar, AdminArea.name_en).where(
            AdminArea.id.in_(ids)
        )
    )
    return {i: {"ar": ar, "en": en} for i, ar, en in rows}


def _incident_row(inc: dict, reason: str) -> dict:
    return {
        "id": inc["id"].removeprefix("incident:"),
        "title": inc["title"],
        "window": inc["window"],
        "sources": inc["sources"],
        "people": len(inc["people"]),
        "reason": reason,
        "tier": Tier.ACCEPTED,
    }


def _finding_row(f: dict) -> dict:
    return {
        "key": f["id"],
        "label": f["label"],
        "reason": f["reason"],
        "why": f["why"],
        "band": f["band"],
    }


# --- places --------------------------------------------------------------------


def _stats(found: list[tuple[dict, Place | None]], groups: dict) -> dict:
    out: dict[uuid.UUID, dict] = {}
    for r, place in found:
        if place is None:
            continue
        s = out.setdefault(
            place.id,
            {"claims": 0, "people": set(), "sources": set(), "from": None, "to": None},
        )
        s["claims"] += 1
        s["people"].add(stories._canon(groups, r["subject"]))
        s["sources"].add(r["doc"]["sha256"] if r["doc"] else None)
        if r["from"]:
            s["from"] = min(s["from"] or r["from"], r["from"])
            s["to"] = max(s["to"] or r["to"], r["to"])
    return out


async def place_table(db, f: dict, args: dict) -> dict:
    """Places with how much evidence resolves to each, paged and sorted on the
    server. By default only places something resolves to or an incident names."""
    sort, desc = args["sort"], args["desc"]
    if sort not in PLACE_SORTS:
        raise ValueError(f"sort must be one of {', '.join(PLACE_SORTS)}")
    groups, _ = await people.identity(db)
    stats = _stats(await located(db, f["investigation"]), groups)
    incidents: Counter = Counter()
    for inc in await stories.incidents(db, groups):
        for p in inc["places"]:
            incidents[uuid.UUID(p["id"])] += 1
    q = select(Place)
    if text := args["q"]:
        q = q.where(
            Place.id.in_(
                select(PlaceName.place_id).where(
                    PlaceName.norm.contains(normalise(text), autoescape=True)
                )
            )
        )
    if not args["all"]:
        q = q.where(Place.id.in_(list(stats) + list(incidents)))
    # ponytail: sorts and pages the matching places in Python; move the counts to
    # a projection table when the gazetteer filter returns tens of thousands.
    places = (await db.execute(q)).scalars().all()
    areas = await _areas(
        db, {p.admin1_id for p in places} | {p.admin2_id for p in places}
    )
    kinds = dict((await db.execute(select(PlaceKind.code, PlaceKind.label))).all())
    rows = []
    for p in places:
        s = stats.get(p.id, {})
        gov = areas.get(p.admin1_id) or {}
        rows.append(
            {
                "id": str(p.id),
                "name_ar": p.name_ar,
                "name_en": p.name_en,
                "kind": p.kind,
                "kind_label": kinds.get(p.kind, p.kind),
                "governorate": gov.get("en") or gov.get("ar"),
                "district": (areas.get(p.admin2_id) or {}).get("en"),
                "precision_m": p.precision_m,
                "claims": s.get("claims", 0),
                "people": len(s.get("people", ())),
                "sources": len(s.get("sources", ())),
                "incidents": incidents.get(p.id, 0),
                "from": s.get("from"),
                "last": s.get("to"),
            }
        )
    key = {
        "name": lambda r: ((r["name_en"] or r["name_ar"]).casefold(),),
        "kind": lambda r: (r["kind_label"],),
        "governorate": lambda r: (r["governorate"] or "~",),
        "last": lambda r: (r["last"] or "",),
    }.get(sort, lambda r: (r[sort],))
    rows.sort(key=lambda r: (*key(r), r["id"]), reverse=desc)
    lo = (args["page"] - 1) * args["per_page"]
    return {"items": rows[lo : lo + args["per_page"]], "total": len(rows)}


async def unresolved(db, f: dict, place: Place | None = None) -> list[dict]:
    """Position claims no place resolves, one row per spelling (normalised), most
    claims first, with the gazetteer candidates for that spelling and a sample of
    claims with the link to their span. With `place`, only spellings it is a
    candidate for."""
    found = [r for r, p in await located(db, f["investigation"]) if p is None]
    by: dict[str, list] = {}
    for r in found:
        by.setdefault(normalise(r["display"]), []).append(r)
    if place is not None:
        mine = set(
            await db.scalars(
                select(PlaceName.norm).where(PlaceName.place_id == place.id)
            )
        )
        by = {k: v for k, v in by.items() if k in mine}
    cands = await resolve.candidates_many(db, by)
    shown = [r for rows in by.values() for r in rows[:SAMPLE]]
    hrefs = await stories.links(db, shown)
    proposed = await _proposals(db, [r["id"] for r in shown])
    placed = await _place_rows(db, {c.id for cs in cands.values() for c in cs})
    out = []
    for norm, rows in by.items():
        out.append(
            {
                "norm": norm,
                "name": Counter(r["display"] for r in rows).most_common(1)[0][0],
                "count": len(rows),
                "candidates": [placed[c.id] for c in cands.get(norm, [])],
                "claims": [
                    {
                        "id": r["id"],
                        "prop": r["prop"],
                        "when": r["when"],
                        "tier": r["tier"],
                        "file": r["doc"]["file"] if r["doc"] else None,
                        "href": hrefs.get(r["id"], {}).get("href"),
                        "proposal": proposed.get(r["id"]),
                    }
                    for r in rows[:SAMPLE]
                ],
            }
        )
    return sorted(out, key=lambda u: (-u["count"], u["norm"]))


async def _proposals(db, ids: list[str]) -> dict[str, dict]:
    """Pending located_at proposals (an agent's or a person's) on these claims: a
    second link for the same claim is refused, so the reader decides this one."""
    if not ids:
        return {}
    rows = await db.execute(
        select(Statement.id, Statement.subject, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.prop == Prop.LOCATED_AT,
            Statement.subject.in_([f"statement:{i}" for i in ids]),
            StatementTier.tier == Tier.UNCONFIRMED,
            current_clause(),
        )
    )
    found = [(sid, s.removeprefix("statement:"), v) for sid, s, v in rows]
    places = await _place_rows(db, {_pid(v["place"]) for *_, v in found})
    return {
        claim: {"id": str(sid), "place": places.get(_pid(v["place"]))}
        for sid, claim, v in found
    }


async def place(db, user, place_id: uuid.UUID, f: dict) -> dict:
    """One place: its names over time and the spellings mapped to it, kind,
    precision, source and boundary areas, geometry, the place story, and the
    people, incidents, findings and nearby places related to it. LookupError when
    there is no such place."""
    p = await db.get(Place, place_id)
    if p is None:
        raise LookupError("no such place")
    story = await stories.story(db, user, "place", str(place_id), f)
    groups, _ = await people.identity(db)
    here = [
        r for r, at in await located(db, f["investigation"]) if at and at.id == p.id
    ]
    names = (
        (await db.execute(select(PlaceName).where(PlaceName.place_id == p.id)))
        .scalars()
        .all()
    )
    known = {n.norm for n in names}
    spellings = Counter(r["display"] for r in here)
    areas = await _areas(db, {p.admin1_id, p.admin2_id})
    kind = await db.get(PlaceKind, p.kind)
    geom = await db.scalar(
        select(func.ST_AsGeoJSON(Place.__table__.c.geom)).where(Place.id == p.id)
    )
    here_c = (
        select(Place.__table__.c.centroid).where(Place.id == p.id).scalar_subquery()
    )
    near = (
        await db.execute(
            select(
                Place.id,
                func.ST_Distance(Place.__table__.c.centroid, here_c).label("m"),
            )
            .where(
                Place.id != p.id,
                # Admin areas contain places; their centroids are not near anything.
                Place.kind.in_(
                    select(PlaceKind.code).where(PlaceKind.family != "admin")
                ),
                func.ST_DWithin(Place.__table__.c.centroid, here_c, NEARBY_M),
            )
            .order_by("m")
            .limit(6)
        )
    ).all()
    nearby = await _place_rows(db, [i for i, _ in near])
    incidents = [
        _incident_row(i, f"occurred here, {data.when(*_window(i)) or 'undated'}")
        for i in await stories.incidents(db, groups)
        if any(x["id"] == str(p.id) for x in i["places"])
    ]
    ref = str(p.id)
    finds = [
        _finding_row(s)
        for s in story["suggestions"]
        if any(x.get("id") == ref for x in s["places"])
    ]
    ids = [r["id"] for r in here]
    hist = await history(db, ids)
    # Each decision names the claim it was about, so grouped decisions can list them.
    labels = {r["key"]: r["label"] for r in story["rows"]}
    claims = {r["id"]: r for r in here}
    for h in hist:
        if c := claims.get(h["subject"].removeprefix("statement:")):
            who = labels.get(stories._canon(groups, c["subject"]), "a person")
            h["about"] = f"{who}, {c['when']}" if c["when"] else who
    return {
        "id": ref,
        "name_ar": p.name_ar,
        "name_en": p.name_en,
        "kind": p.kind,
        "kind_label": kind.label if kind else p.kind,
        "family": kind.family if kind else None,
        "precision_m": p.precision_m,
        "geo_source": p.geo_source,
        "source": p.source,
        "lonlat": [float(p.lon), float(p.lat)],
        "geometry": orjson.loads(geom),
        "governorate": areas.get(p.admin1_id),
        "district": areas.get(p.admin2_id),
        "names": sorted(
            (
                {
                    "name": n.name,
                    "lang": n.lang,
                    "kind": n.kind,
                    "from": n.valid_from.isoformat() if n.valid_from else None,
                    "to": n.valid_to.isoformat() if n.valid_to else None,
                    "source": n.source,
                }
                for n in names
            ),
            key=lambda n: (n["from"] or "", n["lang"], n["name"]),
        ),
        "spellings": [
            {"name": s, "claims": n, "gazetteer": normalise(s) in known}
            for s, n in spellings.most_common()
        ],
        "unresolved": await unresolved(db, f, p),
        "claims": len(here),
        "sources": len({r["doc"]["sha256"] for r in here if r["doc"]}),
        "story": story,
        "relations": {
            "people": _people(story),
            "incidents": incidents,
            "findings": finds,
            "places": [
                nearby[i] | {"reason": f"{findings.metres(int(m))} away", "m": int(m)}
                for i, m in near
                if i in nearby
            ],
        },
        "threads": await threads(db, ids, user),
        "history": hist,
        "attribution": story["attribution"],
    }


def _window(inc: dict):
    if not inc["window"]:
        return (None, None)
    return tuple(date.fromisoformat(d) for d in inc["window"])


async def place_claims(db, place_id: uuid.UUID, f: dict) -> list[str]:
    return [
        r["id"]
        for r, at in await located(db, f["investigation"])
        if at and at.id == place_id
    ]


# --- incidents -----------------------------------------------------------------


async def incident(db, user, ref: str) -> dict:
    """One accepted incident: title and kind, when and where from its accepted
    statements, people by role, the findings whose acceptance built it, related
    incidents, threads and the signed history with the gestures this reader may
    undo. LookupError when it is not current or not visible."""
    f = {"investigation": None, "time": None}
    story = await stories.story(db, user, "incident", ref, f)
    inc = story["incidents"][0]
    groups, _ = await people.identity(db)
    rows = (
        await db.execute(
            select(Statement, Payload.value, StatementTier.tier)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(Statement.subject == ref, Statement.prop.in_(stories.INCIDENT_PROPS))
            .order_by(Statement.recorded_at)
        )
    ).all()
    current = [(st, v) for st, v, t in rows if t == Tier.ACCEPTED and v is not None]
    kind = next((v.get("kind") for st, v in current if st.prop == Prop.INCIDENT), None)
    labels = {r["key"]: r for r in story["rows"]}
    involved: dict[str, dict] = {}
    for st, v in current:
        if st.prop != Prop.INVOLVED:
            continue
        key = stories._canon(groups, v["person"])
        row = labels.get(key, {})
        involved.setdefault(
            key,
            {
                "person": key.removeprefix("person:"),
                "label": row.get("label", "Unnamed person"),
                "named": row.get("named", False),
                "roles": [],
                "statement": str(st.id),
            },
        )["roles"].append(v["role"])
    gestures = {v.get("gesture") for _, v in current if v.get("gesture")}
    built = []
    if gestures:
        reviews = (
            await db.execute(
                select(Statement.subject)
                .join(Payload, Payload.commitment == Statement.commitment)
                .where(
                    Statement.prop == Prop.REVIEW,
                    Payload.value["gesture"].astext.in_(gestures),
                )
            )
        ).scalars()
        fids = [uuid.UUID(s.removeprefix("statement:")) for s in reviews]
        found = (
            await db.execute(
                select(FindingKey, Payload.value)
                .join(Statement, Statement.id == FindingKey.statement_id)
                .outerjoin(Payload, Payload.commitment == Statement.commitment)
                .where(FindingKey.statement_id.in_(fids))
            )
        ).all()
        names = await _place_rows(
            db, {_pid(p) for _, v in found if v for p in v["places"]}
        )
        for fk, v in found:
            if v is None:
                continue  # erased: the key and state remain, the reason does not
            pl = [names.get(_pid(p), {}) for p in v["places"]]
            built.append(
                {
                    "key": fk.key,
                    "label": findings.KINDS.get(fk.kind, fk.kind),
                    "state": fk.state,
                    "reason": findings.reason(
                        fk.kind,
                        len(v["people"]),
                        [
                            x.get("name_en") or x.get("name_ar") or "an unnamed place"
                            for x in pl
                        ],
                        v["month"],
                        v["features"],
                    ),
                    "why": findings.why(fk.kind, v["features"]),
                    "band": findings.band(fk.kind, v["features"]),
                    "investigation": fk.investigation,
                }
            )
    mine = set(inc["people"])
    related = []
    for other in await stories.incidents(db, groups):
        if other["id"] == ref:
            continue
        shared = len(mine & set(other["people"]))
        same = {x["id"] for x in other["places"]} & {x["id"] for x in inc["places"]}
        if shared or same:
            why = (
                [f"{shared} {'person' if shared == 1 else 'people'} in common"]
                if shared
                else []
            )
            why += ["same place"] if same else []
            related.append(_incident_row(other, ", ".join(why)))
    window = data.when(*_window(inc))
    ids = [str(st.id) for st, _, _ in rows] + [
        str(fk)
        for fk in await db.scalars(
            select(FindingKey.statement_id).where(
                FindingKey.key.in_([b["key"] for b in built])
            )
        )
    ]
    hist = await history(db, ids, ref)
    for h in hist:
        if h["person"]:
            h["person"] = stories._canon(groups, h["person"]).removeprefix("person:")
    signed = next((st for st, _ in current if st.prop == Prop.INCIDENT), None)
    ctx = await data.Context.load(db, principals=[signed.author_id] if signed else [])
    return {
        "id": ref.removeprefix("incident:"),
        "title": inc["title"],
        "kind": kind,
        "window": inc["window"],
        "when": window,
        "places": inc["places"],
        "sources": inc["sources"],
        "support": len(inc["statements"]),
        "signed": {
            "by": ctx.principal(signed.author_id)["name"],
            "at": signed.recorded_at.isoformat(),
        }
        if signed
        else None,
        "story": story,
        "relations": {
            "people": [
                v
                | next(
                    (
                        {k: x[k] for k in ("points", "from", "to", "tier")}
                        for x in _people(story)
                        if x["person"] == v["person"]
                    ),
                    {"points": 0, "from": None, "to": None, "tier": None},
                )
                for v in involved.values()
            ],
            "places": [
                p | {"reason": f"occurred here, {window or 'undated'}"}
                for p in inc["places"]
            ],
            "incidents": related,
            "findings": built,
        },
        "threads": await threads(db, ids, user),
        "history": hist,
        "undoable": await undoable(db, user, hist),
    }


async def incident_claims(db, ref: str) -> list[str]:
    groups, _ = await people.identity(db)
    found = await stories.incidents(db, groups, [ref])
    if not found:
        raise LookupError("no current incident with that id that you may see")
    return found[0]["statements"]
