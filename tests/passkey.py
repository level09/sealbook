"""A software passkey for tests: registers a P-256 credential for a user and
answers assertion challenges the way a browser authenticator would."""

from __future__ import annotations

import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from quart_security.utils import naive_utcnow
from quart_security.webauthn import bytes_to_base64url
from stk.user.models import User, WebAuthn

from sealbook.ledger.models import Principal


class SoftPasskey:
    def __init__(self, rp_id: str = "localhost", origin: str = "http://localhost"):
        self.rp_id, self.origin = rp_id, origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.count = 0

    def cose_public_key(self) -> bytes:
        n = self.key.public_key().public_numbers()
        x, y = n.x.to_bytes(32, "big"), n.y.to_bytes(32, "big")
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: x, -3: y})

    def register(self, user) -> WebAuthn:
        """A WebAuthn row for `user`; add it to the session yourself. Sets the
        user's handle if it has none."""
        if not user.fs_webauthn_user_handle:
            user.fs_webauthn_user_handle = os.urandom(16).hex()
        return WebAuthn(
            credential_id=self.credential_id,
            public_key=self.cose_public_key(),
            sign_count=0,
            lastuse_datetime=naive_utcnow(),
            name="test passkey",
            usage="secondary",
            backup_state=False,
            device_type="single_device",
            user_id=user.fs_webauthn_user_handle,
        )

    def assert_(self, digest: str) -> dict:
        """The credential JSON a browser posts after navigator.credentials.get()
        with the hash as challenge."""
        self.count += 1
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([0x05])  # user present + user verified
            + struct.pack(">I", self.count)
        )
        client_data = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": bytes_to_base64url(bytes.fromhex(digest)),
                "origin": self.origin,
                "crossOrigin": False,
            }
        ).encode()
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        raw = bytes_to_base64url(self.credential_id)
        return {
            "id": raw,
            "rawId": raw,
            "type": "public-key",
            "response": {
                "authenticatorData": bytes_to_base64url(auth_data),
                "clientDataJSON": bytes_to_base64url(client_data),
                "signature": bytes_to_base64url(signature),
            },
            "clientExtensionResults": {},
        }

    def sig(self, digest: str) -> dict:
        """The stored `sig` that ledger.attest.verify returns for this assertion."""
        r = SoftPasskey.assert_(self, digest)
        return {
            "kind": "webauthn",
            "challenge": digest,
            "credential_id": r["rawId"],
            "authenticator_data": r["response"]["authenticatorData"],
            "client_data_json": r["response"]["clientDataJSON"],
            "signature": r["response"]["signature"],
            "public_key": bytes_to_base64url(self.cose_public_key()),
            "rp_id": self.rp_id,
            "origin": self.origin,
        }


async def attester(session, name: str = "analyst") -> tuple[Principal, SoftPasskey]:
    """A human principal backed by a user with a registered passkey, so its
    statements pass signature verification; sign with `passkey.sig`."""
    passkey = SoftPasskey()
    user = User(email=f"{os.urandom(6).hex()}@example.com", password="x", active=True)
    session.add(user)
    await session.flush()
    session.add(passkey.register(user))
    principal = Principal(kind="human", name=name, user_id=user.id)
    session.add(principal)
    await session.flush()
    return principal, passkey
