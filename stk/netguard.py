"""Outbound fetches of URLs that users or clients supply. Resolve once, refuse any
non-public address, then connect to that exact IP while TLS (SNI and certificate
checks) still uses the hostname, so DNS rebinding cannot swap in an internal
address between the check and the connection. Every redirect hop is re-checked."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

MAX_REDIRECTS = 5


class Blocked(Exception):
    """The URL points somewhere this server must not connect to."""


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return sorted({info[4][0] for info in infos})


async def pin(url: str) -> tuple[str, str, str]:
    """Return (url with the checked IP, Host header, TLS server name)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Blocked(f"not an http(s) url: {url!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        addrs = await resolve(parts.hostname, port)
    except socket.gaierror as exc:
        raise Blocked(f"host does not resolve: {parts.hostname}") from exc
    if not addrs or not all(
        ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs
    ):
        raise Blocked(f"host resolves to a non-public address: {parts.hostname}")
    ip = addrs[0]
    netloc = f"[{ip}]" if ":" in ip else ip
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    host_header = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    if parts.port:
        host_header = f"{host_header}:{parts.port}"
    return (
        urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, "")),
        host_header,
        parts.hostname,
    )


@asynccontextmanager
async def stream(
    url: str,
    *,
    follow_redirects: bool = True,
    headers: dict | None = None,
    timeout: float = 30,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[tuple[httpx.Response, str]]:
    """Yield (response, final url). Environment proxies are ignored: a proxy
    would resolve the name itself and undo the pin."""
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            target, host_header, server_name = await pin(url)
            request = client.build_request(
                "GET",
                target,
                headers={**(headers or {}), "Host": host_header},
                extensions={"sni_hostname": server_name},
            )
            response = await client.send(request, stream=True)
            # The jar keys cookies by the pinned IP, not the hostname; never carry
            # them across hops.
            client.cookies.clear()
            if follow_redirects and response.is_redirect:
                await response.aclose()
                url = urljoin(url, response.headers["location"])
                continue
            try:
                yield response, url
            finally:
                await response.aclose()
            return
        raise Blocked("too many redirects")
