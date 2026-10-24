"""MCP server and its OAuth authorization server, driven through the Quart test
client against a throwaway Postgres database migrated to head."""

import base64
import hashlib
import json
import secrets
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from quart_security import hash_password
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import make_url

import stk.extensions as ext
from alembic import command
from stk import blobs
from stk.app import create_app
from stk.ledger.contracts import Prop, StatementIn
from stk.ledger.models import Principal, Statement
from stk.ledger.write import append, principal_for_user, record_acquisition, verify_db
from stk.mcp import oauth
from stk.mcp.models import McpGrant, McpToken
from stk.migrations import (
    build_alembic_config,
    get_database_url,
    throwaway_database_url,
)
from stk.settings import Config
from stk.user.models import Role, User, roles_users
from tests.passkey import SoftPasskey

POSTGRES = make_url(get_database_url()).get_backend_name() == "postgresql"
SNAPSHOT = Path(__file__).parent / "snapshots" / "mcp_tools.json"
CLIENT_ID = "https://agent.example.org/client.json"
REDIRECT = "http://127.0.0.1/callback"
ALL_SCOPES = "read propose_statements propose_identity"
PASSWORD = "Password123!"


class McpConfig(Config):
    TESTING = True
    SESSION_TYPE = None
    SECURITY_COOKIE_SECURE = False
    SESSION_COOKIE_SECURE = False
    SECURITY_PASSWORD_BREACH_CHECK = False
    QUART_RATE_LIMITER_ENABLED = False
    STK_PUBLIC_URL = None  # the issuer and audience come from the test host


def client_metadata(client_id):
    return oauth.validate_client_metadata(
        client_id,
        {
            "client_id": CLIENT_ID,
            "client_name": "Test Agent",
            "redirect_uris": ["http://127.0.0.1/callback"],
            "token_endpoint_auth_method": "none",
        },
    )


