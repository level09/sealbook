"""Acceptance 5, 9 and 19: a cited brief exports as a package that verifies offline
(verify_package and c2patool, no database), carries a methodology report from its
runs, and leaves only after two different people attest its manifest hash over
anchored rows."""

import asyncio
import hashlib
import io
import re
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import orjson
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
from sealbook.app import app as sealbook_app
from sealbook.app import create_app
from sealbook.ledger import attest, merkle
from sealbook.ledger.contracts import Prop, Refused, StatementIn
from sealbook.ledger.models import Blob, Chain, Principal, Run, Statement
from sealbook.ledger.verify_package import verify_package
from sealbook.ledger.write import (
    append,
    commit,
    prepare,
    principal_for_user,
    record_acquisition,
    seal_run,
    verify_db,
)
from sealbook.packages import service
from sealbook.packages.models import Package, Sentence
from sealbook.settings import Config
from tests.passkey import SoftPasskey

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"


def make_signing_cert(directory: Path) -> tuple[str, str]:
    """A throwaway ES256 CA plus leaf, the shape c2patool accepts for signing."""
    from datetime import timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.now(UTC)

    def name(cn):
        return x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, cn),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Sealbook test"),
            ]
        )

    def builder(subject, issuer, key):
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
        )

    ca, leaf = (
        ec.generate_private_key(ec.SECP256R1()),
        ec.generate_private_key(ec.SECP256R1()),
    )
    ca_cert = (
        builder(name("ca"), name("ca"), ca)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            True,
        )
        .sign(ca, hashes.SHA256())
    )
    leaf_cert = (
        builder(name("signer"), ca_cert.subject, leaf)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False),
            True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()), False
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf.public_key()), False
        )
        .sign(ca, hashes.SHA256())
    )
    cert, key = directory / "cert.pem", directory / "key.pem"
    cert.write_bytes(
        leaf_cert.public_bytes(serialization.Encoding.PEM)
        + ca_cert.public_bytes(serialization.Encoding.PEM)
    )
    key.write_bytes(
        leaf.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert), str(key)


class FakeAnchor:
    """Stands in for sealbook.anchor.service: one real Merkle batch over the whole chain and a
    partner-held root file."""

    def __init__(self):
        self.ready = True

    async def anchored(self, session, seqs):
        return self.ready

    async def proofs(self, session, seqs):
        hashes = list(
            (await session.execute(select(Chain.hash).order_by(Chain.seq))).scalars()
        )
        root = merkle.root(hashes)
        return {
            "batches": [
                {
                    "first_seq": 1,
                    "last_seq": len(hashes),
                    "size": len(hashes),
                    "root": root,
                }
            ],
            "proofs": [
                {
                    "seq": seq,
                    "index": seq - 1,
                    "batch_root": root,
                    "path": merkle.inclusion_proof(hashes, seq - 1),
                }
                for seq in seqs
            ],
            "anchors": [
                {
                    "root": root,
                    "kind": "partner",
                    "file": f"{root}.partner",
                    "bytes": root.encode(),
                }
            ],
        }


