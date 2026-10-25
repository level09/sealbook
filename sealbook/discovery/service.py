"""Local enrichment workers and a hybrid search over the same source-aware filter."""

import asyncio
import re

import orjson
from sqlalchemy import (
    Text,
    and_,
    case,
    cast,
    delete,
    exists,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from sealbook import blobs
from sealbook.discovery import audio, compute, embeddings, media
from sealbook.discovery.models import (
    DIMENSIONS,
    AudioLandmark,
    EntityEmbedding,
    MediaFrame,
    SearchChunk,
    Segment,
    SegmentEmbedding,
)
from sealbook.discovery.text import first_match, folded, identifier
from sealbook.ledger import chain, write
from sealbook.ledger.contracts import (
    EXTRACTED_PROPS,
    Prop,
    Refused,
    StatementIn,
    Tier,
)
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Run,
    Statement,
    StatementTier,
)
from sealbook.monitor import filter as mfilter
from sealbook.settings import Config


def available(sha):
    """Erased source material must not reappear through a derived search index."""
    erased = aliased(Statement)
    value = aliased(Payload)
    candidate, candidate_value = aliased(Statement), aliased(Payload)
    related, related_value = aliased(Statement), aliased(Payload)
    expression = literal(sha) if isinstance(sha, str) else sha
    roots = (
        select(expression)
        .correlate_except()
        .union_all(
            select(candidate_value.value["blob"].astext)
            .join(candidate, candidate.commitment == candidate_value.commitment)
            .where(
                candidate.subject == func.concat("blob:", expression),
                candidate.prop == Prop.DERIVED_FROM,
            )
            .correlate_except(candidate, candidate_value)
        )
    )
    shares_root = exists(
        select(related.id)
        .join(related_value, related_value.commitment == related.commitment)
        .where(
            related.subject == func.concat("blob:", erased.span_blob),
            related.prop == Prop.DERIVED_FROM,
            related_value.value["blob"].astext.in_(roots),
        )
        .correlate_except(related, related_value)
    )
    return ~exists(
        select(erased.id)
        .outerjoin(value, value.commitment == erased.commitment)
        .where(
            erased.span_blob.is_not(None),
            value.commitment.is_(None),
            or_(erased.span_blob.in_(roots), shares_root),
        )
        .correlate_except(erased, value)
    )


async def queue(
    db, acq, mime, blob_sha256=None, rebuild=False, location=None, ordinal=0
):
    from sealbook.ingest.service import system_agent

    tools = []
    if mime == "application/pdf":
        tools.append(("pdf", None))
    if Config.EMBEDDING_MODEL and (
        mime == "application/pdf"
        or mime.startswith(("text/", "image/", "audio/", "video/"))
    ):
        tools.append(("embed", Config.EMBEDDING_MODEL))
    if mime.startswith(("image/", "video/", "audio/")):
        tools.append(("fingerprint", None))
    if mime == "application/pdf" or mime.startswith(("image/", "video/", "audio/")):
        tools.append(("thumbnail", None))
    if mime.startswith(("image/", "video/")):
        tools.append(("geotag", None))
    if mime.startswith("text/"):
        tools.append(("index", None))
    ids = []
    for tool, model in tools:
        pending = await db.scalar(
            select(Run)
            .where(
                Run.tool == tool,
                Run.model_requested == model,
                Run.status.in_(["queued", "running"]),
                Run.inputs["acquisition"].astext == str(acq.id),
                Run.inputs["blobs"][0].astext == (blob_sha256 or acq.blob_sha256),
                Run.inputs["ordinal"].as_integer() == ordinal,
            )
            .limit(1)
        )
        if pending:
            if rebuild:
                # Only a run already reading the source races a rebuild. A queued
                # one has read nothing yet, so it becomes the rebuild; this keeps an
                # unrelated queued tool (a thumbnail) from blocking a fingerprint
                # rebuild.
                if pending.status == "running":
                    raise Refused("wait for current discovery work before rebuilding")
                pending.inputs = {**pending.inputs, "rebuild": True}
                ids.append(str(pending.id))
            continue
        run = Run(
            principal_id=(await system_agent(db, "discovery")).id,
            tool=tool,
            route=Config.EMBEDDING_ROUTE if tool == "embed" else "local",
            model_requested=model,
            inputs={
                "blobs": [blob_sha256 or acq.blob_sha256],
                "acquisition": str(acq.id),
                "rebuild": rebuild,
                "location": location or {},
                "segment_kind": "page" if location and "page" in location else "text",
                "ordinal": ordinal,
            },
        )
        db.add(run)
        await db.flush()
        ids.append(str(run.id))
    return ids


