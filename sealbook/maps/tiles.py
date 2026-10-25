"""Vector tiles from PostGIS: located claims a reader may see, at their resolved places.

Built per request from visible statements only: the value is kept (not erased), the
source is available, the claim is current and in the filter, its date is its own or
its document's, and it resolves to a place by the rules of `resolve.resolved`.
Position props are never personal data (see PERSONAL), so nothing here needs a
mask. Tiles carry ids, tiers and gazetteer geometry, never claim text."""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass

import orjson
from sqlalchemy import Text, cast, func, literal, select, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import aliased

from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import (
    Acquisition,
    Chain,
    Payload,
    Principal,
    Statement,
    StatementTier,
)
from sealbook.ledger.write import current_clause
from sealbook.monitor import filter as mfilter
from sealbook.monitor.data import LOCATED, PERSONAL
from sealbook.places.models import Place, PlaceName
from sealbook.search.arabic import _FROM, _TO

assert not LOCATED & PERSONAL, "located claims would need masking in tiles"

EXTENT, BUFFER = 4096, 64
# Below CLUSTER_BELOW, claims in one of CELLS x CELLS cells per tile (about 51
# screen pixels on a 256 pixel tile) merge.
CLUSTER_BELOW, CELLS, IDS_PER_CLUSTER = 11, 5, 200
SEQUENCES_FROM, UNCERTAINTY_FROM, FOOTPRINTS_FROM = 8, 8, 10
WORLD = 40075016.685578488  # Web Mercator width in metres
TIERS = (Tier.ACCEPTED, Tier.CORROBORATED, Tier.UNCONFIRMED, Tier.REJECTED)
ID = UUID(as_uuid=True)


def norm(value):
    """SQL twin of `sealbook.places.normalise`: Arabic folding, whitespace collapsed,
    trimmed, lower case.
    ponytail: Postgres and Python disagree on a few Unicode spaces; store the norm
    on the claim if that ever splits a confirmed variant."""
    folded = func.translate(value, literal(_FROM), literal(_TO))
    return func.lower(func.btrim(func.regexp_replace(folded, r"\s+", " ", "g")))


def _ref(expr, prefix: str):
    return cast(func.substr(expr, len(prefix) + 1), ID)


def scope(place):
    """SQL twin of `resolve.scope`."""
    return func.coalesce(place.admin1_id, place.governorate_id)


def _current(st):
    """`current_clause` for an aliased statement: no accepted correction of it."""
    newer, tier = aliased(Statement), aliased(StatementTier)
    return ~(
        select(newer.id)
        .join(tier, tier.statement_id == newer.id)
        .where(newer.supersedes == st.id, tier.tier == Tier.ACCEPTED)
        .correlate(st)
        .exists()
    )


