import asyncio
import io
import re
import time
import uuid
from datetime import UTC, datetime

import httpx
import orjson
from quart import (
    Blueprint,
    Response,
    abort,
    current_app,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
)
from quart_security import current_user
from sqlalchemy import select

from stk import blobs
from stk.collaboration.views import guard, no_store
from stk.discovery import coverage, embeddings, evidence, media, service
from stk.discovery.models import Segment
from stk.ingest import batch
from stk.ingest import service as ingest
from stk.ledger import write
from stk.ledger.contracts import Prop, Refused, StatementIn
from stk.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Run,
    Statement,
    StatementTier,
)
from stk.monitor import data
from stk.monitor import filter as mfilter
from stk.people import data as people_data
from stk.settings import Config

bp = Blueprint("discovery", __name__)
bp.before_request(guard)
bp.after_request(no_store)


def output(value, status=200):
    return Response(orjson.dumps(value), status=status, content_type="application/json")


@bp.errorhandler(Refused)
async def refused(exc):
    return output({"message": exc.reason}, 409)


@bp.get("/evidence")
async def page():
    return await render_template(
        "evidence/index.html",
        boot={
            "investigations": await data.investigations(g.db_session),
            "embeddings": bool(Config.EMBEDDING_MODEL),
        },
    )


@bp.post("/api/evidence/search")
async def search():
    db = g.db_session
    item = ((await request.get_json(silent=True)) or {}).get("item", {})
    query, mode = item.get("query"), item.get("mode", "keyword")
    if (
        not isinstance(query, str)
        or not 1 <= len(query.strip()) <= 1000
        or mode not in ("keyword", "hybrid")
    ):
        abort(
            400,
            description="query must contain 1 to 1000 characters; mode is keyword or hybrid",
        )
    try:
        f = mfilter.parse(orjson.dumps(item.get("filter", {})).decode())
    except ValueError as exc:
        abort(400, description=str(exc))
    embedding, semantic = None, {"status": "off"}
    if mode == "hybrid":
        if not Config.EMBEDDING_MODEL:
            abort(
                503,
                description="Configure a local embedding model to use semantic search",
            )
        try:
            embedding = await embeddings.query(db, query, Config.EMBEDDING_MODEL)
            semantic = {"status": "ok"}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # Exact matches still answer; the failure is shown, never silent.
            current_app.logger.warning("semantic search failed: %s", exc)
            semantic = {
                "status": "failed",
                "message": f"Local embedding service failed: {exc}",
            }
    named = await people_data.search(db, query)
    hits = await service.search(
        db,
        query,
        "hybrid" if embedding else "keyword",
        f,
        query_embedding=embedding,
        named=named,
    )
    people = await service.actor_search(
        db, query, f, query_embedding=embedding, named=named
    )
    ctx = await data.Context.load(db, blobs=[r["sha256"] for r in hits])
    for row in hits:
        row["doc"] = ctx.doc(row["sha256"])
    # documents and media keep the per-segment shape of the current evidence page.
    documents = [row for row in hits if "span" in row]
    media_hits = [row for row in hits if "segment" in row and "span" not in row]
    who = await write.principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    await write.append(
        db,
        StatementIn(
            subject="search:evidence",
            prop=Prop.ACCESS,
            value={
                "action": "search",
                "query": query,
                "mode": mode,
                "filter": mfilter.dump(f),
                "at": datetime.now(UTC).isoformat(),
            },
            author_id=who.id,
        ),
    )
    await db.commit()
    return output(
        {
            "results": hits,
            "people": people,
            "semantic": semantic,
            "documents": documents,
            "media": media_hits,
        }
    )


@bp.get("/api/evidence/coverage")
async def index_coverage():
    try:
        f = mfilter.parse(request.args.get("filter"))
    except ValueError as exc:
        abort(400, description=str(exc))
    return output(await coverage.report(g.db_session, f))


async def source_segment(sid):
    row = await g.db_session.get(Segment, sid)
    if row is None or not await g.db_session.scalar(
        select(service.available(Blob.sha256)).where(Blob.sha256 == row.source_sha256)
    ):
        abort(404)
    return row


def selected_span(row):
    if row.text_sha256 is None:
        return None
    try:
        start = int(request.args.get("start", row.start))
        end = int(request.args.get("end", row.end))
    except (TypeError, ValueError):
        abort(400, description="source positions must be integers")
    if not row.start <= start < end <= row.end:
        abort(400, description="source positions are outside the indexed segment")
    return {"start": start, "end": end}


