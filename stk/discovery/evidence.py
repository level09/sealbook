"""Read side of the evidence page: one acquired source with its claims, copies,
relations, custody and threads, in a fixed number of queries whatever its size.

Values read out of the source (text, page text, masked claim values) are returned
only by `content_read`, which the caller gates behind the logged reveal."""

import asyncio
import hmac
import time
import uuid
from collections import Counter
from datetime import UTC, datetime

import orjson
from quart import session
from sqlalchemy import func, literal, or_, select, text, true
from sqlalchemy.orm import aliased

from stk import blobs
from stk.discovery.models import EmbeddingSpace, Segment, SegmentEmbedding
from stk.discovery.service import available
from stk.ledger import write
from stk.ledger.contracts import EXTRACTED_PROPS, Prop, StatementIn, Tier
from stk.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Principal,
    Run,
    Statement,
    StatementTier,
)
from stk.monitor import data
from stk.monitor import filter as mfilter
from stk.people import data as people_data
from stk.places import resolve
from stk.settings import Config
from stk.sheets.parse import MIMES as SHEETS

# ponytail: strength bands carried over from the approved mockup, not calibrated
# against labelled pairs. Replace with measured cut-offs once reviewers have
# decided enough copies and leads to fit them.
NEAR_CLOSE, NEAR_MODERATE = 0.25, 0.4
SOUND_STRONG_LANDMARKS = 4
SAME_PERSON_STRONG_BASIS = 5
# ponytail: nearest neighbours for at most this many segments of the item (one
# lateral HNSW probe each); sample evenly across a long recording if it matters.
NEAR_SEGMENTS = 64
REVIEW_PROPS = (Prop.REVIEW, Prop.RETRACTION, Prop.LOCATED_AT)


def granted(sha: str) -> bool:
    """The session holds an unexpired reveal of `sha`, logged when it was given."""
    return session.get("evidence_previews", {}).get(sha, 0) > time.time()


async def is_sensitive(db, sha: str) -> bool:
    """Sensitive when any acquisition says so, or when none set it (the default)."""
    levels = set(
        (
            await db.execute(
                select(Acquisition.sensitivity).where(Acquisition.blob_sha256 == sha)
            )
        ).scalars()
    )
    return not levels or "sensitive" in levels


def view_session() -> str:
    """A stable, non-reversible key for this login session. Concurrent requests share
    it (unlike a value written into the cookie), so one session logs one view."""
    token = (session.get("_id") or "").encode()
    return hmac.new(Config.SECRET_KEY.encode(), token, "sha256").hexdigest()[:16]


async def viewable(db, sha: str, user) -> bool:
    """Whether this session may see the bytes or text of `sha`. A sensitive source
    needs the explicit logged reveal. Any other source is shown at once, and its
    first view in a login session writes the access record, so everyone who saw a
    source is on its custody chain."""
    if await is_sensitive(db, sha):
        return granted(sha)
    # Serialise this person's loads (content, text and frames arrive together) so
    # neither the principal lookup nor the check below can race.
    await db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"evidence-view:{user.id}"},
    )
    who = await write.principal_for_user(db, user.id, user.name or user.email)
    key = view_session()
    seen = await db.scalar(
        select(Statement.id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            Statement.subject == f"blob:{sha}",
            Statement.prop == Prop.ACCESS,
            Statement.author_id == who.id,
            Payload.value["session"].astext == key,
        )
        .limit(1)
    )
    if seen is None:
        await write.append(
            db,
            StatementIn(
                subject=f"blob:{sha}",
                prop=Prop.ACCESS,
                value={
                    "action": "view",
                    "at": datetime.now(UTC).isoformat(),
                    "session": key,
                },
                author_id=who.id,
            ),
        )
    await db.commit()
    return True


def kind_of(mime: str) -> str:
    if mime in SHEETS:
        return "sheet"
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith(("audio/", "video/")):
        return mime.split("/")[0]
    return "web" if mime == "text/html" else "text"


def moments(value: dict, as_query: bool) -> tuple[list[int], list[int]]:
    """Matched interval here and there, in ms. A sound match records only where the
    alignment starts: candidate time = query time + offset."""
    if pic := value.get("picture"):
        q, c = pic["query_ms"], pic["candidate_ms"]
    else:
        off = value["sound"]["offset_ms"]
        q, c = [max(0, -off)] * 2, [max(0, off)] * 2
    return (q, c) if as_query else (c, q)


