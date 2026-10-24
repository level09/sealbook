"""Cloudflare Browser Rendering REST client. One `snapshot` call renders a page and
returns its HTML and a full-page screenshot, so both come from the same visit.
Media on the page and a login form are read from that HTML, not from a second
fetch. Cloudflare's browser visits the page; this server never does."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from stk.settings import Config

TIMEOUT = httpx.Timeout(90.0, connect=10.0)
ENDPOINT = "browser-rendering/snapshot"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic")
VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v", ".ogv")
AUDIO_EXT = (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aac")
META_KIND = {
    "og:image": "image",
    "og:image:url": "image",
    "og:image:secure_url": "image",
    "twitter:image": "image",
    "og:video": "video",
    "og:video:url": "video",
    "og:video:secure_url": "video",
    "og:audio": "audio",
}
# Icons, spacers and avatars say they are small; leave them.
MIN_SIDE = 120


class RenderError(Exception):
    """The renderer did not return the page."""


@dataclass(frozen=True)
class Snapshot:
    html: str
    screenshot: bytes  # PNG
    browser_ms: str | None  # what Cloudflare billed, from X-Browser-Ms-Used
    endpoint: str


def url() -> str:
    return (
        f"{Config.CLOUDFLARE_BASE_URL.rstrip('/')}/accounts/"
        f"{Config.CLOUDFLARE_ACCOUNT_ID}/{ENDPOINT}"
    )


async def snapshot(
    page: str, transport: httpx.AsyncBaseTransport | None = None
) -> Snapshot:
    if not (Config.CLOUDFLARE_ACCOUNT_ID and Config.CLOUDFLARE_API_TOKEN):
        raise RenderError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are not set")
    target = url()
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT, trust_env=False
        ) as client:
            resp = await client.post(
                target,
                json={
                    "url": page,
                    "screenshotOptions": {"fullPage": True},
                    "gotoOptions": {"waitUntil": "networkidle2", "timeout": 45000},
                },
                headers={"Authorization": f"Bearer {Config.CLOUDFLARE_API_TOKEN}"},
            )
    except httpx.HTTPError as exc:
        raise RenderError(f"Cloudflare could not be reached: {exc!r}") from exc
    if resp.status_code == 429:
        raise RenderError("Cloudflare refused: browser rendering rate limit reached")
    body = (
        resp.json()
        if resp.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if resp.status_code != 200 or not body.get("success"):
        errors = "; ".join(e.get("message", "") for e in body.get("errors") or [])
        raise RenderError(
            f"Cloudflare could not render the page ({resp.status_code}) {errors}"
        )
    result = body["result"]
    return Snapshot(
        html=result["content"],
        screenshot=base64.b64decode(result["screenshot"]),
        browser_ms=resp.headers.get("x-browser-ms-used"),
        endpoint=target,
    )


class _Page(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.found, self.password, self.parents = base, [], False, []

    def add(self, src, kind, tag):
        if not src or src.startswith(("data:", "blob:")):
            return
        full = urljoin(self.base, src.strip())
        if urlsplit(full).scheme in ("http", "https"):
            self.found.append({"url": full, "kind": kind, "tag": tag})

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "input" and a.get("type", "").lower() == "password":
            self.password = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in META_KIND:
                self.add(a.get("content"), META_KIND[key], "meta")
        elif tag == "img":
            sides = [a.get("width", ""), a.get("height", "")]
            if any(s.isdigit() and int(s) < MIN_SIDE for s in sides):
                return
            # The last srcset entry is conventionally the largest.
            srcset = [p.split()[0] for p in a.get("srcset", "").split(",") if p.strip()]
            self.add(
                srcset[-1] if srcset else a.get("src") or a.get("data-src"),
                "image",
                "img",
            )
        elif tag in ("video", "audio"):
            self.parents.append(tag)
            self.add(a.get("src"), tag, tag)
        elif tag == "source" and self.parents:
            self.add(a.get("src"), self.parents[-1], "source")

    def handle_endtag(self, tag):
        if tag in ("video", "audio") and self.parents:
            self.parents.pop()


def read(html: str, page: str) -> tuple[list[dict], bool]:
    """(media on the page, whether it asks for a password). Each media URL once,
    in page order; svg and declared-small images are left out."""
    parser = _Page(page)
    parser.feed(html)
    seen, media = set(), []
    for m in parser.found:
        path = urlsplit(m["url"]).path.lower()
        if path.endswith(".svg") or m["url"] in seen:
            continue
        if m["tag"] == "meta" and path.endswith(VIDEO_EXT + AUDIO_EXT + IMAGE_EXT):
            m = m | {
                "kind": "video"
                if path.endswith(VIDEO_EXT)
                else "audio"
                if path.endswith(AUDIO_EXT)
                else "image"
            }
        seen.add(m["url"])
        media.append(m)
    return media, parser.password