def _context():
    """Per-request CTEs, each one scan with hash joins, replacing the per-row
    lookups of the monitor filter: derived-to-source edges, each blob's source and
    investigations, erased roots, document dates and accepted place links."""
    d, dv = aliased(Statement), aliased(Payload)
    derived = (
        select(
            func.substr(d.subject, 6).label("child"),
            dv.value["blob"].astext.label("parent"),
        )
        .join(dv, dv.commitment == d.commitment)
        .where(d.prop == Prop.DERIVED_FROM, d.subject.startswith("blob:"))
        .cte("derived")
    )
    # `mfilter.source`: a derived blob's acquired parent, else the blob itself.
    parent = (
        select(derived.c.child, func.min(derived.c.parent).label("parent"))
        .group_by(derived.c.child)
        .cte("parent")
    )
    acq = (
        select(
            Acquisition.blob_sha256.label("blob"),
            Acquisition.context["investigation"].astext.label("inv"),
        )
        .distinct()
        .cte("acq")
    )
    # `discovery.service.available`: a blob is out when it or a parent is a span
    # of an erased statement, or the parent of such a span.
    e, ev = aliased(Statement), aliased(Payload)
    erased = (
        select(e.span_blob.label("blob"))
        .outerjoin(ev, ev.commitment == e.commitment)
        .where(e.span_blob.is_not(None), ev.commitment.is_(None))
        .distinct()
        .cte("erased")
    )
    bad = (
        select(erased.c.blob)
        .union(select(derived.c.parent).join(erased, erased.c.blob == derived.c.child))
        .cte("bad")
    )
    dd, dt = aliased(Statement), aliased(StatementTier)
    doc_day = (
        select(
            func.substr(dd.subject, 6).label("blob"),
            func.min(dd.valid_from).label("day"),
        )
        .join(dt, dt.statement_id == dd.id)
        .where(
            dd.prop == Prop.DOCUMENT_DATE,
            dd.subject.startswith("blob:"),
            dd.valid_from.is_not(None),
            dt.tier != Tier.REJECTED,
        )
        .group_by(dd.subject)
        .cte("doc_day")
    )
    link, lv, lt = aliased(Statement), aliased(Payload), aliased(StatementTier)
    links = (
        select(
            _ref(link.subject, "statement:").label("claim"),
            _ref(lv.value["place"].astext, "place:").label("place"),
            lv.value["name"].astext.label("name"),
            link.recorded_at,
            link.id,
        )
        .join(lv, lv.commitment == link.commitment)
        .join(lt, lt.statement_id == link.id)
        .where(
            link.prop == Prop.LOCATED_AT,
            link.subject.startswith("statement:"),
            lt.tier == Tier.ACCEPTED,
            _current(link),
        )
        .cte("links")
        .prefix_with("MATERIALIZED")
    )
    return derived, parent, acq, bad, doc_day, links


def _source(parent, blob):
    return func.coalesce(parent.c.parent, blob)


def _window(parent, window):
    """Source blobs with a dated, unrejected statement overlapping `window`: the
    time half of `mfilter.blob_clause`."""
    st, tier = aliased(Statement), aliased(StatementTier)
    p = aliased(parent)
    blob = mfilter.statement_blob(st)
    lo, hi = window
    return (
        select(_source(p, blob).label("blob"))
        .select_from(st)
        .outerjoin(p, p.c.child == blob)
        .where(
            st.valid_from.is_not(None),
            st.valid_from <= hi,
            func.coalesce(st.valid_to, st.valid_from) >= lo,
            ~select(tier.statement_id)
            .where(tier.statement_id == st.id, tier.tier == Tier.REJECTED)
            .exists(),
        )
        .distinct()
        .cte("in_window")
    )


