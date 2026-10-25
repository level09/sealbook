"""Canonical encoding, row hashes and the hash chain. Pure: no I/O, no clock."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sealbook.ledger.contracts import CHAINED_FIELDS

GENESIS = "0" * 64


def _plain(value):
    """Reduce a value to canonical JSON types. Floats are refused: their text form
    is not stable across encoders, and a hash must be."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        raise TypeError("floats are not canonical; use int or str")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("naive datetime in a hashed field")
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    raise TypeError(f"not canonical: {type(value).__name__}")


def canon(value) -> bytes:
    return json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def commitment(salt: bytes, value) -> str:
    """Salted commitment of a value. The chain hashes this, never the value, so the
    value can be erased and the chain still verifies."""
    return sha256(salt + canon(value))


def row_hash(kind: str, row: dict) -> str:
    return sha256(canon({name: row[name] for name in CHAINED_FIELDS[kind]}))


def link(prev_hash: str, seq: int, kind: str, row_hash_: str) -> str:
    return sha256(canon([prev_hash, seq, kind, row_hash_]))


@dataclass(frozen=True)
class Entry:
    seq: int
    kind: str
    ref: str
    row_hash: str
    prev_hash: str
    hash: str


def verify(
    entries: list[Entry], recomputed: dict[tuple[str, str], str], prev: str = GENESIS
) -> list[str]:
    """Check a chain segment in seq order. `recomputed` maps (kind, ref) to the row
    hash computed from the row as it stands now; `prev` is the hash the segment
    must link to (GENESIS for the full chain, or an anchored entry's hash).
    Returns problems; empty means the segment is intact."""
    problems = []
    for e in entries:
        if e.prev_hash != prev:
            problems.append(f"seq {e.seq}: prev_hash does not match previous entry")
        if link(e.prev_hash, e.seq, e.kind, e.row_hash) != e.hash:
            problems.append(f"seq {e.seq}: entry hash does not match its contents")
        now = recomputed.get((e.kind, e.ref))
        if now is None:
            problems.append(f"seq {e.seq}: {e.kind} {e.ref} is missing")
        elif now != e.row_hash:
            problems.append(f"seq {e.seq}: {e.kind} {e.ref} was modified")
        prev = e.hash
    return problems
