"""Stateless Streamable HTTP JSON-RPC for MCP 2026-07-28: server/discover,
tools/list and tools/call, plus initialize and ping for clients still on the
2025 handshake revisions. Every response is a single JSON object; no sessions,
no SSE, no server-to-client requests."""

from __future__ import annotations

import base64
import binascii
import tomllib

import orjson
from quart import Response
from sqlalchemy.ext.asyncio import AsyncSession

from stk.mcp import oauth, tools
from stk.settings import Config

PROTOCOL = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED = (PROTOCOL, *LEGACY)
# Read from pyproject, not package metadata: the container installs dependencies
# without the project itself.
with open(f"{Config.PROJECT_ROOT}/pyproject.toml", "rb") as _f:
    SERVER_INFO = {
        "name": "sealbook",
        "version": tomllib.load(_f)["project"]["version"],
    }
INSTRUCTIONS = (
    "Sealbook is an evidence ledger for human rights documentation. You act as "
    "your own agent principal on behalf of one human, never as that human, and "
    "never with more than their role allows. You can read and propose; you "
    "cannot accept, delete or export anything. Every read is logged. Start with "
    "read whoami, find material with read search, check a subject with read "
    "history before proposing, and cite the exact span for every value. "
    "Proposals wait for human review."
)
TOOLS_LIST = {
    "resultType": "complete",
    "tools": tools.TOOLS,
    "ttlMs": 3_600_000,
    "cacheScope": "public",
}
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
FORBIDDEN = -32001  # implementation-defined range
HEADER_MISMATCH = -32020
UNSUPPORTED_VERSION = -32022


def _json(payload: dict, status: int = 200, headers: dict | None = None) -> Response:
    return Response(
        orjson.dumps(payload),
        status=status,
        content_type="application/json",
        headers=headers,
    )


def result(rid, value: dict) -> Response:
    return _json({"jsonrpc": "2.0", "id": rid, "result": value})


def error(rid, code: int, message: str, status: int = 200, **extra) -> Response:
    body = {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
    if "data" in extra:
        body["error"]["data"] = extra["data"]
    return _json(body, status, extra.get("headers"))


def _header_value(value: str | None) -> str | None:
    """Mcp-Name and Mcp-Param values may carry =?base64?...?= for non-ASCII."""
    if value and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            return None
    return value


def challenge(**params) -> dict:
    fields = {"resource_metadata": oauth.resource_metadata_url(), **params}
    return {
        "WWW-Authenticate": "Bearer "
        + ", ".join(f'{k}="{v}"' for k, v in fields.items())
    }


async def dispatch(db: AsyncSession, caller: oauth.Caller, body, headers) -> Response:
    if (
        not isinstance(body, dict)
        or body.get("jsonrpc") != "2.0"
        or not isinstance(body.get("method"), str)
    ):
        return error(None, INVALID_REQUEST, "Expected one JSON-RPC message", 400)
    if "id" not in body:
        return Response("", status=202)  # notifications need no answer
    rid, method, params = body["id"], body["method"], body.get("params") or {}
    if not isinstance(params, dict):
        return error(rid, INVALID_PARAMS, "params must be an object")
    if method == "initialize":
        asked = params.get("protocolVersion")
        return result(
            rid,
            {
                "protocolVersion": asked if asked in LEGACY else LEGACY[0],
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            },
        )
    meta = params.get("_meta") or {}
    version = meta.get("io.modelcontextprotocol/protocolVersion")
    if version is not None:
        # Modern requests mirror body fields into headers; a mismatch could let a
        # proxy route on one value while we execute another.
        mirrored = {
            "MCP-Protocol-Version": version,
            "Mcp-Method": method,
        }
        if method == "tools/call":
            mirrored["Mcp-Name"] = params.get("name")
        for name, want in mirrored.items():
            if _header_value(headers.get(name)) != want:
                return error(rid, HEADER_MISMATCH, f"{name} header mismatch", 400)
    else:
        # 2025-03-26 clients predate the version header.
        version = headers.get("MCP-Protocol-Version", "2025-03-26")
    if version not in SUPPORTED:
        return error(
            rid,
            UNSUPPORTED_VERSION,
            "Unsupported protocol version",
            400,
            data={"supported": list(SUPPORTED), "requested": version},
        )
    if method == "ping":
        return result(rid, {"resultType": "complete"})
    if method == "server/discover":
        return result(
            rid,
            {
                "resultType": "complete",
                "supportedVersions": list(SUPPORTED),
                "capabilities": {"tools": {}},
                "instructions": INSTRUCTIONS,
                "ttlMs": 3_600_000,
                "cacheScope": "public",
                "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO},
            },
        )
    if method == "tools/list":
        return result(rid, TOOLS_LIST)
    if method == "tools/call":
        return await _call_tool(db, caller, rid, params)
    return error(rid, METHOD_NOT_FOUND, "Method not found", 404)


async def _call_tool(db: AsyncSession, caller: oauth.Caller, rid, params) -> Response:
    name, arguments = params.get("name"), params.get("arguments") or {}
    if name not in tools.VALIDATORS or not isinstance(arguments, dict):
        return error(rid, INVALID_PARAMS, f"Unknown tool: {name}")
    if name not in caller.scopes:
        if name not in oauth.role_scopes(caller.user):
            # A new token would not help: the human lacks the permission.
            return error(
                rid,
                FORBIDDEN,
                "The user you act for has no role that permits this",
                403,
            )
        return error(
            rid,
            FORBIDDEN,
            f"This grant lacks the {name} scope",
            403,
            headers=challenge(error="insufficient_scope", scope=name),
        )
    try:
        value = await tools.call(db, caller, name, arguments)
    except tools.ToolError as exc:
        await db.rollback()
        return result(
            rid,
            {
                "resultType": "complete",
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            },
        )
    await db.commit()
    return result(
        rid,
        {
            "resultType": "complete",
            "content": [{"type": "text", "text": orjson.dumps(value).decode()}],
            "structuredContent": value,
        },
    )
