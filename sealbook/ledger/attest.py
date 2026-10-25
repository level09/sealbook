"""Human attestation: a passkey assertion whose challenge is a ledger hash (a
statement hash or a package manifest hash), so the signature covers exactly that
row. This is attribution, not an eIDAS advanced or qualified e-signature."""

from __future__ import annotations

from quart import current_app, request
from quart_security import webauthn as wan
from quart_security.utils import naive_utcnow
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stk.user.models import WebAuthn

from sealbook.ledger.contracts import Refused
from sealbook.ledger.models import AttestKey


def _rp_id() -> str:
    return current_app.config.get("SECURITY_WAN_RP_ID") or request.host.split(":")[0]


def _origin() -> str:
    return (
        current_app.config.get("SECURITY_WAN_EXPECTED_ORIGIN")
        or f"{request.scheme}://{request.host}"
    )


async def _credentials(db: AsyncSession, user) -> list[WebAuthn]:
    q = select(WebAuthn).where(WebAuthn.user_id == user.fs_webauthn_user_handle)
    return list((await db.execute(q)).scalars())


async def registered(db: AsyncSession, user) -> bool:
    """Whether `user` has a passkey to attest with."""
    return bool(user.fs_webauthn_user_handle and await _credentials(db, user))


async def options(db: AsyncSession, user, digest: str) -> dict:
    """navigator.credentials.get() options whose challenge is the hash itself."""
    creds = await _credentials(db, user)
    if not user.fs_webauthn_user_handle or not creds:
        raise Refused("register a passkey before attesting")
    return wan.options_to_json_dict(
        await wan.begin_authentication(creds, _rp_id(), bytes.fromhex(digest))
    )


async def verify(db: AsyncSession, user, digest: str, credential: dict) -> dict:
    """Check the assertion against the user's own passkey and return the `sig` to
    store: everything needed to re-verify it later without this database."""
    raw_id = wan.base64url_to_bytes(credential["rawId"])
    stored = next(
        (c for c in await _credentials(db, user) if c.credential_id == raw_id), None
    )
    if stored is None:
        raise Refused("passkey is not registered to this user")
    try:
        count = await wan.complete_authentication(
            credential,
            challenge=bytes.fromhex(digest),
            rp_id=_rp_id(),
            expected_origin=_origin(),
            stored_credential=stored,
            require_user_verification=current_app.config.get(
                "SECURITY_WAN_REQUIRE_USER_VERIFICATION", True
            ),
        )
    except Exception as exc:
        raise Refused("passkey assertion does not verify") from exc
    stored.sign_count = int(count)
    stored.lastuse_datetime = naive_utcnow()
    await db.execute(
        pg_insert(AttestKey)
        .values(user_id=user.id, public_key=stored.public_key)
        .on_conflict_do_nothing(constraint="uq_attest_key_user_key")
    )
    r = credential["response"]
    return {
        "kind": "webauthn",
        "challenge": digest,
        "credential_id": credential["rawId"],
        "authenticator_data": r["authenticatorData"],
        "client_data_json": r["clientDataJSON"],
        "signature": r["signature"],
        "public_key": wan.bytes_to_base64url(stored.public_key),
        "rp_id": _rp_id(),
        "origin": _origin(),
    }