def copy_reason(value: dict) -> tuple[str, str, str]:
    """(kind, why, strength) of a media match, read from its own measurements."""
    if pic := value.get("picture"):
        n, (hit, total) = len(pic["sample_pairs"]), pic["query_coverage"]
        why = (
            f"{n} frame pair{'s' if n != 1 else ''} within Hamming distance "
            f"{pic['max_distance']}, {hit} of {total} distinct query frames matched"
        )
        return "Re-upload", why, "strong"
    snd = value["sound"]
    why = (
        f"{snd['aligned_landmarks']} of {snd['shared_landmarks']} shared sound "
        f"landmarks agree on an offset of {snd['offset_ms'] / 1000:.2f} s"
    )
    strong = snd["aligned_landmarks"] >= SOUND_STRONG_LANDMARKS
    return "Same recording", why, "strong" if strong else "weak"


def where(kind: str, location: dict) -> str:
    def ms(v):
        s = round(v / 1000)
        return f"{s // 60:02d}:{s % 60:02d}"

    if kind == "frame":
        return f"the frame at {ms(location['ms'][0])}"
    if kind == "audio":
        return f"audio {ms(location['ms'][0])} to {ms(location['ms'][1])}"
    if location.get("page"):
        return f"page {location['page']}"
    return "the whole image" if kind == "image" else "a passage"


def band(distance: float) -> str:
    if distance < NEAR_CLOSE:
        return "close"
    return "moderate" if distance < NEAR_MODERATE else "weak"


async def family(db, sha: str) -> list[str]:
    """The source and the blobs derived from it (page text, frames, audio windows)."""
    rows = await db.execute(
        select(Statement.subject)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(Statement.prop == Prop.DERIVED_FROM, Payload.value["blob"].astext == sha)
    )
    return [sha, *(s.removeprefix("blob:") for s in rows.scalars())]


async def parent(db, sha: str) -> str | None:
    """The blob `sha` was derived from, if any."""
    return await db.scalar(
        select(Payload.value["blob"].astext)
        .join(Statement, Statement.commitment == Payload.commitment)
        .where(Statement.subject == f"blob:{sha}", Statement.prop == Prop.DERIVED_FROM)
        .limit(1)
    )


async def source_blob(db, sha: str) -> Blob | None:
    """An acquired, unerased source, or None."""
    blob = await db.get(Blob, sha)
    if blob is None or not await db.scalar(
        select(available(Blob.sha256)).where(Blob.sha256 == sha)
    ):
        return None
    acquired = await db.scalar(
        select(Acquisition.id).where(Acquisition.blob_sha256 == sha).limit(1)
    )
    return blob if acquired else None


def _pdf_pages(path) -> int:
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as document:
        return len(document)


async def reviewable(db, ids: list[str], user) -> dict[str, bool]:
    """Whether `user` may decide each statement, by the review flow's own rule
    (collaboration.service.check_reviewer), for many statements at once."""
    if not ids:
        return {}
    uids = [uuid.UUID(i) for i in ids]
    authors = dict(
        (
            await db.execute(
                select(Statement.id, Principal.user_id)
                .join(Principal, Principal.id == Statement.author_id)
                .where(Statement.id.in_(uids))
            )
        ).all()
    )
    reviewers: dict[str, set] = {}
    for subject, value in await db.execute(
        select(Statement.subject, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            Statement.prop == Prop.REVIEW_REQUEST,
            Statement.subject.in_([f"statement:{i}" for i in ids]),
        )
    ):
        reviewers.setdefault(subject.removeprefix("statement:"), set()).add(
            value["reviewer"]
        )
    admin = user.has_role("admin")
    return {
        i: i not in reviewers
        or (authors.get(uuid.UUID(i)) != user.id and (admin or user.id in reviewers[i]))
        for i in ids
    }


