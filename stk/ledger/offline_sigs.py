"""Offline statement signature checks: ed25519 run keys and webauthn passkeys."""

import base64
import hashlib
import json
import re
from io import BytesIO

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _b64(value):
    _require(isinstance(value, str), "expected base64url string")
    data = base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )
    _require(
        base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii") == value,
        "expected unpadded base64url",
    )
    return data


def fingerprint(public_key) -> str:
    """sha256 of the DER SubjectPublicKeyInfo: what --trusted-keys lists."""
    return hashlib.sha256(
        public_key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).hexdigest()


# One passkey gesture signs at most this many rows. Accepting a finding writes one
# incident, one place, then one involved and one supported_by per kept member, and
# the review: review.findings caps kept members to stay under it.
GESTURE_MAX = 512
GESTURE_DOMAIN = b"sealbook-gesture-v1\n"
_HASH = re.compile(r"[0-9a-f]{64}")


def gesture_digest(hashes: list[str]) -> str:
    """The challenge one passkey assertion signs for several statements: the sha256
    of a versioned domain prefix and their ordered row hashes as compact JSON. The
    prefix keeps it apart from a single row's hash."""
    return hashlib.sha256(
        GESTURE_DOMAIN + json.dumps(list(hashes), separators=(",", ":")).encode("ascii")
    ).hexdigest()


def verify_statement_signature(
    digest, sig, pubkey=None, *, rp_id=None, origin=None, trusted=None
):
    """Verify a stored signature; pubkey is the matching run's hex key for ed25519.
    A webauthn sig carries its own key, origin and RP ID, so offline they prove
    nothing unless the caller pins them: rp_id, origin and trusted (a set of key
    fingerprints) do that when given. Returns the webauthn key fingerprint."""
    _require(isinstance(sig, dict), "signature must be an object")
    try:
        if sig.get("kind") == "ed25519":
            _require(isinstance(pubkey, str), "signature needs an exported run key")
            ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey)).verify(
                bytes.fromhex(sig["sig"]), digest.encode("ascii")
            )
            return None
        _require(sig.get("kind") == "webauthn", "unsupported signature kind")
        if "batch" in sig:
            # One gesture signed many rows: the row must be in the signed list.
            batch = sig["batch"]
            _require(
                isinstance(batch, list)
                and 0 < len(batch) <= GESTURE_MAX
                and all(isinstance(h, str) and _HASH.fullmatch(h) for h in batch)
                and len(set(batch)) == len(batch),
                "invalid gesture batch",
            )
            _require(digest in batch, "statement is not in the signed gesture")
            digest = gesture_digest(batch)
        _require(sig["challenge"] == digest, "WebAuthn challenge mismatch")
        _require(bool(_b64(sig["credential_id"])), "empty credential id")
        client_bytes = _b64(sig["client_data_json"])
        client = json.loads(client_bytes)
        _require(isinstance(client, dict), "clientDataJSON must be an object")
        _require(client.get("type") == "webauthn.get", "wrong clientDataJSON type")
        _require(
            _b64(client["challenge"]) == bytes.fromhex(digest),
            "WebAuthn client challenge mismatch",
        )
        _require(
            isinstance(sig["origin"], str) and client.get("origin") == sig["origin"],
            "WebAuthn origin mismatch",
        )
        _require(client.get("crossOrigin") in (None, False), "WebAuthn cross-origin")
        _require(origin is None or sig["origin"] == origin, "untrusted WebAuthn origin")
        _require(isinstance(sig["rp_id"], str), "invalid rp id")
        _require(rp_id is None or sig["rp_id"] == rp_id, "untrusted WebAuthn rp id")
        auth = _b64(sig["authenticator_data"])
        _require(len(auth) >= 37, "truncated authenticatorData")
        _require(
            auth[:32] == hashlib.sha256(sig["rp_id"].encode("utf-8")).digest(),
            "WebAuthn rp id mismatch",
        )
        _require(auth[32] & 1, "WebAuthn user present flag missing")
        _require(auth[32] & 4, "WebAuthn user verified flag missing")
        public_key = _b64(sig["public_key"])
        stream = BytesIO(public_key)
        cose = cbor2.load(stream)
        _require(isinstance(cose, dict) and not stream.read(), "invalid COSE key")
        data = auth + hashlib.sha256(client_bytes).digest()
        signature = _b64(sig["signature"])
        kind = (cose.get(1), cose.get(3), cose.get(-1))
        if kind == (2, -7, 1):
            x, y = cose[-2], cose[-3]
            _require(
                isinstance(x, bytes)
                and isinstance(y, bytes)
                and len(x) == len(y) == 32,
                "invalid P-256 coordinates",
            )
            key = ec.EllipticCurvePublicNumbers(
                int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
            ).public_key()
            key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
        else:
            _require(kind == (1, -8, 6), "unsupported COSE algorithm or curve")
            key = ed25519.Ed25519PublicKey.from_public_bytes(cose[-2])
            key.verify(signature, data)
        fp = fingerprint(key)
        _require(trusted is None or fp in trusted, f"untrusted key {fp}")
        return fp
    except InvalidSignature as exc:
        raise ValueError("statement signature does not verify") from exc
    except cbor2.CBORDecodeError as exc:
        raise ValueError("invalid COSE key") from exc
