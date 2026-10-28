"""Model routes: one OpenAI-compatible chat client, a route is a base URL plus key.
The policy table decides which routes a run may use; there is no fallback."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from sealbook.ledger.contracts import Route
from sealbook.settings import Config

OPENROUTER = "https://openrouter.ai/api/v1"
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class RouteError(Exception):
    """The route could not serve the call. The run fails; nothing is retried elsewhere."""


@dataclass(frozen=True)
class Served:
    data: dict  # parsed response body
    raw: bytes  # response body as received, kept as the run's raw output
    endpoint: str
    model: str
    provider: str


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RouteError(f"{name} is not set")
    return value


def zdr_provider() -> dict:
    """OpenRouter provider preferences for a cloud-zdr call."""
    pins = Config.ROUTE_CLOUD_ZDR_PROVIDERS
    return {"zdr": True, "only": pins} if pins else {"zdr": True}


def _fold(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def check_pinned(served: str | None) -> None:
    """Fail a cloud-zdr call served outside ROUTE_CLOUD_ZDR_PROVIDERS.

    OpenRouter names the served provider by display name ("DeepInfra", "Google AI
    Studio") while `only` takes slugs ("deepinfra", "google-ai-studio/us"). A name
    matches a pin when both, lowercased and stripped to letters and digits, are
    equal, taking the pin's base slug (before the first "/"). A display name that
    does not fold to its slug fails the call rather than pass unchecked."""
    pins = Config.ROUTE_CLOUD_ZDR_PROVIDERS
    if pins and _fold(served or "") not in {_fold(p.split("/")[0]) for p in pins}:
        raise RouteError(
            f"cloud-zdr served by {served or 'an unnamed provider'}, "
            f"not a pinned provider ({', '.join(pins)})"
        )


def _target(route: Route) -> tuple[str, str | None]:
    if route is Route.LOCAL:
        # vLLM and Ollama run without a key unless one is configured.
        return _env("ROUTE_LOCAL_BASE_URL"), os.environ.get("ROUTE_LOCAL_API_KEY")
    return OPENROUTER, _env("OPENROUTER_API_KEY")


async def chat(
    route: Route, body: dict, transport: httpx.AsyncBaseTransport | None = None
) -> Served:
    """POST one chat completion on `route`. OpenRouter reports the served model in
    `model` and the upstream provider in `provider` (checked 2026-10-21)."""
    base, key = _target(route)
    if route is Route.CLOUD_ZDR:
        body = {**body, "provider": zdr_provider()}
    endpoint = f"{base.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        timeout = (
            httpx.Timeout(float(Config.ROUTE_LOCAL_TIMEOUT), connect=10.0)
            if route is Route.LOCAL
            else TIMEOUT
        )
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            resp = await client.post(endpoint, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise RouteError(f"{route} unreachable: {exc!r}") from exc
    if resp.status_code != 200:
        raise RouteError(f"{route} returned {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if "error" in data:
        raise RouteError(f"{route} error: {data['error']}")
    if not data.get("model"):
        raise RouteError(f"{route} response names no served model")
    if route is Route.LOCAL:
        provider = "local"
    elif data.get("provider"):
        provider = data["provider"]
    else:
        raise RouteError(f"{route} response names no provider")
    if route is Route.CLOUD_ZDR:
        check_pinned(provider)
    return Served(data, resp.content, endpoint, data["model"], provider)


def content(served: Served) -> str:
    choice = served.data["choices"][0]
    if choice.get("finish_reason") not in ("stop", None):
        raise RouteError(f"model stopped early: {choice.get('finish_reason')}")
    return choice["message"]["content"] or ""