def claims(f: dict, tiers, subjects=None, ids=None):
    """Visible located claims with their resolved place (NULL when unresolved):
    id, subject, prop, tier, agent, day, place. Set-based, so its cost grows with
    the case, not with claims times ledger."""
    derived, parent, acq, bad, doc_day, links = _context()
    st = Statement
    blob = mfilter.statement_blob(st)
    src = _source(parent, blob)
    own = select(acq.c.blob, func.min(acq.c.inv).label("inv")).group_by(acq.c.blob)
    own = own.cte("own").prefix_with("MATERIALIZED")
    direct = (
        select(links.c.claim, links.c.place)
        .distinct(links.c.claim)
        .order_by(links.c.claim, links.c.recorded_at.desc(), links.c.id.desc())
        .cte("direct")
        .prefix_with("MATERIALIZED")
    )
    q = (
        select(
            st.id,
            st.subject,
            st.prop,
            StatementTier.tier,
            (Principal.kind == "agent").label("agent"),
            func.coalesce(st.valid_from, doc_day.c.day).label("day"),
            Payload.value["text"].astext.label("text"),
            own.c.inv,
            direct.c.place.label("direct"),
        )
        .join(StatementTier, StatementTier.statement_id == st.id)
        .join(Payload, Payload.commitment == st.commitment)
        .join(Principal, Principal.id == st.author_id)
        .outerjoin(parent, parent.c.child == blob)
        .outerjoin(own, own.c.blob == src)
        .outerjoin(doc_day, doc_day.c.blob == st.span_blob)
        .outerjoin(direct, direct.c.claim == st.id)
        .where(
            st.prop.in_(LOCATED),
            StatementTier.tier.in_(tiers),
            current_clause(),
            ~select(bad.c.blob).where(bad.c.blob == st.span_blob).exists(),
            ~select(derived.c.child)
            .join(bad, bad.c.blob == derived.c.parent)
            .where(derived.c.child == st.span_blob)
            .exists(),
        )
    )
    if f["investigation"]:
        q = q.where(
            select(acq.c.blob)
            .where(acq.c.blob == src, acq.c.inv == f["investigation"])
            .exists()
        )
    if f["time"]:
        lo, hi = f["time"]
        inside = _window(parent, f["time"])
        q = q.where(
            (st.valid_from.is_not(None) & (st.valid_from <= hi))
            & (func.coalesce(st.valid_to, st.valid_from) >= lo)
            | (st.valid_from.is_(None) & src.in_(select(inside.c.blob)))
        )
    if subjects is not None:
        q = q.where(st.subject.in_(subjects))
    if ids is not None:
        q = q.where(st.id.in_(ids))
    # Planner estimates on CTEs are poor; materialising keeps every step a single
    # pass instead of a nested loop that reruns one per row.
    base = q.cte("base").prefix_with("MATERIALIZED")

    # A name variant resolves when it has one candidate place and people confirmed
    # exactly one place for it in the claim's investigation and that governorate.
    unresolved = (
        select(base.c.id, norm(base.c.text).label("norm"), base.c.inv)
        .where(base.c.direct.is_(None), base.c.inv.is_not(None))
        .cte("unresolved")
        .prefix_with("MATERIALIZED")
    )
    single = (
        select(
            PlaceName.norm,
            cast(func.min(cast(PlaceName.place_id, Text)), ID).label("place"),
        )
        .where(PlaceName.norm.in_(select(unresolved.c.norm)))
        .group_by(PlaceName.norm)
        .having(func.count(PlaceName.place_id.distinct()) == 1)
        .cte("single")
        .prefix_with("MATERIALIZED")
    )
    other, lp = aliased(Statement), aliased(Place)
    op = aliased(parent)
    other_blob = mfilter.statement_blob(other)
    oo = aliased(own)
    confirmed = (
        select(
            links.c.name,
            oo.c.inv,
            scope(lp).label("scope"),
            links.c.place,
        )
        .select_from(links)
        .join(other, other.id == links.c.claim)
        .outerjoin(op, op.c.child == other_blob)
        .outerjoin(oo, oo.c.blob == _source(op, other_blob))
        .join(lp, lp.id == links.c.place)
        .where(links.c.name.in_(select(single.c.norm)))
        .distinct()
        .subquery()
    )
    chosen = (
        select(
            confirmed.c.name,
            confirmed.c.inv,
            confirmed.c.scope,
            cast(func.min(cast(confirmed.c.place, Text)), ID).label("place"),
        )
        .group_by(confirmed.c.name, confirmed.c.inv, confirmed.c.scope)
        .having(func.count(confirmed.c.place.distinct()) == 1)
        .cte("chosen")
        .prefix_with("MATERIALIZED")
    )
    cand = aliased(Place)
    variant = (
        select(unresolved.c.id, chosen.c.place)
        .join(single, single.c.norm == unresolved.c.norm)
        .join(cand, cand.id == single.c.place)
        .join(
            chosen,
            (chosen.c.name == unresolved.c.norm)
            & (chosen.c.inv == unresolved.c.inv)
            & (chosen.c.scope == scope(cand)),
        )
        .cte("variant")
        .prefix_with("MATERIALIZED")
    )
    return (
        select(
            base.c.id,
            base.c.subject,
            base.c.prop,
            base.c.tier,
            base.c.agent,
            base.c.day,
            func.coalesce(base.c.direct, variant.c.place).label("place"),
        )
        .outerjoin(variant, variant.c.id == base.c.id)
        .where(base.c.day.is_not(None))
    )


# --- tiles --------------------------------------------------------------------

R = 6378137.0  # Web Mercator sphere radius


def mercator(lon: float, lat: float) -> tuple[float, float]:
    return (
        math.radians(lon) * R,
        math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R,
    )


def bounds(z: int, x: int, y: int, pad: float = 0.0):
    """A tile's Web Mercator box, grown by `pad` of its width on every side."""
    size = WORLD / 2**z
    x0, y1 = -WORLD / 2 + x * size, WORLD / 2 - y * size
    return (
        x0 - pad * size,
        y1 - size - pad * size,
        x0 + size + pad * size,
        y1 + pad * size,
    )


