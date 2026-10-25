"""Processing counts describe indexed coverage, never an archive-wide absence."""

from collections import defaultdict

from sqlalchemy import Text, cast, exists, func, select

from sealbook.discovery import embeddings
from sealbook.discovery.models import IndexProgress, Segment, SegmentEmbedding
from sealbook.discovery.service import available
from sealbook.ledger.models import Acquisition, Blob, Run
from sealbook.monitor import data
from sealbook.monitor import filter as mfilter
from sealbook.settings import Config


async def report(db, f):
    allowed = (
        select(Blob.sha256, Blob.mime)
        .join(Acquisition, Acquisition.blob_sha256 == Blob.sha256)
        .where(mfilter.blob_clause(f, Blob.sha256), available(Blob.sha256))
        .distinct()
    )
    total = await db.scalar(select(func.count()).select_from(allowed.subquery()))
    sources = list((await db.execute(allowed.order_by(Blob.sha256).limit(100))).all())
    shas = [sha for sha, _ in sources]
    ctx = await data.Context.load(db, blobs=shas)
    counters = defaultdict(list)
    for row in (
        await db.execute(
            select(IndexProgress).where(IndexProgress.source_sha256.in_(shas))
        )
    ).scalars():
        counters[row.source_sha256].append(row)
    states = defaultdict(dict)
    runs = (
        await db.execute(
            select(Acquisition.blob_sha256, Run)
            .join(Run, Run.inputs["acquisition"].astext == cast(Acquisition.id, Text))
            .where(
                Acquisition.blob_sha256.in_(shas),
                Run.tool.in_(["index", "pdf", "embed", "fingerprint"]),
            )
            .order_by(Run.created_at)
        )
    ).all()
    for sha, run in runs:
        if run.tool == "embed" and run.model_requested != Config.EMBEDDING_MODEL:
            continue
        part = (
            run.tool,
            run.inputs.get("blobs", [None])[0],
            run.inputs.get("ordinal", 0),
        )
        states[sha][part] = run.status
    segments = dict(
        (
            await db.execute(
                select(Segment.source_sha256, func.count())
                .where(Segment.source_sha256.in_(shas))
                .group_by(Segment.source_sha256)
            )
        ).all()
    )

    async def counts(clause):
        return dict(
            (
                await db.execute(
                    select(Segment.source_sha256, func.count())
                    .where(Segment.source_sha256.in_(shas), clause)
                    .group_by(Segment.source_sha256)
                )
            ).all()
        )

    def ready(clause):
        return counts(
            clause
            & exists(
                select(SegmentEmbedding.segment_id).where(
                    SegmentEmbedding.segment_id == Segment.id,
                    SegmentEmbedding.space_id.in_(embeddings.active_spaces()),
                )
            )
        )

    text_total = await counts(Segment.text_sha256.is_not(None))
    text_ready = await ready(Segment.text_sha256.is_not(None))
    media_ready = await ready(Segment.text_sha256.is_(None))
    unreadable = await counts((Segment.kind == "page") & Segment.text_sha256.is_(None))
    items = []
    for sha, mime in sources:
        statuses = list(states[sha].values())
        steps = defaultdict(lambda: {"completed": 0, "total": 0})
        for row in counters[sha]:
            key = (row.tool, row.unit)
            steps[key]["completed"] += row.completed
            steps[key]["total"] += row.total
        if "failed" in statuses:
            state = "failed"
        elif "running" in statuses:
            state = "running"
        elif "queued" in statuses:
            state = "partial" if segments.get(sha) else "queued"
        elif statuses:
            state = "done"
        else:
            state = "not_indexed"
        text_complete = text_ready.get(sha, 0) == text_total.get(sha, 0)
        media_required = Config.EMBEDDING_MODEL and (
            mime == "application/pdf" or mime.startswith(("image/", "audio/", "video/"))
        )
        media_step = steps.get(("embed", "segments"))
        media_complete = bool(
            media_step
            and media_step["completed"] == media_step["total"]
            and media_ready.get(sha, 0) >= media_step["total"]
        )
        if state == "done" and (
            unreadable.get(sha, 0)
            or (Config.EMBEDDING_MODEL and not text_complete)
            or (media_required and not media_complete)
        ):
            state = "incomplete"
        items.append(
            {
                "sha256": sha,
                "doc": ctx.doc(sha),
                "mime": mime,
                "state": state,
                "segments": text_total.get(sha, 0) + media_ready.get(sha, 0),
                "unreadable_pages": unreadable.get(sha, 0),
                "text_vectors": {
                    "ready": text_ready.get(sha, 0),
                    "total": text_total.get(sha, 0),
                },
                "media_vectors": {
                    "ready": media_ready.get(sha, 0),
                    "total": media_step["total"] if media_step else None,
                    "complete": media_complete if media_required else None,
                },
                "steps": [
                    {"tool": tool, "unit": unit, **values}
                    for (tool, unit), values in steps.items()
                ],
            }
        )
    return {
        "items": items,
        "total_sources": total,
        "truncated": total > len(items),
        "complete": not (total > len(items))
        and all(item["state"] == "done" and item["segments"] > 0 for item in items),
        "semantic_configured": bool(Config.EMBEDDING_MODEL),
    }
