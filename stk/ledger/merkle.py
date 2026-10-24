"""RFC 6962 Merkle trees over chain entry hashes. Pure: no I/O.

A proof names the (first_seq, last_seq) batch it belongs to; its zero-based
index is seq - first_seq, and size is last_seq - first_seq + 1.
"""

from __future__ import annotations

import hashlib


def _hash(value: str) -> bytes:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("expected a 32-byte hex hash")
    result = bytes.fromhex(value)
    if len(result) != 32:
        raise ValueError("expected a 32-byte hex hash")
    return result


def _node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(size: int) -> int:
    return 1 << ((size - 1).bit_length() - 1)


def _root(leaves: list[bytes]) -> bytes:
    if not leaves:
        return hashlib.sha256(b"").digest()
    if len(leaves) == 1:
        return leaves[0]
    split = _split(len(leaves))
    return _node(_root(leaves[:split]), _root(leaves[split:]))


def _leaves(hashes: list[str]) -> list[bytes]:
    return [hashlib.sha256(b"\x00" + _hash(h)).digest() for h in hashes]


def root(hashes: list[str]) -> str:
    return _root(_leaves(hashes)).hex()


def inclusion_proof(hashes: list[str], index: int) -> list[str]:
    if type(index) is not int or not 0 <= index < len(hashes):
        raise ValueError("leaf index outside tree")

    def path(leaves, index):
        if len(leaves) == 1:
            return []
        split = _split(len(leaves))
        if index < split:
            return path(leaves[:split], index) + [_root(leaves[split:]).hex()]
        return path(leaves[split:], index - split) + [_root(leaves[:split]).hex()]

    return path(_leaves(hashes), index)


def verify_inclusion(
    leaf_hash: str, index: int, size: int, proof: list[str], root: str
) -> bool:
    if type(index) is not int or type(size) is not int or not 0 <= index < size:
        return False
    if not isinstance(proof, list):
        return False
    try:
        leaf = hashlib.sha256(b"\x00" + _hash(leaf_hash)).digest()
        expected = _hash(root)
        steps = iter(_hash(step) for step in proof)

        def rebuild(index, size):
            if size == 1:
                return leaf
            split = _split(size)
            if index < split:
                child = rebuild(index, split)
                return _node(child, next(steps))
            child = rebuild(index - split, size - split)
            return _node(next(steps), child)

        actual = rebuild(index, size)
        return next(steps, None) is None and actual == expected
    except (ValueError, TypeError, StopIteration):
        return False
