"""MCP endpoint and the authorization server in front of it: RFC 9728 and RFC
8414 metadata, authorization code with PKCE S256, client ID metadata
documents, rotating refresh tokens, revocation, and the Agents page."""

import secrets
import uuid
from urllib.parse import urlsplit

from quart import (
    Blueprint,
    Response,
    current_app,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from quart_security import auth_required, current_user
from sqlalchemy import select, update

from stk.ledger import write
from stk.ledger.contracts import PrincipalKind
from stk.ledger.models import Principal
from stk.mcp import oauth, server
from stk.mcp.models import McpGrant, McpToken
from stk.security import csrf_exempt

bp_mcp = Blueprint("mcp", __name__)

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


# --- discovery -------------------------------------------------------------------


@bp_mcp.get("/.well-known/oauth-protected-resource")
@bp_mcp.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource():
    return {
        "resource": oauth.resource_url(),
        "authorization_servers": [oauth.base_url()],
        "scopes_supported": list(oauth.SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": current_app.config["APP_NAME"],
    }


@bp_mcp.get("/.well-known/oauth-authorization-server")
async def authorization_server():
    base = oauth.base_url()
    return {
        "issuer": base,
        "authorization_endpoint": base + url_for("mcp.authorize"),
        "token_endpoint": base + url_for("mcp.token"),
        "revocation_endpoint": base + url_for("mcp.revoke"),
        "scopes_supported": list(oauth.SCOPES),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }


# --- authorization ---------------------------------------------------------------


async def _refuse(message: str, status: int = 400):
    return await render_template("mcp/consent.html", page={"error": message}), status


def _back(pending: dict, **params):
    """Answer the client at its verified redirect URI, naming ourselves (RFC 9207)."""
    return redirect(
        oauth.with_params(
            pending["redirect_uri"],
            state=pending.get("state"),
            iss=oauth.base_url(),
            **params,
        )
    )


# quart-security's password login ignores ?next= and clears the session, so a
# signed-out user would land on the dashboard and the agent's flow would die.
# A short-lived cookie remembers the request; the first redirect after sign-in
# goes back to it.
RESUME = "mcp_resume"


@bp_mcp.after_request
async def remember_authorization(response):
    if request.endpoint == "mcp.authorize" and not current_user.is_authenticated:
        response.set_cookie(
            RESUME,
            request.full_path,
            max_age=600,
            secure=current_app.config["SESSION_COOKIE_SECURE"],
            httponly=True,
            samesite="Lax",
        )
    return response


@bp_mcp.after_app_request
async def resume_authorization(response):
    target = request.cookies.get(RESUME, "")
    if (
        request.blueprint == "security"
        and response.status_code == 302
        and current_user.is_authenticated
        and target.startswith(url_for("mcp.authorize") + "?")
    ):
        response.headers["Location"] = target
        response.delete_cookie(RESUME)
    return response


@bp_mcp.get("/oauth/authorize")
@auth_required("session")
async def authorize():
    args = request.args
    try:
        client = await oauth.fetch_client_metadata(args.get("client_id", ""))
    except oauth.ClientError as exc:
        return await _refuse(str(exc))
    redirect_uri = args.get("redirect_uri", "")
    if not any(
        oauth.redirect_matches(redirect_uri, r) for r in client["redirect_uris"]
    ):
        # Never redirect to an unverified URI: that is an open redirector.
        return await _refuse("redirect_uri is not registered by this client")
    pending = {
        "client_id": client["client_id"],
        "client_name": client["client_name"].strip(),
        "redirect_uri": redirect_uri,
        "state": args.get("state"),
    }
    scopes = list(dict.fromkeys(args.get("scope", "").split()))
    if args.get("response_type") != "code":
        return _back(pending, error="unsupported_response_type")
    if args.get("code_challenge_method") != "S256" or not oauth.CHALLENGE.fullmatch(
        args.get("code_challenge", "")
    ):
        return _back(pending, error="invalid_request", error_description="PKCE S256")
    if args.get("resource", "").rstrip("/") != oauth.resource_url():
        return _back(pending, error="invalid_target")
    if not scopes or not set(scopes) <= oauth.SCOPES.keys():
        return _back(pending, error="invalid_scope")
    if not oauth.role_scopes(current_user):
        return await _refuse("Your account has no role that permits agent access.", 403)
    pending |= {
        "nonce": secrets.token_urlsafe(16),
        "code_challenge": args["code_challenge"],
        "scope": " ".join(scopes),
        "resource": oauth.resource_url(),
    }
    session["mcp_authz"] = pending
    host = urlsplit(redirect_uri).hostname
    page = {
        "client_name": pending["client_name"],
        "client_id": pending["client_id"],
        "redirect_host": host,
        "loopback": oauth.is_loopback(redirect_uri),
        "scopes": [{"name": s, "description": oauth.SCOPES[s]} for s in scopes],
    }
    return await render_template("mcp/consent.html", page=page, nonce=pending["nonce"])


@bp_mcp.post("/oauth/authorize")
@auth_required("session")
async def consent():
    form = await request.form
    pending = session.pop("mcp_authz", None)
    if not pending or not secrets.compare_digest(
        pending["nonce"], form.get("nonce", "")
    ):
        return await _refuse("This request expired. Start again from your agent.")
    if form.get("decision") != "approve":
        return _back(pending, error="access_denied")
    if not oauth.role_scopes(current_user):
        return await _refuse("Your account has no role that permits agent access.", 403)
    db = g.db_session
    human = await write.principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    agent = Principal(
        kind=PrincipalKind.AGENT, name=pending["client_name"], on_behalf_of=human.id
    )
    db.add(agent)
    await db.flush()
    code = oauth.new_secret()
    db.add(
        McpGrant(
            user_id=current_user.id,
            agent_id=agent.id,
            client_id=pending["client_id"],
            client_name=pending["client_name"],
            redirect_uri=pending["redirect_uri"],
            scope=pending["scope"],
            resource=pending["resource"],
            code_hash=oauth.digest(code),
            code_challenge=pending["code_challenge"],
            code_expires_at=oauth.now() + oauth.CODE_TTL,
        )
    )
    await db.commit()
    return _back(pending, code=code)


# --- tokens ----------------------------------------------------------------------


def _token_error(error: str, status: int = 400):
    return {"error": error}, status, NO_STORE


@bp_mcp.post("/oauth/token")
@csrf_exempt
async def token():
    form = await request.form
    db = g.db_session
    if form.get("grant_type") == "authorization_code":
        grant = (
            await db.execute(
                select(McpGrant)
                .where(McpGrant.code_hash == oauth.digest(form.get("code", "")))
                .with_for_update()
            )
        ).scalar()
        if grant is None:
            return _token_error("invalid_grant")
        # A code is spent by its first redemption attempt, good or bad. A failed
        # or repeated one means the code may have leaked, so the grant dies.
        ok = (
            grant.code_used_at is None
            and grant.revoked_at is None
            and grant.code_expires_at > oauth.now()
            and form.get("client_id") == grant.client_id
            and form.get("redirect_uri") == grant.redirect_uri
            and form.get("resource", grant.resource).rstrip("/") == grant.resource
            and oauth.pkce_ok(form.get("code_verifier", ""), grant.code_challenge)
        )
        grant.code_used_at = grant.code_used_at or oauth.now()
        if not ok:
            grant.revoked_at = grant.revoked_at or oauth.now()
            await db.commit()
            return _token_error("invalid_grant")
    elif form.get("grant_type") == "refresh_token":
        old = (
            await db.execute(
                select(McpToken)
                .where(
                    McpToken.hash == oauth.digest(form.get("refresh_token", "")),
                    McpToken.kind == "refresh",
                )
                .with_for_update()
            )
        ).scalar()
        if old is None:
            return _token_error("invalid_grant")
        grant = await db.get(McpGrant, old.grant_id)
        if old.used_at is not None:
            # Rotated tokens are single use; a replay means one was stolen.
            grant.revoked_at = grant.revoked_at or oauth.now()
            await db.commit()
            return _token_error("invalid_grant")
        if (
            grant.revoked_at is not None
            or old.expires_at <= oauth.now()
            or form.get("client_id") != grant.client_id
        ):
            return _token_error("invalid_grant")
        if not set(form.get("scope", "").split()) <= set(grant.scope.split()):
            return _token_error("invalid_scope")
        old.used_at = oauth.now()
    else:
        return _token_error("unsupported_grant_type")
    issued = await oauth.issue_tokens(db, grant)
    await db.commit()
    return issued, 200, NO_STORE


@bp_mcp.post("/oauth/revoke")
@csrf_exempt
async def revoke():
    """RFC 7009. Revoking either token disconnects the whole grant."""
    form = await request.form
    db = g.db_session
    found = await db.get(McpToken, oauth.digest(form.get("token", "")))
    if found is not None:
        await db.execute(
            update(McpGrant)
            .where(
                McpGrant.id == found.grant_id,
                McpGrant.client_id == form.get("client_id"),
                McpGrant.revoked_at.is_(None),
            )
            .values(revoked_at=oauth.now())
        )
        await db.commit()
    return "", 200


# --- MCP endpoint ----------------------------------------------------------------


@bp_mcp.post("/mcp")
@csrf_exempt
async def mcp():
    origin = request.headers.get("Origin")
    if origin is not None and origin != oauth.base_url():
        return server.error(None, server.FORBIDDEN, "Origin not allowed", 403)
    db = g.db_session
    authorization = request.headers.get("Authorization")
    caller = await oauth.authenticate(db, authorization)
    if caller is None:
        params = {"scope": " ".join(oauth.SCOPES)}
        if authorization:
            params = {"error": "invalid_token"} | params
        return Response("", status=401, headers=server.challenge(**params) | NO_STORE)
    body = await request.get_json(silent=True)
    return await server.dispatch(db, caller, body, request.headers)


# --- the Agents page ---------------------------------------------------------------


@bp_mcp.get("/agents/")
@auth_required("session")
async def agents():
    grants = (
        await g.db_session.execute(
            select(McpGrant)
            .where(
                McpGrant.user_id == current_user.id,
                McpGrant.revoked_at.is_(None),
                McpGrant.code_used_at.is_not(None),
            )
            .order_by(McpGrant.created_at.desc())
        )
    ).scalars()
    rows = [
        {
            "id": str(grant.id),
            "name": grant.client_name,
            "client_id": grant.client_id,
            "scopes": grant.scope.split(),
            "connected": grant.created_at.isoformat(),
        }
        for grant in grants
    ]
    mcp = {
        "url": oauth.resource_url(),
        "scopes": [{"name": k, "description": v} for k, v in oauth.SCOPES.items()],
    }
    return await render_template("mcp/agents.html", rows=rows, mcp=mcp)


@bp_mcp.post("/agents/<uuid:grant_id>/revoke")
@auth_required("session")
async def revoke_agent(grant_id: uuid.UUID):
    await g.db_session.execute(
        update(McpGrant)
        .where(
            McpGrant.id == grant_id,
            McpGrant.user_id == current_user.id,
            McpGrant.revoked_at.is_(None),
        )
        .values(revoked_at=oauth.now())
    )
    await g.db_session.commit()
    return redirect(url_for("mcp.agents"))