async def actor_profile(db, subject, investigation):
    rows = (
        await db.execute(
            select(Statement, Payload.value, StatementTier.tier)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.subject == subject,
                Statement.prop.in_(EXTRACTED_PROPS),
                StatementTier.tier != Tier.REJECTED,
                write.current_clause(),
                mfilter.statement_clause(
                    {"investigation": investigation or None, "time": None}
                ),
                available(Statement.span_blob),
            )
            .order_by(Statement.prop, Statement.recorded_at, Statement.id)
        )
    ).all()
    facts = [
        {
            "statement": str(st.id),
            "hash": st.hash,
            "prop": st.prop,
            "tier": tier,
            "value": value,
            "from": st.valid_from.isoformat() if st.valid_from else None,
            "to": st.valid_to.isoformat() if st.valid_to else None,
        }
        for st, value, tier in rows
    ]
    text = "\n".join(
        f"{fact['prop']} [{fact['tier']}; {fact['from'] or 'undated'}]: {orjson.dumps(fact['value']).decode()}"
        for fact in facts
    )
    pointers = [
        {key: fact[key] for key in ("statement", "hash", "tier")} for fact in facts
    ]
    return text, chain.sha256(chain.canon(pointers)), facts


async def queue_actor(db, subject, acq, rebuild=False):
    if not Config.EMBEDDING_MODEL or not subject.startswith("person:"):
        return
    from sealbook.ingest.service import system_agent

    investigation = acq.context.get("investigation") or ""
    pending = await db.scalar(
        select(Run.id)
        .where(
            Run.tool == "embed_actor",
            Run.status == "queued",
            Run.model_requested == Config.EMBEDDING_MODEL,
            Run.inputs["subject"].astext == subject,
            Run.inputs["investigation"].astext == investigation,
        )
        .limit(1)
    )
    if pending:
        if rebuild:
            run = await db.get(Run, pending)
            run.inputs = {**run.inputs, "rebuild": True}
        return
    db.add(
        Run(
            principal_id=(await system_agent(db, "discovery")).id,
            tool="embed_actor",
            route=Config.EMBEDDING_ROUTE,
            model_requested=Config.EMBEDDING_MODEL,
            inputs={
                "subject": subject,
                "investigation": investigation,
                "blobs": [acq.blob_sha256],
                "acquisition": str(acq.id),
                "rebuild": rebuild,
            },
        )
    )


async def actor_changed(db, statement):
    if not statement.subject.startswith("person:") or not statement.span_blob:
        return
    source = await db.scalar(select(mfilter.source(statement.span_blob)))
    for acq in (
        await db.execute(select(Acquisition).where(Acquisition.blob_sha256 == source))
    ).scalars():
        await queue_actor(db, statement.subject, acq)


