"""One search across every entity type, grouped, exact matches before leads.

Every group item carries a type, title, why-matched signals and a deep link.
Semantic items are leads by meaning, labelled as such and never accepted. Values
follow the monitor's masking rule: personal values and source text from a
sensitive source stay hidden until the analyst reveals that source."""

import asyncio
import re
from collections import defaultdict

from sqlalchemy import select

from sealbook import blobs
from sealbook.discovery import service
from sealbook.discovery.text import folded
from sealbook.ledger.contracts import EXTRACTED_PROPS, Prop, Tier
from sealbook.ledger.models import Blob, Payload, Statement, StatementTier
from sealbook.ledger.write import current_clause
from sealbook.monitor import data
from sealbook.monitor import filter as mfilter
from sealbook.people import data as people_data
from sealbook.places import normalise
from sealbook.places.models import Place, PlaceName
from sealbook.places.resolve import link_counts
from sealbook.search.arabic import candidates
from sealbook.settings import Config

TYPES = (
    "people",
    "places",
    "sources",
    "passages",
    "media",
    "claims",
    "findings",
    "incidents",
)
TEXT_KINDS = {"text", "page"}
PICTURE_KINDS = {"image", "frame", "page_image"}
CONTEXT = 80
MARKUP = re.compile(r"<(style|script)\b[\s\S]*?</\1>|<[^>]+>", re.IGNORECASE)


def _snippet(source, start, end):
    """A short window around [start, end) with the highlight relative to it."""
    lo, hi = max(0, start - CONTEXT), min(len(source), end + CONTEXT)
    text = source[lo:hi]
    return {
        "text": ("…" if lo else "") + text + ("…" if hi < len(source) else ""),
        "highlights": [[start - lo + (1 if lo else 0), end - lo + (1 if lo else 0)]],
    }


def _seconds(location):
    ms = location.get("ms")
    return ms[0] if isinstance(ms, list) else None


def _link(sha, segment=None, location=None):
    if not segment:
        return f"/evidence/{sha}"
    link = f"/evidence/{sha}?segment={segment}"
    at = _seconds(location or {})
    if at is not None:
        link += f"&at={at}"
    elif (location or {}).get("page"):
        link += f"&page={location['page']}"
    return link


async def _texts(db, rows):
    """Source text for snippets, read once per text blob."""
    shas = {r["text_blob"] for r in rows if r.get("text_blob")}
    found = {}
    for blob in (await db.execute(select(Blob).where(Blob.sha256.in_(shas)))).scalars():
        text = (await asyncio.to_thread(blobs.read, blob, Config.BLOB_DIR)).decode()
        if blob.mime == "text/html":
            # Blank markup at equal length so span offsets still point into the source.
            text = MARKUP.sub(lambda m: " " * len(m[0]), text)
        found[blob.sha256] = text
    return found


def _segment_item(row, doc, masked, texts, revealed):
    kind = row.get("kind")
    item = {
        "type": "passage" if kind in TEXT_KINDS else "media",
        "id": row["segment"],
        "sha256": row["sha256"],
        "title": doc["title"],
        "subtitle": ", ".join(x for x in (_kind(doc), doc["date"]) if x),
        "kind": kind,
        "location": row["location"],
        "span": row.get("span"),
        "exact": row["group"] == "text_match",
        "why": {k: row[k] for k in ("signals", "matched", "score", "near") if k in row},
        "masked": masked,
        "snippet": None,
        "thumb": None,
        "link": _link(row["sha256"], row["segment"], row["location"]),
        "also": row.get("also", []),
    }
    source = texts.get(row.get("text_blob"))
    if source is not None and not masked:
        span = row["span"]
        if item["exact"]:
            item["snippet"] = _snippet(source, span["start"], span["end"])
        else:
            text = source[span["start"] : span["end"]]
            item["snippet"] = {"text": text[:240], "highlights": []}
    if kind in PICTURE_KINDS and row["sha256"] in revealed:
        item["thumb"] = f"/api/evidence/{row['sha256']}/segments/{row['segment']}/image"
    return item


