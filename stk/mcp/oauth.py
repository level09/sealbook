"""Authorization server pieces for MCP agents: client ID metadata documents,
PKCE, opaque rotating tokens, and the bearer check that computes effective
scopes on every request."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import orjson
from quart import current_app, request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stk import netguard
from stk.ledger.models import Principal
from stk.mcp.models import McpGrant, McpToken
from stk.user.models import User

# One scope per tool, named after it.
SCOPES = {
    "read": "Search and read evidence and its history. Every read is logged.",
    "propose_statements": "Propose facts read from documents, for human review.",
    "propose_identity": "Propose that two person mentions are or are not the same "
    "person, for human review.",
}
# v0 rule: analysts and admins may read and propose; nobody else may do anything.
ROLE_SCOPES = {"analyst": frozenset(SCOPES), "admin": frozenset(SCOPES)}

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=5)
METADATA_MAX_BYTES = 5_000  # the CIMD draft's recommended cap
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")


class ClientError(Exception):
    """The client_id or its metadata document is unusable."""


@dataclass(frozen=True)
class Caller:
    grant: McpGrant
    agent: Principal
    user: User
    scopes: frozenset[str]  # effective: role and grant, computed per request


def now() -> datetime:
    return datetime.now(UTC)


def base_url() -> str:
    return (current_app.config.get("STK_PUBLIC_URL") or request.host_url).rstrip("/")


def resource_url() -> str:
    return base_url() + "/mcp"


def resource_metadata_url() -> str:
    return base_url() + "/.well-known/oauth-protected-resource/mcp"


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def role_scopes(user: User) -> frozenset[str]:
    return frozenset().union(*(ROLE_SCOPES.get(r.name, ()) for r in user.roles))


def pkce_ok(verifier: str, challenge: str) -> bool:
    if not VERIFIER.fullmatch(verifier):
        return False
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return secrets.compare_digest(computed.rstrip(b"=").decode(), challenge)


def with_params(uri: str, **params) -> str:
    parts = urlsplit(uri)
    query = parse_qsl(parts.query) + [(k, v) for k, v in params.items() if v]
    return urlunsplit(parts._replace(query=urlencode(query)))


def is_loopback(uri: str) -> bool:
    parts = urlsplit(uri)
    return parts.scheme == "http" and parts.hostname in LOOPBACK


def redirect_matches(requested: str, registered: str) -> bool:
    """Exact match, except that a loopback redirect may use any port (OAuth 2.1
    section 8.4.2): native clients bind an ephemeral one."""
    if requested == registered:
        return True
    a, b = urlsplit(requested), urlsplit(registered)
    return (
        is_loopback(requested)
        and not a.fragment
        and (a.scheme, a.hostname, a.path, a.query)
        == (b.scheme, b.hostname, b.path, b.query)
    )


def _redirect_allowed(uri) -> bool:
    if not isinstance(uri, str):
        return False
    parts = urlsplit(uri)
    return not parts.fragment and (parts.scheme == "https" or is_loopback(uri))


async def fetch_client_metadata(client_id: str) -> dict:
    """Fetch and validate a Client ID Metadata Document. The client_id is an
    https URL whose JSON names the client and its redirect URIs."""
    parts = urlsplit(client_id)
    segments = parts.path.split("/")
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.path in ("", "/")
        or "." in segments
        or ".." in segments
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise ClientError("client_id must be an https URL with a path")
    body = b""
    try:
        async with netguard.stream(
            client_id,
            follow_redirects=False,
            headers={"Accept": "application/json"},
            timeout=5,
        ) as (response, _):
            if response.status_code != 200:
                raise ClientError(
                    f"client metadata fetch returned {response.status_code}"
                )
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > METADATA_MAX_BYTES:
                    raise ClientError("client metadata document is too large")
    except netguard.Blocked as exc:
        raise ClientError(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise ClientError("client metadata could not be fetched") from exc
    try:
        doc = orjson.loads(body)
    except orjson.JSONDecodeError as exc:
        raise ClientError("client metadata is not JSON") from exc
    return validate_client_metadata(client_id, doc)


def validate_client_metadata(client_id: str, doc) -> dict:
    if not isinstance(doc, dict) or doc.get("client_id") != client_id:
        raise ClientError("client metadata client_id does not match its URL")
    name = doc.get("client_name")
    if not isinstance(name, str) or not 0 < len(name.strip()) <= 100:
        raise ClientError("client metadata needs a client_name")
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(map(_redirect_allowed, uris)):
        raise ClientError("redirect_uris must be https or http loopback URLs")
    if "client_secret" in doc or doc.get("token_endpoint_auth_method", "none") != (
        "none"
    ):
        raise ClientError("only public clients (token_endpoint_auth_method none)")
    return doc


async def issue_tokens(db: AsyncSession, grant: McpGrant) -> dict:
    access, refresh = new_secret(), new_secret()
    db.add_all(
        [
            McpToken(
                hash=digest(access),
                grant_id=grant.id,
                kind="access",
                expires_at=now() + ACCESS_TTL,
            ),
            McpToken(
                hash=digest(refresh),
                grant_id=grant.id,
                kind="refresh",
                expires_at=now() + REFRESH_TTL,
            ),
        ]
    )
    await db.flush()
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh,
        "scope": grant.scope,
    }


async def authenticate(db: AsyncSession, header: str | None) -> Caller | None:
    """The caller behind a bearer token, or None. Role and grant are read fresh
    on every request, so a role change or revocation applies to the next call."""
    if not header or not header.startswith("Bearer "):
        return None
    row = (
        await db.execute(
            select(McpToken, McpGrant)
            .join(McpGrant, McpGrant.id == McpToken.grant_id)
            .where(McpToken.hash == digest(header[7:].strip()))
            .where(McpToken.kind == "access")
        )
    ).first()
    if row is None:
        return None
    token, grant = row
    if (
        token.expires_at <= now()
        or grant.revoked_at is not None
        or grant.resource != resource_url()
    ):
        return None
    user = await db.get(User, grant.user_id)
    if not user.active:
        return None
    agent = await db.get(Principal, grant.agent_id)
    scopes = frozenset(grant.scope.split()) & role_scopes(user)
    return Caller(grant=grant, agent=agent, user=user, scopes=scopes)