@dataclass
class Model:
    """The visible, resolved claims of one query: points in Web Mercator and the
    sequence segments between consecutive dated steps of each subject."""

    points: list[tuple]  # (mx, my, id, tier, agent, subject, prop, place, day, rank)
    spots: dict  # place -> (lon, lat, precision_m)
    segments: list[tuple]  # (ax, ay, bx, by, subject)


async def build(db, f: dict, tiers, subjects=None, place=None) -> Model:
    found = claims(f, tiers, subjects).subquery()
    p = aliased(Place)
    q = (
        select(
            found,
            func.ST_X(func.geometry(p.centroid)),
            func.ST_Y(func.geometry(p.centroid)),
            p.precision_m,
        )
        .join(p, p.id == found.c.place)
        .order_by(found.c.subject, found.c.day, found.c.id)
    )
    if place is not None:
        q = q.where(found.c.place == place)
    rank = {t: i for i, t in enumerate(TIERS)}
    points, spots, steps = [], {}, defaultdict(dict)
    # Estimates on the grouped CTEs are a row or two, which makes the planner
    # rerun them in nested loops (minutes at 100k claims); every join here is an
    # equality that hashes. Scoped to this transaction.
    await db.execute(text("SET LOCAL enable_nestloop = off"))
    for sid, subject, prop, tier, agent, day, pid, lon, lat, prec in (
        await db.execute(q)
    ).all():
        mx, my = mercator(lon, lat)
        pid = str(pid)
        points.append(
            (
                mx,
                my,
                str(sid),
                tier,
                agent,
                subject,
                prop,
                pid,
                day.isoformat(),
                rank[tier],
            )
        )
        if tier != Tier.REJECTED:
            spots[pid] = (lon, lat, prec)
            steps[subject].setdefault(day, {})[pid] = (mx, my)
    # Every place of one step to every place of the next, as `monitor.data.track`.
    segments = []
    for subject, by_day in steps.items():
        days = sorted(by_day)
        for before, after in zip(days, days[1:], strict=False):
            for a, (ax, ay) in by_day[before].items():
                for b, (bx, by) in by_day[after].items():
                    if a != b:
                        segments.append((ax, ay, bx, by, subject))
    return Model(points, spots, segments)


# Statements whose arrival can change which claims a map shows, or where.
MAP_PROPS = (
    *sorted(LOCATED),
    Prop.LOCATED_AT,
    Prop.REVIEW,
    Prop.RETRACTION,
    Prop.ERASURE,
    Prop.DOCUMENT_DATE,
    Prop.DERIVED_FROM,
)


async def version(db) -> tuple:
    """What a model was built from: map statements and acquisitions so far."""
    st = (
        await db.execute(
            select(func.count(), func.max(Statement.recorded_at)).where(
                Statement.prop.in_(MAP_PROPS)
            )
        )
    ).one()
    acq = (await db.execute(select(func.count(), func.max(Acquisition.at)))).one()
    return (*st, *acq)


class Cache:
    """Models per reader and query. A model is reused while the ledger head is the
    one it was built at, or while `version` says nothing map-relevant arrived since
    (an access log moves the head, not the map), for at most `ttl` seconds, since
    place and boundary edits are not chained. Never shared between users;
    concurrent tile requests for one key wait for a single build.
    ponytail: process memory, `size` entries; move to a shared store when the app
    runs more than one process."""

    def __init__(self, ttl: float = 30.0, size: int = 8):
        self.ttl, self.size = ttl, size
        self.entries: OrderedDict = OrderedDict()

    async def get(self, db, user_id, f, tiers, subjects=None, place=None) -> Model:
        head = await db.scalar(select(func.max(Chain.seq)))
        key = (
            user_id,
            orjson.dumps(mfilter.dump(f)),
            tuple(tiers),
            tuple(subjects) if subjects is not None else None,
            place,
        )
        found = self.entries.get(key)
        fresh = found and time.monotonic() - found["at"] < self.ttl
        if fresh and found["head"] != head:
            if found["version"] == await version(db):
                found["head"] = head
        if fresh and found["head"] == head:
            self.entries.move_to_end(key)
            return await asyncio.shield(found["task"])
        entry = {
            "head": head,
            "version": await version(db),
            "at": time.monotonic(),
            "task": asyncio.ensure_future(build(db, f, tiers, subjects, place)),
        }
        self.entries[key] = entry
        self.entries.move_to_end(key)
        while len(self.entries) > self.size:
            self.entries.popitem(last=False)
        try:
            return await entry["task"]
        except BaseException:
            self.entries.pop(key, None)
            raise