async def item_page(sha, segment=None, span=None):
    """One page per source. A search hit (a segment) opens it at its page, second
    or characters. A derived blob (page text, a frame) opens its source."""
    if await evidence.source_blob(g.db_session, sha) is None:
        parent = await evidence.parent(g.db_session, sha)
        if parent and await evidence.source_blob(g.db_session, parent):
            return redirect(f"/evidence/{parent}")
        abort(404)
    other = request.args.get("with")
    if other is not None and not re.fullmatch(r"[0-9a-f]{64}", other):
        abort(400, description="with must be a source SHA-256")
    try:
        at = int(request.args["at"]) if "at" in request.args else None
        page = int(request.args["page"]) if "page" in request.args else None
    except ValueError:
        abort(400, description="at and page must be integers")
    return await render_template(
        "evidence/item.html",
        boot={
            "sha": sha,
            "with": other,
            "at": at,
            "page": page,
            "segment": segment
            and {
                "id": str(segment.id),
                "kind": segment.kind,
                "location": segment.location,
                "span": span,
            },
        },
    )


@bp.get("/evidence/<sha>")
async def source_item(sha):
    if sid := request.args.get("segment"):
        try:
            row = await source_segment(uuid.UUID(sid))
        except ValueError:
            abort(400, description="segment must be a UUID")
        if row.source_sha256 != sha:
            abort(400, description="segment belongs to another source")
        return await item_page(sha, row, selected_span(row))
    return await item_page(sha)


@bp.get("/evidence/segments/<uuid:sid>")
async def source_page(sid):
    row = await source_segment(sid)
    return await item_page(row.source_sha256, row, selected_span(row))


@bp.get("/api/evidence/<sha>/item")
async def source_detail(sha):
    found = await evidence.item(g.db_session, sha, current_user)
    if found is None:
        abort(404)
    return output(found)


@bp.get("/api/evidence/<sha>/read")
async def source_read(sha):
    blob = await evidence.source_blob(g.db_session, sha)
    if blob is None:
        abort(404)
    if not await evidence.viewable(g.db_session, sha, current_user):
        abort(403, description="Reveal this source before reading it")
    return output(await evidence.content_read(g.db_session, sha, blob))


@bp.get("/api/evidence/<sha>/segments/<uuid:sid>/image")
async def segment_image(sha, sid):
    db = g.db_session
    row = await db.get(Segment, sid)
    if (
        row is None
        or row.source_sha256 != sha
        or row.artifact_sha256 is None
        or await evidence.source_blob(db, sha) is None
    ):
        abort(404)
    if not await evidence.viewable(g.db_session, sha, current_user):
        abort(403, description="Reveal the evidence before previewing it")
    artifact = await db.get(Blob, row.artifact_sha256)
    if artifact.mime != "image/png":
        abort(404)
    data = await asyncio.to_thread(blobs.read, artifact, Config.BLOB_DIR)
    response = await send_file(io.BytesIO(data), mimetype="image/png")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@bp.get("/api/evidence/segments/<uuid:sid>/excerpt")
async def excerpt(sid):
    row = await source_segment(sid)
    if not await evidence.viewable(g.db_session, row.source_sha256, current_user):
        abort(403, description="Reveal this source before reading its text")
    span = selected_span(row)
    if span is None:
        abort(404)
    blob = await g.db_session.get(Blob, row.text_sha256)
    source = (await asyncio.to_thread(blobs.read, blob, Config.BLOB_DIR)).decode()
    return output(
        {
            "before": source[max(row.start, span["start"] - 160) : span["start"]],
            "match": source[span["start"] : span["end"]],
            "after": source[span["end"] : min(row.end, span["end"] + 160)],
            "span": span,
            "text_blob": row.text_sha256,
        }
    )


