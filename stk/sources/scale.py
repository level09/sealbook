"""Dev only: bulk-insert N synthetic sources to prove the Sources table at scale.

    SQLALCHEMY_DATABASE_URI=postgresql+asyncpg:///sealbook_scale \\
        uv run python -m stk.sources.scale --count 20000

Ledger tables are insert-only, so these rows can never be removed. The seeder writes
straight to the tables (no chain entries, no signatures) and therefore refuses any
database whose name does not mark it as throwaway, and any case not named
case-scale-test*. Every row says synthetic: file, origin and context."""

from __future__ import annotations

import asyncio
import hashlib
import random
import uuid
from datetime import UTC, datetime, timedelta

import click
from sqlalchemy import column, insert, select, table, text
from sqlalchemy.engine import make_url

from stk.discovery.models import FindingKey
from stk.ledger.contracts import EXTRACTED_PROPS, Prop, Tier
from stk.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Principal,
    Run,
    Statement,
    StatementTier,
)

MIMES = [
    ("image/jpeg", "photo"),
    ("application/pdf", "scanned_order"),
    ("audio/ogg", "voice_note"),
    ("video/mp4", "video"),
    ("text/plain", "news_article"),
    ("text/html", "web_post"),
]
CLAIM_PROPS = sorted(EXTRACTED_PROPS)
ASSIGNMENT = table(
    "assignment",
    *map(
        column,
        "id investigation subject assignee_id assigned_by due priority status"
        " created_at updated_at".split(),
    ),
)
TIERS = [Tier.UNCONFIRMED, Tier.ACCEPTED, Tier.CORROBORATED, Tier.REJECTED]


def throwaway(url: str) -> bool:
    name = make_url(url).database or ""
    return "scale" in name or name.startswith("stk_tmp_")


def _hex(rng: random.Random, n=64) -> str:
    return f"{rng.getrandbits(n * 4):0{n}x}"


