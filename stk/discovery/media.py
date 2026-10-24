"""Located media inputs embedded into the one local retrieval space."""

import asyncio
import io
import json
import math
import re
import subprocess

from PIL import Image, ImageOps
from sqlalchemy.dialects.postgresql import insert

from stk import blobs
from stk.discovery import embeddings
from stk.discovery.indexing import continue_run, progress, store
from stk.discovery.models import Segment
from stk.ledger import write
from stk.ledger.contracts import Prop, StatementIn
from stk.settings import Config


def png(image):
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((1024, 1024))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def pdf_image(path, page):
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as document:
        if not 1 <= page <= len(document):
            raise ValueError("PDF page is outside the source")
        item = document[page - 1]
        try:
            bitmap = item.render(scale=min(1.5, 1200 / max(item.get_size())))
            try:
                return png(bitmap.to_pil())
            finally:
                bitmap.close()
        finally:
            item.close()


def parts(path, mime, cursor, limit):
    if limit < 1:
        raise ValueError("media batch size must be positive")
    if mime.startswith("image/"):
        with Image.open(path) as image:
            return (
                [{"kind": "image", "ordinal": 0, "location": {}, "data": png(image)}]
                if cursor == 0
                else []
            ), 1
    if mime == "application/pdf":
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(path) as document:
            total = len(document)
        return [
            {
                "kind": "page_image",
                "ordinal": i + 1,
                "location": {"page": i + 1},
                "data": pdf_image(path, i + 1),
            }
            for i in range(cursor, min(total, cursor + limit))
        ], total
    metadata = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,start_time:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
    )
    seconds = float(metadata["format"]["duration"])
    if not math.isfinite(seconds) or not 0 < seconds <= Config.FINGERPRINT_MAX_SECONDS:
        raise ValueError("media duration is outside the configured decode bound")
    streams = {row["codec_type"] for row in metadata["streams"]}
    windows = []
    if "video" in streams:
        windows += [(t, "frame") for t in range(0, math.ceil(seconds), 3)]
    if "audio" in streams:
        windows += [
            (t, "audio") for t in range(0, math.ceil(seconds), 25) if seconds - t >= 2
        ]
    windows.sort()
    output = []
    for start, kind in windows[cursor : cursor + limit]:
        command = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "info",
            "-ss",
            str(start),
            "-i",
            str(path),
        ]
        if kind == "frame":
            command += [
                "-copyts",
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-vf",
                "scale=1024:1024:force_original_aspect_ratio=decrease,showinfo",
                "-f",
                "image2pipe",
                "-c:v",
                "png",
                "pipe:1",
            ]
        else:
            command += [
                "-map",
                "0:a:0",
                "-t",
                str(min(30, seconds - start)),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                "-c:a",
                "pcm_s16le",
                "pipe:1",
            ]
        result = subprocess.run(command, check=True, capture_output=True, timeout=120)
        first = start
        if kind == "frame":
            # The container's duration can run past the last frame (a longer audio
            # track, rounding): a window there decodes nothing and is not an error.
            if not result.stdout:
                continue
            stamp = re.search(rb"pts_time:([-\d.]+)", result.stderr)
            if stamp is None:
                raise ValueError("decoded frame has no source timestamp")
            first = float(stamp[1]) - float(metadata["format"].get("start_time", 0))
        location = {
            "ms": [
                max(0, round(first * 1000)),
                min(
                    round(seconds * 1000),
                    round((first + (30 if kind == "audio" else 0.001)) * 1000),
                ),
            ]
        }
        output.append(
            {
                "kind": kind,
                "ordinal": round(start * 1000),
                "location": location,
                "data": result.stdout,
            }
        )
    return output, len(windows)


async def index(c):
    from stk.discovery.service import _input

    blob = await _input(c)
    path = await asyncio.to_thread(blobs.verified_path, blob, Config.BLOB_DIR)
    cursor = c.run.inputs.get("cursor", 0)
    if type(cursor) is not int or cursor < 0:
        raise ValueError("invalid media cursor")
    prepared, total = await asyncio.to_thread(
        parts, path, blob.mime, cursor, Config.MEDIA_BATCH_SEGMENTS
    )
    if cursor > total:
        raise ValueError("media cursor exceeds its source")
    inputs, metadata = [], []
    for item in prepared:
        artifact = await blobs.put(
            c.s,
            item["data"],
            "audio/wav" if item["kind"] == "audio" else "image/png",
            Config.BLOB_DIR,
        )
        if artifact.sha256 != blob.sha256:
            await write.append(
                c.s,
                StatementIn(
                    subject=f"blob:{artifact.sha256}",
                    prop=Prop.DERIVED_FROM,
                    value={"blob": blob.sha256},
                    author_id=c.run.principal_id,
                    run_id=c.run.id,
                ),
                sign=c.sign,
            )
        inputs.append(
            embeddings.content(
                "audio" if item["kind"] == "audio" else "image", item["data"]
            )
        )
        metadata.append(
            {
                "kind": item["kind"],
                "ordinal": item["ordinal"],
                "location": item["location"],
                "artifact": artifact.sha256,
            }
        )
    if inputs:
        vectors, served, endpoint, raw, space = await embeddings.call(
            c.s,
            inputs,
            c.run.model_requested,
            c.transport,
            rebuild=bool(c.run.inputs.get("rebuild")),
        )
        c.run.model_served, c.run.provider_served, c.run.endpoint = (
            served,
            "local",
            endpoint,
        )
        c.run.inputs = {**c.run.inputs, "prepared": metadata}
        await embeddings.bind_run(c, space)
        for item, vector in zip(metadata, vectors, strict=True):
            segment = await c.s.scalar(
                insert(Segment)
                .values(
                    source_sha256=blob.sha256,
                    kind=item["kind"],
                    ordinal=item["ordinal"],
                    start=0,
                    location=item["location"],
                    artifact_sha256=item["artifact"],
                    run_id=c.run.id,
                )
                .on_conflict_do_update(
                    constraint="uq_evidence_segment_location",
                    set_={
                        "location": item["location"],
                        "artifact_sha256": item["artifact"],
                        "run_id": c.run.id,
                    },
                )
                .returning(Segment.id)
            )
            await store(c, segment, space, vector)
        artifact = await blobs.put(c.s, raw, "application/json", Config.BLOB_DIR)
        c.run.raw_output_ref = artifact.sha256
    await progress(c, cursor + len(prepared), total, "segments")
    if cursor + len(prepared) < total:
        continue_run(c, cursor + len(prepared))
    await c.emit("media_indexed", completed=cursor + len(prepared), total=total)
    return c.run.raw_output_ref
