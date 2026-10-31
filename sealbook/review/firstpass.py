"""Accept an item's first pass (triage, title, description lines, labels) in one
signed gesture: a review accepting each one still open that this person may decide."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from sealbook.collaboration.service import check_reviewer
from sealbook.ledger.contracts import ASSESSED_PROPS, Prop, StatementIn, Tier
from sealbook.ledger.models import Statement, StatementTier


async def plan(db, user, sha: str, author) -> tuple[str, list[StatementIn]]:
    if not isinstance(sha, str) or len(sha) != 64:
        raise ValueError("send the item's sha256")
    open_ = list(
        await db.scalars(
            select(Statement)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.subject == f"blob:{sha}",
                Statement.prop.in_(ASSESSED_PROPS),
                StatementTier.tier == Tier.UNCONFIRMED,
            )
            .order_by(Statement.recorded_at)
        )
    )
    if not open_:
        raise LookupError("nothing in this item's first pass is waiting")
    # Sent for peer review, the pass is the reviewer's to decide as a whole: the
    # request sits on its triage line.
    triage = await db.scalar(
        select(Statement).where(
            Statement.subject == f"blob:{sha}", Statement.prop == Prop.TRIAGE
        )
    )
    for st in [triage, *open_] if triage else open_:
        await check_reviewer(db, st, user)
    gesture = str(uuid.uuid4())
    at = datetime.now(UTC).isoformat()
    return gesture, [
        StatementIn(
            subject=f"statement:{st.id}",
            prop=Prop.REVIEW,
            value={"verdict": "accept", "reason": "", "at": at, "gesture": gesture},
            author_id=author,
        )
        for st in open_
    ]
