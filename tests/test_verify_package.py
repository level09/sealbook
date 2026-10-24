import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import cbor2
from asn1crypto import tsp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from pyasn1.codec.der.encoder import encode
from pyasn1.type import tag, univ, useful

from stk.ledger import chain, merkle
from stk.ledger.contracts import CHAINED_FIELDS
from stk.ledger.offline_sigs import GESTURE_MAX, gesture_digest
from tests.passkey import SoftPasskey


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def key_fingerprint(key):
    return hashlib.sha256(
        key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).hexdigest()


def sequence(*items):
    result = univ.Sequence()
    for i, item in enumerate(items):
        result.setComponentByPosition(i, item)
    return result


def explicit(value):
    result = univ.Sequence().subtype(
        implicitTag=tag.Tag(tag.tagClassContext, tag.tagFormatConstructed, 0),
    )
    result.setComponentByPosition(0, value)
    return result


def tsa_response(root, *, tamper=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Test TSA")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.TIME_STAMPING]),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    sha256 = sequence(univ.ObjectIdentifier("2.16.840.1.101.3.4.2.1"), univ.Null(""))
    content_type = univ.ObjectIdentifier("1.2.840.113549.1.9.16.1.4")
    info = encode(
        sequence(
            univ.Integer(1),
            univ.ObjectIdentifier("1.2.3.4"),
            sequence(sha256, univ.OctetString(bytes.fromhex(root))),
            univ.Integer(1),
            useful.GeneralizedTime("20261021000000Z"),
        )
    )
    attrs = univ.SetOf(componentType=univ.Any())
    for oid, value in (
        ("1.2.840.113549.1.9.3", content_type),
        ("1.2.840.113549.1.9.4", univ.OctetString(hashlib.sha256(info).digest())),
    ):
        values = univ.SetOf(componentType=univ.Any())
        values.append(encode(value))
        attrs.append(encode(sequence(univ.ObjectIdentifier(oid), values)))
    signature = key.sign(encode(attrs), padding.PKCS1v15(), hashes.SHA256())
    if tamper:
        signature = bytes([signature[0] ^ 1]) + signature[1:]
    signer = sequence(
        univ.Integer(1),
        sequence(
            univ.Any(cert.issuer.public_bytes()), univ.Integer(cert.serial_number)
        ),
        sha256,
        attrs.subtype(
            implicitTag=tag.Tag(tag.tagClassContext, tag.tagFormatConstructed, 0),
            cloneValueFlag=True,
        ),
        sequence(univ.ObjectIdentifier("1.2.840.113549.1.1.1"), univ.Null("")),
        univ.OctetString(signature),
    )
    certificates = univ.SetOf(componentType=univ.Any()).subtype(
        implicitTag=tag.Tag(tag.tagClassContext, tag.tagFormatConstructed, 0)
    )
    certificates.append(cert.public_bytes(serialization.Encoding.DER))
    digests, signers = (
        univ.SetOf(componentType=univ.Any()),
        univ.SetOf(componentType=univ.Any()),
    )
    digests.append(encode(sha256))
    signers.append(encode(signer))
    signed_data = sequence(
        univ.Integer(3),
        digests,
        sequence(content_type, explicit(univ.OctetString(info))),
        certificates,
        signers,
    )
    token = sequence(
        univ.ObjectIdentifier("1.2.840.113549.1.7.2"), explicit(signed_data)
    )
    return encode(sequence(sequence(univ.Integer(0)), token))


def webauthn_sig(passkey, digest):
    """The sig object ledger.attest stores for an assertion over digest."""
    credential = passkey.assert_(digest)
    response = credential["response"]
    return {
        "kind": "webauthn",
        "challenge": digest,
        "credential_id": credential["rawId"],
        "authenticator_data": response["authenticatorData"],
        "client_data_json": response["clientDataJSON"],
        "signature": response["signature"],
        "public_key": b64(passkey.cose_public_key()),
        "rp_id": passkey.rp_id,
        "origin": passkey.origin,
    }


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        (self.directory / "blobs").mkdir()
        (self.directory / "anchors").mkdir()
        data = b"source bytes"
        (self.directory / "blobs/source").write_bytes(data)
        self.value = {"text": "محمد", "lang": "ar"}
        self.salt = b"a" * 32
        rows = {}
        for kind, fields in CHAINED_FIELDS.items():
            rows[kind] = dict.fromkeys(fields)
            rows[kind]["id"] = uuid.uuid4()
        rows["statement"].update(
            subject="person:example",
            prop="name",
            author_id=uuid.uuid4(),
            commitment=chain.commitment(self.salt, self.value),
            evidence=("printed_text",),
            valid_from=date(2026, 1, 1),
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        rows["acquisition"].update(
            blob_sha256=hashlib.sha256(data).hexdigest(),
            at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.run_key = ed25519.Ed25519PrivateKey.generate()
        rows["run"]["pubkey"] = self.run_key.public_key().public_bytes_raw()
        rows["statement"]["run_id"] = rows["run"]["id"]
        self.python_rows = rows
        entries, prev = [], chain.GENESIS
        for seq, (kind, row) in enumerate(rows.items(), 1):
            row["hash"] = chain.row_hash(kind, row)
            h = chain.link(prev, seq, kind, row["hash"])
            entries.append(
                asdict(chain.Entry(seq, kind, str(row["id"]), row["hash"], prev, h))
            )
            prev = h
        hashes = [entry["hash"] for entry in entries]
        root = merkle.root(hashes)
        self.manifest = json.loads(
            chain.canon(
                {
                    "version": 1,
                    "statements": [rows["statement"]],
                    "acquisitions": [rows["acquisition"]],
                    "runs": [rows["run"]],
                    "chain": entries,
                    "batches": [
                        {"first_seq": 1, "last_seq": 3, "size": 3, "root": root}
                    ],
                    "proofs": [
                        {
                            "seq": e["seq"],
                            "index": i,
                            "batch_root": root,
                            "path": merkle.inclusion_proof(hashes, i),
                        }
                        for i, e in enumerate(entries)
                    ],
                    "anchors": [{"root": root, "kind": "ots", "file": "receipt"}],
                    "blobs": [
                        {
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "md5": hashlib.md5(data).hexdigest(),
                            "file": "source",
                        }
                    ],
                    "values": [
                        {
                            "commitment": rows["statement"]["commitment"],
                            "salt": self.salt.hex(),
                            "value": self.value,
                        }
                    ],
                    "signatures": [],
                    "documents": [],
                }
            )
        )
        self.anchor("ots")

    def anchor(self, kind, root=None, **kwargs):
        anchor = self.manifest["anchors"][0]
        anchor["kind"] = kind
        root = root or anchor["root"]
        if kind == "partner":
            data = f"  {root}\n".encode()
        elif kind == "tsa":
            data = tsa_response(root, **kwargs)
        else:
            data = (
                b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
                b"\x01\x08"
                + bytes.fromhex(root)
                + b"\x00\x05\x88\x96\x0d\x73\xd7\x19\x01\x01\x01"
            )
        (self.directory / "anchors/receipt").write_bytes(data)

    def ed25519_signature(self, key=None):
        digest = self.manifest["statements"][0]["hash"]
        self.manifest["signatures"] = [
            {
                "hash": digest,
                "sig": {
                    "kind": "ed25519",
                    "sig": (key or self.run_key).sign(digest.encode("ascii")).hex(),
                },
            }
        ]
        return self.manifest["signatures"][0]["sig"]

    def passkey_signature(self, passkey=None):
        digest = self.manifest["statements"][0]["hash"]
        sig = webauthn_sig(passkey or SoftPasskey(), digest)
        self.manifest["signatures"] = [{"hash": digest, "sig": sig}]
        return sig

    def resign(self, passkey, sig, flags=None, **client):
        """Re-sign sig after changing its authenticator flags or clientDataJSON."""
        auth = bytearray(base64.urlsafe_b64decode(sig["authenticator_data"] + "=="))
        if flags is not None:
            auth[32] = flags
        data = json.loads(base64.urlsafe_b64decode(sig["client_data_json"] + "=="))
        data = json.dumps(data | client).encode()
        sig["authenticator_data"], sig["client_data_json"] = b64(auth), b64(data)
        sig["signature"] = b64(
            passkey.key.sign(
                bytes(auth) + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256())
            )
        )

    def attest(self, *passkeys):
        """Write attestations.json over the manifest as run_verifier writes it."""
        digest = hashlib.sha256(chain.canon(self.manifest)).hexdigest()
        rows = []
        for i, passkey in enumerate(passkeys):
            sig = webauthn_sig(passkey, digest)
            rows.append(
                {"name": f"r{i}", "credential_id": sig["credential_id"], "sig": sig}
            )
        (self.directory / "attestations.json").write_text(json.dumps(rows))
        return rows

    def assert_valid(self, *args, attest=True):
        result = self.run_verifier(*args, attest=attest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def run_verifier(self, *args, attest=True):
        if attest:
            self.attest(SoftPasskey(), SoftPasskey())
        (self.directory / "manifest.json").write_bytes(chain.canon(self.manifest))
        return subprocess.run(
            [sys.executable, "-m", "stk.ledger.verify_package", str(self.directory)]
            + list(args),
            capture_output=True,
            text=True,
        )

    def assert_problem(self, text, *args, attest=True):
        result = self.run_verifier(*args, attest=attest)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(text, result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_documents_are_covered(self):
        (self.directory / "brief.json").write_text("{}")
        self.assert_problem("brief.json: file not covered")
        self.manifest["documents"] = [
            {"file": "brief.json", "sha256": hashlib.sha256(b"{}").hexdigest()}
        ]
        self.assert_valid()
        (self.directory / "brief.json").write_text('{"edited": 1}')
        self.assert_problem("sha256 mismatch")

    def test_attestations_need_two_keys_over_the_manifest(self):
        one, two = SoftPasskey(), SoftPasskey()
        self.attest(one, two)
        result = self.assert_valid(attest=False)
        for passkey in (one, two):
            fp = key_fingerprint(passkey.key.public_key())
            self.assertIn(fp, result.stdout)
        self.assertIn("self-asserted", result.stdout)
        self.attest(one, one)
        self.assert_problem("fewer than two distinct", attest=False)
        rows = self.attest(one, two)
        rows[1]["sig"] = webauthn_sig(two, "aa" * 32)
        (self.directory / "attestations.json").write_text(json.dumps(rows))
        self.assert_problem("challenge", attest=False)

    def test_missing_attestations(self):
        self.attest(SoftPasskey(), SoftPasskey())
        self.assert_valid(attest=False)
        (self.directory / "attestations.json").unlink()
        self.assert_problem("attestations", attest=False)

    def test_duplicate_assertion_with_different_key_encodings(self):
        passkey = SoftPasskey()
        cose = cbor2.loads(passkey.cose_public_key())
        for encoded in (
            cbor2.dumps(dict(reversed(list(cose.items())))),
            cbor2.dumps(cose | {2: b"another key id"}),
        ):
            with self.subTest(encoded=encoded):
                rows = self.attest(passkey)
                duplicate = dict(rows[0], sig=dict(rows[0]["sig"]))
                duplicate["sig"]["public_key"] = b64(encoded)
                rows.append(duplicate)
                (self.directory / "attestations.json").write_text(json.dumps(rows))
                self.assert_problem("fewer than two distinct", attest=False)

    def test_pinned_trust(self):
        one, two = SoftPasskey(), SoftPasskey()
        rows = self.attest(one, two)
        for row, passkey in zip(rows, (one, two), strict=True):
            cose = cbor2.loads(passkey.cose_public_key())
            row["sig"]["public_key"] = b64(
                cbor2.dumps(dict(reversed(list(cose.items()))) | {2: b"key id"})
            )
        (self.directory / "attestations.json").write_text(json.dumps(rows))
        keys = Path(self.temp.name + ".keys")
        self.addCleanup(keys.unlink, missing_ok=True)
        fps = [key_fingerprint(p.key.public_key()) for p in (one, two)]
        keys.write_text(f"# reviewers\n{fps[0]}\n")
        pins = ["--rp-id", "localhost", "--origin", "http://localhost"]
        self.assert_problem(
            "untrusted key", *pins, "--trusted-keys", str(keys), attest=False
        )
        keys.write_text("\n".join(fps))
        result = self.assert_valid(*pins, "--trusted-keys", str(keys), attest=False)
        self.assertNotIn("self-asserted", result.stdout)
        self.assert_problem("untrusted WebAuthn rp id", "--rp-id", "other.example")
        self.assert_problem(
            "untrusted WebAuthn origin", "--origin", "https://x.example"
        )

    def test_webauthn_requires_user_verification(self):
        passkey = SoftPasskey()
        self.resign(passkey, self.passkey_signature(passkey), flags=0x01)
        self.assert_problem("user verified")

    def test_webauthn_refuses_cross_origin(self):
        passkey = SoftPasskey()
        self.resign(passkey, self.passkey_signature(passkey), crossOrigin=True)
        self.assert_problem("cross-origin")

    def test_valid_package_and_canonical_round_trip(self):
        for kind, collection in [
            ("statement", "statements"),
            ("acquisition", "acquisitions"),
            ("run", "runs"),
        ]:
            self.assertEqual(
                chain.row_hash(kind, self.python_rows[kind]),
                chain.row_hash(kind, self.manifest[collection][0]),
            )
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK", result.stdout)
        self.assertIn("ots", result.stdout)
        self.assertIn("Bitcoin attestations not verified", result.stdout)

    def test_selected_entries_and_redacted_values(self):
        self.manifest["chain"].pop(1)
        self.manifest["acquisitions"] = []
        self.manifest["proofs"].pop(1)
        self.manifest["values"] = []
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_other_anchor_kinds(self):
        for kind in ("tsa", "partner"):
            self.anchor(kind)
            result = self.assert_valid()
            self.assertIn(kind, result.stdout)

    def test_partner_root_mismatch(self):
        self.anchor("partner", "aa" * 32)
        self.assert_problem("partner root does not match")

    def test_tsa_imprint_mismatch(self):
        self.anchor("tsa", "aa" * 32)
        self.assert_problem("TSA imprint does not match root")

    def test_tsa_signature_mismatch(self):
        self.anchor("tsa", tamper=True)
        self.assert_problem("TSA signature")

    def test_tsa_signed_message_digest_mismatch(self):
        self.anchor("tsa")
        path = self.directory / "anchors/receipt"
        data = path.read_bytes()
        self.assertIn(b"20261021000000Z", data)
        path.write_bytes(data.replace(b"20261021000000Z", b"20261022000000Z", 1))
        self.assert_problem("TSA token content digest mismatch")

    def test_tsa_signer_pins(self):
        self.anchor("tsa")
        path = self.directory / "anchors/receipt"
        receipt = tsp.TimeStampResp.load(path.read_bytes())
        signed = receipt["time_stamp_token"]["content"]
        cert = signed["certificates"][0].chosen
        fp = (
            x509.load_der_x509_certificate(cert.dump())
            .fingerprint(hashes.SHA256())
            .hex()
        )
        extra = tsp.TimeStampResp.load(
            tsa_response(self.manifest["anchors"][0]["root"])
        )
        extra_cert = extra["time_stamp_token"]["content"]["certificates"][0]
        extra_cert.chosen["tbs_certificate"]["serial_number"] = 2
        extra_fp = hashlib.sha256(extra_cert.chosen.dump()).hexdigest()
        signed["certificates"] = [extra_cert, signed["certificates"][0]]
        path.write_bytes(receipt.dump())
        result = self.assert_valid()
        self.assertIn("TSA signers are unpinned", result.stdout)
        self.assertIn(fp, result.stdout)
        self.assertNotIn(extra_fp, result.stdout)
        pins = Path(self.temp.name + ".tsa")
        self.addCleanup(pins.unlink, missing_ok=True)
        for content in ("# no signers\n", "aa" * 32, extra_fp):
            with self.subTest(content=content):
                pins.write_text(content)
                self.assert_problem("TSA signer not pinned", "--trusted-tsa", str(pins))
        pins.write_text(f"# TSA signers\n\n{fp} # approved\n")
        result = self.assert_valid("--trusted-tsa", str(pins))
        self.assertNotIn("TSA signers are unpinned", result.stdout)

    def test_malformed_tsa(self):
        self.manifest["anchors"][0]["kind"] = "tsa"
        path = self.directory / "anchors/receipt"
        for data in (b"", b"not DER", b"\x30\x00", b"\x30\x02\x30\x00"):
            with self.subTest(data=data):
                path.write_bytes(data)
                self.assert_problem("anchors[0]")

    def test_ots_digest_mismatch(self):
        self.anchor("ots", "aa" * 32)
        self.assert_problem("ots file digest does not match root")

    def test_ots_header_version_and_hash_op(self):
        path = self.directory / "anchors/receipt"
        data = path.read_bytes()
        for offset in (0, 31, 32):
            with self.subTest(offset=offset):
                changed = bytearray(data)
                changed[offset] ^= 1
                path.write_bytes(changed)
                self.assert_problem(".ots")

    def test_ed25519(self):
        self.ed25519_signature()
        self.assert_valid()

    def test_ed25519_wrong_run_key(self):
        self.ed25519_signature(ed25519.Ed25519PrivateKey.generate())
        self.assert_problem("signature")

    def test_ed25519_flipped_signature(self):
        sig = self.ed25519_signature()
        raw = bytes.fromhex(sig["sig"])
        sig["sig"] = (bytes([raw[0] ^ 1]) + raw[1:]).hex()
        self.assert_problem("signature")

    def test_signature_requires_exported_statement_and_run(self):
        self.ed25519_signature()
        self.manifest["signatures"][0]["hash"] = self.manifest["runs"][0]["hash"]
        self.assert_problem("statement")
        self.ed25519_signature()
        self.manifest["runs"] = []
        self.assert_problem("signatures[0]")

    def test_webauthn_es256(self):
        self.passkey_signature()
        self.assert_valid()

    def test_webauthn_eddsa(self):
        sig = self.passkey_signature()
        key = ed25519.Ed25519PrivateKey.generate()
        sig["public_key"] = b64(
            cbor2.dumps({1: 1, 3: -8, -1: 6, -2: key.public_key().public_bytes_raw()})
        )
        auth = base64.urlsafe_b64decode(sig["authenticator_data"] + "==")
        client = base64.urlsafe_b64decode(sig["client_data_json"] + "==")
        sig["signature"] = b64(key.sign(auth + hashlib.sha256(client).digest()))
        result = self.assert_valid()
        self.assertIn(key_fingerprint(key.public_key()), result.stdout)

    def gesture_signature(self, batch):
        """A passkey sig over a gesture digest, attached to the exported row."""
        digest = self.manifest["statements"][0]["hash"]
        sig = webauthn_sig(SoftPasskey(), gesture_digest(batch)) | {"batch": batch}
        self.manifest["signatures"] = [{"hash": digest, "sig": sig}]
        return sig

    def test_gesture_batch_signature(self):
        row = self.manifest["statements"][0]["hash"]
        self.gesture_signature([row, "bb" * 32])
        self.assert_valid()

    def test_gesture_batch_stripped(self):
        row = self.manifest["statements"][0]["hash"]
        del self.gesture_signature([row, "bb" * 32])["batch"]
        self.assert_problem("challenge")

    def test_gesture_batch_swapped_onto_non_member_row(self):
        self.gesture_signature(["bb" * 32, "cc" * 32])
        self.assert_problem("not in the signed gesture")

    def test_gesture_batch_malformed(self):
        row = self.manifest["statements"][0]["hash"]
        oversized = [f"{i:064x}" for i in range(GESTURE_MAX)] + [row]
        for batch in ([], [row, row], oversized):
            with self.subTest(size=len(batch)):
                self.gesture_signature(batch)
                self.assert_problem("invalid gesture batch")

    def test_webauthn_wrong_challenge(self):
        sig = self.passkey_signature()
        sig["challenge"] = "aa" * 32
        self.assert_problem("challenge")

    def test_webauthn_wrong_client_challenge(self):
        sig = self.passkey_signature()
        other = SoftPasskey().assert_("aa" * 32)
        sig["client_data_json"] = other["response"]["clientDataJSON"]
        self.assert_problem("challenge")

    def test_webauthn_wrong_client_type(self):
        passkey = SoftPasskey()
        sig = self.passkey_signature(passkey)
        client = json.loads(base64.urlsafe_b64decode(sig["client_data_json"] + "=="))
        client["type"] = "webauthn.create"
        client = json.dumps(client).encode()
        auth = base64.urlsafe_b64decode(sig["authenticator_data"] + "==")
        sig["client_data_json"] = b64(client)
        sig["signature"] = b64(
            passkey.key.sign(
                auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256())
            )
        )
        self.assert_problem("clientDataJSON type")

    def test_webauthn_wrong_origin(self):
        sig = self.passkey_signature()
        sig["origin"] = "https://other.example"
        self.assert_problem("origin")

    def test_webauthn_wrong_rp_id(self):
        sig = self.passkey_signature()
        sig["rp_id"] = "other.example"
        self.assert_problem("rp")

    def test_webauthn_flipped_signature(self):
        sig = self.passkey_signature()
        raw = base64.urlsafe_b64decode(sig["signature"] + "==")
        sig["signature"] = b64(raw[:-1] + bytes([raw[-1] ^ 1]))
        self.assert_problem("signature")

    def test_webauthn_requires_user_presence(self):
        passkey = SoftPasskey()
        sig = self.passkey_signature(passkey)
        auth = bytearray(base64.urlsafe_b64decode(sig["authenticator_data"] + "=="))
        auth[32] = 0
        client = base64.urlsafe_b64decode(sig["client_data_json"] + "==")
        sig["authenticator_data"] = b64(auth)
        sig["signature"] = b64(
            passkey.key.sign(
                bytes(auth) + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256())
            )
        )
        self.assert_problem("user present")

    def test_webauthn_unsupported_algorithm(self):
        sig = self.passkey_signature()
        sig["public_key"] = b64(
            cbor2.dumps({1: 2, 3: -35, -1: 1, -2: b"x" * 32, -3: b"y" * 32})
        )
        self.assert_problem("unsupported")

    def test_malformed_signatures(self):
        for sig in (None, [], {"kind": "unknown"}, {"kind": "webauthn"}):
            with self.subTest(sig=sig):
                self.manifest["signatures"] = [
                    {"hash": self.manifest["statements"][0]["hash"], "sig": sig}
                ]
                self.assert_problem("signatures[0]")

    def test_malformed_webauthn_encodings(self):
        for field, value in (
            ("credential_id", ""),
            ("signature", "+/=="),
            ("client_data_json", b64(b"[]")),
            ("authenticator_data", b64(b"short")),
            ("public_key", b64(b"")),
            ("public_key", b64(cbor2.dumps([]))),
            ("public_key", b64(cbor2.dumps({}) + b"extra")),
        ):
            with self.subTest(field=field, value=value):
                sig = self.passkey_signature()
                sig[field] = value
                self.assert_problem("signatures[0]")

    def test_unknown_anchor_kind(self):
        self.manifest["anchors"][0]["kind"] = "unknown"
        self.assert_problem("anchor kind")

    def test_modified_entry_hash(self):
        self.manifest["chain"][0]["hash"] = "aa" * 32
        self.assert_problem("entry hash")

    def test_modified_batch_root(self):
        self.manifest["batches"][0]["root"] = "aa" * 32
        self.assert_problem("batch root")

    def test_missing_blob(self):
        self.manifest["blobs"][0]["file"] = "absent"
        self.assert_problem("file missing")

    def test_symlink_escape(self):
        (self.directory / "blobs/link").symlink_to(self.directory / "manifest.json")
        self.manifest["blobs"][0]["file"] = "link"
        self.assert_problem("file")

    def test_modified_row(self):
        self.manifest["statements"][0]["subject"] = "changed"
        self.assert_problem("hash")

    def test_modified_blob(self):
        (self.directory / "blobs/source").write_bytes(b"changed")
        self.assert_problem("sha256")
        self.assert_problem("md5")

    def test_modified_proof(self):
        self.manifest["proofs"][0]["path"][0] = "00" * 32
        self.assert_problem("proof")

    def test_missing_anchor(self):
        self.manifest["anchors"] = []
        self.assert_problem("anchor")

    def test_missing_anchor_file(self):
        self.manifest["anchors"][0]["file"] = "absent"
        self.assert_problem("anchor")

    def test_wrong_salt(self):
        self.manifest["values"][0]["salt"] = "bb" * 32
        self.assert_problem("commitment")

    def test_unmatched_value(self):
        self.manifest["values"][0]["commitment"] = "cc" * 32
        self.assert_problem("statement")

    def test_missing_entry(self):
        self.manifest["chain"].pop()
        self.assert_problem("entry")

    def test_missing_proof(self):
        self.manifest["proofs"].pop()
        self.assert_problem("proof")

    def test_wrong_batch_range(self):
        self.manifest["batches"][0]["first_seq"] = 2
        self.assert_problem("batch")

    def test_wrong_proof_index(self):
        self.manifest["proofs"][0]["index"] = 1
        self.assert_problem("index")

    def test_broken_chain_link(self):
        self.manifest["chain"][1]["prev_hash"] = chain.GENESIS
        self.assert_problem("hash")

    def test_row_without_entry(self):
        self.manifest["statements"][0]["id"] = str(uuid.uuid4())
        self.assert_problem("entry")

    def test_extra_row_field(self):
        self.manifest["runs"][0]["status"] = "done"
        self.assert_problem("fields")

    def test_duplicate_sequence(self):
        self.manifest["chain"].append(self.manifest["chain"][0])
        self.assert_problem("duplicate")

    def test_path_escape(self):
        self.manifest["blobs"][0]["file"] = "../manifest.json"
        self.assert_problem("file")

    def test_malformed_records_and_all_problems(self):
        self.manifest["proofs"][0]["seq"] = []
        self.manifest["blobs"][0]["sha256"] = "bad"
        self.manifest["values"][0]["salt"] = "not hex"
        result = self.run_verifier()
        self.assertEqual(result.returncode, 1)
        for text in ("proof", "blob", "value"):
            self.assertIn(text, result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_json(self):
        (self.directory / "manifest.json").write_text("{")
        result = subprocess.run(
            [sys.executable, "-m", "stk.ledger.verify_package", str(self.directory)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("manifest", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