def pkce():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@unittest.skipUnless(POSTGRES, "MCP needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class McpTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(throwaway_database_url(sync=True))
        config = build_alembic_config()
        config.config_file_name = None
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        async_url = make_url(url).set(drivername="postgresql+asyncpg")
        self.app = create_app(
            type(
                "Bound",
                (McpConfig,),
                {
                    "SQLALCHEMY_DATABASE_URI": async_url.render_as_string(
                        hide_password=False
                    )
                },
            )
        )
        self.enterContext(
            patch("stk.mcp.oauth.fetch_client_metadata", side_effect=client_metadata)
        )
        blob_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(Config, "BLOB_DIR", blob_dir))

    async def asyncSetUp(self):
        async with ext.async_session_factory() as db, db.begin():
            analyst = (
                await db.execute(select(Role).where(Role.name == "analyst"))
            ).scalar_one()
            user = User(
                email="analyst@example.org",
                name="Test Analyst",
                active=True,
                password=hash_password(PASSWORD, app=self.app),
            )
            user.roles.append(analyst)
            db.add(user)
            await db.flush()
            self.user_id = user.id
            self.passkey = SoftPasskey()
            db.add(self.passkey.register(user))
            self.human = await principal_for_user(db, user.id, "Test Analyst")
            out = BytesIO()
            Image.new("RGB", (64, 64), "white").save(out, "PNG")
            self.sha = (
                await blobs.put(db, out.getvalue(), "image/png", Config.BLOB_DIR)
            ).sha256
            await record_acquisition(
                db,
                blob_sha256=self.sha,
                origin="device:test-scanner",
                collector_id=self.human.id,
                at=datetime.now(UTC),
                context={},
                sensitivity="ordinary",
                doc_type="register",
            )
        self.browser = self.app.test_client()
        self.agent = self.app.test_client()

    async def asyncTearDown(self):
        async with ext.async_session_factory() as db:
            self.assertEqual(await verify_db(db), [])
        await ext.engine.dispose()

    # --- helpers -------------------------------------------------------------------

    async def login(self):
        await self.browser.get("/login")
        async with self.browser.session_transaction() as cookie:
            csrf = cookie["_csrf_token"]
        response = await self.browser.post(
            "/login",
            form={
                "email": "analyst@example.org",
                "password": PASSWORD,
                "csrf_token": csrf,
            },
        )
        self.assertEqual(response.status_code, 302)

    async def authorize(self, challenge, scope=ALL_SCOPES, decision="approve"):
        response = await self.browser.get(
            "/oauth/authorize",
            query_string={
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": "http://127.0.0.1:53682/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": scope,
                "state": "s1",
                "resource": "http://localhost/mcp",
            },
        )
        self.assertEqual(response.status_code, 200, await response.get_data(True))
        async with self.browser.session_transaction() as cookie:
            nonce = cookie["mcp_authz"]["nonce"]
            csrf = cookie["_csrf_token"]
        response = await self.browser.post(
            "/oauth/authorize",
            form={"nonce": nonce, "csrf_token": csrf, "decision": decision},
        )
        self.assertEqual(response.status_code, 302)
        location = urlsplit(response.headers["Location"])
        self.assertEqual(location.port, 53682)
        params = {k: v[0] for k, v in parse_qs(location.query).items()}
        self.assertEqual(params["iss"], "http://localhost")
        self.assertEqual(params["state"], "s1")
        return params

    async def redeem(self, code, verifier):
        return await self.agent.post(
            "/oauth/token",
            form={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": CLIENT_ID,
                "redirect_uri": "http://127.0.0.1:53682/callback",
                "resource": "http://localhost/mcp",
            },
        )

    async def connect(self, scope=ALL_SCOPES):
        await self.login()
        verifier, challenge = pkce()
        params = await self.authorize(challenge, scope)
        response = await self.redeem(params["code"], verifier)
        self.assertEqual(response.status_code, 200)
        return await response.get_json()

    async def rpc(self, token, method, params=None):
        meta = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": method,
        }
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        return await self.agent.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": {**(params or {}), "_meta": meta},
            },
            headers=headers,
        )

    async def call(self, token, name, arguments):
        response = await self.rpc(
            token, "tools/call", {"name": name, "arguments": arguments}
        )
        self.assertEqual(response.status_code, 200, await response.get_data(True))
        result = (await response.get_json())["result"]
        if result.get("isError"):
            return {"error": result["content"][0]["text"]}
        return result["structuredContent"]

    def name_item(self, subject, text="محمد الأحمد", based_on=None):
        return {
            "subject": subject,
            "prop": "name",
            "value": {"text": text, "lang": "ar", "norm": "محمد الاحمد"},
            "span": {"blob": self.sha, "span": {"page": 1, "bbox": [0, 0, 40, 12]}},
            "evidence": ["handwritten_entry"],
            "based_on": based_on,
        }

    async def review(self, statement_ref, verdict):
        async with ext.async_session_factory() as db, db.begin():
            await append(
                db,
                StatementIn(
                    subject=statement_ref,
                    prop=Prop.REVIEW,
                    value={"verdict": verdict, "reason": "test"},
                    author_id=self.human.id,
                ),
                sign=self.passkey.sig,
            )

    # --- discovery and OAuth -------------------------------------------------------

    async def test_metadata_and_unauthenticated_challenge(self):
        prm = await (
            await self.agent.get("/.well-known/oauth-protected-resource/mcp")
        ).get_json()
        self.assertEqual(prm["resource"], "http://localhost/mcp")
        self.assertEqual(prm["authorization_servers"], ["http://localhost"])
        asm = await (
            await self.agent.get("/.well-known/oauth-authorization-server")
        ).get_json()
        self.assertEqual(asm["issuer"], "http://localhost")
        self.assertEqual(asm["code_challenge_methods_supported"], ["S256"])
        self.assertTrue(asm["client_id_metadata_document_supported"])
        self.assertTrue(asm["authorization_response_iss_parameter_supported"])
        self.assertNotIn("registration_endpoint", asm)
        response = await self.agent.post("/mcp", json={})
        self.assertEqual(response.status_code, 401)
        self.assertIn(
            'resource_metadata="http://localhost/.well-known/oauth-protected-resource/mcp"',
            response.headers["WWW-Authenticate"],
        )

    async def test_full_flow_tools_snapshot_and_refresh_rotation(self):
        tokens = await self.connect()
        self.assertEqual(tokens["scope"], ALL_SCOPES)
        discover = await (
            await self.rpc(tokens["access_token"], "server/discover")
        ).get_json()
        self.assertIn("2026-07-28", discover["result"]["supportedVersions"])
        listed = await (await self.rpc(tokens["access_token"], "tools/list")).get_json()
        self.assertEqual(listed["result"], json.loads(SNAPSHOT.read_text()))

        me = await self.call(tokens["access_token"], "read", {"target": "whoami"})
        self.assertEqual(me["agent"]["kind"], "agent")
        self.assertEqual(me["agent"]["name"], "Test Agent")
        self.assertEqual(me["agent"]["on_behalf_of"], f"principal:{self.human.id}")
        self.assertEqual(me["scopes"], sorted(ALL_SCOPES.split()))

        refreshed = await self.agent.post(
            "/oauth/token",
            form={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": CLIENT_ID,
            },
        )
        self.assertEqual(refreshed.status_code, 200)
        fresh = await refreshed.get_json()
        self.assertNotEqual(fresh["refresh_token"], tokens["refresh_token"])
        me = await self.call(fresh["access_token"], "read", {"target": "whoami"})
        self.assertEqual(me["agent"]["name"], "Test Agent")
        # Replaying a rotated refresh token means it leaked: the grant dies.
        replay = await self.agent.post(
            "/oauth/token",
            form={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": CLIENT_ID,
            },
        )
        self.assertEqual((await replay.get_json())["error"], "invalid_grant")
        response = await self.rpc(fresh["access_token"], "tools/list")
        self.assertEqual(response.status_code, 401)

    async def test_wrong_verifier_and_reused_code_fail(self):
        await self.login()
        verifier, challenge = pkce()
        params = await self.authorize(challenge)
        response = await self.redeem(params["code"], pkce()[0])
        self.assertEqual((await response.get_json())["error"], "invalid_grant")
        response = await self.redeem(params["code"], verifier)
        self.assertEqual((await response.get_json())["error"], "invalid_grant")

        verifier, challenge = pkce()
        params = await self.authorize(challenge)
        first = await self.redeem(params["code"], verifier)
        self.assertEqual(first.status_code, 200)
        token = (await first.get_json())["access_token"]
        again = await self.redeem(params["code"], verifier)
        self.assertEqual((await again.get_json())["error"], "invalid_grant")
        self.assertEqual((await self.rpc(token, "tools/list")).status_code, 401)

    async def test_signed_out_user_returns_to_consent_after_login(self):
        start = "/oauth/authorize?client_id=x&state=s1"
        response = await self.browser.get(start)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/login"))
        await self.browser.get("/login")
        async with self.browser.session_transaction() as cookie:
            csrf = cookie["_csrf_token"]
        response = await self.browser.post(
            "/login",
            form={
                "email": "analyst@example.org",
                "password": PASSWORD,
                "csrf_token": csrf,
            },
        )
        self.assertEqual(response.headers["Location"], start)

    async def test_denied_consent_and_plain_challenge_refused(self):
        await self.login()
        params = await self.authorize(pkce()[1], decision="deny")
        self.assertEqual(params["error"], "access_denied")
        response = await self.browser.get(
            "/oauth/authorize",
            query_string={
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT,
                "code_challenge": "x" * 43,
                "code_challenge_method": "plain",
                "scope": "read",
                "resource": "http://localhost/mcp",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=invalid_request", response.headers["Location"])
        response = await self.browser.get(
            "/oauth/authorize",
            query_string={
                "client_id": CLIENT_ID,
                "redirect_uri": "https://evil.example/",
            },
        )
        self.assertEqual(response.status_code, 400)

    async def test_expired_and_revoked_tokens_fail(self):
        tokens = await self.connect()
        async with ext.async_session_factory() as db, db.begin():
            await db.execute(
                update(McpToken)
                .where(McpToken.hash == oauth.digest(tokens["access_token"]))
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        response = await self.rpc(tokens["access_token"], "tools/list")
        self.assertEqual(response.status_code, 401)
        self.assertIn('error="invalid_token"', response.headers["WWW-Authenticate"])

        tokens = await self.connect()
        revoked = await self.agent.post(
            "/oauth/revoke",
            form={"token": tokens["refresh_token"], "client_id": CLIENT_ID},
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(
            (await self.rpc(tokens["access_token"], "tools/list")).status_code, 401
        )

        tokens = await self.connect()
        page = await (await self.browser.get("/agents/")).get_data(True)
        self.assertIn("Test Agent", page)
        async with ext.async_session_factory() as db:
            grant_id = await db.scalar(
                select(McpGrant.id).order_by(McpGrant.created_at.desc()).limit(1)
            )
        async with self.browser.session_transaction() as cookie:
            csrf = cookie["_csrf_token"]
        response = await self.browser.post(
            f"/agents/{grant_id}/revoke", form={"csrf_token": csrf}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            (await self.rpc(tokens["access_token"], "tools/list")).status_code, 401
        )

    # --- permissions ---------------------------------------------------------------

    async def test_missing_scope_cannot_propose(self):
        tokens = await self.connect(scope="read")
        response = await self.rpc(
            tokens["access_token"],
            "tools/call",
            {
                "name": "propose_statements",
                "arguments": {"items": [self.name_item(f"person:{uuid.uuid4()}")]},
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn(
            'error="insufficient_scope"', response.headers["WWW-Authenticate"]
        )
        self.assertIn(
            'scope="propose_statements"', response.headers["WWW-Authenticate"]
        )
        async with ext.async_session_factory() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(Statement)
                .where(Statement.prop == "name")
            )
        self.assertEqual(count, 0)

    async def test_role_removal_blocks_existing_token(self):
        tokens = await self.connect()
        self.assertIn(
            "scopes",
            await self.call(tokens["access_token"], "read", {"target": "whoami"}),
        )
        async with ext.async_session_factory() as db, db.begin():
            await db.execute(
                delete(roles_users).where(roles_users.c.user_id == self.user_id)
            )
        for name, arguments in (
            ("read", {"target": "whoami"}),
            (
                "propose_statements",
                {"items": [self.name_item(f"person:{uuid.uuid4()}")]},
            ),
        ):
            response = await self.rpc(
                tokens["access_token"],
                "tools/call",
                {"name": name, "arguments": arguments},
            )
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("WWW-Authenticate", response.headers)
        # Without a role the human cannot hand out access either.
        response = await self.browser.get(
            "/oauth/authorize",
            query_string={
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT,
                "code_challenge": pkce()[1],
                "code_challenge_method": "S256",
                "scope": "read",
                "resource": "http://localhost/mcp",
            },
        )
        self.assertEqual(response.status_code, 403)

    # --- tools ---------------------------------------------------------------------

    async def test_proposal_attribution_retry_and_rejection(self):
        token = (await self.connect())["access_token"]
        subject = f"person:{uuid.uuid4()}"
        item = self.name_item(subject)
        first = await self.call(token, "propose_statements", {"items": [item]})
        [result] = first["results"]
        self.assertEqual(result["tier"], "unconfirmed")

        fetched = await self.call(
            token, "read", {"target": "fetch", "ref": result["statement"]}
        )
        self.assertEqual(fetched["author"]["kind"], "agent")
        self.assertEqual(
            fetched["author"]["on_behalf_of"], f"principal:{self.human.id}"
        )
        self.assertEqual(fetched["run"]["tool"], "mcp")
        self.assertEqual(fetched["span"]["blob"], f"blob:{self.sha}")

        retry = await self.call(token, "propose_statements", {"items": [item]})
        self.assertEqual(retry["results"][0]["statement"], result["statement"])
        async with ext.async_session_factory() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(Statement)
                .where(Statement.subject == subject)
            )
            agent = await db.get(
                Principal, uuid.UUID(fetched["author"]["id"].removeprefix("principal:"))
            )
        self.assertEqual(count, 1)
        self.assertEqual(agent.on_behalf_of, self.human.id)

        await self.review(result["statement"], "reject")
        again = await self.call(token, "propose_statements", {"items": [item]})
        self.assertEqual(
            again["results"][0]["refused"],
            "value was rejected by a human for this subject",
        )
        self.assertEqual(again["results"][0]["existing"], result["statement"])

    async def test_bad_proposals_are_explained(self):
        token = (await self.connect())["access_token"]
        item = self.name_item(f"person:{uuid.uuid4()}")
        del item["span"]
        self.assertIn(
            "span",
            (await self.call(token, "propose_statements", {"items": [item]}))["error"],
        )
        item = self.name_item(f"blob:{self.sha}")
        self.assertIn(
            "person:<uuid>",
            (await self.call(token, "propose_statements", {"items": [item]}))["error"],
        )

    async def test_reads_are_logged_as_access(self):
        token = (await self.connect())["access_token"]
        subject = f"person:{uuid.uuid4()}"
        proposed = await self.call(
            token, "propose_statements", {"items": [self.name_item(subject)]}
        )
        statement = proposed["results"][0]["statement"]
        found = await self.call(token, "read", {"target": "search", "query": "الاحمد"})
        [hit] = found["hits"]
        self.assertEqual(hit["id"], statement)
        self.assertEqual(hit["neighbours"]["blob"], f"blob:{self.sha}")
        self.assertTrue(hit["neighbours"]["author"].startswith("principal:"))
        history = await self.call(token, "read", {"target": "history", "ref": subject})
        self.assertEqual([s["id"] for s in history["statements"]], [statement])
        self.assertIsNone(history["head"])
        blob = await self.call(
            token, "read", {"target": "fetch", "ref": f"blob:{self.sha}"}
        )
        self.assertEqual([s["id"] for s in blob["statements"]], [statement])
        person = await self.call(token, "read", {"target": "fetch", "ref": subject})
        self.assertEqual(person["blobs"], [f"blob:{self.sha}"])
        await self.call(token, "read", {"target": "whoami"})
        async with ext.async_session_factory() as db:
            access = (
                (await db.execute(select(Statement).where(Statement.prop == "access")))
                .scalars()
                .all()
            )
        self.assertEqual(len(access), 5)
        self.assertTrue(all(a.author_id != self.human.id for a in access))
        self.assertIn(subject, {a.subject for a in access})

    async def test_identity_needs_more_than_names(self):
        token = (await self.connect())["access_token"]
        a, b = f"person:{uuid.uuid4()}", f"person:{uuid.uuid4()}"
        birth = {
            "prop": "birth_date",
            "value": {"text": "1985-03-02", "lang": "en"},
            "span": {"blob": self.sha, "span": {"page": 1, "bbox": [0, 20, 40, 32]}},
            "evidence": ["handwritten_entry"],
            "based_on": None,
        }
        made = await self.call(
            token,
            "propose_statements",
            {
                "items": [
                    self.name_item(a),
                    self.name_item(b, text="محمد الاحمد"),
                    birth | {"subject": a},
                    birth
                    | {"subject": b, "value": {"text": "02/03/1985", "lang": "en"}},
                ]
            },
        )
        name_a, name_b, birth_a, birth_b = [r["statement"] for r in made["results"]]
        proposal = {
            "relation": "same_as",
            "person": a,
            "other": b,
            "basis": [name_a, name_b],
            "evidence": ["corroborating_field"],
            "based_on": None,
        }
        refused = await self.call(token, "propose_identity", proposal)
        self.assertIn("names alone", refused["error"])
        proposal["basis"] = [name_a, name_b, birth_a, birth_b]
        made = await self.call(token, "propose_identity", proposal)
        self.assertEqual(made["tier"], "unconfirmed")
        fetched = await self.call(
            token, "read", {"target": "fetch", "ref": made["statement"]}
        )
        self.assertEqual(fetched["prop"], "same_as")
        self.assertEqual(fetched["value"], {"person": b.removeprefix("person:")})

    # --- transport -----------------------------------------------------------------

    async def test_transport_validation(self):
        token = (await self.connect())["access_token"]
        response = await self.agent.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {
                    "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
                },
            },
            headers={
                "Authorization": f"Bearer {token}",
                "MCP-Protocol-Version": "2025-06-18",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual((await response.get_json())["error"]["code"], -32020)
        response = await self.agent.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={
                "Authorization": f"Bearer {token}",
                "MCP-Protocol-Version": "1900-01-01",
            },
        )
        self.assertEqual((await response.get_json())["error"]["code"], -32022)
        legacy = await self.agent.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(
            (await legacy.get_json())["result"]["protocolVersion"], "2025-06-18"
        )
        response = await self.agent.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 202)
        response = await self.agent.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "https://evil.example",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.agent.get("/mcp")).status_code, 405)


class ClientMetadataTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_id_must_be_public_https(self):
        for client_id in (
            "http://agent.example.org/client.json",
            "https://agent.example.org/",
            "https://127.0.0.1/client.json",
            "https://10.0.0.8/client.json",
        ):
            with self.assertRaises(oauth.ClientError, msg=client_id):
                await oauth.fetch_client_metadata(client_id)

    def test_metadata_must_match_and_stay_public(self):
        good = {
            "client_id": CLIENT_ID,
            "client_name": "Agent",
            "redirect_uris": [REDIRECT],
        }
        self.assertEqual(oauth.validate_client_metadata(CLIENT_ID, good), good)
        for bad in (
            good | {"client_id": "https://other.example/client.json"},
            good | {"redirect_uris": ["http://evil.example/callback"]},
            good | {"token_endpoint_auth_method": "client_secret_basic"},
            {k: v for k, v in good.items() if k != "client_name"},
        ):
            with self.assertRaises(oauth.ClientError):
                oauth.validate_client_metadata(CLIENT_ID, bad)
        self.assertTrue(
            oauth.redirect_matches("http://127.0.0.1:4000/callback", REDIRECT)
        )
        self.assertFalse(
            oauth.redirect_matches(
                "https://agent.example.org:444/cb", "https://agent.example.org/cb"
            )
        )


if __name__ == "__main__":
    unittest.main()
