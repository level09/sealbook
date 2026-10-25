"""Brave Search API client: web, image and video results as one candidate shape.
Field names follow Brave's published response schema; the recorded fixtures in
tests/fixtures/research are synthetic and shaped after it, not captured traffic."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from sealbook.settings import Config

KINDS = {"web": "web/search", "images": "images/search", "videos": "videos/search"}
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_TAGS = re.compile(r"<[^>]+>")


class SearchError(Exception):
    """The provider did not answer with results; shown to the analyst as is."""


@dataclass(frozen=True)
class Result:
    kind: str  # web | images | videos
    url: str  # the page the result lives on
    title: str
    domain: str
    date: str | None  # as the provider states it (page_age, else age)
    description: str
    thumbnail: str | None  # provider thumbnail URL, fetched by the server only
    media_url: str | None  # the image itself, for image results


def endpoint(kind: str) -> str:
    return f"{Config.BRAVE_BASE_URL.rstrip('/')}/{KINDS[kind]}"


def freshness(window: tuple[str, str]) -> str:
    """Brave's custom date range: YYYY-MM-DDtoYYYY-MM-DD (web search documentation,
    api-dashboard.search.brave.com, checked 2026-10-24)."""
    return f"{window[0]}to{window[1]}"


def _text(value) -> str:
    return html.unescape(_TAGS.sub("", value or "")).strip()


def _result(kind: str, row: dict) -> Result | None:
    url = row.get("url") or ""
    if urlsplit(url).scheme not in ("http", "https"):
        return None
    props = row.get("properties") or {}
    return Result(
        kind=kind,
        url=url,
        title=_text(row.get("title")),
        domain=(row.get("meta_url") or {}).get("hostname")
        or urlsplit(url).hostname
        or "",
        date=row.get("page_age") or row.get("page_fetched") or row.get("age"),
        description=_text(row.get("description")),
        thumbnail=(row.get("thumbnail") or {}).get("src"),
        media_url=props.get("url") if kind == "images" else None,
    )


async def search(
    kind: str,
    query: str,
    count: int,
    transport: httpx.AsyncBaseTransport | None = None,
    window: tuple[str, str] | None = None,
) -> list[Result]:
    if not Config.BRAVE_API_KEY:
        raise SearchError("BRAVE_API_KEY is not set")
    # Videos cap count at 50, web at 20; images allow more.
    count = min(count, {"web": 20, "videos": 50}.get(kind, 100))
    params = {"q": query, "count": count, "safesearch": "off"}
    # Only web search honours a date range: the images documentation lists no
    # freshness parameter, and videos accepted it but returned results outside it.
    if window and kind == "web":
        params["freshness"] = freshness(window)
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT, trust_env=False
        ) as client:
            resp = await client.get(
                endpoint(kind),
                params=params,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": Config.BRAVE_API_KEY,
                },
            )
    except httpx.HTTPError as exc:
        raise SearchError(f"Brave Search could not be reached: {exc!r}") from exc
    if resp.status_code == 429:
        raise SearchError("Brave Search refused: rate limit or monthly quota reached")
    if resp.status_code != 200:
        raise SearchError(f"Brave Search answered {resp.status_code}")
    body = resp.json()
    rows = (
        (body.get("web") or {}).get("results", [])
        if kind == "web"
        else body.get("results", [])
    )
    return [r for row in rows if (r := _result(kind, row))]