@unittest.skipUnless(POSTGRES, "packages need Postgres (set SQLALCHEMY_DATABASE_URI)")
class PackagesTest(unittest.IsolatedAsyncioTestCase):
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
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        cert, key = make_signing_cert(tmp)

        class TestConfig(stk.TestingConfig, Config):
            SQLALCHEMY_DATABASE_URI = db_url
            TESTING = True
            SESSION_TYPE = None
            STK_ENV = "testing"
            STK_ENABLE_AGENT_LOGIN = True
            STK_AGENT_LOGIN_MAX_TTL_SECONDS = 60
            SECURITY_WAN_RP_ID = None
            SECURITY_WAN_EXPECTED_ORIGIN = None
            BLOB_DIR = str(tmp / "blobs")
            C2PA_SIGN_CERT = cert
            C2PA_SIGN_KEY = key

        # Blob readers outside a request (the gate's span check) use Config itself.
        self.enterContext(mock.patch.object(Config, "BLOB_DIR", TestConfig.BLOB_DIR))
        self.tmp = tmp
        self.app = create_app(TestConfig)
        self.anchor = FakeAnchor()
        for name in ("anchored", "proofs"):
            self.enterContext(
                mock.patch(
                    f"sealbook.anchor.service.{name}", getattr(self.anchor, name)
                )
            )

    async def asyncSetUp(self):
        self.keys = {}
        async with ext.async_session_factory() as s, s.begin():
            analyst = (
                await s.execute(select(Role).where(Role.name == "analyst"))
            ).scalar_one()
            users = {}
            for name in ("amal", "basel"):
                user = User(
                    email=f"{name}@example.com",
                    name=name,
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                user.roles.append(analyst)
                s.add(user)
                await s.flush()
                self.keys[name] = SoftPasskey()
                s.add(self.keys[name].register(user))
                users[name] = user
            amal = await principal_for_user(s, users["amal"].id, "amal")
            blob = await blobs.put(
                s,
                "سجل الفرع 999: يوسف الأحمد، أوقف 2013-05-02".encode(),
                "text/plain",
                self.app.config["BLOB_DIR"],
            )
            await record_acquisition(
                s,
                blob_sha256=blob.sha256,
                origin="url:https://example.org/register-page-1",
                collector_id=amal.id,
                at=datetime.now(UTC),
                context={},
                sensitivity="ordinary",
                doc_type="detention_register",
            )
            agent = Principal(kind="agent", name="extractor")
            s.add(agent)
            await s.flush()
            run_key = Ed25519PrivateKey.generate()
            run = Run(
                principal_id=agent.id,
                tool="extract",
                route="cloud",
                endpoint="https://openrouter.example/v1",
                model_requested="qwen3-vl-4b",
                model_served="qwen3-vl-4b-sealbook-ft",
                provider_served="example-provider",
                prompt_hash="ab" * 32,
                inputs={"blobs": [blob.sha256]},
                pubkey=run_key.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                ),
            )
            s.add(run)
            await s.flush()
            person = f"person:{uuid.uuid4()}"

            def sign(h):
                return {"kind": "ed25519", "sig": run_key.sign(h.encode()).hex()}

            def extracted(prop, value, span):
                return StatementIn(
                    subject=person,
                    prop=prop,
                    value=value,
                    author_id=agent.id,
                    run_id=run.id,
                    span_blob=blob.sha256,
                    span=span,
                )

            name = await append(
                s,
                extracted(
                    Prop.NAME,
                    {"text": "يوسف الأحمد", "lang": "ar"},
                    {"start": 16, "end": 27},
                ),
                sign,
            )
            self.draft = await append(
                s,
                extracted(
                    Prop.ARRESTED_AT, {"date": "2013-05-02"}, {"start": 34, "end": 44}
                ),
                sign,
            )
            await seal_run(s, run, "cd" * 32)
            review = await prepare(
                s,
                StatementIn(
                    subject=f"statement:{name.id}",
                    prop=Prop.REVIEW,
                    value={"verdict": "accept", "reason": "matches the register"},
                    author_id=amal.id,
                ),
            )
            async with self.app.test_request_context(
                "/", headers={"Host": "localhost"}
            ):
                sig = await attest.verify(
                    s,
                    users["amal"],
                    review.hash,
                    self.keys["amal"].assert_(review.hash),
                )
            await commit(s, review, sig)
            self.fact = name

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def login(self, client, name):
        async with self.app.app_context():
            token = create_agent_login_token(f"{name}@example.com", "/packages")
        self.assertEqual(
            (await client.get(f"/_test/login?token={token}")).status_code, 302
        )
        page = await (await client.get("/packages")).get_data(as_text=True)
        return {
            "X-CSRFToken": re.search(
                r'name="csrf-token" content="([^"]+)"', page
            ).group(1)
        }

    async def attest(self, client, headers, name, package_id, sha):
        opts = await client.post(
            f"/api/packages/{package_id}/attest/options", json={}, headers=headers
        )
        self.assertEqual(opts.status_code, 200, await opts.get_data(as_text=True))
        return await client.post(
            f"/api/packages/{package_id}/attest",
            json={"item": self.keys[name].assert_(sha)},
            headers=headers,
        )

    async def test_cited_brief_exports_after_two_attestations(self):
        async with self.app.test_client() as amal, self.app.test_client() as basel:
            ha = await self.login(amal, "amal")
            hb = await self.login(basel, "basel")
            r = await amal.post(
                "/api/packages/briefs",
                json={"item": {"title": "What we hold on Yusuf", "audience": "court"}},
                headers=ha,
            )
            bid = (await r.get_json())["id"]
            sentences = f"/api/packages/briefs/{bid}/sentences"

            # (19) uncited, and citing a draft, are both refused server side
            for ids in ([], [str(self.draft.id)]):
                r = await amal.post(
                    sentences,
                    json={"item": {"text": "He was arrested.", "statement_ids": ids}},
                    headers=ha,
                )
                self.assertEqual(r.status_code, 400)
            r = await amal.post(
                sentences,
                json={
                    "item": {
                        "text": "The register names Yusuf al-Ahmad.",
                        "statement_ids": [str(self.fact.id)],
                    }
                },
                headers=ha,
            )
            self.assertEqual(r.status_code, 200, await r.get_data(as_text=True))
            for case, listed in ((None, True), ("case-nowhere", False)):
                r = await amal.get(
                    "/api/packages/briefs",
                    query_string={"investigation": case} if case else None,
                )
                self.assertEqual(
                    bid in [b["id"] for b in (await r.get_json())["items"]], listed
                )
            async with ext.async_session_factory() as s:
                cites = (
                    (
                        await s.execute(
                            select(Statement).where(Statement.subject == f"brief:{bid}")
                        )
                    )
                    .scalars()
                    .all()
                )
                self.assertEqual([c.prop for c in cites], [Prop.ACCESS])

            r = await amal.post(
                f"/api/packages/briefs/{bid}/build", json={}, headers=ha
            )
            self.assertEqual(r.status_code, 200, await r.get_data(as_text=True))
            built = await r.get_json()
            pid, sha = built["id"], built["manifest_sha256"]

            # (19) no download before finalizing; one person twice is refused
            self.assertEqual(
                (await amal.get(f"/packages/{pid}/download")).status_code, 400
            )
            self.assertEqual(
                (await self.attest(amal, ha, "amal", pid, sha)).status_code, 200
            )
            r = await self.attest(amal, ha, "amal", pid, sha)
            self.assertEqual(r.status_code, 400)
            self.assertIn("second person", (await r.get_json())["message"])
            r = await amal.post(f"/api/packages/{pid}/finalize", json={}, headers=ha)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(
                (await self.attest(basel, hb, "basel", pid, sha)).status_code, 200
            )

            # (19) unanchored rows block finalizing
            self.anchor.ready = False
            r = await amal.post(f"/api/packages/{pid}/finalize", json={}, headers=ha)
            self.assertEqual(r.status_code, 400)
            self.assertIn("anchored", (await r.get_json())["message"])
            self.anchor.ready = True
            r = await basel.post(f"/api/packages/{pid}/finalize", json={}, headers=hb)
            self.assertEqual(r.status_code, 200)
            r = await basel.get(f"/packages/{pid}/download")
            self.assertEqual(r.status_code, 200)
            body = await r.get_data()

        async with ext.async_session_factory() as s:
            # Attestations sign the manifest hash; the ledger check must accept that.
            self.assertEqual(await verify_db(s), [])
            props = (
                (
                    await s.execute(
                        select(Statement.prop).where(Statement.subject == f"blob:{sha}")
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(sorted(props), ["access", "attest", "attest", "package"])

        # (5) offline: drop every database connection, then verify the unzipped package
        await ext.engine.dispose()
        out = self.tmp / "unzipped"
        zipfile.ZipFile(io.BytesIO(body)).extractall(out)
        manifest = (out / "manifest.json").read_bytes()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), sha)
        problems, _ = verify_package(out)
        self.assertEqual(problems, [])
        cli = subprocess.run(
            [sys.executable, "-m", "sealbook.ledger.verify_package", str(out)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(cli.returncode, 0, cli.stdout)
        report = orjson.loads(
            subprocess.run(
                ["c2patool", str(out / "package.svg")],
                capture_output=True,
                check=True,
            ).stdout
        )
        self.assertNotEqual(report["validation_state"], "Invalid")
        active = report["manifests"][report["active_manifest"]]
        claim = next(
            a for a in active["assertions"] if a["label"] == "org.sealbook.package"
        )
        self.assertEqual(
            claim["data"]["documents"]["brief.json"],
            hashlib.sha256((out / "brief.json").read_bytes()).hexdigest(),
        )
        # (P1) the export carries both approvals, verified offline over the manifest
        signed = orjson.loads((out / "attestations.json").read_bytes())
        self.assertEqual(sorted(a["name"] for a in signed), ["amal", "basel"])
        self.assertIn("attestation by 'basel'", cli.stdout)
        data = orjson.loads(manifest)
        self.assertTrue(data["blobs"])  # court sees source bytes
        self.assertEqual(len(data["signatures"]), 2)  # run sig and passkey review
        brief = orjson.loads((out / "brief.json").read_bytes())
        self.assertEqual(brief["sentences"][0]["statements"], [str(self.fact.id)])

        # (9) methodology from the runs: model requested vs served, reviewers
        method = (out / "methodology.md").read_text()
        for needle in (
            "Tool: extract",
            "Model requested: qwen3-vl-4b",
            "Model served: qwen3-vl-4b-sealbook-ft",
            "Provider served: example-provider",
            "Route: cloud",
            "Measured error rate: not measured",
            "amal: attested 1 statement",
        ):
            self.assertIn(needle, method)
        (out / "brief.json").write_text('{"sentences": []}')
        self.assertIn("sha256 mismatch", "\n".join(verify_package(out)[0]))

    async def test_family_profile_withholds_blobs_and_reviews(self):
        async with self.app.test_client() as amal:
            ha = await self.login(amal, "amal")
            r = await amal.post(
                "/api/packages/briefs",
                json={"item": {"title": "For the family", "audience": "family"}},
                headers=ha,
            )
            bid = (await r.get_json())["id"]
            await amal.post(
                f"/api/packages/briefs/{bid}/sentences",
                json={"item": {"text": "Named.", "statement_ids": [str(self.fact.id)]}},
                headers=ha,
            )
            r = await amal.post(
                f"/api/packages/briefs/{bid}/build", json={}, headers=ha
            )
            pid = (await r.get_json())["id"]
            self.assertEqual((await amal.get("/packages")).status_code, 200)
            self.assertEqual(
                (await amal.get(f"/packages/brief/{bid}")).status_code, 200
            )
        async with ext.async_session_factory() as s:
            package = await s.get(Package, uuid.UUID(pid))
            blob = await s.get(Blob, package.manifest_sha256)
            manifest = orjson.loads(blobs.read(blob, self.app.config["BLOB_DIR"]))
        self.assertEqual(manifest["blobs"], [])
        self.assertEqual(len(manifest["values"]), 1)  # the name, not the review

    async def built(self, client, headers):
        r = await client.post(
            "/api/packages/briefs",
            json={"item": {"title": "Race", "audience": "court"}},
            headers=headers,
        )
        bid = (await r.get_json())["id"]
        await client.post(
            f"/api/packages/briefs/{bid}/sentences",
            json={"item": {"text": "Named.", "statement_ids": [str(self.fact.id)]}},
            headers=headers,
        )
        r = await client.post(
            f"/api/packages/briefs/{bid}/build", json={}, headers=headers
        )
        built = await r.get_json()
        return bid, built["id"], built["manifest_sha256"]

    async def test_one_person_racing_twice_attests_once(self):
        async with self.app.test_client() as amal:
            ha = await self.login(amal, "amal")
            _, pid, sha = await self.built(amal, ha)
            first, second = await asyncio.gather(
                self.attest(amal, ha, "amal", pid, sha),
                self.attest(amal, ha, "amal", pid, sha),
            )
            self.assertEqual(
                sorted([first.status_code, second.status_code]), [200, 400]
            )

    async def test_edited_brief_builds_a_new_package_and_revoked_refuses(self):
        async with self.app.test_client() as amal, self.app.test_client() as basel:
            ha = await self.login(amal, "amal")
            hb = await self.login(basel, "basel")
            bid, pid, sha = await self.built(amal, ha)
            async with ext.async_session_factory() as s:
                sentence = (
                    await s.execute(select(Sentence.id).where(Sentence.brief_id == bid))
                ).scalar_one()
            await amal.post(
                f"/api/packages/briefs/{bid}/sentences",
                json={
                    "item": {
                        "id": str(sentence),
                        "text": "Named, differently.",
                        "statement_ids": [str(self.fact.id)],
                    }
                },
                headers=ha,
            )
            r = await amal.post(
                f"/api/packages/briefs/{bid}/build", json={}, headers=ha
            )
            self.assertNotEqual((await r.get_json())["id"], pid)

            await self.attest(amal, ha, "amal", pid, sha)
            await self.attest(basel, hb, "basel", pid, sha)
            r = await amal.post(f"/api/packages/{pid}/finalize", json={}, headers=ha)
            self.assertEqual(r.status_code, 200)
            async with ext.async_session_factory() as s, s.begin():
                package = await s.get(Package, uuid.UUID(pid))
                package.revoked_at = datetime.now(UTC)
            r = await amal.get(f"/packages/{pid}/download")
            self.assertEqual(r.status_code, 400)
            async with ext.async_session_factory() as s:
                package = await s.get(Package, uuid.UUID(pid))
                with self.assertRaisesRegex(Refused, "revoked"):
                    await service.export(s, None, package, "unused")