async def threads(db, ids: list[str], user) -> list[dict]:
    """Review requests on these statements. A request is private to its sender,
    reviewer and admins (collaboration.service.task); others see only that it exists."""
    if not ids:
        return []
    rows = (
        await db.execute(
            select(Statement, Payload.value, Principal.user_id, Principal.name)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(Principal, Principal.id == Statement.author_id)
            .where(
                Statement.prop == Prop.REVIEW_REQUEST,
                Statement.subject.in_([f"statement:{i}" for i in ids]),
            )
            .order_by(Statement.recorded_at)
        )
    ).all()
    counts = (
        dict(
            (
                await db.execute(
                    select(Statement.subject, func.count())
                    .where(
                        Statement.prop == Prop.REVIEW_COMMENT,
                        Statement.subject.in_([f"statement:{r[0].id}" for r in rows]),
                    )
                    .group_by(Statement.subject)
                )
            ).all()
        )
        if rows
        else {}
    )
    admin = user.has_role("admin")
    out = []
    for st, value, sender_id, sender in rows:
        visible = admin or user.id in (sender_id, value["reviewer"])
        out.append(
            {
                "id": str(st.id),
                "about": st.subject.removeprefix("statement:"),
                "visible": visible,
                "sender": sender if visible else None,
                "reason": value["reason"] if visible else None,
                "mine": user.id == value["reviewer"],
                "comments": counts.get(f"statement:{st.id}", 0),
                "at": st.recorded_at.isoformat(),
            }
        )
    return out


async def copies(db, sha: str) -> list[dict]:
    other = Payload.value["blob"].astext
    rows = (
        await db.execute(
            select(Statement, Payload.value, StatementTier.tier)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == Prop.MEDIA_MATCH,
                StatementTier.tier != Tier.REJECTED,
                or_(Statement.subject == f"blob:{sha}", other == sha),
                available(mfilter.statement_blob()),
                available(other),
            )
            .order_by(Statement.recorded_at.desc())
            .limit(50)
        )
    ).all()
    out = []
    for st, value, tier in rows:
        as_query = st.subject == f"blob:{sha}"
        here, there = moments(value, as_query)
        kind, why, strength = copy_reason(value)
        out.append(
            {
                "id": str(st.id),
                "statement": str(st.id),
                "other": value["blob"] if as_query else st.subject[5:],
                "kind": kind,
                "why": why,
                "strength": strength,
                "tier": tier,
                "here": here,
                "there": there,
            }
        )
    return out


async def same_person(db, sha: str, people: set[str]) -> list[dict]:
    if not people:
        return []
    rows = (
        await db.execute(
            select(Statement, Payload.value, StatementTier.tier)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == Prop.SAME_AS,
                StatementTier.tier != Tier.REJECTED,
                or_(
                    Statement.subject.in_(people),
                    Payload.value["person"].astext.in_(
                        [p.removeprefix("person:") for p in people]
                    ),
                ),
            )
        )
    ).all()
    pairs = []
    for st, value, tier in rows:
        b = f"person:{value['person']}"
        mine, theirs = (st.subject, b) if st.subject in people else (b, st.subject)
        pairs.append((st, value, tier, mine, theirs))
    src = mfilter.source(mfilter.statement_blob())
    sources: dict[str, str] = {}
    for subject, source in await db.execute(
        select(Statement.subject, src)
        .where(
            Statement.subject.in_({p[4] for p in pairs}),
            Statement.prop.in_(EXTRACTED_PROPS),
            available(mfilter.statement_blob()),
        )
        .distinct()
    ):
        if source != sha or subject not in sources:
            sources[subject] = source
    # Only names the reader may see; a masked one leaves the row unnamed.
    named = await people_data.names(
        db, sorted({people_data.pid(x) for p in pairs for x in p[3:]})
    )
    out = []
    for st, value, tier, mine, theirs in pairs:
        n = len(value.get("basis", []))
        out.append(
            {
                "id": str(st.id),
                "statement": str(st.id),
                "kind": "Same person?",
                "here": mine,
                "theirs": theirs,
                "here_name": named.get(people_data.pid(mine)),
                "their_name": named.get(people_data.pid(theirs)),
                "other": sources.get(theirs),
                "why": f"based on {n} statement{'s' if n != 1 else ''}",
                "strength": "strong" if n >= SAME_PERSON_STRONG_BASIS else "moderate",
                "tier": tier,
            }
        )
    return out


