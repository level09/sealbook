"""Anchor receipt checks shared by the anchoring service and the offline package
verifier. Pure: bytes in, ValueError naming what is wrong out.

  ots     detached .ots file whose sha256 file digest is the batch root
  tsa     DER TimeStampResp, messageImprint sha256 over bytes.fromhex(root)
  partner text file holding the root hex
"""

from __future__ import annotations

import hashlib

from asn1crypto import tsp
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding

OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
OTS_HEAD = OTS_MAGIC + b"\x01\x08"  # major version 1, file hash op sha256
OTS_BITCOIN = bytes.fromhex("0588960d73d71901")  # Bitcoin block header attestation tag


def ots_check(receipt: bytes, root: str) -> None:
    if not receipt.startswith(OTS_HEAD):
        raise ValueError("not a sha256 detached .ots file")
    if receipt[len(OTS_HEAD) : len(OTS_HEAD) + 32].hex() != root:
        raise ValueError("ots file digest does not match root")


def ots_pending(receipt: bytes) -> bool:
    """True while the proof holds only calendar commitments, no Bitcoin attestation.
    ponytail: a byte search for the attestation tag, not a parse of the proof; good
    for a count, parse the timestamp tree once upgraded proofs are verified."""
    return OTS_BITCOIN not in receipt


def tsa_check(receipt: bytes, root: str, trusted: set[str] | None = None):
    """Check the token's imprint and its signature against the cert inside it.
    With `trusted` (sha256 hex of signer certs), the signer must be one of them.
    ponytail: without `trusted` the embedded cert is taken on its word, as the
    offline package verifier still does; ship the pins with packages to close it."""
    resp = tsp.TimeStampResp.load(receipt)
    if resp["status"]["status"].native not in ("granted", "granted_with_mods"):
        raise ValueError(f"TSA status {resp['status']['status'].native}")
    signed = resp["time_stamp_token"]["content"]
    econtent = signed["encap_content_info"]["content"]
    info = econtent.parsed
    imprint = info["message_imprint"]
    if imprint["hash_algorithm"]["algorithm"].native != "sha256":
        raise ValueError("TSA imprint is not sha256")
    if imprint["hashed_message"].native.hex() != root:
        raise ValueError("TSA imprint does not match root")

    (si,) = signed["signer_infos"]
    algo = si["digest_algorithm"]["algorithm"].native
    attrs = {a["type"].native: a["values"][0].native for a in si["signed_attrs"]}
    if attrs.get("message_digest") != hashlib.new(algo, econtent.contents).digest():
        raise ValueError("TSA token content digest mismatch")
    sid = si["sid"]
    for choice in signed["certificates"]:
        cert = choice.chosen
        if sid.name == "issuer_and_serial_number":
            match = (
                cert.serial_number == sid.chosen["serial_number"].native
                and cert.issuer == sid.chosen["issuer"]
            )
        else:
            match = cert.key_identifier == sid.chosen.native
        if match:
            break
    else:
        raise ValueError("TSA signer certificate not in token")
    if trusted is not None:
        fingerprint = hashlib.sha256(cert.dump()).hexdigest()
        if fingerprint not in trusted:
            raise ValueError(f"TSA signer not pinned ({fingerprint})")
    key = x509.load_der_x509_certificate(cert.dump()).public_key()
    data = si["signed_attrs"].untag().dump()
    digest = getattr(hashes, algo.upper())()
    sig = si["signature"].native
    kind = si["signature_algorithm"].signature_algo
    try:
        if kind == "rsassa_pkcs1v15":
            key.verify(sig, data, padding.PKCS1v15(), digest)
        elif kind == "ecdsa":
            key.verify(sig, data, ec.ECDSA(digest))
        else:
            raise ValueError(f"unsupported TSA signature {kind}")
    except InvalidSignature as exc:
        raise ValueError("TSA signature does not verify") from exc
    return info


def partner_check(receipt: bytes, root: str) -> None:
    if receipt.decode("ascii").strip() != root:
        raise ValueError("partner root does not match")


CHECKS = {"ots": ots_check, "tsa": tsa_check, "partner": partner_check}
