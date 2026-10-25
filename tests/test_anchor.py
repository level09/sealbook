"""Acceptance test 7: anchored history survives an insider rewrite. Throwaway
Postgres per test; OTS calendar and TSA are local fakes."""

import hashlib
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import httpx
from alembic import command
from asn1crypto import cms, tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)

from sealbook.anchor.models import Anchor, Batch
from sealbook.anchor.service import (
    anchored,
    proofs,
    provisional,
    run_once,
    verify_anchors,
)
from sealbook.app import app as sealbook_app
from sealbook.ledger import chain, merkle
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Chain, Statement
from sealbook.ledger.write import append, row_dict, verify_db
from sealbook.settings import Config
from tests.passkey import attester

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
CALENDAR = "https://calendar.test"
TSA = "https://tsa.test/tsr"


class FakeTsa:
    """Signs RFC 3161 tokens with a throwaway self-signed EC key."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake tsa")])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=1))
            .sign(self.key, hashes.SHA256())
        )
        self.cert = asn1_x509.Certificate.load(cert.public_bytes(Encoding.DER))

    def respond(self, body: bytes) -> bytes:
        req = tsp.TimeStampReq.load(body)
        tst = tsp.TSTInfo(
            {
                "version": 1,
                "policy": "1.2.3.4",
                "message_imprint": req["message_imprint"],
                "serial_number": 1,
                "gen_time": datetime.now(UTC),
                "nonce": req["nonce"],
            }
        )
        attrs = cms.CMSAttributes(
            [
                {"type": "content_type", "values": ["tst_info"]},
                {
                    "type": "message_digest",
                    "values": [hashlib.sha256(tst.dump()).digest()],
                },
            ]
        )
        signer = {
            "version": "v1",
            "sid": cms.SignerIdentifier(
                {
                    "issuer_and_serial_number": {
                        "issuer": self.cert.issuer,
                        "serial_number": self.cert.serial_number,
                    }
                }
            ),
            "digest_algorithm": {"algorithm": "sha256"},
            "signed_attrs": attrs,
            "signature_algorithm": {"algorithm": "sha256_ecdsa"},
            "signature": self.key.sign(attrs.dump(), ec.ECDSA(hashes.SHA256())),
        }
        signed = cms.SignedData(
            {
                "version": "v3",
                "digest_algorithms": [{"algorithm": "sha256"}],
                "encap_content_info": {"content_type": "tst_info", "content": tst},
                "certificates": [self.cert],
                "signer_infos": [signer],
            }
        )
        return tsp.TimeStampResp(
            {
                "status": {"status": "granted"},
                "time_stamp_token": {"content_type": "signed_data", "content": signed},
            }
        ).dump()


@unittest.skipUnless(POSTGRES, "anchoring needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class AnchorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config(sealbook_app, url, quiet=True)
        command.upgrade(config, "heads")
        self.url = make_url(url).set(drivername="postgresql+asyncpg")
        self.partner = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in {
            "OTS_CALENDARS": [CALENDAR],
            "TSA_URL": TSA,
            "PARTNER_ROOT_DIRS": [str(self.partner)],
        }.items():
            self.enterContext(mock.patch.object(Config, name, value))
        self.tsa = FakeTsa()
        self.tsa_down = False
        pin = hashlib.sha256(self.tsa.cert.dump()).hexdigest()
        self.enterContext(mock.patch.object(Config, "TSA_CERT_SHA256", [pin]))

    def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == CALENDAR + "/digest":
            # append(nonce), sha256, then a pending attestation stub
            return httpx.Response(200, content=b"\xf0\x10" + bytes(16) + b"\x08\x00")
        if str(request.url) == TSA:
            if self.tsa_down:
                return httpx.Response(503)
            return httpx.Response(200, content=self.tsa.respond(request.content))
        raise AssertionError(f"unexpected request {request.url}")

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.url)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        async with self.Session() as s, s.begin():
            self.human, self.passkey = await attester(s)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def statements(self, n):
        async with self.Session() as s, s.begin():
            for i in range(n):
                await append(
                    s,
                    StatementIn(
                        subject=f"person:{uuid.uuid4()}",
                        prop=Prop.NAME,
                        value={"text": f"name {i}", "lang": "en"},
                        author_id=self.human.id,
                    ),
                    sign=self.passkey.sig,
                )

    async def seqs(self, s):
        return list((await s.execute(select(Chain.seq).order_by(Chain.seq))).scalars())

    async def test_anchor_then_provisional(self):
        await self.statements(3)
        batch, errors = await run_once(self.Session, self.client)
        self.assertEqual(errors, [])
        self.assertEqual((batch.first_seq, batch.last_seq), (1, 3))
        self.assertEqual(await run_once(self.Session, self.client), (None, []))

        await self.statements(2)
        async with self.Session() as s:
            self.assertEqual(await provisional(s), 4)
            self.assertTrue(await anchored(s, [1, 3]))
            self.assertFalse(await anchored(s, [3, 4]))
            package = await proofs(s, [2])
            self.assertEqual(
                await verify_anchors(s),
                ([], ["1 OTS receipts pending Bitcoin attestation"]),
            )
        b = package["batches"][0]
        p = package["proofs"][0]
        async with self.Session() as s:
            leaf = (await s.get(Chain, 2)).hash
        self.assertTrue(
            merkle.verify_inclusion(leaf, p["index"], b["size"], p["path"], b["root"])
        )
        self.assertEqual(
            {(a["kind"], a["file"]) for a in package["anchors"]},
            {
                ("ots", f"{b['root']}.ots"),
                ("tsa", f"{b['root']}.tsr"),
                ("partner", "1-3.root"),
            },
        )
        self.assertEqual((self.partner / "1-3.root").read_text(), b["root"])

        await run_once(self.Session, self.client)
        async with self.Session() as s:
            self.assertIsNone(await provisional(s))
            self.assertTrue(await anchored(s, [4, 5]))

    async def test_failed_kind_recorded_and_retried(self):
        await self.statements(1)
        self.tsa_down = True
        batch, errors = await run_once(self.Session, self.client)
        self.assertIsNotNone(batch)
        self.assertEqual(len(errors), 1)
        self.assertIn("tsa", errors[0])
        async with self.Session() as s:
            self.assertFalse(await anchored(s, [1]))
        self.tsa_down = False
        self.assertEqual(await run_once(self.Session, self.client), (None, []))
        async with self.Session() as s:
            rows = (await s.execute(select(Anchor.kind, Anchor.error))).all()
        tsa = [error for kind, error in rows if kind == "tsa"]
        self.assertEqual(len(tsa), 2)
        self.assertEqual(sum(e is None for e in tsa), 1)
        async with self.Session() as s:
            self.assertTrue(await anchored(s, [1]))
            # A partner added later must hold a copy before the batch counts again.
            later = self.enterContext(tempfile.TemporaryDirectory())
            with mock.patch.object(
                Config, "PARTNER_ROOT_DIRS", [str(self.partner), later]
            ):
                self.assertFalse(await anchored(s, [1]))

    async def test_tsa_signer_must_be_pinned(self):
        await self.statements(1)
        await run_once(self.Session, self.client)
        async with self.Session() as s:
            with mock.patch.object(Config, "TSA_CERT_SHA256", []):
                problems, _ = await verify_anchors(s)
        self.assertEqual(len(problems), 1)
        self.assertIn("TSA signer not pinned", problems[0])

    async def test_insider_rewrite_caught(self):
        await self.statements(3)
        await run_once(self.Session, self.client)
        async with self.Session() as s, s.begin():
            # Insider with superuser rights: triggers off, rewrite a statement,
            # recompute its hash, the chain from there on, and the batch root.
            await s.execute(text("set local session_replication_role = replica"))
            first = (await s.get(Chain, 2)).ref
            st = await s.get(Statement, uuid.UUID(first))
            st.subject = "person:forged"
            st.hash = chain.row_hash("statement", row_dict(st, "statement"))
            # The passkey signed the old hash; an unsigned human row still verifies.
            st.sig = None
            await s.flush()
            prev = (await s.get(Chain, 1)).hash
            for seq in await self.seqs(s):
                if seq < 2:
                    continue
                e = await s.get(Chain, seq)
                if e.ref == first:
                    e.row_hash = st.hash
                e.prev_hash = prev
                e.hash = prev = chain.link(prev, seq, e.kind, e.row_hash)
                await s.flush()
            b = (await s.execute(select(Batch))).scalar_one()
            hashes = [(await s.get(Chain, q)).hash for q in await self.seqs(s)]
            b.root = merkle.root(hashes)
        async with self.Session() as s:
            self.assertEqual(await verify_db(s), [])
            problems, _ = await verify_anchors(s)
        for kind in ("ots", "tsa", "partner"):
            self.assertTrue(any(kind in p for p in problems), (kind, problems))