async def same_place(db, sha: str, fam: list[str], investigation, claims) -> list:
    """Other sources with claims resolved to a place a claim here resolves to: one
    row per source and place, with the props on each side and the claim count. A
    place many sources name says least, so its rows come last."""
    mine = [uuid.UUID(c["id"]) for c in claims if c["prop"] in data.LOCATED]
    if not mine:
        return []
    case = {"investigation": investigation, "time": None}
    # ponytail: resolves every located claim of the case per page view, as the
    # monitor panels do; cache per case if large cases make this slow.
    ids = list(
        (
            await db.execute(
                select(Statement.id)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .where(
                    Statement.prop.in_(data.LOCATED),
                    StatementTier.tier != Tier.REJECTED,
                    mfilter.statement_clause(case),
                    available(mfilter.statement_blob()),
                )
                .limit(2000)
            )
        ).scalars()
    )
    places = await resolve.resolved(db, list({*ids, *mine}), investigation)
    props = {c["id"]: c["prop"] for c in claims}
    here: dict = {}
    for i in mine:
        if place := places.get(i):
            here.setdefault(place.id, {})[props[str(i)]] = None
    near = [i for i in ids if i not in mine and places.get(i) and places[i].id in here]
    if not near:
        return []
    src = mfilter.source(mfilter.statement_blob())
    rows = await db.execute(
        select(Statement.id, Statement.prop, src).where(Statement.id.in_(near))
    )
    return group_places(
        [
            (source, places[sid], prop)
            for sid, prop, source in rows
            if source not in fam
        ],
        here,
    )


def group_places(rows, here: dict) -> list[dict]:
    """(source, place, prop) claims elsewhere into one row per source and place."""
    groups: dict[tuple, Counter] = {}
    names = {}
    for source, place, prop in rows:
        groups.setdefault((source, place.id), Counter())[prop] += 1
        names[place.id] = place.name_ar or place.name_en
    sources = Counter(pid for _, pid in groups)
    out = [
        {
            "id": f"place:{pid}:{source}",
            "kind": "Same place",
            "other": source,
            "place": str(pid),
            "at_place": names[pid],
            "props_there": [p for p, _ in props.most_common()],
            "props_here": list(here[pid]),
            "count": props.total(),
            "sources": sources[pid],
            "strength": "resolved place",
        }
        for (source, pid), props in groups.items()
    ]
    out.sort(key=lambda r: (r["sources"], -r["count"], r["at_place"], r["other"]))
    return out[:20]


async def nearest(db, sha: str, fam: list[str], investigation) -> list[dict]:
    """Closest segments of other sources in the active embedding space, best per
    source. Leads by meaning only."""
    await db.execute(text("set local hnsw.iterative_scan = 'strict_order'"))
    mine_seg, mine_vec = aliased(Segment), aliased(SegmentEmbedding)
    seg, vec = aliased(Segment), aliased(SegmentEmbedding)
    distance = vec.vector.cosine_distance(mine_vec.vector)
    case = {"investigation": investigation, "time": None}
    probe = (
        select(
            seg.source_sha256.label("source"),
            seg.kind.label("kind"),
            seg.location.label("location"),
            distance.label("distance"),
        )
        .join(vec, vec.segment_id == seg.id)
        .where(
            vec.space_id == mine_vec.space_id,
            seg.source_sha256.not_in(fam),
            mfilter.blob_clause(case, seg.source_sha256),
            available(seg.source_sha256),
        )
        .order_by(distance)
        .limit(4)
        .lateral()
    )
    mine = (
        select(mine_seg.id)
        .join(mine_vec, mine_vec.segment_id == mine_seg.id)
        .join(EmbeddingSpace, EmbeddingSpace.id == mine_vec.space_id)
        .where(mine_seg.source_sha256 == sha, EmbeddingSpace.active.is_(True))
        .order_by(mine_seg.kind, mine_seg.ordinal, mine_seg.start)
        .limit(NEAR_SEGMENTS)
    )
    rows = await db.execute(
        select(
            mine_seg.kind,
            mine_seg.location,
            probe.c.source,
            probe.c.kind,
            probe.c.location,
            probe.c.distance,
        )
        .select_from(mine_seg)
        .join(mine_vec, mine_vec.segment_id == mine_seg.id)
        .join(probe, true())
        .where(mine_seg.id.in_(mine))
    )
    best: dict[str, tuple] = {}
    for row in rows:
        if row[2] not in best or row[5] < best[row[2]][5]:
            best[row[2]] = row
    return [lead(*row) for row in sorted(best.values(), key=lambda r: r[5])[:6]]


def lead(kind, location, source, their_kind, theirs, d) -> dict:
    """A nearest-by-meaning row. The distance stays out of the sentence: a band
    word reads plainly, and the number is for the tooltip."""
    return {
        "id": f"near:{source}",
        "kind": "Nearest by meaning",
        "other": source,
        "why": f"{where(kind, location)} here is close to "
        f"{where(their_kind, theirs)} there. A lead, not a match.",
        "strength": band(d),
        "distance": round(d, 4),
        "here": location.get("ms"),
        "there": theirs.get("ms"),
    }