def _clusters(points, z, x, y):
    """Points merged per grid cell, CELLS cells to a tile side, so every cell lies
    inside exactly one tile and no cluster is split or lost at a tile edge."""
    size = WORLD / 2**z / CELLS
    cells: dict = {}
    for pt in points:
        cx = math.floor((pt[0] + WORLD / 2) / size)
        cy = math.floor((WORLD / 2 - pt[1]) / size)
        if (cx // CELLS, cy // CELLS) == (x, y):
            cells.setdefault((cx, cy), []).append(pt)
    out = []
    for members in cells.values():
        members.sort(key=lambda m: m[2])
        n = len(members)
        best = min(m[9] for m in members)
        out.append(
            {
                "x": sum(m[0] for m in members) / n,
                "y": sum(m[1] for m in members) / n,
                "count": n,
                "cluster": n > 1 or None,
                "tier": TIERS[best],
                "agent": all(m[4] for m in members),
                "id": members[0][2] if n == 1 else None,
                "ids": ",".join(m[2] for m in members[:IDS_PER_CLUSTER]),
            }
        )
    return out


TILE_SQL = """
WITH env AS (SELECT ST_TileEnvelope(:z, :x, :y) AS env),
pad AS (SELECT ST_Transform(ST_Expand(env, :pad), 4326) AS pad FROM env),
pts AS (
  SELECT ST_AsMVTGeom(ST_SetSRID(ST_MakePoint(t.x, t.y), 3857), env, :extent,
                      :buffer, true) AS geom,
         t.id, t.tier, t.agent, t.subject, t.prop, t.place, t.day, t.count,
         t.cluster, t.ids
  FROM env, unnest(CAST(:px AS float8[]), CAST(:py AS float8[]),
    CAST(:pid AS text[]), CAST(:ptier AS text[]), CAST(:pagent AS bool[]),
    CAST(:psubject AS text[]), CAST(:pprop AS text[]), CAST(:pplace AS text[]),
    CAST(:pday AS text[]), CAST(:pcount AS int[]), CAST(:pcluster AS bool[]),
    CAST(:pids AS text[]))
    AS t(x, y, id, tier, agent, subject, prop, place, day, count, cluster, ids)
),
seqs AS (
  SELECT ST_AsMVTGeom(ST_MakeLine(ST_SetSRID(ST_MakePoint(s.ax, s.ay), 3857),
                                  ST_SetSRID(ST_MakePoint(s.bx, s.by), 3857)),
                      env, :extent, :buffer, true) AS geom, s.subject
  FROM env, unnest(CAST(:sax AS float8[]), CAST(:say AS float8[]),
    CAST(:sbx AS float8[]), CAST(:sby AS float8[]), CAST(:ssubject AS text[]))
    AS s(ax, ay, bx, by, subject)
),
rings AS (
  SELECT ST_AsMVTGeom(ST_Transform(ST_Buffer(
           ST_SetSRID(ST_MakePoint(u.lon, u.lat), 4326)::geography, u.precision_m
         )::geometry, 3857), env, :extent, :buffer, true) AS geom,
         u.place, u.precision_m
  FROM env, unnest(CAST(:uplace AS text[]), CAST(:ulon AS float8[]),
    CAST(:ulat AS float8[]), CAST(:uprec AS int[]))
    AS u(place, lon, lat, precision_m)
),
feet AS (
  SELECT ST_AsMVTGeom(ST_Transform(p.geom, 3857), env, :extent, :buffer, true)
           AS geom, p.id::text AS place, p.kind, p.name_ar, p.name_en
  FROM env, pad, place p JOIN place_kind k ON k.code = p.kind
  WHERE :feet AND k.family = 'facility' AND ST_Dimension(p.geom) = 2
    AND p.geom && pad.pad
),
areas AS (
  SELECT ST_AsMVTGeom(ST_Transform(a.geom, 3857), env, :extent, :buffer, true)
           AS geom, a.pcode, a.level, a.name_ar, a.name_en
  FROM env, pad, admin_area a
  WHERE a.set_id = (SELECT id FROM boundary_set
                    ORDER BY valid_on DESC, imported_at DESC LIMIT 1)
    AND a.level <= :level AND a.geom && pad.pad
)
SELECT coalesce((SELECT ST_AsMVT(pts, 'points', :extent, 'geom') FROM pts), '')
    || coalesce((SELECT ST_AsMVT(seqs, 'sequences', :extent, 'geom') FROM seqs), '')
    || coalesce((SELECT ST_AsMVT(rings, 'uncertainty', :extent, 'geom') FROM rings), '')
    || coalesce((SELECT ST_AsMVT(feet, 'footprints', :extent, 'geom') FROM feet), '')
    || coalesce((SELECT ST_AsMVT(areas, 'admin', :extent, 'geom') FROM areas), '')
"""


async def tile(db, model: Model, z: int, x: int, y: int) -> bytes:
    """MVT bytes for tile z/x/y of `model`: points (clusters below CLUSTER_BELOW),
    sequence segments from SEQUENCES_FROM, uncertainty circles from
    UNCERTAINTY_FROM, facility footprints from FOOTPRINTS_FROM, admin boundaries."""
    margin = BUFFER / EXTENT
    x0, y0, x1, y1 = bounds(z, x, y, margin)
    here = [p for p in model.points if x0 <= p[0] <= x1 and y0 <= p[1] <= y1]
    if z >= CLUSTER_BELOW:
        feats = [
            {
                "x": p[0],
                "y": p[1],
                "id": p[2],
                "tier": p[3],
                "agent": p[4],
                "subject": p[5],
                "prop": p[6],
                "place": p[7],
                "day": p[8],
                "count": 1,
                "cluster": None,
                "ids": None,
            }
            for p in here
        ]
    else:
        feats = _clusters(model.points, z, x, y)
    segs = []
    if z >= SEQUENCES_FROM:
        segs = [
            s
            for s in model.segments
            if max(s[0], s[2]) >= x0
            and min(s[0], s[2]) <= x1
            and max(s[1], s[3]) >= y0
            and min(s[1], s[3]) <= y1
        ]
    rings = []
    if z >= UNCERTAINTY_FROM:
        places = {p[7] for p in here if p[3] != Tier.REJECTED}
        rings = [(pl, *model.spots[pl]) for pl in sorted(places) if model.spots[pl][2]]

    def col(rows, key):
        return [r.get(key) for r in rows]

    params = {
        "z": z,
        "x": x,
        "y": y,
        "pad": WORLD / 2**z * margin,
        "extent": EXTENT,
        "buffer": BUFFER,
        "feet": z >= FOOTPRINTS_FROM,
        "level": 1 if z < 7 else 2 if z < 9 else 3,
        "px": col(feats, "x"),
        "py": col(feats, "y"),
        "pid": col(feats, "id"),
        "ptier": col(feats, "tier"),
        "pagent": col(feats, "agent"),
        "psubject": col(feats, "subject"),
        "pprop": col(feats, "prop"),
        "pplace": col(feats, "place"),
        "pday": col(feats, "day"),
        "pcount": col(feats, "count"),
        "pcluster": col(feats, "cluster"),
        "pids": col(feats, "ids"),
        "sax": [s[0] for s in segs],
        "say": [s[1] for s in segs],
        "sbx": [s[2] for s in segs],
        "sby": [s[3] for s in segs],
        "ssubject": [s[4] for s in segs],
        "uplace": [r[0] for r in rings],
        "ulon": [r[1] for r in rings],
        "ulat": [r[2] for r in rings],
        "uprec": [r[3] for r in rings],
    }
    return bytes(await db.scalar(text(TILE_SQL), params))
