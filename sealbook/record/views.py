"""The record's state for the top bar: chain counts, the latest batches and what
each witness holds for them. Hashes, counts and times only, never a value. It
states what the stored receipts say; recomputing roots is `verify-anchors`."""

from urllib.parse import urlsplit

import orjson
from quart import Blueprint, Response, abort, g
from quart_security import auth_required, current_user
from sqlalchemy import func, select

from sealbook.anchor.models import Anchor, Batch
from sealbook.anchor.service import FILES
from sealbook.ledger.contracts import Prop
from sealbook.ledger.models import Chain, Principal, Statement
from sealbook.ledger.receipts import ots_pending

bp_record = Blueprint("record", __name__)

NAMES = {
    "ots": "OpenTimestamps",
    "tsa": "Time-stamp authority",
    "partner": "Partner copy",
}
LABELS = {
    "held": "Receipt held",
    "pending": "Receipt held, waiting for Bitcoin",
    "failed": "Not sent yet, retried next run",
    "none": "No receipt for this batch",
}


@bp_record.before_request
@auth_required("session")
async def guard():
    pass


def _json(payload):
    return Response(
        orjson.dumps(payload),
        content_type="application/json",
        headers={"Cache-Control": "private, no-store"},
    )


def _where(kind: str, target: str) -> str | None:
    # A partner target is a directory on this server: not shown.
    if kind == "partner":
        return None
    return ", ".join(urlsplit(u).netloc or u for u in target.split(","))


def _witness(kind, state, anchor=None, where=None):
    return {
        "kind": kind,
        "name": NAMES[kind],
        "state": state,
        "label": LABELS[state],
        "where": where,
        "id": anchor.id if anchor and state != "failed" else None,
        "at": anchor.at if anchor and state != "failed" else None,
    }


async def witnesses(db, batch_id: int) -> list[dict]:
    """Receipt state per target. A failed attempt is retried next run, so any
    receipt for a target wins over its earlier failures."""
    rows = (
        await db.execute(
            select(Anchor).where(Anchor.batch_id == batch_id).order_by(Anchor.id)
        )
    ).scalars()
    out = {}
    for a in rows:
        where = _where(a.kind, a.target)
        if a.receipt is not None:
            pending = a.kind == "ots" and ots_pending(a.receipt)
            state = "pending" if pending else "held"
            out[(a.kind, a.target)] = _witness(a.kind, state, a, where)
        else:
            out.setdefault((a.kind, a.target), _witness(a.kind, "failed", a, where))
    return list(out.values())


def _held(ws) -> int:
    return sum(w["state"] in ("held", "pending") for w in ws)


def _batch(b: Batch) -> dict:
    return {
        "first_seq": b.first_seq,
        "last_seq": b.last_seq,
        "root": b.root,
        "at": b.at,
    }


async def _covering(db, seq: int) -> Batch | None:
    return await db.scalar(
        select(Batch).where(Batch.first_seq <= seq, Batch.last_seq >= seq)
    )


async def _last_decision(db) -> dict | None:
    """The current user's most recent passkey-signed review and where it sits."""
    me = await db.scalar(
        select(Principal.id).where(Principal.user_id == current_user.id)
    )
    if me is None:
        return None
    st = await db.scalar(
        select(Statement)
        .where(
            Statement.author_id == me,
            Statement.prop == Prop.REVIEW,
            Statement.sig["kind"].astext == "webauthn",
        )
        .order_by(Statement.recorded_at.desc())
        .limit(1)
    )
    if st is None:
        return None
    seq = await db.scalar(
        select(Chain.seq).where(Chain.kind == "statement", Chain.ref == str(st.id))
    )
    batch = await _covering(db, seq) if seq else None
    ws = await witnesses(db, batch.id) if batch else []
    times = [w["at"] for w in ws if w["at"]]
    return {
        "at": st.recorded_at,
        "seq": seq,
        "anchored": bool(times),
        "anchored_at": min(times) if times else None,
    }


@bp_record.get("/api/record/status")
async def status():
    db = g.db_session
    entries = await db.scalar(select(func.count()).select_from(Chain))
    top = await db.scalar(select(func.max(Chain.seq))) or 0
    batches = await db.scalar(select(func.count()).select_from(Batch))
    recent = (
        (await db.execute(select(Batch).order_by(Batch.last_seq.desc()).limit(3)))
        .scalars()
        .all()
    )
    rows, latest = [], None
    for i, b in enumerate(recent):
        ws = await witnesses(db, b.id)
        rows.append({**_batch(b), "held": _held(ws), "witnesses": len(ws)})
        if i == 0:
            kinds = {w["kind"] for w in ws}
            ws += [_witness(k, "none") for k in NAMES if k not in kinds]
            ws.sort(key=lambda w: list(NAMES).index(w["kind"]))
            times = [w["at"] for w in ws if w["at"]]
            latest = {
                **_batch(b),
                "witnesses": ws,
                "held": _held(ws),
                "failed": sum(w["state"] == "failed" for w in ws),
                "anchored_at": max(times) if times else None,
            }
    return _json(
        {
            "entries": entries,
            "batches": batches,
            "unanchored": top - (recent[0].last_seq if recent else 0),
            "latest": latest,
            "recent": rows,
            "decision": await _last_decision(g.db_session),
        }
    )


@bp_record.get("/api/record/receipt/<int:anchor_id>")
async def receipt(anchor_id: int):
    db = g.db_session
    a = await db.get(Anchor, anchor_id)
    if a is None or a.receipt is None:
        abort(404)
    name = FILES[a.kind](await db.get(Batch, a.batch_id))
    return Response(
        a.receipt,
        content_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "private, no-store",
        },
    )
