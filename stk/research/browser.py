"""Local headless Chromium (Playwright). One `snapshot` visit returns the final URL,
the HTTP status, the rendered HTML, a full-page screenshot and a network log of the
documents and media it requested, so all of them come from the same visit and no
third party sits in the chain of custody. Every request, including each redirect
hop, is checked against the private-address rules before it leaves, and the address
Chromium actually connected to is checked again afterwards. Each visit gets a fresh
browser with no cookies, no service workers, no downloads and no extra windows."""

from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html.parser import HTMLParser
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import orjson

from stk import netguard

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
LOAD_MS = 45_000  # the page's own load event
SETTLE_MS = 8_000  # extra wait for network quiet; busy pages never reach it
TOTAL_SECONDS = 90  # everything: launch, load, settle, shoot
MAX_HTML = 20_000_000
MAX_SHOT_HEIGHT = 16_000  # Chromium cannot paint a taller texture
LOGGED = ("document", "media", "image")
ARGS = [
    "--block-new-web-contents",
    "--autoplay-policy=document-user-activation-required",
    "--mute-audio",
]
BOT_TITLES = ("just a moment", "attention required", "verify you are human")
INSTALL = "Chromium is not installed (run `uv run playwright install chromium`)"


class RenderError(Exception):
    """The browser did not return the page."""


@dataclass(frozen=True)
class Snapshot:
    final_url: str
    status: int
    html: str
    screenshot: bytes  # PNG
    netlog: bytes  # JSON, documents and media only
    browser: str  # "chromium <version>"


def _probe() -> bool:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        return Path(p.chromium.executable_path).exists()


@functools.cache
def installed() -> bool:
    """Whether Playwright's Chromium is on this machine. Cached for the process, so
    restart the app and workers after installing it."""
    with ThreadPoolExecutor(1) as pool:  # the sync API refuses to run in a loop
        return pool.submit(_probe).result()


def public(address: str) -> bool:
    return ip_address(address.split("%")[0]).is_global


def _plain(exc: Exception) -> str:
    line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return line.replace(": ", " ")[:160]


async def snapshot(page: str) -> Snapshot:
    from playwright.async_api import Error, async_playwright
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    log: list[dict] = []
    refused: list[str] = []  # main-frame hops that pointed at a private address
    seen = []

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(args=ARGS + EXTRA_ARGS)
        except Error as exc:
            raise RenderError(INSTALL) from exc
        try:
            async with asyncio.timeout(TOTAL_SECONDS):
                context = await browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    viewport={"width": 1280, "height": 900},
                )
                tab = await context.new_page()
                context.on("page", lambda extra: asyncio.ensure_future(extra.close()))

                # Playwright's own routing does not see redirect hops, so the guard
                # is the DevTools Fetch domain, which pauses every request, hops included.
                cdp = await context.new_cdp_session(tab)
                main_frame = (await cdp.send("Page.getFrameTree"))["frameTree"][
                    "frame"
                ]["id"]

                async def guard(event):
                    req, rid = event["request"], event["requestId"]
                    kind = event["resourceType"].lower()
                    entry = {"url": req["url"], "type": kind}
                    try:
                        if urlsplit(req["url"]).scheme not in ("http", "https"):
                            raise netguard.Blocked("not an http(s) url")
                        await netguard.pin(req["url"])
                        if kind == "media":
                            # Files are fetched afterwards through the pinned client,
                            # with size caps; the browser must not stream them.
                            raise netguard.Blocked("media is fetched separately")
                    except netguard.Blocked as exc:
                        entry["blocked"] = str(exc)
                        if kind == "document" and event.get("frameId") == main_frame:
                            refused.append(str(exc))
                        log.append(entry)
                        await cdp.send(
                            "Fetch.failRequest",
                            {"requestId": rid, "errorReason": "BlockedByClient"},
                        )
                        return
                    await cdp.send("Fetch.continueRequest", {"requestId": rid})

                cdp.on(
                    "Fetch.requestPaused", lambda ev: asyncio.ensure_future(guard(ev))
                )
                await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})

                async def logged(resp):
                    seen.append(resp)

                tab.on("response", logged)
                try:
                    main = await tab.goto(page, wait_until="load", timeout=LOAD_MS)
                    try:
                        await tab.wait_for_load_state("networkidle", timeout=SETTLE_MS)
                    except PlaywrightTimeout:
                        pass
                except PlaywrightTimeout as exc:
                    raise RenderError(
                        f"The page took longer than {LOAD_MS // 1000} seconds to load"
                    ) from exc
                except Error as exc:
                    if refused:
                        raise netguard.Blocked(refused[0]) from exc
                    if "Download is starting" in str(exc):
                        raise RenderError(
                            "This link starts a download. Sealbook blocks downloads"
                        ) from exc
                    raise RenderError(
                        f"The page could not be loaded ({_plain(exc)})"
                    ) from exc
                if refused:
                    raise netguard.Blocked(refused[0])
                final = tab.url
                await netguard.pin(final)
                for resp in seen:
                    if resp.request.resource_type not in LOGGED:
                        continue
                    addr = await resp.server_addr()
                    ip = addr["ipAddress"] if addr else None
                    if ip and not public(ip):
                        raise netguard.Blocked(
                            f"{urlsplit(resp.url).hostname} connected to a non-public address"
                        )
                    prev = resp.request.redirected_from
                    log.append(
                        {
                            "url": resp.url,
                            "type": resp.request.resource_type,
                            "method": resp.request.method,
                            "status": resp.status,
                            "content_type": resp.headers.get("content-type"),
                            "server_ip": ip,
                            "redirected_from": prev.url if prev else None,
                        }
                    )
                headers = main.headers if main else {}
                status = main.status if main else 0
                title = (await tab.title()).lower()
                if headers.get("cf-mitigated") == "challenge" or title.startswith(
                    BOT_TITLES
                ):
                    raise RenderError(
                        "The site asked for a bot check. Sealbook does not solve or "
                        "bypass bot checks"
                    )
                if status >= 400:
                    raise RenderError(f"The site answered with status {status}")
                html = await tab.content()
                if len(html) > MAX_HTML:
                    raise RenderError("The page is larger than the capture limit")
                height = await tab.evaluate("document.documentElement.scrollHeight")
                shot = await tab.screenshot(
                    full_page=True,
                    clip={
                        "x": 0,
                        "y": 0,
                        "width": 1280,
                        "height": min(max(height, 1), MAX_SHOT_HEIGHT),
                    },
                )
                version = f"chromium {browser.version}"
        except TimeoutError as exc:
            raise RenderError(
                f"The capture took longer than {TOTAL_SECONDS} seconds"
            ) from exc
        finally:
            await browser.close()
    return Snapshot(
        final_url=final,
        status=status,
        html=html,
        screenshot=shot,
        netlog=orjson.dumps({"final_url": final, "requests": log}),
        browser=version,
    )


# Tests add launch arguments (host mapping to a local server); production adds none.
EXTRA_ARGS: list[str] = []


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
