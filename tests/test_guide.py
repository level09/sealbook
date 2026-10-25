"""The Guide: its scenarios are well formed, every Open it link is a real page for
an analyst, and the proof a reader recomputes in the browser holds."""

import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from io import BytesIO
from unittest import mock
from urllib.parse import urlsplit

import stk
import stk.extensions as ext
from alembic import command
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PIL import Image
from quart_security import hash_password
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)
from stk.user.models import Role, User

from sealbook import blobs
from sealbook.app import create_app
from sealbook.guide import content
from sealbook.ledger import chain, merkle
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Principal, Run, Statement
from sealbook.ledger.write import append, record_acquisition
from sealbook.settings import Config
from tests.passkey import attester

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
CASE = "case-guide-test"


def links():
    return [link["href"] for s in content.SCENARIOS for link in s["links"]]


def words():
    for s in content.SCENARIOS:
        yield from (s["title"], s["who"], s["result"], s.get("needs") or "")
        yield from s["steps"] + s["ai"] + s["you"]
        yield from (link["label"] for link in s["links"])
    for t in content.TRUST:
        yield from (t["title"], t["body"])


class ContentTest(unittest.TestCase):
    def test_scenarios_are_well_formed(self):
        ids = [s["id"] for s in content.SCENARIOS]
        self.assertEqual(len(ids), len(set(ids)), "scenario ids must be unique")
        areas = {a["id"] for a in content.AREAS}
        for s in content.SCENARIOS:
            self.assertRegex(s["id"], r"^[a-z0-9-]+$")
            self.assertIn(s["area"], areas)
            self.assertTrue(3 <= len(s["steps"]) <= 6, s["id"])
            self.assertTrue(s["ai"] and s["you"] and s["links"], s["id"])

    def test_no_dashes_in_the_copy(self):
        for text in words():
            self.assertFalse({chr(0x2013), chr(0x2014)} & set(text), text)

    def test_every_link_is_a_get_page(self):
        adapter = create_app().url_map.bind("localhost")
        for href in links():
            path = urlsplit(href).path
            self.assertFalse(path.startswith("/api/"), href)
            adapter.match(path, method="GET")  # raises NotFound on a dead link


@unittest.skipUnless(POSTGRES, "the guide needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class GuideTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config("sealbook", url, quiet=True)
        command.upgrade(config, "heads")
        self.enterContext(
            mock.patch.object(
                Config, "BLOB_DIR", self.enterContext(tempfile.TemporaryDirectory())
            )
        )
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

        self.app = create_app(TestConfig)

    async def asyncSetUp(self):
        async with ext.async_session_factory() as s, s.begin():
            analyst = await s.scalar(select(Role).where(Role.name == "analyst"))
            for email, roles in (("me@example.com", [analyst]), ("no@example.com", [])):
                user = User(
                    email=email,
                    name=email.split("@")[0],
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                user.roles.extend(roles)
                s.add(user)
            reviewer, passkey = await attester(s, "reviewer")
            agent = Principal(kind="agent", name="extractor")
            s.add(agent)
            await s.flush()
            key = Ed25519PrivateKey.generate()
            run = Run(
                principal_id=agent.id,
                tool="extract",
                route="local",
                pubkey=key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
            )
            s.add(run)
            image = BytesIO()
            Image.new("RGB", (64, 64), "white").save(image, "PNG")
            sha = (
                await blobs.put(s, image.getvalue(), "image/png", Config.BLOB_DIR)
            ).sha256
            await record_acquisition(
                s,
                blob_sha256=sha,
                origin="device:test",
                collector_id=reviewer.id,
                at=datetime.now(UTC),
                context={"investigation": CASE, "title": "Synthetic register"},
                sensitivity="sensitive",
                doc_type="register",
            )
            claim = await append(
                s,
                StatementIn(
                    subject=f"person:{uuid.uuid4()}",
                    prop=Prop.NAME,
                    value={"text": "محمد", "lang": "ar"},
                    author_id=agent.id,
                    run_id=run.id,
                    span_blob=sha,
                    span={"page": 1, "bbox": [0, 0, 10, 10]},
                ),
                sign=lambda h: {"kind": "ed25519", "sig": key.sign(h.encode()).hex()},
            )
            await append(
                s,
                StatementIn(
                    subject=f"statement:{claim.id}",
                    prop=Prop.REVIEW,
                    value={"verdict": "accept", "reason": "test"},
                    author_id=reviewer.id,
                ),
                sign=passkey.sig,
            )
            self.claim = claim.id

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def client(self, email="me@example.com"):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(email, "/guide")
        await client.get(f"/_test/login?token={token}")
        return client

    async def test_page_and_every_link_open_for_an_analyst(self):
        me = await self.client()
        page = await me.get("/guide")
        self.assertEqual(page.status_code, 200)
        html = await page.get_data(as_text=True)
        for s in content.SCENARIOS:
            self.assertIn(s["id"], html)
        for href in links():
            r = await me.get(href)
            self.assertEqual(r.status_code, 200, href)
        nobody = await self.client("no@example.com")
        self.assertEqual((await nobody.get("/guide")).status_code, 403)
        self.assertEqual(
            (await nobody.get(f"/api/guide/proof?investigation={CASE}")).status_code,
            403,
        )

    async def test_proof_recomputes_and_logs_one_view(self):
        me = await self.client()
        r = await me.get(f"/api/guide/proof?investigation={CASE}")
        p = await r.get_json()
        self.assertEqual(r.status_code, 200, p)
        self.assertEqual(p["claim"]["title"], "Synthetic register")
        self.assertNotIn("محمد", await r.get_data(as_text=True))
        # What the browser does: row to row hash, entry link, next link, Merkle path.
        self.assertEqual(p["row"]["id"], str(self.claim))
        self.assertEqual(chain.row_hash("statement", p["row"]), p["row_hash"])
        e = p["entry"]
        self.assertEqual(
            chain.link(e["prev_hash"], e["seq"], p["kind"], p["row_hash"]), e["hash"]
        )
        self.assertEqual(p["next"]["prev_hash"], e["hash"])
        b = p["batch"]
        self.assertFalse(b["closed"])
        self.assertTrue(
            merkle.verify_inclusion(
                e["hash"], b["index"], b["size"], b["path"], b["root"]
            )
        )
        sig = p["run_signature"]
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(sig["pubkey"])).verify(
            bytes.fromhex(sig["sig"]), p["row_hash"].encode()
        )
        self.assertTrue(sig["verifies"])
        self.assertEqual(p["review"]["by"], "reviewer")
        self.assertTrue(p["review"]["passkey"] and p["review"]["verifies"])
        # A changed character breaks the row hash.
        c = p["row"]["commitment"]
        forged = p["row"] | {"commitment": ("1" if c[0] == "0" else "0") + c[1:]}
        self.assertNotEqual(chain.row_hash("statement", forged), p["row_hash"])

        await me.get(f"/api/guide/proof?investigation={CASE}")
        async with ext.async_session_factory() as s:
            views = await s.scalar(
                select(func.count()).where(
                    Statement.subject == f"statement:{self.claim}",
                    Statement.prop == Prop.ACCESS,
                )
            )
        self.assertEqual(views, 1, "one access record per reader and session")

    async def test_case_without_a_signed_claim(self):
        me = await self.client()
        r = await me.get("/api/guide/proof?investigation=case-nobody")
        self.assertEqual(await r.get_json(), {"claim": None})
        self.assertEqual((await me.get("/api/guide/proof")).status_code, 400)


if __name__ == "__main__":
    unittest.main()