@bp.post("/api/evidence/<sha>/analyse")
async def analyse(sha):
    db = g.db_session
    blob = await db.get(Blob, sha)
    acq = await db.scalar(
        select(Acquisition)
        .where(Acquisition.blob_sha256 == sha)
        .order_by(Acquisition.at)
        .limit(1)
    )
    if blob is None or acq is None:
        abort(404)
    item = ((await request.get_json(silent=True)) or {}).get("item", {})
    rebuild = item.get("rebuild", False)
    if not isinstance(rebuild, bool):
        abort(400, description="rebuild must be a boolean")
    queued = await service.queue(db, acq, blob.mime, rebuild=rebuild)
    # Reading claims (OCR, extraction, speech) is queued here too, once.
    read = await ingest.queue_processing(db, acq, blob.mime)
    if read.get("run"):
        queued.append(read["run"])
    subjects = (
        await db.execute(
            select(Statement.subject)
            .where(
                mfilter.source(Statement.span_blob) == sha,
                Statement.subject.startswith("person:"),
            )
            .distinct()
        )
    ).scalars()
    for subject in subjects:
        await service.queue_actor(db, subject, acq, rebuild=rebuild)
    tools = (
        await db.scalars(
            select(Run.tool).where(Run.id.in_([uuid.UUID(i) for i in queued]))
        )
    ).all()
    labels = sorted({batch.LABEL.get(t, t).lower() for t in tools})
    said = f"Queued {', '.join(labels)}." if labels else "Nothing new to queue."
    if not read["queued"]:
        said += f" {read['reason']}"
    await db.commit()
    return output({"runs": queued, "processing": read, "message": said}, 202)


@bp.get("/api/evidence/matches")
async def matches():
    try:
        f = mfilter.parse(request.args.get("filter"))
    except ValueError as exc:
        abort(400, description=str(exc))
    rows = (
        await g.db_session.execute(
            select(Statement, Payload.value, StatementTier.tier)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == Prop.MEDIA_MATCH,
                StatementTier.tier != "rejected",
                mfilter.statement_clause(f),
                service.available(mfilter.statement_blob()),
                service.available(Payload.value["blob"].astext),
            )
            .order_by(Statement.recorded_at.desc())
            .limit(100)
        )
    ).all()
    ctx = await data.Context.load(
        g.db_session,
        blobs=[
            sha
            for st, value, _ in rows
            for sha in (st.subject.removeprefix("blob:"), value["blob"])
        ],
    )
    shas = {
        sha
        for st, value, _ in rows
        for sha in (st.subject.removeprefix("blob:"), value["blob"])
    }
    mimes = dict(
        (
            await g.db_session.execute(
                select(Blob.sha256, Blob.mime).where(Blob.sha256.in_(shas))
            )
        ).all()
    )
    return output(
        {
            "items": [
                {
                    "id": str(st.id),
                    "tier": tier,
                    "left": ctx.doc(st.subject.removeprefix("blob:"))
                    | {"mime": mimes[st.subject.removeprefix("blob:")]},
                    "right": ctx.doc(value["blob"]) | {"mime": mimes[value["blob"]]},
                    "picture": value.get("picture"),
                    "sound": value.get("sound"),
                }
                for st, value, tier in rows
            ]
        }
    )


async def media_blob(sha):
    blob = await g.db_session.get(Blob, sha)
    if blob is None or not await g.db_session.scalar(
        select(service.available(Blob.sha256)).where(Blob.sha256 == sha)
    ):
        abort(404)
    if not blob.mime.startswith("text/") and blob.mime not in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
        "video/mp4",
        "video/webm",
        "video/ogg",
        "audio/ogg",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/flac",
        "audio/mp4",
    }:
        abort(415, description="This file type cannot be previewed")
    return blob


@bp.post("/api/evidence/<sha>/reveal")
async def reveal(sha):
    await media_blob(sha)
    db = g.db_session
    who = await write.principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    await write.append(
        db,
        StatementIn(
            subject=f"blob:{sha}",
            prop=Prop.ACCESS,
            value={"action": "reveal", "at": datetime.now(UTC).isoformat()},
            author_id=who.id,
        ),
    )
    await db.commit()
    grants = {
        k: v for k, v in session.get("evidence_previews", {}).items() if v > time.time()
    }
    grants[sha] = int(time.time()) + 3600
    session["evidence_previews"] = dict(list(grants.items())[-8:])
    return output({"url": f"/api/evidence/{sha}/content"})


@bp.get("/api/evidence/<sha>/content")
async def content(sha):
    blob = await media_blob(sha)
    if not await evidence.viewable(g.db_session, sha, current_user):
        abort(403, description="Reveal the evidence before previewing it")
    path = await asyncio.to_thread(blobs.verified_path, blob, Config.BLOB_DIR)
    if blob.mime == "application/pdf" and request.args.get("raw") != "1":
        try:
            page = int(request.args.get("page", "1"))
            data = await asyncio.to_thread(media.pdf_image, path, page)
        except ValueError as exc:
            abort(400, description=str(exc))
        response = await send_file(io.BytesIO(data), mimetype="image/png")
    else:
        response = await send_file(
            path,
            mimetype="text/plain" if blob.mime.startswith("text/") else blob.mime,
            conditional=True,
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