async def embed_actor(c):
    subject, investigation = c.run.inputs["subject"], c.run.inputs["investigation"]
    profile, digest, facts = await actor_profile(c.s, subject, investigation)
    old = await c.s.scalar(
        select(EntityEmbedding).where(
            EntityEmbedding.subject == subject,
            EntityEmbedding.investigation == investigation,
            EntityEmbedding.space_id.in_(
                embeddings.active_spaces(model=c.run.model_requested)
            ),
        )
    )
    if (
        old is not None
        and old.content_hash == digest
        and not c.run.inputs.get("rebuild")
    ):
        await c.emit("unchanged", actors=1)
        return None
    if not facts:
        raise Refused("actor has no current source-backed claims")
    vectors, served, endpoint, raw, space = await embeddings.call(
        c.s,
        [embeddings.DOCUMENT_PREFIX + profile],
        c.run.model_requested,
        c.transport,
        rebuild=bool(c.run.inputs.get("rebuild")),
    )
    pointers = [
        {key: fact[key] for key in ("statement", "hash", "tier")} for fact in facts
    ]
    snapshot = await blobs.put(
        c.s,
        orjson.dumps({"subject": subject, "statements": pointers}),
        "application/json",
        Config.BLOB_DIR,
    )
    c.run.inputs = {
        **c.run.inputs,
        "profile": snapshot.sha256,
        "content_hash": digest,
    }
    await embeddings.bind_run(c, space)
    c.run.model_served, c.run.endpoint, c.run.provider_served = (
        served,
        endpoint,
        "local",
    )
    artifact = await blobs.put(c.s, raw, "application/json", Config.BLOB_DIR)
    c.run.raw_output_ref = artifact.sha256
    fields = {
        "subject": subject,
        "investigation": investigation,
        "space_id": space,
        "content_hash": digest,
        "run_id": c.run.id,
        "vector": vectors[0],
    }
    await c.s.execute(
        insert(EntityEmbedding)
        .values(**fields)
        .on_conflict_do_update(
            index_elements=[
                EntityEmbedding.subject,
                EntityEmbedding.investigation,
                EntityEmbedding.space_id,
            ],
            set_={key: fields[key] for key in ("content_hash", "run_id", "vector")},
        )
    )
    await c.emit("actor_embedded", actors=1, statements=len(facts))
    return artifact.sha256


async def actor_search(db, query, f, query_embedding=None, named=None):
    from sealbook.people import data as people

    if named is None:
        named = await people.search(db, query)
    keyword = [f"person:{r['person']}" for r in named]
    semantic, details = [], {}
    if query_embedding:
        values, space = query_embedding
        distance = EntityEmbedding.vector.cosine_distance(values)
        q = select(EntityEmbedding, distance).where(EntityEmbedding.space_id == space)
        if f["investigation"]:
            q = q.where(EntityEmbedding.investigation == f["investigation"])
        rows = await db.execute(q.order_by(distance).limit(10))
        for row, d in rows:
            _, digest, facts = await actor_profile(db, row.subject, row.investigation)
            if digest == row.content_hash and facts:
                semantic.append(row.subject)
                details[row.subject] = {
                    "statements": len(facts),
                    "score": round(1 - float(d), 3),
                }
    result = []
    # Name matches first; profiles near by meaning are leads after them.
    for subject in dict.fromkeys(keyword + semantic):
        visible = await db.scalar(
            select(Statement.id)
            .where(
                Statement.subject == subject,
                Statement.prop.in_(EXTRACTED_PROPS),
                mfilter.statement_clause(f),
                available(Statement.span_blob),
            )
            .limit(1)
        )
        if visible:
            result.append(
                {
                    "subject": subject,
                    "kind": "person",
                    **details.get(subject, {}),
                    "signals": [
                        name
                        for name, ranking in (
                            ("keyword", keyword),
                            ("semantic", semantic),
                        )
                        if subject in ranking
                    ],
                }
            )
    return result[:50]


async def _input(c):
    sha = c.run.inputs["blobs"][0]
    blob = await c.s.get(Blob, sha)
    if not await c.s.scalar(
        select(available(Blob.sha256) & available(c.acq.blob_sha256)).where(
            Blob.sha256 == sha
        )
    ):
        raise Refused("source contains erased claims")
    return blob