async def links(db, acqs, investigation) -> list[dict]:
    """Excerpt relations recorded at intake, both directions."""
    mine = acqs[0].context
    file = Acquisition.context["file"].astext
    rows = []
    if excerpt_of := mine.get("excerpt_of"):
        rows += [
            (a, "Excerpt of", excerpt_of)
            for a in await _acquired(
                db, investigation, file.is_not(None), _prefix(excerpt_of, file)
            )
        ]
    if mine.get("file"):
        rows += [
            (a, "Has excerpt", a.context["excerpt_of"])
            for a in await _acquired(
                db,
                investigation,
                Acquisition.context["excerpt_of"].astext.startswith(mine["file"]),
            )
        ]
    return [
        {
            "id": f"link:{a.blob_sha256}:{kind}",
            "kind": kind,
            "other": a.blob_sha256,
            "why": f"Recorded at intake: excerpt of {why}",
            "strength": "recorded",
        }
        for a, kind, why in rows
        if a.blob_sha256 != acqs[0].blob_sha256
    ]


def _prefix(value: str, column):
    return literal(value).startswith(column)


async def _acquired(db, investigation, *where):
    return list(
        (
            await db.execute(
                select(Acquisition).where(
                    Acquisition.context["investigation"].astext == investigation,
                    *where,
                )
            )
        ).scalars()
    )


