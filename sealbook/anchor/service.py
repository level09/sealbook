"""Hourly Merkle anchoring of the chain: OpenTimestamps, an RFC 3161 TSA and
partner-held copies, plus the checks that hold the database to those receipts.

Receipt formats are shared with the offline package verifier:
  ots     detached .ots file whose sha256 file digest is the batch root
  tsa     DER TimeStampResp, messageImprint sha256 over bytes.fromhex(root)
  partner text file holding the root hex, "<first_seq>-<last_seq>.root"
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

import httpx
from asn1crypto import tsp
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sealbook.anchor.models import Anchor, Batch
from sealbook.ledger import merkle
from sealbook.ledger.models import Chain
from sealbook.ledger.receipts import CHECKS, OTS_HEAD, ots_pending, tsa_check
from sealbook.settings import Config

log = logging.getLogger(__name__)

OTS_FORK = b"\xff"
ANCHOR_LOCK = 0x5EA1A2C0  # distinct from the chain append lock

FILES = {
    "ots": lambda b: f"{b.root}.ots",
    "tsa": lambda b: f"{b.root}.tsr",
    "partner": lambda b: f"{b.first_seq}-{b.last_seq}.root",
}


async def _hashes(session: AsyncSession, first: int, last: int) -> list[str]:
    rows = await session.execute(
        select(Chain.seq, Chain.hash)
        .where(Chain.seq.between(first, last))
        .order_by(Chain.seq)
    )
    rows = rows.all()
    if [seq for seq, _ in rows] != list(range(first, last + 1)):
        raise ValueError(f"chain seqs {first}-{last} are not contiguous")
    return [h for _, h in rows]


async def _last_batch(session: AsyncSession) -> Batch | None:
    return (
        await session.execute(select(Batch).order_by(Batch.last_seq.desc()).limit(1))
    ).scalar()


async def close_batch(session: AsyncSession) -> Batch | None:
    """Close one batch over every chain entry after the previous batch."""
    await session.execute(text("select pg_advisory_xact_lock(:k)"), {"k": ANCHOR_LOCK})
    last = await _last_batch(session)
    first = last.last_seq + 1 if last else 1
    top = (await session.execute(select(func.max(Chain.seq)))).scalar()
    if top is None or top < first:
        return None
    batch = Batch(
        first_seq=first,
        last_seq=top,
        size=top - first + 1,
        root=merkle.root(await _hashes(session, first, top)),
    )
    session.add(batch)
    await session.flush()
    return batch


# Clients. Each returns receipt bytes or raises.


async def ots_stamp(
    client: httpx.AsyncClient, calendars: list[str], root: str
) -> bytes:
    digest = bytes.fromhex(root)
    branches = []
    for url in calendars:
        r = await client.post(
            url.rstrip("/") + "/digest",
            content=digest,
            headers={"Accept": "application/vnd.opentimestamps.v1"},
        )
        r.raise_for_status()
        # ponytail: forks calendar replies by prefixing 0xff, valid only while each
        # reply starts with a single op (true of the public calendars); parse and
        # merge timestamp trees if a calendar ever returns a top-level fork.
        if not r.content or r.content[:1] == OTS_FORK:
            raise ValueError(f"{url}: unexpected calendar reply")
        branches.append(r.content)
    if not branches:
        raise ValueError("no OTS calendars configured")
    return (
        OTS_HEAD + digest + b"".join(OTS_FORK + b for b in branches[:-1]) + branches[-1]
    )


async def tsa_stamp(client: httpx.AsyncClient, url: str, root: str) -> bytes:
    nonce = secrets.randbits(63)
    req = tsp.TimeStampReq(
        {
            "version": 1,
            "message_imprint": {
                "hash_algorithm": {"algorithm": "sha256"},
                "hashed_message": bytes.fromhex(root),
            },
            "nonce": nonce,
            "cert_req": True,
        }
    )
    r = await client.post(
        url,
        content=req.dump(),
        headers={"Content-Type": "application/timestamp-query"},
    )
    r.raise_for_status()
    info = tsa_check(r.content, root)
    if info["nonce"].native != nonce:
        raise ValueError("TSA nonce mismatch")
    return r.content


def partner_write(directory: str, batch: Batch) -> bytes:
    path = Path(directory) / FILES["partner"](batch)
    if path.exists():
        if path.read_text().strip() != batch.root:
            raise ValueError(f"{path} holds a different root")
    else:
        path.write_text(batch.root)
    return batch.root.encode()


# Receipt checks. Each raises ValueError naming what is wrong.


def _targets() -> list[tuple[str, str]]:
    return [
        ("ots", ",".join(Config.OTS_CALENDARS)),
        ("tsa", Config.TSA_URL),
        *(("partner", d) for d in Config.PARTNER_ROOT_DIRS),
    ]


async def _attempt(client, batch: Batch, kind: str, target: str) -> bytes:
    if kind == "ots":
        return await ots_stamp(client, target.split(","), batch.root)
    if kind == "tsa":
        return await tsa_stamp(client, target, batch.root)
    return partner_write(target, batch)


async def anchor_pending(session: AsyncSession, client: httpx.AsyncClient) -> list[str]:
    """Anchor every batch lacking a receipt for a configured target. Failures are
    stored as error rows and retried next run. Returns the errors."""
    done = set(
        (
            await session.execute(
                select(Anchor.batch_id, Anchor.kind, Anchor.target).where(
                    Anchor.receipt.is_not(None)
                )
            )
        ).all()
    )
    errors = []
    for batch in (await session.execute(select(Batch).order_by(Batch.id))).scalars():
        for kind, target in _targets():
            if (batch.id, kind, target) in done:
                continue
            try:
                row = Anchor(receipt=await _attempt(client, batch, kind, target))
            except Exception as exc:
                log.exception("anchor %s %s failed", kind, target)
                row = Anchor(error=f"{type(exc).__name__}: {exc}")
                errors.append(f"batch {batch.first_seq}-{batch.last_seq} {kind}: {exc}")
            row.batch_id, row.kind, row.target = batch.id, kind, target
            session.add(row)
    await session.flush()
    return errors


async def run_once(
    session_factory, client: httpx.AsyncClient
) -> tuple[Batch | None, list[str]]:
    """Close a batch in its own transaction, so it survives any anchoring failure."""
    async with session_factory() as s, s.begin():
        batch = await close_batch(s)
    async with session_factory() as s, s.begin():
        errors = await anchor_pending(s, client)
    return batch, errors


# Interface for packages.


async def provisional(session: AsyncSession) -> int | None:
    last = await _last_batch(session)
    after = last.last_seq if last else 0
    return (
        await session.execute(select(func.min(Chain.seq)).where(Chain.seq > after))
    ).scalar()


async def _anchored_batches(session: AsyncSession, seqs: list[int]) -> dict[int, Batch]:
    """Map each seq to its batch if that batch is fully anchored: an OTS receipt, a
    TSA receipt and one for every configured partner directory.
    ponytail: a pending .ots (calendar commitments only, no Bitcoin attestation)
    counts as the OTS receipt; build an `ots upgrade` loop that replaces pending
    proofs with attested ones, then require the attestation here."""
    have: dict[int, set] = {}
    for batch_id, kind, target in await session.execute(
        select(Anchor.batch_id, Anchor.kind, Anchor.target).where(
            Anchor.receipt.is_not(None)
        )
    ):
        have.setdefault(batch_id, set()).update({kind, (kind, target)})
    need = {"ots", "tsa", *(("partner", d) for d in Config.PARTNER_ROOT_DIRS)}
    batches = [
        b
        for b in (await session.execute(select(Batch))).scalars()
        if need <= have.get(b.id, set())
    ]
    return {seq: b for seq in seqs for b in batches if b.first_seq <= seq <= b.last_seq}


async def anchored(session: AsyncSession, seqs: list[int]) -> bool:
    return len(await _anchored_batches(session, seqs)) == len(set(seqs))


async def proofs(session: AsyncSession, seqs: list[int]) -> dict:
    found = await _anchored_batches(session, seqs)
    missing = sorted(set(seqs) - found.keys())
    if missing:
        raise ValueError(f"seqs not anchored: {missing}")
    batches = {b.id: b for b in found.values()}
    leaves = {
        b.id: await _hashes(session, b.first_seq, b.last_seq) for b in batches.values()
    }
    receipts = await session.execute(
        select(Anchor)
        .where(Anchor.batch_id.in_(batches), Anchor.receipt.is_not(None))
        .order_by(Anchor.id)
    )
    files = {}
    for a in receipts.scalars():
        b = batches[a.batch_id]
        name = FILES[a.kind](b)
        files.setdefault(
            name, {"root": b.root, "kind": a.kind, "file": name, "bytes": a.receipt}
        )
    ordered = sorted(batches.values(), key=lambda b: b.first_seq)
    return {
        "batches": [
            {
                "first_seq": b.first_seq,
                "last_seq": b.last_seq,
                "size": b.size,
                "root": b.root,
            }
            for b in ordered
        ],
        "proofs": [
            {
                "seq": seq,
                "index": seq - found[seq].first_seq,
                "batch_root": found[seq].root,
                "path": merkle.inclusion_proof(
                    leaves[found[seq].id], seq - found[seq].first_seq
                ),
            }
            for seq in sorted(set(seqs))
        ],
        "anchors": list(files.values()),
    }


# Verification.


def _same(found: str, root: str) -> None:
    if found != root:
        raise ValueError(f"holds {found}, chain gives {root}")


async def verify_anchors(session: AsyncSession) -> tuple[list[str], list[str]]:
    """Hold every batch to the chain and every receipt to the recomputed root.
    Receipts commit to the root at anchoring time, so rewriting rows, chain and
    batch rows together is still caught. TSA signers must be pinned in
    TSA_CERT_SHA256, or a forged receipt would carry its own cert.
    Returns (problems, notes); notes are information, not failures."""
    problems = []
    trusted = set(Config.TSA_CERT_SHA256)
    pending = 0
    roots: dict[int, str] = {}
    batches = (await session.execute(select(Batch).order_by(Batch.first_seq))).scalars()
    expected_first = 1
    for b in batches:
        label = f"batch {b.first_seq}-{b.last_seq}"
        if b.first_seq != expected_first:
            problems.append(f"{label}: expected to start at {expected_first}")
        expected_first = b.last_seq + 1
        if b.size != b.last_seq - b.first_seq + 1:
            problems.append(f"{label}: size {b.size} does not match range")
        try:
            roots[b.id] = merkle.root(await _hashes(session, b.first_seq, b.last_seq))
        except ValueError as exc:
            problems.append(f"{label}: {exc}")
            continue
        if roots[b.id] != b.root:
            problems.append(f"{label}: root does not match chain")

    receipts = await session.execute(
        select(Anchor, Batch)
        .join(Batch, Anchor.batch_id == Batch.id)
        .where(Anchor.receipt.is_not(None))
    )
    anchored = set()
    for a, b in receipts.all():
        if b.id not in roots:
            continue
        anchored.add(b.id)
        try:
            if a.kind == "tsa":
                tsa_check(a.receipt, roots[b.id], trusted)
            else:
                CHECKS[a.kind](a.receipt, roots[b.id])
            pending += a.kind == "ots" and ots_pending(a.receipt)
        except Exception as exc:
            problems.append(
                f"batch {b.first_seq}-{b.last_seq} {a.kind} {a.target}: {exc}"
            )
    problems += [
        f"batch {b.first_seq}-{b.last_seq}: no anchor receipt"
        for b in (await session.execute(select(Batch))).scalars()
        if b.id in roots and b.id not in anchored
    ]

    # Partner files are read from disk, not from anchor rows the insider controls.
    for directory in Config.PARTNER_ROOT_DIRS:
        for path in sorted(Path(directory).glob("*.root")):
            first, last = (int(n) for n in path.stem.split("-"))
            try:
                root = merkle.root(await _hashes(session, first, last))
                _same(path.read_text().strip(), root)
            except ValueError as exc:
                problems.append(f"partner {path}: {exc}")
    notes = [f"{pending} OTS receipts pending Bitcoin attestation"] if pending else []
    return problems, notes