async def embed(c):
    from sealbook.discovery.indexing import continue_run, progress, store, text_segment

    blob = await _input(c)
    if not blob.mime.startswith("text/"):
        return await media.index(c)
    source = blobs.read(blob, Config.BLOB_DIR).decode()
    if not source.strip():
        raise ValueError("cannot embed empty text")
    spans = compute.chunks(source)
    cursor = c.run.inputs.get("cursor", 0)
    if type(cursor) is not int or not 0 <= cursor <= len(spans):
        raise ValueError("invalid embedding cursor")
    if c.run.inputs.get("rebuild") and cursor == 0:
        await c.s.execute(
            delete(SegmentEmbedding).where(
                SegmentEmbedding.segment_id.in_(
                    select(Segment.id).where(Segment.text_sha256 == blob.sha256)
                )
            )
        )
    existing = set(
        (
            await c.s.execute(
                select(Segment.start)
                .join(SegmentEmbedding, SegmentEmbedding.segment_id == Segment.id)
                .where(
                    Segment.text_sha256 == blob.sha256,
                    SegmentEmbedding.space_id.in_(
                        embeddings.active_spaces(model=c.run.model_requested)
                    ),
                )
            )
        ).scalars()
    )
    end = min(len(spans), cursor + 16)
    batch = [(a, b) for a, b in spans[cursor:end] if a not in existing]
    c.run.prompt_hash = chain.sha256(
        chain.canon({"chunk_size": 1800, "dimensions": DIMENSIONS})
    )
    if batch:
        vectors, served, endpoint, raw, space = await embeddings.call(
            c.s,
            [embeddings.DOCUMENT_PREFIX + source[a:b] for a, b in batch],
            c.run.model_requested,
            c.transport,
            rebuild=bool(c.run.inputs.get("rebuild")),
        )
        await embeddings.bind_run(c, space)
        c.run.model_served, c.run.endpoint, c.run.provider_served = (
            served,
            endpoint,
            "local",
        )
        artifact = await blobs.put(c.s, raw, "application/json", Config.BLOB_DIR)
        manifest = await blobs.put(
            c.s,
            orjson.dumps({"responses": [artifact.sha256]}),
            "application/json",
            Config.BLOB_DIR,
        )
        c.run.raw_output_ref = manifest.sha256
        for (start, end_char), value in zip(batch, vectors, strict=True):
            await store(
                c, await text_segment(c, blob.sha256, start, end_char), space, value
            )
        await c.emit("embedded", chunks=len(batch))
    else:
        await c.emit("unchanged", blobs=1)
    await progress(
        c, end, len(spans), "chunks", f"{blob.sha256}:{c.run.model_requested}"
    )
    if end < len(spans):
        continue_run(c, end)
    return c.run.raw_output_ref


