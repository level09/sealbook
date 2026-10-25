"""A passkey assertion over a prepared statement hash makes a human review count."""

import unittest
import uuid
from datetime import datetime

import stk
import stk.extensions as ext
from alembic import command
from quart_security import hash_password
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)
from stk.user.models import User, WebAuthn

from sealbook.app import app as sealbook_app
from sealbook.app import create_app
from sealbook.ledger import attest
from sealbook.ledger.contracts import Prop, Refused, StatementIn
from sealbook.ledger.models import StatementTier
from sealbook.ledger.write import append, commit, prepare, principal_for_user, verify_db
from sealbook.settings import Config
from tests.passkey import SoftPasskey

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"


@unittest.skipUnless(POSTGRES, "ledger needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class AttestTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config(sealbook_app, url, quiet=True)
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
            SECURITY_WAN_RP_ID = None
            SECURITY_WAN_EXPECTED_ORIGIN = None

        self.app = create_app(TestConfig)

    async def test_passkey_review_accepts(self):
        key, other = SoftPasskey(), SoftPasskey()
        async with ext.async_session_factory() as s, s.begin():
            user = User(
                email="reviewer@example.com",
                name="reviewer",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            s.add(user)
            await s.flush()
            s.add(key.register(user))
            human = await principal_for_user(s, user.id, user.name)
            target = await append(
                s,
                StatementIn(
                    subject=f"person:{uuid.uuid4()}",
                    prop=Prop.NAME,
                    value={"text": "يوسف", "lang": "ar"},
                    author_id=human.id,
                ),
            )
            review = await prepare(
                s,
                StatementIn(
                    subject=f"statement:{target.id}",
                    prop=Prop.REVIEW,
                    value={"verdict": "accept", "reason": ""},
                    author_id=human.id,
                ),
            )
            async with self.app.test_request_context(
                "/", headers={"Host": "localhost"}
            ):
                opts = await attest.options(s, user, review.hash)
                self.assertEqual(opts["rpId"], "localhost")
                with self.assertRaises(Refused):
                    await attest.verify(
                        s, user, review.hash, other.assert_(review.hash)
                    )
                with self.assertRaises(Refused):
                    await attest.verify(s, user, "0" * 64, key.assert_(review.hash))
                sig = await attest.verify(
                    s, user, review.hash, key.assert_(review.hash)
                )
            await commit(s, review, sig)
            tier = (
                await s.execute(
                    select(StatementTier.reason).where(
                        StatementTier.statement_id == target.id
                    )
                )
            ).scalar()
            self.assertEqual(tier, "human_accept")
            # Removing the passkey later must not void what it already signed.
            await s.execute(
                delete(WebAuthn).where(WebAuthn.user_id == user.fs_webauthn_user_handle)
            )
            self.assertEqual(await verify_db(s), [])
