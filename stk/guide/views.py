"""The Guide: task scenarios over the demo case and one live proof of a claim's
place in the chain. The proof sends hashes and the chained row, never a value."""

from datetime import UTC, datetime

import orjson
from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import auth_required, current_user
from sqlalchemy import Text, cast, func, select

from stk.anchor.models import Anchor, Batch
from stk.anchor.service import _hashes
from stk.cases.models import Case
from stk.discovery.evidence import view_session
from stk.discovery.service import available
from stk.guide import content
from stk.ledger import chain, merkle, write
from stk.ledger.contracts import EXTRACTED_PROPS, Prop, StatementIn, Tier
from stk.ledger.models import (
    Acquisition,
    Chain,
    Payload,
    Principal,
    Run,
    Statement,
    StatementTier,
)
from stk.ledger.receipts import ots_pending
from stk.monitor.data import Context
from stk.monitor.views import ROLES

bp_guide = Blueprint("guide", __name__)


@bp_guide.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_guide.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


@bp_guide.get("/guide")
async def page():
    demo = await g.db_session.scalar(select(Case.id).where(Case.slug == content.CASE))
    return await render_template(
        "guide/index.html",
        guide={
            "case": content.CASE,
            "demo": demo is not None,
            "areas": content.AREAS,
            "scenarios": content.SCENARIOS,
            "trust": content.TRUST,
        },
    )


async def _example(db, investigation: str) -> Statement | None:
    """The earliest claim in the case that a run signed and a person accepted."""
    in_case = select(Acquisition.blob_sha256).where(
        Acquisition.context["investigation"].astext == investigation
    )
    return await db.scalar(
        select(Statement)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(
            Chain, (Chain.kind == "statement") & (Chain.ref == cast(Statement.id, Text))
        )
        .where(
            Statement.prop.in_([str(p) for p in EXTRACTED_PROPS]),
            Statement.span_blob.in_(in_case),
            StatementTier.tier == Tier.ACCEPTED,
            Statement.sig["kind"].astext == "ed25519",
            available(Statement.span_blob),
            write.current_clause(),
        )
        .order_by(Chain.seq)
        .limit(1)
    )


async def _log_view(db, st: Statement) -> None:
    """One access record per reader, claim and login session, as the brief logs."""
    me = await write.principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    key = view_session()
    subject = f"statement:{st.id}"
    seen = await db.scalar(
        select(Statement.id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            Statement.subject == subject,
            Statement.prop == Prop.ACCESS,
            Statement.author_id == me.id,
            Payload.value["session"].astext == key,
        )
        .limit(1)
    )
    if seen is None:
        await write.append(
            db,
            StatementIn(
                subject=subject,
                prop=Prop.ACCESS,
                value={
                    "action": "view",
                    "at": datetime.now(UTC).isoformat(),
                    "session": key,
                    "statements": [str(st.id)],
                },
                author_id=me.id,
            ),
        )
    await db.commit()


async def _anchors(db, batch: Batch) -> list[dict]:
    """Receipt state per target. A failed attempt is retried next run, so any
    receipt for a target wins over its earlier failures."""
    rows = (
        await db.execute(
            select(Anchor).where(Anchor.batch_id == batch.id).order_by(Anchor.id)
        )
    ).scalars()
    out = {}
    for a in rows:
        if a.receipt is not None:
            pending = a.kind == "ots" and ots_pending(a.receipt)
            out[(a.kind, a.target)] = "pending" if pending else "held"
        else:
            out.setdefault((a.kind, a.target), "failed")
    return [{"kind": kind, "state": state} for (kind, _), state in out.items()]


@bp_guide.get("/api/guide/proof")
async def proof():
    db = g.db_session
    investigation = request.args.get("investigation")
    if not investigation:
        abort(400, description="investigation is required")
    st = await _example(db, investigation)
    if st is None:
        return _json({"claim": None})
    await _log_view(db, st)

    entry = await db.scalar(
        select(Chain).where(Chain.kind == "statement", Chain.ref == str(st.id))
    )
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

    run = await db.get(Run, st.run_id)
    review = await db.scalar(
        select(Statement)
        .where(Statement.subject == f"statement:{st.id}", Statement.prop == Prop.REVIEW)
        .order_by(Statement.recorded_at.desc())
        .limit(1)
    )
    ctx = await Context.load(db, blobs=[st.span_blob])
    doc = ctx.doc(st.span_blob)
    reviewer = await db.get(Principal, review.author_id) if review else None
    return _json(
        {
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
                "anchors": await _anchors(db, batch) if batch else [],
            },
            "run_signature": {
                "sig": st.sig["sig"],
                "pubkey": run.pubkey.hex(),
                "verifies": await write.sig_problem(db, st) is None,
            },
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
    )