async def seed(db, investigation: str, count: int, rng_seed: int = 7) -> dict:
    """Insert `count` synthetic sources into `investigation`. Returns what it wrote,
    so a test can check the listing's counts against the truth."""
    if not investigation.startswith("case-scale-test"):
        raise ValueError("synthetic sources go only into a case-scale-test* case")
    rng = random.Random(rng_seed)
    now = datetime.now(UTC)
    collectors = [
        {"id": uuid.uuid4(), "kind": "human", "name": f"synthetic-collector-{i}"}
        for i in range(1, 4)
    ]
    agent = {"id": uuid.uuid4(), "kind": "agent", "name": "synthetic-extractor"}
    await db.execute(insert(Principal), [*collectors, agent])
    blobs, acqs, stmts, tiers, payloads, runs, findings = [], [], [], [], [], [], []
    truth = {}
    for i in range(1, count + 1):
        sha = _hex(rng)
        mime, doc_type = MIMES[rng.randrange(len(MIMES))]
        sensitive = rng.random() < 0.2
        blobs.append(
            {
                "sha256": sha,
                "md5": _hex(rng, 32),
                "size": rng.randint(2_000, 6_000_000),
                "mime": mime,
                "storage_ref": "synthetic",
                "created_at": now,
            }
        )
        acqs.append(
            {
                "id": uuid.uuid4(),
                "blob_sha256": sha,
                "origin": f"synthetic:scale-test/{i % 97}",
                "collector_id": collectors[i % 3]["id"],
                "at": now - timedelta(seconds=rng.randint(0, 9_000_000)),
                "context": {
                    "file": f"synthetic-{i:05d}",
                    "investigation": investigation,
                    "synthetic": True,
                },
                "sensitivity": "sensitive" if sensitive else "ordinary",
                "doc_type": doc_type,
            }
        )
        claims = pending = 0
        claim_ids = []
        for _ in range(rng.randrange(14)):
            sid, tier = uuid.uuid4(), rng.choice(TIERS)
            stmts.append(
                {
                    "id": sid,
                    "subject": f"person:{uuid.uuid4()}",
                    "prop": rng.choice(CLAIM_PROPS),
                    "commitment": _hex(rng),
                    "span_blob": sha,
                    "author_id": agent["id"],
                    "recorded_at": now,
                    "hash": _hex(rng),
                    "evidence": [],
                }
            )
            tiers.append({"statement_id": sid, "tier": tier, "reason": "synthetic"})
            claim_ids.append(str(sid))
            claims += tier != Tier.REJECTED
            pending += tier == Tier.UNCONFIRMED
        c2pa = "unchecked"
        if mime.startswith(("image/", "video/")):
            c2pa = "valid" if rng.random() < 0.15 else "absent"
            commitment = _hex(rng)
            stmts.append(
                {
                    "id": uuid.uuid4(),
                    "subject": f"blob:{sha}",
                    "prop": Prop.C2PA_VERIFY,
                    "commitment": commitment,
                    "author_id": agent["id"],
                    "recorded_at": now,
                    "hash": _hex(rng),
                    "evidence": [],
                }
            )
            payloads.append(
                {
                    "commitment": commitment,
                    "salt": rng.randbytes(32),
                    "value": {"status": c2pa, "issuer": None, "codes": []},
                    "value_key": _hex(rng),
                    "idem_key": _hex(rng),
                }
            )
        open_findings = 0
        if claim_ids and rng.random() < 0.1:
            open_findings = 1
            findings.append(
                {
                    "key": hashlib.sha256(sha.encode()).hexdigest(),
                    "kind": "shared_presence",
                    "state": "open",
                    "rank": 1,
                    "members": claim_ids[:2],
                    "notes": [],
                    "created_at": now,
                }
            )
        failed = rng.random() < 0.03
        if failed:
            runs.append(
                {
                    "id": uuid.uuid4(),
                    "principal_id": agent["id"],
                    "tool": "extract",
                    "inputs": {"blobs": [sha], "synthetic": True},
                    "status": "failed",
                    "error": "synthetic failure",
                }
            )
        truth[sha] = {
            "claims": claims,
            "pending": pending,
            "findings": open_findings,
            "c2pa": c2pa,
            "sensitive": sensitive,
            "failed": failed,
        }
    finding_run = uuid.uuid4()
    runs.append(
        {
            "id": finding_run,
            "principal_id": agent["id"],
            "tool": "discover",
            "inputs": {"investigation": investigation, "synthetic": True},
            "status": "done",
        }
    )
    for f in findings:
        f["run_id"] = finding_run
        f["investigation"] = investigation
    for model, rows in (
        (Blob, blobs),
        (Acquisition, acqs),
        (Run, runs),
        (Payload, payloads),
        (Statement, stmts),
        (StatementTier, tiers),
        (FindingKey, findings),
    ):
        for n in range(0, len(rows), 5_000):
            await db.execute(insert(model), rows[n : n + 5_000])
    assigned = await _assign(db, rng, investigation, list(truth), now)
    for sha, who in assigned.items():
        truth[sha]["assignee"] = who
    await db.execute(text("analyze"))
    return truth


async def _assign(db, rng, investigation, shas, now, share=0.5) -> dict:
    """Assign `share` of the sources to existing analysts, when the table exists.
    Admins are left out: synthetic filler would bury the owner's real Inbox."""
    if not await db.scalar(text("select to_regclass('assignment') is not null")):
        return {}
    users = list(
        await db.scalars(
            text(
                'select u.id from "user" u join roles_users ru on ru.user_id = u.id'
                " join role r on r.id = ru.role_id where r.name = 'analyst'"
                " and u.active and not exists (select 1 from roles_users a"
                " join role ar on ar.id = a.role_id"
                " where a.user_id = u.id and ar.name = 'admin') order by u.id"
            )
        )
    )
    if not users:
        return {}
    out, rows = {}, []
    for sha in shas:
        if rng.random() < share:
            who = rng.choice(users)
            out[sha] = who
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "investigation": investigation,
                    "subject": f"blob:{sha}",
                    "assignee_id": who,
                    "assigned_by": who,
                    "due": (now + timedelta(days=rng.randint(-10, 30))).date(),
                    "priority": "normal",
                    "status": "open",
                    "created_at": now,
                    "updated_at": now,
                }
            )
    for n in range(0, len(rows), 5_000):
        await db.execute(insert(ASSIGNMENT), rows[n : n + 5_000])
    return out