async def fingerprint(c):
    blob = await _input(c)
    if not c.run.inputs.get("rebuild") and await c.s.scalar(
        select(Statement.id)
        .where(
            Statement.subject == f"blob:{blob.sha256}",
            Statement.prop == Prop.FINGERPRINT,
        )
        .limit(1)
    ):
        await c.emit("unchanged", blobs=1)
        return None
    path = await asyncio.to_thread(blobs.verified_path, blob, Config.BLOB_DIR)
    signals = await asyncio.to_thread(
        compute.fingerprint, path, blob.mime, Config.FINGERPRINT_MAX_SECONDS
    )
    frames, marks = signals["visual"], signals["audio"]
    params = {
        "algorithm": "pdq-frames+audio-landmarks-v1",
        "sample_seconds": 1,
        "audio_rate": audio.SAMPLE_RATE,
        "audio_hop": audio.HOP,
        "distance": Config.PDQ_DISTANCE,
        "quality": Config.PDQ_QUALITY,
        "candidate_limit": Config.MEDIA_CANDIDATES,
    }
    c.run.prompt_hash = chain.sha256(chain.canon(params))
    artifact = await blobs.put(
        c.s, orjson.dumps(params | signals), "application/json", Config.BLOB_DIR
    )
    c.run.raw_output_ref = artifact.sha256
    await write.append(
        c.s,
        StatementIn(
            subject=f"blob:{blob.sha256}",
            prop=Prop.FINGERPRINT,
            value={"artifact": artifact.sha256, **params},
            author_id=c.run.principal_id,
            run_id=c.run.id,
        ),
        sign=c.sign,
    )
    f = {"investigation": c.acq.context.get("investigation"), "time": None}
    candidates = {}
    await c.s.execute(text("set local hnsw.iterative_scan = 'strict_order'"))
    # Bounded sampling retrieves candidates. Full fingerprints decide which frames match.
    usable = list(
        {f["hash"]: f for f in frames if f["quality"] >= Config.PDQ_QUALITY}.values()
    )
    step = max(1, (len(usable) + 31) // 32)
    for frame in usable[::step]:
        bits = f"{int(frame['hash'], 16):0256b}"
        distance = MediaFrame.hash.hamming_distance(bits)
        found = await c.s.execute(
            select(MediaFrame.blob_sha256, distance.label("distance"))
            .where(
                MediaFrame.blob_sha256 != blob.sha256,
                MediaFrame.quality >= Config.PDQ_QUALITY,
                mfilter.blob_clause(f, MediaFrame.blob_sha256),
                available(MediaFrame.blob_sha256),
            )
            .order_by(distance)
            .limit(8)
        )
        for sha, measured in found:
            if measured <= Config.PDQ_DISTANCE:
                candidates[sha] = min(candidates.get(sha, 256), measured)
    picture_candidates = sorted(candidates, key=candidates.get)[
        : Config.MEDIA_CANDIDATES
    ]
    sound_candidates = []
    if marks:
        hashes = sorted({m["hash"] for m in marks})
        hashes = hashes[:: max(1, (len(hashes) + 4095) // 4096)]
        sound_candidates = list(
            (
                await c.s.execute(
                    select(AudioLandmark.blob_sha256)
                    .where(
                        AudioLandmark.hash.in_(hashes),
                        AudioLandmark.blob_sha256 != blob.sha256,
                        mfilter.blob_clause(f, AudioLandmark.blob_sha256),
                        available(AudioLandmark.blob_sha256),
                    )
                    .group_by(AudioLandmark.blob_sha256)
                    .order_by(func.count(func.distinct(AudioLandmark.hash)).desc())
                    .limit(Config.MEDIA_CANDIDATES)
                )
            ).scalars()
        )
    selected = compute.rrf(picture_candidates, sound_candidates)[
        : Config.MEDIA_CANDIDATES
    ]
    for sha in selected:
        rejected = await c.s.scalar(
            select(Statement.id)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == Prop.MEDIA_MATCH,
                StatementTier.reason == "human_reject",
                tuple_(Statement.subject, Payload.value["blob"].astext).in_(
                    [(f"blob:{blob.sha256}", sha), (f"blob:{sha}", blob.sha256)]
                ),
            )
            .limit(1)
        )
        if rejected:
            await c.emit("candidate_skipped", drop_reason="human_reject", candidates=1)
            continue
        rows = (
            await c.s.execute(
                select(
                    MediaFrame.ms, MediaFrame.quality, cast(MediaFrame.hash, Text)
                ).where(MediaFrame.blob_sha256 == sha)
            )
        ).all()
        other = [
            {"hash": f"{int(bits, 2):064x}", "quality": quality, "ms": ms}
            for ms, quality, bits in rows
        ]
        measured = await asyncio.to_thread(
            compute.match_frames, frames, other, Config.PDQ_DISTANCE, Config.PDQ_QUALITY
        )
        sound_rows = (
            await c.s.execute(
                select(AudioLandmark.hash, AudioLandmark.time).where(
                    AudioLandmark.blob_sha256 == sha
                )
            )
        ).all()
        sound = await asyncio.to_thread(
            audio.align, marks, [{"hash": h, "time": t} for h, t in sound_rows]
        )
        if measured or sound:
            await write.append(
                c.s,
                StatementIn(
                    subject=f"blob:{blob.sha256}",
                    prop=Prop.MEDIA_MATCH,
                    value={
                        "blob": sha,
                        "kind": "media_similarity",
                        "picture": measured,
                        "sound": sound,
                        "fingerprint": artifact.sha256,
                    },
                    author_id=c.run.principal_id,
                    run_id=c.run.id,
                ),
                sign=c.sign,
            )
    if c.run.inputs.get("rebuild"):
        for projection in (MediaFrame, AudioLandmark):
            await c.s.execute(
                delete(projection).where(projection.blob_sha256 == blob.sha256)
            )
    for frame in frames:
        c.s.add(
            MediaFrame(
                blob_sha256=blob.sha256,
                run_id=c.run.id,
                ms=frame["ms"],
                quality=frame["quality"],
                hash=f"{int(frame['hash'], 16):0256b}",
            )
        )
    for h, t in {(m["hash"], m["time"]) for m in marks}:
        c.s.add(AudioLandmark(blob_sha256=blob.sha256, run_id=c.run.id, hash=h, time=t))
    await c.emit(
        "fingerprinted",
        frames=len(frames),
        audio_landmarks=len(marks),
        candidates=len(selected),
    )
    return artifact.sha256


SHA = re.compile(r"[0-9a-f]{8,64}")
LIMIT = 50
# Exact signals rank in this order; meaning-only rows always follow them.
EXACT = (
    "sha256",
    "literal",
    "normalized",
    "words",
    "file_name",
    "name_variant",
    "place_name",
)
SEEDS, NEAR = 5, 8


def _contains(needle):
    clause = SearchChunk.normalized.contains(needle, autoescape=True)
    if identifier(needle):
        # Boundaries apply before the candidate limit, not after it.
        clause &= SearchChunk.normalized.op("~")(
            r"(^|[^[:alnum:]])" + re.escape(needle) + r"($|[^[:alnum:]])"
        )
    return clause


async def _chunks(db, clause, f, prefer=None):
    order = [SearchChunk.blob_sha256, SearchChunk.start]
    if prefer:
        order.insert(
            0, case((SearchChunk.text.icontains(prefer, autoescape=True), 0), else_=1)
        )
    return (
        await db.execute(
            select(SearchChunk, Segment)
            .join(
                Segment,
                (Segment.text_sha256 == SearchChunk.blob_sha256)
                & (Segment.start == SearchChunk.start),
            )
            .where(
                clause,
                mfilter.blob_clause(f, Segment.source_sha256),
                available(Segment.source_sha256),
            )
            .order_by(*order)
            .limit(LIMIT)
        )
    ).all()


async def spellings(db, query, named=None):
    """Other recorded spellings of a person or gazetteer place the query names.
    Name keys are lossy, so a document found through one stays labelled with the
    spelling that matched; it is a place to look, never an identity decision."""
    from sealbook.people import data as people
    from sealbook.places.models import PlaceName
    from sealbook.places.resolve import candidates

    out = {}
    if named is None:
        named = await people.search(db, query)
    ids = [sid for row in named for sid in row["statements"]]
    if ids:
        for (value,) in await db.execute(
            select(Payload.value)
            .join(Statement, Statement.commitment == Payload.commitment)
            .where(Statement.id.in_(ids))
        ):
            for name in (value.get("text"),) if isinstance(value, dict) else (value,):
                if isinstance(name, str):
                    out.setdefault(name, "name_variant")
    places = [place.id for place in await candidates(db, query)]
    if places:
        for (name,) in await db.execute(
            select(PlaceName.name).where(PlaceName.place_id.in_(places))
        ):
            out.setdefault(name, "place_name")
    needle = folded(query)[0].strip()
    # ponytail: first 12 spellings; rank by frequency if a name gathers more.
    return dict([(k, v) for k, v in out.items() if folded(k)[0].strip() != needle][:12])


def _text_row(row, segment, span, signal, **why):
    return {
        "sha256": segment.source_sha256,
        "text_blob": row.blob_sha256,
        "segment": str(segment.id),
        "kind": segment.kind,
        "location": segment.location,
        "span": {"start": row.start + span["start"], "end": row.start + span["end"]},
        "signals": [signal],
        **why,
    }


def _lead(segment, signal):
    row = {
        "sha256": segment.source_sha256,
        "segment": str(segment.id),
        "kind": segment.kind,
        "location": segment.location,
        "signals": [signal],
    }
    if segment.text_sha256:
        row |= {
            "text_blob": segment.text_sha256,
            "span": {"start": segment.start, "end": segment.end},
        }
    return row


async def search(db, query, mode, f, transport=None, query_embedding=None, named=None):
    """Exact matches first (hash, phrase, other spellings, all words), then leads
    by meaning. One row per source; further located hits of a source go to `also`.
    Offsets live in the index; text is always read from the immutable source."""
    if mode not in ("keyword", "hybrid"):
        raise ValueError("mode must be keyword or hybrid")
    needle = folded(query)[0].strip()
    if not needle:
        return []
    details = {}

    def add(key, row):
        if key in details:
            for signal in row["signals"]:
                if signal not in details[key]["signals"]:
                    details[key]["signals"].append(signal)
            # A spelling signal names its spelling; keep it when an earlier probe
            # found the same passage without one.
            if "matched" in row:
                details[key].setdefault("matched", row["matched"])
        else:
            details[key] = row

    if SHA.fullmatch(query.strip().lower()):
        prefix = query.strip().lower()
        for sha in (
            await db.execute(
                select(Blob.sha256)
                .where(
                    Blob.sha256.startswith(prefix)
                    if len(prefix) < 64
                    else Blob.sha256 == prefix,
                    mfilter.blob_clause(f, Blob.sha256),
                    available(Blob.sha256),
                )
                .order_by(Blob.sha256)
                .limit(10)
            )
        ).scalars():
            add(
                sha,
                {
                    "sha256": sha,
                    "location": {},
                    "signals": ["sha256"],
                    "matched": prefix,
                },
            )
    names = func.coalesce(
        Acquisition.context["file"].astext, Acquisition.context["filename"].astext
    )
    for sha, name, origin in (
        await db.execute(
            select(Acquisition.blob_sha256, names, Acquisition.origin)
            .where(
                or_(
                    names.icontains(query.strip(), autoescape=True),
                    Acquisition.origin.icontains(query.strip(), autoescape=True),
                ),
                mfilter.blob_clause(f, Acquisition.blob_sha256),
                available(Acquisition.blob_sha256),
            )
            .order_by(Acquisition.at)
            .limit(10)
        )
    ).all():
        add(
            sha,
            {
                "sha256": sha,
                "location": {},
                "signals": ["file_name"],
                "matched": name
                if name and query.strip().lower() in name.lower()
                else origin,
            },
        )
    for row, segment in await _chunks(db, _contains(needle), f, prefer=query):
        matched = first_match(row.text, query)
        if matched:
            add(segment.id, _text_row(row, segment, *matched))
    variants = await spellings(db, query, named)
    if variants:
        keys = {name: folded(name)[0].strip() for name in variants}
        for row, segment in await _chunks(
            db, or_(*(_contains(k) for k in keys.values() if k)), f
        ):
            for name, signal in variants.items():
                matched = first_match(row.text, name)
                if matched:
                    add(
                        segment.id,
                        _text_row(row, segment, matched[0], signal, matched=name),
                    )
                    break
    terms = list(dict.fromkeys(needle.split()))
    if len(terms) > 1:
        # Every word, any order: PDF text often comes out in visual RTL order and
        # names carry middle names the query leaves out.
        for row, segment in await _chunks(db, and_(*(_contains(t) for t in terms)), f):
            spans = [first_match(row.text, t) for t in terms]
            if all(spans):
                first = min((m[0] for m in spans), key=lambda m: m["start"])
                add(segment.id, _text_row(row, segment, first, "words"))
    semantic, near = [], []
    # A hash names one item; its nearest neighbours by meaning are noise.
    if mode == "hybrid" and not SHA.fullmatch(query.strip().lower()):
        if not Config.EMBEDDING_MODEL:
            raise ValueError("EMBEDDING_MODEL is not configured")
        if query_embedding:
            vector, space = query_embedding
        else:
            vector, space = await embeddings.query(
                db, query, Config.EMBEDDING_MODEL, transport
            )
        distance = SegmentEmbedding.vector.cosine_distance(vector)
        # One pass over text and media segments; a changed model is a new space, never a mix.
        rows = await db.execute(
            select(Segment, distance)
            .join(SegmentEmbedding, SegmentEmbedding.segment_id == Segment.id)
            .where(
                SegmentEmbedding.space_id == space,
                mfilter.blob_clause(f, Segment.source_sha256),
                available(Segment.source_sha256),
            )
            .order_by(distance)
            .limit(LIMIT)
        )
        for segment, measured in rows:
            semantic.append(segment.id)
            add(segment.id, _lead(segment, "semantic"))
            details[segment.id].setdefault("score", round(1 - measured, 3))
        # Leads near the exact hits, as in the approved search design: the passage,
        # page, frame or audio window closest to what the analyst already found.
        seeds = [
            key for key in details if any(s in EXACT for s in details[key]["signals"])
        ]
        seeds = dict(
            (
                await db.execute(
                    select(SegmentEmbedding.segment_id, SegmentEmbedding.vector).where(
                        SegmentEmbedding.space_id == space,
                        SegmentEmbedding.segment_id.in_(
                            [k for k in seeds if not isinstance(k, str)][:SEEDS]
                        ),
                    )
                )
            ).all()
        )
        found = {}
        for seed, values in seeds.items():
            distance = SegmentEmbedding.vector.cosine_distance(values)
            for segment, measured in await db.execute(
                select(Segment, distance)
                .join(SegmentEmbedding, SegmentEmbedding.segment_id == Segment.id)
                .where(
                    SegmentEmbedding.space_id == space,
                    Segment.source_sha256 != details[seed]["sha256"],
                    mfilter.blob_clause(f, Segment.source_sha256),
                    available(Segment.source_sha256),
                )
                .order_by(distance)
                .limit(NEAR)
            ):
                if segment.id not in found or found[segment.id][1] > measured:
                    found[segment.id] = (segment, measured, seed)
        for segment, measured, seed in sorted(found.values(), key=lambda v: v[1]):
            near.append(segment.id)
            add(segment.id, _lead(segment, "near_match"))
            details[segment.id].setdefault(
                "near",
                {
                    "sha256": details[seed]["sha256"],
                    "segment": str(seed),
                    "score": round(1 - measured, 3),
                },
            )
    fused = {key: i for i, key in enumerate(compute.rrf(list(details), semantic, near))}

    def strength(key):
        signals = details[key]["signals"]
        tier = min((EXACT.index(s) for s in signals if s in EXACT), default=len(EXACT))
        return tier, fused[key]

    by_source = {}
    for key in sorted(details, key=strength):
        row = details[key]
        exact = any(signal in EXACT for signal in row["signals"])
        row["group"] = "text_match" if exact else "semantic_lead"
        # A whole-source identifier hit stays apart from located hits in that source.
        place = (row["sha256"], "segment" in row)
        lead = by_source.get(place)
        if lead is None:
            by_source[place] = row | {"also": []}
        elif len(lead["also"]) < 5:
            lead["also"].append(
                {
                    k: row[k]
                    for k in (
                        "segment",
                        "kind",
                        "location",
                        "span",
                        "signals",
                        "score",
                        "near",
                        "matched",
                    )
                    if k in row
                }
            )
    return list(by_source.values())[:LIMIT]
