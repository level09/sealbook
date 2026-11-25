"""The Guide: task scenarios over the demo case and one live proof of a claim's
place in the chain. The proof sends hashes and the chained row, never a value."""

from datetime import UTC, datetime

from quart import Blueprint, abort, g, render_template, request
from quart_security import current_user
from sqlalchemy import Text, cast, select

from sealbook.anchor.proof import statement_proof
from sealbook.cases.models import Case
from sealbook.discovery.evidence import view_session
from sealbook.discovery.service import available
from sealbook.guide import content, film
from sealbook.ledger import write
from sealbook.ledger.contracts import EXTRACTED_PROPS, Prop, StatementIn, Tier
from sealbook.ledger.models import (
    Acquisition,
    Chain,
    Payload,
    Statement,
    StatementTier,
)
from sealbook.web import json_response, protect

bp_guide = Blueprint("guide", __name__)


protect(bp_guide)


async def first_cards(db) -> list[dict]:
    """The starter cards this server can honour: one that opens a demo source is
    left out where that source was never added."""
    cards = []
    for card in content.FIRST:
        sha = card["href"].partition("source=")[2]
        if sha and not await db.scalar(
            select(Acquisition.id).where(Acquisition.blob_sha256 == sha).limit(1)
        ):
            continue
        cards.append(card)
    return cards


@bp_guide.get("/guide")
async def page():
    demo = await g.db_session.scalar(select(Case.id).where(Case.slug == content.CASE))
    return await render_template(
        "guide/index.html",
        guide={
            "case": content.CASE,
            "demo": demo is not None,
            "areas": content.AREAS,
            "first": await first_cards(g.db_session),
            "scenarios": content.SCENARIOS,
            "trust": content.TRUST,
            "film": {"src": film.urls(), "chapters": film.chapters()},
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
    me = await write.principal_of(db, current_user)
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


@bp_guide.get("/api/guide/proof")
async def proof():
    db = g.db_session
    investigation = request.args.get("investigation")
    if not investigation:
        abort(400, description="investigation is required")
    st = await _example(db, investigation)
    if st is None:
        return json_response({"claim": None})
    await _log_view(db, st)
    entry = await db.scalar(
        select(Chain).where(Chain.kind == "statement", Chain.ref == str(st.id))
    )
    return json_response(await statement_proof(db, st, entry))
