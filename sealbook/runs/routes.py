"""Model routes: one OpenAI-compatible chat client, a route is a base URL plus key.
The anthropic route speaks the native Messages API, translated both ways, because
Anthropic's OpenAI-compatible endpoint ignores response_format. The policy table
decides which routes a run may use; there is no fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from sealbook.ledger.contracts import Route
from sealbook.settings import Config

OPENROUTER = "https://openrouter.ai/api/v1"
ANTHROPIC = "https://api.anthropic.com"
TIMEOUT = httpx.Timeout(120.0, connect=10.0)
# A hard cap on thinking plus answer. Opus 5.5 always thinks, so this leaves room
# for that while an answer here is a few hundred tokens.
ANTHROPIC_MAX_TOKENS = 16000
# Schema keywords Anthropic structured outputs reject with a 400 (checked
# 2026-10-28). Callers still validate answers against the full schema.
UNSUPPORTED = {
    "minLength",
    "maxLength",
    "maxItems",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
}


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


def _target(route: Route) -> tuple[str, str | None]:
    if route is Route.LOCAL:
        # vLLM and Ollama run without a key unless one is configured.
        return _env("ROUTE_LOCAL_BASE_URL"), os.environ.get("ROUTE_LOCAL_API_KEY")
    if route is Route.ANTHROPIC:
        return ANTHROPIC, _env("ANTHROPIC_API_KEY")
    return OPENROUTER, _env("OPENROUTER_API_KEY")


def _wire(schema):
    if isinstance(schema, list):
        return [_wire(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        k: {p: _wire(s) for p, s in v.items()} if k == "properties" else _wire(v)
        for k, v in schema.items()
        if k not in UNSUPPORTED and not (k == "minItems" and v > 1)
    }


def _to_messages(body: dict) -> dict:
    """An OpenAI chat body as a Messages API request. temperature is dropped: current
    Claude models answer any non-default value with a 400."""
    turns = body["messages"]
    out = {
        "model": body["model"],
        "max_tokens": body.get("max_tokens", ANTHROPIC_MAX_TOKENS),
        "messages": [
            {"role": m["role"], "content": m["content"]}
            for m in turns
            if m["role"] != "system"
        ],
    }
    if system := "\n\n".join(m["content"] for m in turns if m["role"] == "system"):
        out["system"] = system
    config = {}
    if fmt := body.get("response_format", {}).get("json_schema"):
        config["format"] = {"type": "json_schema", "schema": _wire(fmt["schema"])}
    # Thinking cannot be turned off on Opus 5.5; low effort is the nearest to "none".
    if effort := body.get("reasoning_effort"):
        config["effort"] = "low" if effort in ("none", "minimal") else effort
    if config:
        out["output_config"] = config
    return out


def _from_messages(data: dict) -> dict:
    """A Messages API response in the chat completion shape callers read."""
    stop = data.get("stop_reason")
    text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
    return {
        "model": data.get("model"),
        "provider": "Anthropic",
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": {"end_turn": "stop", "max_tokens": "length"}.get(
                    stop, stop
                ),
            }
        ],
        "usage": {
            "prompt_tokens": data["usage"]["input_tokens"],
            "completion_tokens": data["usage"]["output_tokens"],
        },
    }


async def chat(
    route: Route, body: dict, transport: httpx.AsyncBaseTransport | None = None
) -> Served:
    """POST one chat completion on `route`. OpenRouter reports the served model in
    `model` and the upstream provider in `provider` (checked 2026-10-21)."""
    base, key = _target(route)
    if route is Route.CLOUD_ZDR:
        body = {**body, "provider": {"zdr": True}}
    if route is Route.ANTHROPIC:
        body = _to_messages(body)
        endpoint = f"{base}/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
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
    if route is Route.ANTHROPIC:
        data = _from_messages(data)
    if not data.get("model"):
        raise RouteError(f"{route} response names no served model")
    if route is Route.LOCAL:
        provider = "local"
    elif data.get("provider"):
        provider = data["provider"]
    else:
        raise RouteError(f"{route} response names no provider")
    return Served(data, resp.content, endpoint, data["model"], provider)


def content(served: Served) -> str:
    choice = served.data["choices"][0]
    if choice.get("finish_reason") not in ("stop", None):
        raise RouteError(f"model stopped early: {choice.get('finish_reason')}")
    return choice["message"]["content"] or ""
