"""Shared setup for notification and assignment tests: a throwaway Postgres at head,
users, two investigations with synthetic blobs, and one pending proposal."""

import re
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from unittest import mock

import stk
import stk.extensions as ext
from alembic import command
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from quart_security import hash_password
from sqlalchemy import select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)
from stk.user.models import Role, User

from sealbook import blobs
from sealbook.app import create_app
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Principal, Run
from sealbook.ledger.write import append, record_acquisition
from sealbook.settings import Config

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
USERS = {
    "alice@example.com": ("Alice Hart", ["analyst"]),
    "bob@example.com": ("Bob Marsh", ["analyst"]),
    "dan@example.com": ("Dan Root", ["admin"]),
    "outsider@example.com": ("Eve Out", []),
}
TEXT = "synthetic register line one\nsynthetic register line two\n"


class CollabCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict("stk.websocket._clients", {}, clear=True))
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config("sealbook", url, quiet=True)
        command.upgrade(config, "heads")
        db_url = (
            make_url(url)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False)
        )

        class TestConfig(stk.TestingConfig, Config):
            SQLALCHEMY_DATABASE_URI = db_url
            TESTING = True
            SESSION_TYPE = None
            STK_ENV = "testing"
            STK_ENABLE_AGENT_LOGIN = True
            STK_AGENT_LOGIN_MAX_TTL_SECONDS = 60
            SECURITY_WAN_RP_ID = None
            SECURITY_WAN_EXPECTED_ORIGIN = None

        self.app = create_app(TestConfig)
        self.enterContext(
            mock.patch.object(
                Config, "BLOB_DIR", self.enterContext(tempfile.TemporaryDirectory())
            )
        )

    async def asyncSetUp(self):
        key = Ed25519PrivateKey.generate()
        async with ext.async_session_factory() as s, s.begin():
            roles = {r.name: r for r in (await s.execute(select(Role))).scalars()}
            roles.setdefault("admin", Role(name="admin"))
            self.ids = {}
            for email, (name, names) in USERS.items():
                user = User(
                    email=email,
                    name=name,
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                user.roles.extend(roles[n] for n in names)
                s.add(user)
                await s.flush()
                self.ids[email.split("@")[0]] = user.id
            agent = Principal(kind="agent", name="extractor")
            s.add(agent)
            await s.flush()
            self.blobs = {}
            for inv, n in (("inv-a", 2), ("inv-b", 1)):
                self.blobs[inv] = []
                for i in range(n):
                    sha = (
                        await blobs.put(
                            s, f"{TEXT}{inv}{i}".encode(), "text/plain", Config.BLOB_DIR
                        )
                    ).sha256
                    self.blobs[inv].append(sha)
                    await record_acquisition(
                        s,
                        blob_sha256=sha,
                        origin=f"device:{inv}{i}",
                        collector_id=agent.id,
                        at=datetime.now(UTC),
                        context={"investigation": inv},
                        sensitivity="sensitive",
                        doc_type="register",
                    )
            run = Run(
                principal_id=agent.id,
                tool="extract",
                inputs={"blobs": [self.blobs["inv-a"][0]]},
                pubkey=key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
            )
            s.add(run)
            await s.flush()
            sha = self.blobs["inv-a"][0]
            self.proposal = await append(
                s,
                StatementIn(
                    subject=f"person:{uuid.uuid4()}",
                    prop=Prop.NAME,
                    value={"text": "synthetic", "lang": "en"},
                    author_id=agent.id,
                    run_id=run.id,
                    span_blob=sha,
                    span={"start": 0, "end": 9},
                ),
                sign=lambda h: {"kind": "ed25519", "sig": key.sign(h.encode()).hex()},
            )

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def client(self, who):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(f"{who}@example.com", "/review")
        self.assertEqual(
            (await client.get(f"/_test/login?token={token}")).status_code, 302
        )
        page = await (await client.get("/review")).get_data(as_text=True)
        match = re.search(r'name="csrf-token" content="([^"]+)"', page)
        client.csrf = {"X-CSRFToken": match[1]} if match else {}
        return client
