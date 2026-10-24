"""Serve the derivatives made by the `thumbnail` tool. Access follows the source:
analysts only, erased sources are gone, and a sensitive source shows its pictures
only after this session revealed it (the same logged grant as the content route)."""

import asyncio

import orjson
from quart import Blueprint, Response, abort, g, request, send_file
from sqlalchemy import select

from stk import blobs
from stk.collaboration.views import guard, output
from stk.discovery import evidence, service
from stk.ledger.models import Blob, Run
from stk.monitor.data import Context
from stk.settings import Config

bp = Blueprint("thumbs", __name__)
bp.before_request(guard)


@bp.after_request
async def private(response):
    # Content-addressed, but the answer depends on session and erasure: always
    # revalidate, never share.
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["Vary"] = "Cookie"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def manifest(sha: str, missing_ok: bool = False) -> dict | None:
    db = g.db_session
    if not await db.scalar(
        select(service.available(Blob.sha256)).where(Blob.sha256 == sha)
    ):
        abort(404)
    ref = await db.scalar(
        select(Run.raw_output_ref)
        .where(
            Run.tool == "thumbnail",
            Run.status == "done",
            Run.raw_output_ref.is_not(None),
            Run.inputs["blobs"][0].astext == sha,
        )
        .order_by(Run.finished_at.desc())
        .limit(1)
    )
    if ref is None:
        if missing_ok:
            return None
        abort(404, description="No thumbnail has been made for this source")
    sensitive = (await Context.load(db, blobs=[sha])).doc(sha)["sensitive"]
    if sensitive and not evidence.granted(sha):
        return abort(
            Response(
                orjson.dumps(
                    {
                        "error": "Reveal the evidence to see its pictures",
                        "reason": "reveal_required",
                    }
                ),
                status=403,
                content_type="application/json",
            )
        )
    blob = await db.get(Blob, ref)
    return orjson.loads(await asyncio.to_thread(blobs.read, blob, Config.BLOB_DIR))


async def serve(name: str, sha: str | None):
    if sha is None:
        abort(404, description=f"This source has no {name}")
    db = g.db_session
    blob = await db.get(Blob, sha)
    tag = f'"{sha}"'
    if request.headers.get("If-None-Match") == tag:
        return Response(status=304, headers={"ETag": tag})
    path = await asyncio.to_thread(blobs.verified_path, blob, Config.BLOB_DIR)
    response = await send_file(path, mimetype=blob.mime)
    response.headers["ETag"] = tag
    return response


@bp.get("/api/evidence/<sha>/thumb")
async def thumb(sha):
    """size=160 (square tile) or 480 (longest side); page=N for a PDF page render.
    A source without a thumbnail (text, sheets, or not made yet) answers 204, so a
    list of tiles does not fill the console with expected 404s."""
    m = await manifest(sha, missing_ok=True)
    if m is None:
        return Response(status=204)
    size = request.args.get("size", "160")
    if size not in ("160", "480"):
        abort(400, description="size must be 160 or 480")
    if "page" in request.args:
        pages = m["files"].get("pages")
        try:
            page = int(request.args["page"])
        except ValueError:
            abort(400, description="page must be an integer")
        if pages is None or not 1 <= page <= len(pages):
            abort(404, description="No such rendered page")
        return await serve("page", pages[page - 1])
    return await serve("thumbnail", m["files"].get(f"thumb_{size}"))


@bp.get("/api/evidence/<sha>/strip")
async def strip(sha):
    return await serve("frame strip", (await manifest(sha))["files"].get("strip"))


@bp.get("/api/evidence/<sha>/peaks")
async def peaks(sha):
    return await serve("waveform peaks", (await manifest(sha))["files"].get("peaks"))


@bp.get("/api/evidence/<sha>/thumbs")
async def describe(sha):
    """What exists for this source: kind, strip cell timestamps, page count, version."""
    m = await manifest(sha)
    return output(
        {
            "kind": m["kind"],
            "version": m["version"],
            "has": sorted(k for k in m["files"] if k != "pages"),
            "pages": len(m["files"].get("pages", [])),
        }
        | m["meta"]
    )