async def item(db, sha: str, user) -> dict | None:
    blob = await source_blob(db, sha)
    if blob is None:
        return None
    page = await data.blob_page(db, sha)
    acqs = (
        (
            await db.execute(
                select(Acquisition)
                .where(Acquisition.blob_sha256 == sha)
                .order_by(Acquisition.at)
            )
        )
        .scalars()
        .all()
    )
    investigation = acqs[0].context.get("investigation")
    fam = await family(db, sha)
    claims = await data.statement_rows(
        db,
        mfilter.statement_blob().in_(fam),
        Statement.prop.in_(EXTRACTED_PROPS),
        order=Statement.recorded_at,
        limit=2000,
    )
    # The blob a span counts characters in (the source, or text derived from it).
    span_blobs = dict(
        (
            await db.execute(
                select(Statement.id, Statement.span_blob).where(
                    Statement.id.in_([uuid.UUID(c["id"]) for c in claims])
                )
            )
        ).all()
    )
    for c in claims:
        c["span_blob"] = span_blobs.get(uuid.UUID(c["id"]))
    people = {c["subject"] for c in claims if c["subject"].startswith("person:")}
    found_copies = await copies(db, sha)
    persons = await same_person(db, sha, people)
    relations = [
        *persons,
        *await links(db, acqs, investigation),
        *await same_place(db, sha, fam, investigation, claims),
        *await nearest(db, sha, fam, investigation),
    ]
    decided = [c["id"] for c in claims] + [
        r["statement"] for r in found_copies + persons
    ]
    can = await reviewable(db, decided, user)
    for row in claims + found_copies + persons:
        row["can_review"] = can[row.get("statement", row["id"])]
    others = {r["other"] for r in found_copies + relations if r.get("other")} - {sha}
    ctx = await data.Context.load(db, blobs=others)
    mimes = dict(
        (
            await db.execute(
                select(Blob.sha256, Blob.mime).where(Blob.sha256.in_(others))
            )
        ).all()
    )
    sensitive_docs = {o for o in others if ctx.doc(o)["sensitive"]}
    docs = {
        o: ctx.doc(o)
        | {
            "mime": mimes[o],
            "kind": kind_of(mimes[o]),
            "revealed": granted(o) or o not in sensitive_docs,
        }
        for o in others
    }
    segments = (
        (
            await db.execute(
                select(Segment)
                .where(Segment.source_sha256 == sha)
                .order_by(Segment.kind, Segment.ordinal, Segment.start)
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    pages = None
    has_file = blobs.stored(blob, Config.BLOB_DIR)
    if blob.mime == "application/pdf" and has_file:
        path = await asyncio.to_thread(blobs.verified_path, blob, Config.BLOB_DIR)
        pages = await asyncio.to_thread(_pdf_pages, path)
    custody = await data.statement_rows(
        db,
        Statement.subject == f"blob:{sha}",
        Statement.prop.not_in({Prop.MEDIA_MATCH, *EXTRACTED_PROPS}),
        limit=200,
    )
    meta = {}
    for row in sorted(custody, key=lambda r: r["recorded_at"]):
        meta[row["prop"]] = row  # the latest of each kind
    payloads = dict(
        (
            await db.execute(
                select(Statement.id, Payload.value)
                .join(Payload, Payload.commitment == Statement.commitment)
                .where(
                    Statement.id.in_(
                        [
                            uuid.UUID(meta[p]["id"])
                            for p in (
                                Prop.C2PA_VERIFY,
                                Prop.FINGERPRINT,
                                Prop.URL_LIVENESS,
                            )
                            if p in meta
                        ]
                    )
                )
            )
        ).all()
    )
    history = await data.statement_rows(
        db,
        Statement.subject.in_([f"statement:{i}" for i in decided]),
        Statement.prop.in_(REVIEW_PROPS),
        limit=200,
    )
    return page | {
        "kind": kind_of(blob.mime),
        "investigation": investigation,
        "context": acqs[0].context,
        "revealed": granted(sha) or not page["sensitive"],
        "stored": has_file,
        "synthetic": blob.storage_ref == blobs.SYNTHETIC,
        "pages": pages,
        "claims": claims,
        "copies": found_copies,
        "relations": relations,
        "docs": docs,
        "segments": [
            {
                "id": str(s.id),
                "kind": s.kind,
                "location": s.location,
                "image": s.artifact_sha256 is not None and s.kind != "audio",
                "text": s.text_sha256 is not None,
            }
            for s in segments
        ],
        "c2pa": payloads.get(uuid.UUID(meta["c2pa_verify"]["id"]))
        if "c2pa_verify" in meta
        else None,
        "fingerprint": payloads.get(uuid.UUID(meta["fingerprint"]["id"]))
        if "fingerprint" in meta
        else None,
        "liveness": meta.get("url_liveness"),
        "custody": custody,
        "threads": await threads(db, decided, user),
        "history": history,
    }


async def transcript_timeline(db, sha: str) -> list[list[int]]:
    """[char offset in the joined transcript, start ms, end ms] per spoken line, from
    the segments each transcribe run lists. Claims point at those offsets."""
    segments = []
    for ref in await db.scalars(
        select(Run.raw_output_ref).where(
            Run.tool == "transcribe",
            Run.inputs["blobs"][0].astext == sha,
            Run.raw_output_ref.is_not(None),
        )
    ):
        part = await db.get(Blob, ref)
        segments += orjson.loads(blobs.read(part, Config.BLOB_DIR))["segments"]
    timeline, at = [], 0
    for s in sorted(segments, key=lambda s: s["ms"][0]):
        timeline.append([at, *s["ms"]])
        at += len(s["text"]) + 1  # lines are joined with "\n"
    return timeline


async def content_read(db, sha: str, blob: Blob) -> dict:
    """What the source says: its own text, text read from it (page text, any
    transcript with a time location) and claim values unmasked. Callers check the
    reveal grant first."""
    fam = await family(db, sha)
    values = {
        str(i): data.display(v)
        for i, v in await db.execute(
            select(Statement.id, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(
                mfilter.statement_blob().in_(fam),
                Statement.prop.in_(EXTRACTED_PROPS),
            )
        )
    }
    texts = (
        await db.execute(
            select(Segment.text_sha256, Segment.kind, Segment.location)
            .where(
                Segment.source_sha256 == sha,
                Segment.text_sha256.is_not(None),
                Segment.text_sha256 != sha,
            )
            .distinct(Segment.text_sha256)
            .order_by(Segment.text_sha256)
            .limit(200)
        )
    ).all()
    stored = {
        b.sha256: b
        for b in (
            await db.execute(select(Blob).where(Blob.sha256.in_([t[0] for t in texts])))
        ).scalars()
    }

    def read(b):
        return blobs.read(b, Config.BLOB_DIR).decode()

    passages = [
        {
            "text_blob": t,
            "kind": k,
            "location": loc,
            "text": await asyncio.to_thread(read, stored[t]),
        }
        for t, k, loc in texts
    ]
    passages.sort(
        key=lambda p: (
            p["location"].get("page", 0),
            (p["location"].get("ms") or [0])[0],
        )
    )
    return {
        "timeline": await transcript_timeline(db, sha),
        "text": await asyncio.to_thread(read, blob)
        if blob.mime.startswith("text/")
        else None,
        "passages": passages,
        "values": values,
    }
