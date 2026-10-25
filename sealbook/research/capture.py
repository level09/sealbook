"""The `capture` worker tool: one picked search result into the case. A local
headless Chromium visits the page once (HTML, full-page screenshot and a network
log), the result's own image or the
page's media are fetched, a video page goes to yt-dlp (a YouTube result goes to yt-dlp
alone, with no browser visit), and each file enters normal
intake (hash, C2PA, reading, fingerprints, embeddings, geotag, thumbnails) as its own
acquisition by the analyst. The sealed run's output is a manifest naming every file
and how it was obtained. Nothing on another page is visited."""

from __future__ import annotations

import asyncio
import mimetypes
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import orjson

from sealbook import blobs, netguard
from sealbook.blobs import chunked
from sealbook.ingest import service as ingest
from sealbook.ledger.contracts import Refused
from sealbook.research import browser, service, video, youtube
from sealbook.settings import Config

MEDIA = ("image/", "video/", "audio/")


class TooLarge(Exception):
    pass


async def _capped(resp, limit):
    size = 0
    async for chunk in resp.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise TooLarge(f"larger than {limit} bytes")
        yield chunk


def _mime(header: str, url: str) -> str:
    mime = header.split(";")[0].strip().lower()
    if mime in ("", "application/octet-stream", "binary/octet-stream"):
        mime = mimetypes.guess_type(urlsplit(url).path)[0] or mime
    return mime


def _name(url: str, fallback: str) -> str:
    return PurePosixPath(urlsplit(url).path).name or fallback


async def capture(c) -> str:
    i = c.run.inputs
    if reason := await service.refusal(c.s, i["investigation"]):
        raise Refused(reason)
    page = i["page"]
    await netguard.pin(page)  # refuse a page that resolves to a private address
    collector = uuid.UUID(i["analyst"])
    captured_at = datetime.now(UTC).isoformat()
    tube = i["kind"] == "youtube"
    method = {
        "provider": i["provider"],
        "renderer": None if tube else {"name": "playwright-chromium"},
        "yt_dlp": video.VERSION if i["kind"] in ("videos", "youtube") else None,
    }
    common = {
        "investigation": i["investigation"],
        "batch": i["batch"],
        "final_url": page,
    }

    def context(role: str, **extra) -> dict:
        return common | {
            "capture": orjson.loads(
                orjson.dumps(
                    {
                        "run": str(c.run.id),
                        "role": role,
                        "page": page,
                        "title": i.get("title"),
                        "found_at": i["found_at"],
                        "captured_at": captured_at,
                        "query_access": i["query_access"],
                        "method": method,
                    }
                    | extra
                )
            )
        }

    async def take(data_or_chunks, mime, origin, role, filename, **extra):
        chunks = (
            chunked(data_or_chunks)
            if isinstance(data_or_chunks, bytes)
            else data_or_chunks
        )
        out = await ingest.ingest(
            c.s,
            chunks,
            blob_dir=Config.BLOB_DIR,
            mime=mime,
            origin=origin,
            collector_id=collector,
            doc_type=i["doc_type"],
            sensitivity=i["sensitivity"],
            context=context(role, **extra) | {"filename": filename},
        )
        files.append(
            {
                "role": role,
                "sha256": out["sha256"],
                "mime": mime,
                "origin": origin,
                "acquisition": out["acquisition_id"],
                "new_blob": out["new_blob"],
            }
            | extra
        )
        return out["sha256"]

    files: list[dict] = []
    skipped = []
    if tube:
        page_url = page
        await _youtube(c, i, take, skipped)
    else:
        page_url, found = await _page(c, i, page, take, method, common, skipped)
    await c.emit("captured", files=len(files), skipped=len(skipped))
    manifest = await blobs.put(
        c.s,
        orjson.dumps(
            {
                "tool": "capture",
                "page": page,
                "captured_at": captured_at,
                "method": method,
                "query_access": i["query_access"],
                "files": files,
                "media_on_page": [] if tube else found,
                "skipped": skipped,
            }
        ),
        "application/json",
        Config.BLOB_DIR,
    )
    c.run.raw_output_ref = manifest.sha256
    c.run.endpoint = page_url
    c.run.provider_served = "local"
    return manifest.sha256


