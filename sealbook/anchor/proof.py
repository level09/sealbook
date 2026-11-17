"""One claim's place in the chain, as a reader recomputes it in the browser: the
chained row, its entry and the next one, the Merkle path to its batch root, the
receipts held for that root, the run's signature and the person's review. The
proof sends hashes and the chained row, never a value."""

from sqlalchemy import func, select

from sealbook.anchor.models import Batch
from sealbook.anchor.service import _hashes
from sealbook.ledger import chain, merkle, write
from sealbook.ledger.contracts import Prop
from sealbook.ledger.models import Chain, Principal, Run, Statement
from sealbook.monitor.data import Context
from sealbook.record.views import witnesses


async def statement_proof(db, st: Statement, entry: Chain) -> dict:
    """The proof payload for `st`, chained at `entry`. A batch not closed yet is
    the range the next anchor would close."""
    after = await db.scalar(select(Chain).where(Chain.seq == entry.seq + 1))
    batch = await db.scalar(
        select(Batch).where(Batch.first_seq <= entry.seq, Batch.last_seq >= entry.seq)
    )
    if batch:
        first, last = batch.first_seq, batch.last_seq
    else:
        # The range the next anchor would close, as close_batch computes it.
        first = (await db.scalar(select(func.max(Batch.last_seq))) or 0) + 1
        last = await db.scalar(select(func.max(Chain.seq)))
    hashes = await _hashes(db, first, last)
    index = entry.seq - first

    run = await db.get(Run, st.run_id) if st.run_id else None
    review = await db.scalar(
        select(Statement)
        .where(Statement.subject == f"statement:{st.id}", Statement.prop == Prop.REVIEW)
        .order_by(Statement.recorded_at.desc())
        .limit(1)
    )
    ctx = await Context.load(db, blobs=[st.span_blob])
    doc = ctx.doc(st.span_blob)
    reviewer = await db.get(Principal, review.author_id) if review else None
    signed = run is not None and (st.sig or {}).get("kind") == "ed25519"
    return {
        "claim": {
            "prop": st.prop,
            "title": doc["title"],
            "sensitive": doc["sensitive"],
            "href": f"/evidence/{st.span_blob}",
        },
        "kind": "statement",
        "row": chain._plain(write.row_dict(st, "statement")),
        "row_hash": entry.row_hash,
        "entry": {
            "seq": entry.seq,
            "prev_hash": entry.prev_hash,
            "hash": entry.hash,
        },
        "next": {"seq": after.seq, "prev_hash": after.prev_hash} if after else None,
        "batch": {
            "first_seq": first,
            "last_seq": last,
            "size": len(hashes),
            "index": index,
            "path": merkle.inclusion_proof(hashes, index),
            "root": batch.root if batch else merkle.root(hashes),
            "closed": batch is not None,
            "anchors": await witnesses(db, batch.id) if batch else [],
        },
        "run_signature": {
            "sig": st.sig["sig"],
            "pubkey": run.pubkey.hex(),
            "verifies": await write.sig_problem(db, st) is None,
        }
        if signed
        else None,
        "review": {
            "by": reviewer.name,
            "at": review.recorded_at.isoformat(),
            "passkey": (review.sig or {}).get("kind") == "webauthn",
            "verifies": await write.sig_problem(db, review) is None,
            "demo": bool((review.sig or {}).get("demo")),
        }
        if review
        else None,
    }
