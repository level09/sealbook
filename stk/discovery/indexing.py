"""Bounded projection jobs. Continuations yield to newly queued evidence."""

import asyncio
from datetime import UTC, datetime, timedelta

import orjson
from sqlalchemy.dialects.postgresql import insert

from stk import blobs
from stk.discovery import compute
from stk.discovery.models import IndexProgress, SearchChunk, Segment, SegmentEmbedding
from stk.discovery.text import folded
from stk.ledger import chain, write
from stk.ledger.contracts import Prop, StatementIn
from stk.ledger.models import Run
from stk.settings import Config


async def progress(c, completed, total, unit, part=""):
    values = {
        "source_sha256": c.acq.blob_sha256,
        "tool": c.run.tool,
        "part": part,
        "unit": unit,
        "completed": completed,
        "total": total,
        "run_id": c.run.id,
    }
    await c.s.execute(
        insert(IndexProgress)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[
                IndexProgress.source_sha256,
                IndexProgress.tool,
                IndexProgress.part,
            ],
            set_={key: values[key] for key in ("unit", "completed", "total", "run_id")},
        )
    )


def continue_run(c, cursor):
    c.s.add(
        Run(
            principal_id=c.run.principal_id,
            tool=c.run.tool,
            route=c.run.route,
            model_requested=c.run.model_requested,
            inputs={
                **c.run.inputs,
                "cursor": cursor,
                "rebuild": False,
                "continued_from": str(c.run.id),
            },
            available_at=datetime.now(UTC) + timedelta(seconds=2),
        )
    )


async def text_segment(c, text_sha256, start, end):
    values = {
        "source_sha256": c.acq.blob_sha256,
        "kind": c.run.inputs.get("segment_kind", "text"),
        "ordinal": c.run.inputs.get("ordinal", 0),
        "text_sha256": text_sha256,
        "start": start,
        "end": end,
        "location": c.run.inputs.get("location", {}),
        "run_id": c.run.id,
    }
    return await c.s.scalar(
        insert(Segment)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_evidence_segment_location",
            set_={
                key: values[key] for key in ("text_sha256", "end", "location", "run_id")
            },
        )
        .returning(Segment.id)
    )


async def store(c, segment, space, vector):
    await c.s.execute(
        insert(SegmentEmbedding)
        .values(segment_id=segment, space_id=space, run_id=c.run.id, vector=vector)
        .on_conflict_do_update(
            index_elements=[SegmentEmbedding.segment_id, SegmentEmbedding.space_id],
            set_={"run_id": c.run.id, "vector": vector},
        )
    )


async def index(c):
    from stk.discovery.service import _input

    blob = await _input(c)
    source = blobs.read(blob, Config.BLOB_DIR).decode()
    if Config.INDEX_BATCH_CHUNKS < 1:
        raise ValueError("text indexing batch size must be positive")
    spans = compute.chunks(source)
    cursor = c.run.inputs.get("cursor", 0)
    if type(cursor) is not int or not 0 <= cursor <= len(spans):
        raise ValueError("invalid text indexing cursor")
    end = min(len(spans), cursor + Config.INDEX_BATCH_CHUNKS)
    kind = c.run.inputs.get("segment_kind", "text")
    ordinal = c.run.inputs.get("ordinal", 0)
    for a, b in spans[cursor:end]:
        await c.s.execute(
            insert(SearchChunk)
            .values(
                blob_sha256=blob.sha256,
                start=a,
                end=b,
                text=source[a:b],
                normalized=folded(source[a:b])[0],
                run_id=c.run.id,
            )
            .on_conflict_do_update(
                index_elements=[SearchChunk.blob_sha256, SearchChunk.start],
                set_={
                    "end": b,
                    "text": source[a:b],
                    "normalized": folded(source[a:b])[0],
                    "run_id": c.run.id,
                },
            )
        )
        await text_segment(c, blob.sha256, a, b)
    await progress(c, end, len(spans), "chunks", f"{blob.sha256}:{kind}:{ordinal}")
    if end < len(spans):
        continue_run(c, end)
    await c.emit("indexed", chunks=end - cursor, completed=end, total=len(spans))
    return chain.sha256(chain.canon({"blob": blob.sha256, "spans": spans[cursor:end]}))


def pdf_pages(path, cursor, limit):
    from pypdf import PdfReader

    with PdfReader(path) as reader:
        total = len(reader.pages)
        if type(cursor) is not int or not 0 <= cursor <= total:
            raise ValueError("invalid PDF page cursor")
        pages = []
        for i in range(cursor, min(total, cursor + limit)):
            page = reader.pages[i]
            pages.append(
                (
                    i + 1,
                    page.extract_text() or "",
                    {
                        "page": i + 1,
                        "bbox": [
                            0,
                            0,
                            round(float(page.mediabox.width)),
                            round(float(page.mediabox.height)),
                        ],
                    },
                )
            )
        return pages, total


async def pdf(c):
    from stk.discovery.service import _input, queue

    blob = await _input(c)
    if Config.PDF_BATCH_PAGES < 1:
        raise ValueError("PDF batch size must be positive")
    cursor = c.run.inputs.get("cursor", 0)
    path = await asyncio.to_thread(blobs.verified_path, blob, Config.BLOB_DIR)
    pages, total = await asyncio.to_thread(
        pdf_pages, path, cursor, Config.PDF_BATCH_PAGES
    )
    end = cursor + len(pages)
    output = []
    for ordinal, text, location in pages:
        if not text.strip():
            await c.s.execute(
                insert(Segment)
                .values(
                    source_sha256=blob.sha256,
                    kind="page",
                    ordinal=ordinal,
                    start=0,
                    location=location,
                    run_id=c.run.id,
                )
                .on_conflict_do_nothing(constraint="uq_evidence_segment_location")
            )
            await c.emit("page_without_text", pages=1, page=ordinal)
            continue
        child = await blobs.put(
            c.s, text.encode(), "text/plain; charset=utf-8", Config.BLOB_DIR
        )
        await write.append(
            c.s,
            StatementIn(
                subject=f"blob:{child.sha256}",
                prop=Prop.DERIVED_FROM,
                value={"blob": blob.sha256},
                author_id=c.run.principal_id,
                run_id=c.run.id,
            ),
            sign=c.sign,
        )
        await queue(
            c.s,
            c.acq,
            child.mime,
            blob_sha256=child.sha256,
            location=location,
            ordinal=ordinal,
        )
        output.append({"page": ordinal, "text": child.sha256})
    await progress(c, end, total, "pages")
    if end < total:
        continue_run(c, end)
    artifact = await blobs.put(
        c.s,
        orjson.dumps(
            {"source": blob.sha256, "pages": output, "read_to": end, "total": total}
        ),
        "application/json",
        Config.BLOB_DIR,
    )
    c.run.raw_output_ref = artifact.sha256
    await c.emit("pages_read", completed=end, total=total)
    return artifact.sha256