async def _people(db, query, f, embedding, named, ctx_for):
    found = await service.actor_search(
        db, query, f, query_embedding=embedding, named=named
    )
    subjects = [r["subject"] for r in found]
    matching = {
        sid for r in named for sid in r["statements"]
    }  # statement ids whose name matched the query
    rows = (
        await db.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.subject.in_(subjects),
                Statement.prop == Prop.NAME,
                StatementTier.tier != Tier.REJECTED,
                current_clause(),
            )
        )
    ).all()
    ctx = await ctx_for([st.span_blob for st, _ in rows])
    names = defaultdict(list)
    for st, value in rows:
        doc = ctx.doc(st.span_blob) if st.span_blob else None
        masked = doc is None or doc["sensitive"]
        names[st.subject].append((str(st.id) in matching, masked, data.display(value)))
    # The counts and dates the People list shows for this case, so both agree.
    summary = await people_data.roster(db, f["investigation"])
    items = []
    for row in found:
        person = row["subject"].removeprefix("person:")
        shown = [
            n
            for hit, masked, n in sorted(names[row["subject"]], reverse=True)
            if not masked and n
        ]
        info = summary.get(person, {})
        dates = info.get("from") and (
            f"{info['from']} to {info['to']}"
            if info.get("to") and info["to"] != info["from"]
            else info["from"]
        )
        counts = info.get("facts") and (
            f"{_count(info['facts'], 'fact')} from "
            f"{_count(info['documents'], 'document')}"
        )
        items.append(
            {
                "type": "person",
                "id": row["subject"],
                "title": shown[0] if shown else "Hidden person (sensitive source)",
                "alt": [n for n in dict.fromkeys(shown) if n != shown[0]][:4],
                "masked": not shown,
                "subtitle": ", ".join(filter(None, [counts, dates])) or None,
                "exact": "keyword" in row["signals"],
                "why": {"signals": row["signals"]}
                | ({"score": row["score"]} if "score" in row else {}),
                "link": f"/people/{person}",
            }
        )
    # Two records under one name stay two; say what sets them apart, as People does.
    rows = [
        {"person": p, "name": i["title"]}
        | {k: summary.get(p, {}).get(k) for k in ("from", "to")}
        for i in items
        if (p := i["id"].removeprefix("person:"))
    ]
    told = await people_data.hints(db, rows, f["investigation"])
    for i, r in zip(items, rows, strict=True):
        if hint := told.get(r["person"]):
            i["subtitle"] = ", ".join(filter(None, [i["subtitle"], hint]))
    return items


async def _places(db, query, f):
    norm = normalise(query)
    if not norm:
        return []
    hits = (
        await db.execute(
            select(PlaceName.place_id, PlaceName.name, PlaceName.norm)
            .where(PlaceName.norm.contains(norm, autoescape=True))
            .limit(50)
        )
    ).all()
    best = {}
    for place, name, key in hits:
        rank = 0 if key == norm else 1 if key.startswith(norm) else 2
        if place not in best or best[place][0] > rank:
            best[place] = (rank, name)
    if not best:
        # Other script or transliteration (Jdeideh, الجديدة): lossy consonant keys.
        # ponytail: scans every gazetteer name; index name keys past ~50k names.
        for place, name in await db.execute(select(PlaceName.place_id, PlaceName.name)):
            if candidates(query, name) and place not in best:
                best[place] = (3, name)
    if not best:
        return []
    places = {
        p.id: p
        for p in (await db.execute(select(Place).where(Place.id.in_(best)))).scalars()
    }
    counts = await link_counts(db, f["investigation"])
    signal = {0: "name", 1: "name_prefix", 2: "name_part", 3: "spelling"}
    return [
        {
            "type": "place",
            "id": f"place:{place}",
            "title": places[place].name_ar,
            "alt": [places[place].name_en] if places[place].name_en else [],
            "subtitle": places[place].kind,
            "exact": rank == 0,
            "why": {"signals": [signal[rank]], "matched": name},
            "claims": counts.get(place, 0),
            "link": f"/places/{place}",
        }
        for place, (rank, name) in sorted(
            best.items(), key=lambda kv: (kv[1][0], -counts.get(kv[0], 0))
        )
    ]


async def _claims(db, needles, f, ctx_for):
    """Claims whose value names the query or one of its recorded spellings.
    ponytail: matches up to 2000 current claims in Python; add a folded value
    column with a trigram index when a case passes that."""
    rows = (
        await db.execute(
            select(Statement, Payload.value, StatementTier.tier)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop.in_(EXTRACTED_PROPS | {Prop.INCIDENT}),
                StatementTier.tier != Tier.REJECTED,
                current_clause(),
                mfilter.statement_clause(f),
                service.available(Statement.span_blob),
            )
            .order_by(Statement.recorded_at.desc())
            .limit(2000)
        )
    ).all()
    keys = [(folded(n)[0].strip(), n) for n in needles]
    matched = []
    for st, value, tier in rows:
        shown = data.display(value)
        if not shown:
            continue
        haystack = folded(shown)[0]
        for key, name in keys:
            if key and key in haystack:
                matched.append((st, value, tier, name))
                break
    # A transcript or page text names the recording or file it was read from.
    spans = {st.span_blob for st, *_ in matched if st.span_blob}
    origin = dict(
        (
            await db.execute(
                select(Blob.sha256, mfilter.source(Blob.sha256)).where(
                    Blob.sha256.in_(spans)
                )
            )
        ).all()
    )
    ctx = await ctx_for(origin.values())
    items = []
    for st, value, tier, name in matched:
        sha = origin.get(st.span_blob)
        doc = ctx.doc(sha) if sha else None
        # The monitor's rule: personal values from a sensitive source stay masked.
        masked = st.prop in data.PERSONAL and (doc is None or doc["sensitive"])
        incident = st.prop == Prop.INCIDENT
        items.append(
            {
                "type": "incident" if incident else "claim",
                "id": str(st.id),
                "title": None if masked else data.display(value),
                "prop": st.prop,
                "subtitle": doc["title"] if doc else None,
                "masked": masked,
                "tier": tier,
                "exact": True,
                "why": {"signals": ["claim_value"], "matched": name},
                "span": st.span,
                "link": f"/incidents/{st.subject.removeprefix('incident:')}"
                if incident
                else f"/review?statement={st.id}"
                if not sha
                else _link(sha),
            }
        )
    return items