DEMO = "case-scale-demo"


async def fillers(db, count: int, rng_seed: int = 11) -> int:
    """`stk demo-seed --scale N`: N labelled synthetic sources in `case-scale-demo`,
    so the Sources table, search and case switcher can be shown at thousands of rows.
    Unlike `seed`, each acquisition goes through the chain, so `stk verify` still
    passes; there are no statements and no runs, so nothing is queued and the worker
    has nothing to do. The blobs have no bytes (storage_ref "synthetic"), so
    previews and thumbnails stay empty. Ledger rows cannot be removed again."""
    from stk.ledger.write import record_acquisition

    rng = random.Random(rng_seed)
    now = datetime.now(UTC)
    collectors = [
        {"id": uuid.uuid4(), "kind": "human", "name": f"synthetic-filler-{i}"}
        for i in range(1, 4)
    ]
    await db.execute(insert(Principal), collectors)
    blobs, shas = [], []
    for i in range(1, count + 1):
        sha = _hex(rng)
        mime, doc_type = MIMES[rng.randrange(len(MIMES))]
        blobs.append(
            {
                "sha256": sha,
                "md5": _hex(rng, 32),
                "size": rng.randint(2_000, 6_000_000),
                "mime": mime,
                "storage_ref": "synthetic",
                "created_at": now,
            }
        )
        shas.append((sha, doc_type, i))
    for n in range(0, len(blobs), 5_000):
        await db.execute(insert(Blob), blobs[n : n + 5_000])
    for sha, doc_type, i in shas:
        await record_acquisition(
            db,
            blob_sha256=sha,
            origin=f"synthetic:scale-demo/{i % 97}",
            collector_id=collectors[i % 3]["id"],
            at=now - timedelta(seconds=rng.randint(0, 7_000_000)),
            context={
                "file": f"synthetic-filler-{i:05d}",
                "investigation": DEMO,
                "synthetic": True,
            },
            sensitivity="sensitive" if rng.random() < 0.2 else "ordinary",
            doc_type=doc_type,
        )
    await _assign(db, rng, DEMO, [s[0] for s in shas], now, share=0.08)
    await db.execute(text("analyze"))
    return count


@click.command()
@click.option("--investigation", default="case-scale-test", show_default=True)
@click.option("--count", default=20_000, show_default=True)
def main(investigation, count):
    import stk.extensions as ext
    from stk.app import create_app
    from stk.settings import Config

    if not throwaway(Config.SQLALCHEMY_DATABASE_URI):
        raise click.UsageError(
            "refusing: point SQLALCHEMY_DATABASE_URI at a throwaway database whose"
            " name contains 'scale'; ledger rows can never be deleted"
        )
    create_app()

    async def run():
        async with ext.async_session_factory() as db, db.begin():
            if await db.scalar(
                select(Acquisition.id)
                .where(Acquisition.context["investigation"].astext == investigation)
                .limit(1)
            ):
                raise click.UsageError(f"{investigation} already has sources")
            truth = await seed(db, investigation, count)
        await ext.engine.dispose()
        return truth

    truth = asyncio.run(run())
    click.echo(f"Inserted {len(truth):,} synthetic sources into {investigation}.")


if __name__ == "__main__":
    main()
