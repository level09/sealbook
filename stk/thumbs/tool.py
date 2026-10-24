"""The `thumbnail` worker tool: derived pictures for one root blob, sealed with the
tool version and parameters. The output is a manifest blob naming each derivative."""

import asyncio

import orjson
from sqlalchemy import select

from stk import blobs
from stk.discovery.service import _input
from stk.ledger.models import Run
from stk.settings import Config
from stk.thumbs import make

MIMES = {"audio": {"thumb_480": "image/png", "thumb_160": "image/png"}}


async def thumbnail(c):
    blob = await _input(c)
    c.run.inputs = c.run.inputs | {"version": make.VERSION, "params": make.PARAMS}
    if not c.run.inputs.get("rebuild") and await c.s.scalar(
        select(Run.id)
        .where(
            Run.tool == "thumbnail",
            Run.id != c.run.id,
            Run.status == "done",
            Run.raw_output_ref.is_not(None),
            Run.inputs["blobs"][0].astext == blob.sha256,
            Run.inputs["version"].as_integer() == make.VERSION,
        )
        .limit(1)
    ):
        await c.emit("unchanged", blobs=1)
        return None
    path = await asyncio.to_thread(blobs.verified_path, blob, Config.BLOB_DIR)
    kind = make.kind_of(blob.mime)
    if kind is None:
        raise ValueError(f"no thumbnail for {blob.mime}")
    meta: dict = {}
    if kind == "image":
        files, meta = await asyncio.to_thread(make.image_files, path)
    elif kind == "pdf":
        files, meta = await asyncio.to_thread(make.pdf_files, path)
    else:
        info = await asyncio.to_thread(make.probe, path)
        kind = make.kind_of(blob.mime, info["streams"])
        build = make.video_files if kind == "video" else make.audio_files
        files, meta = await asyncio.to_thread(build, path, info["duration"])
    mimes = {
        "strip": "image/webp",
        "peaks": "application/json",
    } | MIMES.get(kind, {})
    out = {}
    for name, data in files.items():
        if name == "pages":
            out[name] = [
                (await blobs.put(c.s, d, "image/webp", Config.BLOB_DIR)).sha256
                for d in data
            ]
        else:
            mime = mimes.get(name, "image/webp")
            out[name] = (await blobs.put(c.s, data, mime, Config.BLOB_DIR)).sha256
    manifest = await blobs.put(
        c.s,
        orjson.dumps(
            {
                "tool": "thumbnail",
                "version": make.VERSION,
                "params": make.PARAMS,
                "source": blob.sha256,
                "kind": kind,
                "meta": meta,
                "files": out,
            }
        ),
        "application/json",
        Config.BLOB_DIR,
    )
    c.run.raw_output_ref = manifest.sha256
    await c.emit(
        "thumbnails",
        files=len(out) - ("pages" in out) + len(out.get("pages", [])),
    )
    return manifest.sha256