async def _findings(db, f, people, places):
    """Open findings that involve a matched person or place."""
    subjects = {p["id"] for p in people if p["exact"]} | {
        p["id"] for p in places if p["exact"]
    }
    if not subjects:
        return []
    rows = (
        await db.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == Prop.FINDING,
                StatementTier.tier != Tier.REJECTED,
                current_clause(),
            )
            .order_by(Statement.recorded_at.desc())
            .limit(500)
        )
    ).all()
    items = []
    for st, value in rows:
        involved = subjects & (
            set(value.get("people", [])) | set(value.get("places", []))
        )
        if not involved:
            continue
        items.append(
            {
                "type": "finding",
                "id": str(st.id),
                "title": value.get("kind", "finding").replace("_", " ").capitalize(),
                "subtitle": value.get("month"),
                "exact": True,
                "why": {"signals": ["involves"], "matched": sorted(involved)},
                "link": f"/review?statement={st.id}",
            }
        )
    return items


def _kind(doc):
    t = doc["doc_type"]
    return None if t in (None, "unknown") else t.replace("_", " ")


def _count(n, one):
    return f"{n} {one}" if n == 1 else f"{n} {one}s"


# Leads by meaning far below the best score in the same answer are noise (a name
# query pulls every photo in at a flat similarity), so they are left out. The bands
# follow the retrieval gold set: positives score about 0.80, negatives top 0.67.
LEAD_MARGIN = 0.05
BANDS = ((0.75, "close"), (0.68, "related"))


def _score(item):
    why = item["why"]
    return why.get("score") or (why.get("near") or {}).get("score")


def _leads(groups: list[list[dict]]) -> list[list[dict]]:
    scores = [s for items in groups for i in items if (s := _score(i)) is not None]
    floor = max(scores) - LEAD_MARGIN if scores else None
    out = []
    for items in groups:
        kept = []
        for i in items:
            s = _score(i)
            if s is not None:
                i["why"]["band"] = next((b for lo, b in BANDS if s >= lo), "loose")
            if i["exact"] or s is None or s >= floor:
                kept.append(i)
        out.append(kept)
    return out


def _group(items, limit):
    ordered = sorted(items, key=lambda i: not i["exact"])
    return {
        "total": len(ordered),
        "exact": sum(1 for i in ordered if i["exact"]),
        "items": ordered[:limit],
    }


async def find(db, query, f, *, types=TYPES, limit=8, embedding=None, revealed=()):
    """Grouped results. `embedding` is a query vector or None for exact-only;
    `revealed` holds the sources this session revealed."""
    contexts = {}

    async def ctx_for(shas):
        key = frozenset(s for s in shas if s)
        if key not in contexts:
            contexts[key] = await data.Context.load(db, blobs=key)
        return contexts[key]

    named = await people_data.search(db, query)
    groups = {}
    people, places = [], []
    if {"people", "findings"} & set(types):
        people = await _people(db, query, f, embedding, named, ctx_for)
    if {"places", "findings"} & set(types):
        places = await _places(db, query, f)
    rows = await service.search(
        db,
        query,
        "hybrid" if embedding else "keyword",
        f,
        query_embedding=embedding,
        named=named,
    )
    ctx = await ctx_for([r["sha256"] for r in rows])
    texts = await _texts(db, rows)
    sources, passages, media = [], [], []
    for row in rows:
        doc = ctx.doc(row["sha256"])
        masked = doc["sensitive"] and row["sha256"] not in revealed
        if "segment" not in row:
            sources.append(
                {
                    "type": "source",
                    "id": row["sha256"],
                    "sha256": row["sha256"],
                    "title": doc["title"],
                    "subtitle": ", ".join(
                        x for x in (_kind(doc), doc["date"], *doc["origins"]) if x
                    ),
                    "exact": True,
                    "why": {"signals": row["signals"], "matched": row.get("matched")},
                    "masked": masked,
                    "link": _link(row["sha256"]),
                }
            )
            continue
        item = _segment_item(row, doc, masked, texts, revealed)
        (passages if item["type"] == "passage" else media).append(item)
    people, passages, media = _leads([people, passages, media])
    for name, items in (
        ("people", people),
        ("places", places),
        ("sources", sources),
        ("passages", passages),
        ("media", media),
    ):
        if name in types:
            groups[name] = _group(items, limit)
    if {"claims", "incidents"} & set(types):
        needles = [query, *await service.spellings(db, query, named)]
        claims = await _claims(db, needles, f, ctx_for)
        for name, kind in (("claims", "claim"), ("incidents", "incident")):
            if name in types:
                groups[name] = _group([c for c in claims if c["type"] == kind], limit)
    if "findings" in types:
        groups["findings"] = _group(await _findings(db, f, people, places), limit)
    return {"groups": groups, "capped": len(rows) >= service.LIMIT}
