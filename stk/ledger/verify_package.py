"""Offline package contract, version 1. Run: python -m stk.ledger.verify_package DIR.

DIR/manifest.json is an object with exactly these keys:
  version: 1
  statements, acquisitions, runs: lists of row objects containing exactly the
    corresponding contracts.CHAINED_FIELDS plus hash (the row hash).
  chain: [{seq, kind, ref, row_hash, prev_hash, hash}]
  batches: [{first_seq, last_seq, size, root}]
  proofs: [{seq, index, batch_root, path: [hex sibling hashes]}]
  anchors: [{root, kind: "ots" | "tsa" | "partner", file}]
  blobs: [{sha256, md5, file}]
  values: [{commitment, salt, value}] (disclosed, unredacted values only)
  signatures: [{hash, sig}] (a statement row hash and its stored sig object:
    {"kind": "ed25519", "sig"} or {"kind": "webauthn", ...} from ledger.attest;
    a webauthn sig with "batch" (1 to 512 unique ordered row hashes) signs the
    sha256 of b"sealbook-gesture-v1\n" plus that list as compact JSON, and the row
    hash must be in it)
  documents: [{file, sha256}] (every other file at the DIR root except
    attestations.json, e.g. brief.json, methodology.md and the C2PA cover, so
    the manifest hash covers their bytes)

DIR/attestations.json, required and detached because it signs the manifest hash:
  [{name, credential_id, sig}], each sig a webauthn object from ledger.attest
  whose challenge is the manifest.json sha256. Every sig must
  verify and at least two distinct public keys must have signed.

Rows must be exported through chain.canon: UUIDs and dates become strings,
aware datetimes become UTC ISO strings with microseconds, bytes become hex.
All digests and salts are lowercase hex. Files are relative paths within
DIR/blobs/, DIR/anchors/ or DIR respectively, with no symlink escapes.

A batch covers every seq from first_seq through last_seq inclusive; size equals
that range's length. A proof's index is seq - first_seq. Entries may be selected
from the ledger; adjacent exported sequences must link, and seq 1 links to
GENESIS. An export does not establish that all ledger entries were disclosed.
Every exported entry needs a valid proof, every batch needs an anchor record.
Anchor files must contain the batch root: partner text, a sha256 TSA message
imprint in a DER TimeStampResp, or an OTS detached timestamp with magic header,
version 1 and sha256 file-hash op. TSA CMS signatures are checked against the
embedded signer certificate (RSA PKCS1v15, ECDSA or Ed25519; SHA-256/384/512).
Pin TSA signers with --trusted-tsa FILE (one sha256 of a DER signer certificate
per line, # comments allowed). Without it, OK lists the unpinned TSA signer
certificate fingerprints.
Every supplied signature must verify over an exported statement: ed25519 uses
its exported run's pubkey; WebAuthn supports ES256/P-256 and EdDSA/Ed25519 and
needs the user-present and user-verified flags and crossOrigin false.

A WebAuthn sig carries its own public key, origin and RP ID, so by itself it
only proves that some key signed. Pin trust with --rp-id and --origin (every
WebAuthn sig must match them) and --trusted-keys FILE (one key fingerprint,
the sha256 of its DER SubjectPublicKeyInfo, per line; every WebAuthn sig must
use one).
Without them the output lists signer fingerprints as self-asserted.

OK establishes package consistency, supplied signatures and anchor root bindings.
It does not establish complete disclosure, that every statement was signed,
passkey ownership, origins, relying parties or signer keys not pinned by flags,
certificate trust beyond explicit pins, certificate validity/revocation,
timestamp policy, or partner receipt authenticity.
OTS proof trees and Bitcoin attestations are not verified; Bitcoin attestation
verification needs the network and is outside this offline contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

from asn1crypto import tsp

from stk.ledger import chain, merkle, receipts
from stk.ledger.contracts import CHAINED_FIELDS
from stk.ledger.offline_sigs import verify_statement_signature

_COLLECTIONS = {
    "statements": {*CHAINED_FIELDS["statement"], "hash"},
    "acquisitions": {*CHAINED_FIELDS["acquisition"], "hash"},
    "runs": {*CHAINED_FIELDS["run"], "hash"},
    "chain": {"seq", "kind", "ref", "row_hash", "prev_hash", "hash"},
    "batches": {"first_seq", "last_seq", "size", "root"},
    "proofs": {"seq", "index", "batch_root", "path"},
    "anchors": {"root", "kind", "file"},
    "blobs": {"sha256", "md5", "file"},
    "values": {"commitment", "salt", "value"},
    "signatures": {"hash", "sig"},
    "documents": {"file", "sha256"},
}
_ATTESTATION = {"name", "credential_id", "sig"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hex(value, length=64):
    _require(isinstance(value, str), "expected hex string")
    _require(
        len(value) == length and all(c in "0123456789abcdef" for c in value),
        f"expected {length} lowercase hex characters",
    )
    return value


def _integer(value, minimum=1):
    _require(type(value) is int and value >= minimum, "invalid integer")
    return value


def _file(directory, area, name):
    _require(isinstance(name, str) and bool(name), "invalid file name")
    relative = Path(name)
    _require(
        not relative.is_absolute() and ".." not in relative.parts,
        "file path must stay inside its directory",
    )
    base = (directory / area).resolve()
    _require(base.is_relative_to(directory.resolve()), "file directory escapes package")
    path = (base / relative).resolve()
    _require(
        path.is_relative_to(base) and path.is_file(),
        f"file missing or outside {area}: {name}",
    )
    return path


@contextmanager
def _check(label, problems):
    try:
        yield
    except (ValueError, TypeError, KeyError, OSError) as error:
        problems.append(f"{label}: {error}")


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key {key}")
        result[key] = value
    return result


def _anchor(kind, data, root, trusted_tsa):
    if kind != "tsa":
        return receipts.CHECKS[kind](data, root)
    receipts.tsa_check(data, root, trusted_tsa)
    signed = tsp.TimeStampResp.load(data)["time_stamp_token"]["content"]
    (si,) = signed["signer_infos"]
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
            return hashlib.sha256(cert.dump()).hexdigest()


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_package(
    directory: Path, *, rp_id=None, origin=None, trusted=None, trusted_tsa=None
) -> tuple[list[str], list[str]]:
    """Return (problems, successful checks), without database or network access.
    rp_id, origin and trusted (key fingerprints) pin WebAuthn trust when given.
    trusted_tsa pins TSA signer certificate fingerprints when given."""
    directory = Path(directory)
    problems, ok = [], []
    pins = {"rp_id": rp_id, "origin": origin, "trusted": trusted}
    signers = set()
    tsa_signers = set()
    try:
        raw = (directory / "manifest.json").read_bytes()
        manifest = json.loads(raw, object_pairs_hook=_object)
    except (OSError, ValueError, UnicodeError) as error:
        return [f"manifest: {error}"], []
    if not isinstance(manifest, dict):
        return ["manifest: expected an object"], []
    if type(manifest.get("version")) is not int or manifest["version"] != 1:
        problems.append("manifest: unsupported version")
    if set(manifest) != {"version", *_COLLECTIONS}:
        problems.append("manifest: incorrect fields")
    records = {}
    for name, fields in _COLLECTIONS.items():
        records[name] = []
        collection = manifest.get(name)
        if not isinstance(collection, list):
            problems.append(f"{name}: expected a list")
            continue
        for index, row in enumerate(collection):
            label = f"{name}[{index}]"
            if not isinstance(row, dict) or set(row) != fields:
                problems.append(f"{label}: incorrect fields")
            else:
                records[name].append((label, row))

    rows, commitments = {}, set()
    statements, runs = {}, {}
    for kind, name in (
        ("statement", "statements"),
        ("acquisition", "acquisitions"),
        ("run", "runs"),
    ):
        for label, row in records[name]:
            with _check(label, problems):
                _require(isinstance(row["id"], str), "id must be a string")
                key = (kind, row["id"])
                _require(key not in rows, "duplicate row")
                computed = chain.row_hash(kind, row)
                rows[key] = computed
                _require(computed == _hex(row["hash"]), "row hash mismatch")
                if kind == "statement":
                    commitments.add(_hex(row["commitment"]))
                    statements[computed] = row
                elif kind == "run":
                    runs[row["id"]] = row

    for label, signature in records["signatures"]:
        with _check(label, problems):
            digest = _hex(signature["hash"])
            _require(digest in statements, "signature has no exported statement")
            run_id = statements[digest]["run_id"]
            _require(run_id is None or isinstance(run_id, str), "invalid run id")
            fp = verify_statement_signature(
                digest, signature["sig"], runs.get(run_id, {}).get("pubkey"), **pins
            )
            if fp:
                signers.add(fp)

    listed = set()
    for label, document in records["documents"]:
        with _check(label, problems):
            path = _file(directory, ".", document["file"])
            _require(
                path.parent == directory.resolve(), "document must sit at the root"
            )
            _require(path.name not in listed, "duplicate document")
            listed.add(path.name)
            _require(_sha256(path) == _hex(document["sha256"]), "sha256 mismatch")
    for path in directory.iterdir():
        if path.is_file() and path.name not in (
            listed | {"manifest.json", "attestations.json"}
        ):
            problems.append(f"{path.name}: file not covered by the manifest")

    attestations = directory / "attestations.json"
    if not attestations.exists():
        problems.append("attestations: missing required attestations.json")
    else:
        with _check("attestations", problems):
            manifest_sha = hashlib.sha256(raw).hexdigest()
            signed = json.loads(attestations.read_bytes(), object_pairs_hook=_object)
            _require(isinstance(signed, list), "expected a list")
            keys = set()
            for index, row in enumerate(signed):
                with _check(f"attestations[{index}]", problems):
                    _require(
                        isinstance(row, dict) and set(row) == _ATTESTATION,
                        "incorrect fields",
                    )
                    _require(row["sig"].get("kind") == "webauthn", "not a passkey sig")
                    _require(
                        row["credential_id"] == row["sig"]["credential_id"],
                        "credential id mismatch",
                    )
                    fp = verify_statement_signature(manifest_sha, row["sig"], **pins)
                    keys.add(fp)
                    signers.add(fp)
                    ok.append(f"attestation by {row['name']!r}: key {fp}")
            _require(len(keys) >= 2, "fewer than two distinct attesting keys")

    entries, row_entries = {}, set()
    for label, row in records["chain"]:
        with _check(label, problems):
            seq = _integer(row["seq"])
            _require(seq not in entries, "duplicate sequence")
            _require(
                isinstance(row["kind"], str) and row["kind"] in CHAINED_FIELDS,
                "unknown row kind",
            )
            _require(isinstance(row["ref"], str), "ref must be a string")
            for field in ("row_hash", "prev_hash", "hash"):
                _hex(row[field])
            key = (row["kind"], row["ref"])
            _require(key not in row_entries, "duplicate row entry")
            entries[seq] = row
            row_entries.add(key)
            if (
                chain.link(row["prev_hash"], seq, row["kind"], row["row_hash"])
                != row["hash"]
            ):
                problems.append(f"{label}: entry hash mismatch")
            if key not in rows:
                problems.append(f"{label}: chained row missing")
            elif rows[key] != row["row_hash"]:
                problems.append(f"{label}: row hash mismatch")
    for seq, entry in entries.items():
        prev = chain.GENESIS if seq == 1 else entries.get(seq - 1, {}).get("hash")
        if prev is not None and entry["prev_hash"] != prev:
            problems.append(f"seq {seq}: prev_hash mismatch")
    for key in rows.keys() - row_entries:
        problems.append(f"{key[0]} {key[1]}: missing chain entry")

    batches = {}
    for label, batch in records["batches"]:
        with _check(label, problems):
            first, last, size = (
                _integer(batch[f]) for f in ("first_seq", "last_seq", "size")
            )
            _require(
                last - first + 1 == size, "batch size does not match sequence range"
            )
            root = _hex(batch["root"])
            _require(root not in batches, "duplicate batch root")
            batches[root] = batch
            selected = sorted(seq for seq in entries if first <= seq <= last)
            if len(selected) == size:
                _require(
                    merkle.root([entries[seq]["hash"] for seq in selected]) == root,
                    "batch root mismatch",
                )

    proven = set()
    for label, proof in records["proofs"]:
        with _check(label, problems):
            seq = _integer(proof["seq"])
            index = _integer(proof["index"], 0)
            batch = batches[_hex(proof["batch_root"])]
            _require(
                batch["first_seq"] <= seq <= batch["last_seq"],
                "proof outside batch range",
            )
            _require(
                index == seq - batch["first_seq"], "proof index does not match sequence"
            )
            _require(
                merkle.verify_inclusion(
                    entries[seq]["hash"],
                    index,
                    batch["size"],
                    proof["path"],
                    batch["root"],
                ),
                "invalid inclusion proof",
            )
            proven.add(seq)
    for seq in entries.keys() - proven:
        problems.append(f"seq {seq}: missing valid inclusion proof")

    anchored = set()
    for label, anchor in records["anchors"]:
        with _check(label, problems):
            root = _hex(anchor["root"])
            _require(root in batches, "anchor has no batch")
            _require(anchor["kind"] in ("ots", "tsa", "partner"), "unknown anchor kind")
            path = _file(directory, "anchors", anchor["file"])
            fp = _anchor(anchor["kind"], path.read_bytes(), root, trusted_tsa)
            if fp:
                tsa_signers.add(fp)
            anchored.add(root)
            ok.append(f"anchor {anchor['kind']} {root}: root binding verified")
    for root in batches.keys() - anchored:
        problems.append(f"batch {root}: missing anchor")

    for label, blob in records["blobs"]:
        with _check(label, problems):
            path = _file(directory, "blobs", blob["file"])
            sha256, md5 = hashlib.sha256(), hashlib.md5()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    sha256.update(chunk)
                    md5.update(chunk)
            for name, actual, length in (
                ("sha256", sha256.hexdigest(), 64),
                ("md5", md5.hexdigest(), 32),
            ):
                with _check(f"{label} {name}", problems):
                    _require(actual == _hex(blob[name], length), f"{name} mismatch")

    for label, value in records["values"]:
        with _check(label, problems):
            commitment = _hex(value["commitment"])
            if commitment not in commitments:
                problems.append(f"{label}: commitment does not match a statement")
            salt = value["salt"]
            _require(
                isinstance(salt, str) and len(salt) % 2 == 0,
                "salt must be hex bytes",
            )
            _hex(salt, len(salt))
            _require(
                chain.commitment(bytes.fromhex(salt), value["value"]) == commitment,
                "value commitment mismatch",
            )
    if not problems:
        ok.append(
            "rows, chain entries, batch proofs, blobs, documents, disclosed commitments and supplied signatures match"
        )
        if signers and not (rp_id and origin and trusted is not None):
            ok.append(
                "signer keys, origins and RP IDs are self-asserted (pin with "
                "--rp-id, --origin, --trusted-keys): " + ", ".join(sorted(signers))
            )
        if tsa_signers and trusted_tsa is None:
            ok.append(
                "TSA signers are unpinned (pin with --trusted-tsa): "
                + ", ".join(sorted(tsa_signers))
            )
    return problems, ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--rp-id", help="expected WebAuthn relying party id")
    parser.add_argument("--origin", help="expected WebAuthn origin")
    parser.add_argument(
        "--trusted-keys", type=Path, help="file of trusted key fingerprints"
    )
    parser.add_argument(
        "--trusted-tsa", type=Path, help="file of trusted TSA signer cert fingerprints"
    )
    args = parser.parse_args()
    trusted = None
    if args.trusted_keys:
        lines = args.trusted_keys.read_text().splitlines()
        trusted = {ln.split("#")[0].strip() for ln in lines} - {""}
    trusted_tsa = None
    if args.trusted_tsa:
        lines = args.trusted_tsa.read_text().splitlines()
        trusted_tsa = {ln.split("#")[0].strip() for ln in lines} - {""}
    problems, ok = verify_package(
        args.directory,
        rp_id=args.rp_id,
        origin=args.origin,
        trusted=trusted,
        trusted_tsa=trusted_tsa,
    )
    for problem in problems:
        print(f"ERROR {problem}")
    if not problems:
        for message in ok:
            print(f"OK {message}")
        print("OK external trust and OTS Bitcoin attestations not verified")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