async def _youtube(c, i, take, skipped) -> None:
    """The video, then its info, subtitles and thumbnail, each naming the video's
    hash, so the companions stay linked to the media they describe."""
    page = i["page"]
    with tempfile.TemporaryDirectory(prefix="sealbook-youtube-") as tmp:
        try:
            got = await youtube.download(
                i["video_id"],
                tmp,
                Config.RESEARCH_MEDIA_MAX_BYTES,
                Config.RESEARCH_YOUTUBE_SECONDS,
            )
        except (youtube.BotCheck, youtube.LoginRequired) as exc:
            raise Refused(str(exc)) from exc
        path = got["media"]
        sha = await take(
            _read(path),
            mimetypes.guess_type(path.name)[0] or "video/mp4",
            f"url:{page}",
            "video",
            path.name,
            video=got["context"],
        )
        link = {"of_video": sha, "video_id": i["video_id"]}
        await take(
            orjson.dumps(got["info"], option=orjson.OPT_INDENT_2),
            "application/json",
            f"url:{page}",
            "info",
            f"{i['video_id']} info.json",
            **link,
        )
        for sub in got["subtitles"]:
            await take(
                sub["path"].read_bytes(),
                "text/vtt",
                f"url:{page}",
                "subtitles",
                sub["path"].name,
                lang=sub["lang"],
                automatic=sub["auto"],
                **link,
            )
        if thumb := got["thumbnail"]:
            await take(
                thumb.read_bytes(),
                mimetypes.guess_type(thumb.name)[0] or "image/jpeg",
                f"url:{page}",
                "thumbnail",
                thumb.name,
                **link,
            )
        skipped += got["skipped"]
    await c.emit("video_downloaded", files=1, subtitles=len(got["subtitles"]))


async def _read(path):
    with path.open("rb") as f:
        while chunk := await asyncio.to_thread(f.read, blobs.CHUNK):
            yield chunk


async def _page(c, i, page, take, method, common, skipped):
    try:
        snap = await browser.snapshot(page)
    except netguard.Blocked as exc:
        raise Refused(
            "This page, or a redirect from it, leads to a private network address. "
            "Sealbook does not capture those."
        ) from exc
    page_url = snap.final_url
    method["renderer"] |= {"browser": snap.browser, "status": snap.status}
    common["final_url"] = page_url
    await c.emit("rendered", html_chars=len(snap.html), screenshot=len(snap.screenshot))
    found, password = browser.read(snap.html, page_url)
    if password:
        raise Refused(
            "This page asks for a login. Sealbook does not capture pages behind a "
            "login; open it yourself and add what you may lawfully keep through Add "
            "evidence."
        )
    domain = i.get("domain") or urlsplit(page).hostname
    await take(
        snap.html.encode(),
        "text/html; charset=utf-8",
        f"url:{page}",
        "page",
        f"{domain} page.html",
    )
    await take(
        snap.screenshot, "image/png", f"url:{page}", "screenshot", f"{domain} page.png"
    )
    await take(
        snap.netlog,
        "application/json",
        f"url:{page}",
        "network_log",
        f"{domain} network log.json",
    )

    if i["kind"] == "videos":
        with tempfile.TemporaryDirectory(prefix="sealbook-capture-") as tmp:
            try:
                path, info = await video.download(
                    page, tmp, Config.RESEARCH_MEDIA_MAX_BYTES
                )
            except video.LoginRequired as exc:
                raise Refused(
                    "This video needs an account to watch. Sealbook does not log in "
                    "to capture it."
                ) from exc
            mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
            await take(_read(path), mime, f"url:{page}", "video", path.name, video=info)
        await c.emit("video_downloaded", files=1)

    wanted = (
        [{"url": i["media_url"], "kind": "image", "tag": "result"}]
        if i.get("media_url")
        else []
    )
    wanted += [m for m in found if m["url"] != i.get("media_url")]
    wanted = wanted[: Config.RESEARCH_MEDIA_PER_PAGE]
    for m in wanted:
        primary = m["tag"] == "result"
        try:
            url = ingest.normalise_url(m["url"])
            async with netguard.stream(url, timeout=60, transport=c.transport) as (
                resp,
                final,
            ):
                resp.raise_for_status()
                mime = _mime(resp.headers.get("content-type", ""), final)
                if not mime.startswith(MEDIA):
                    raise ValueError(f"not media ({mime or 'no type'})")
                await take(
                    _capped(resp, Config.RESEARCH_MEDIA_MAX_BYTES),
                    mime,
                    f"url:{url}",
                    "media",
                    _name(url, f"{domain} media"),
                    media_url=url,
                    found_by=m["tag"],
                )
        except ingest.C2paError:
            raise
        except Exception as exc:
            if primary:
                raise
            skipped.append({"url": m["url"], "reason": f"{type(exc).__name__}: {exc}"})
            await c.emit("media_skipped", drop_reason=str(exc)[:200])
    return page_url, found
