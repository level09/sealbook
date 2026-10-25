"""Request review of the open proposals in selected sources: one review request per
pending proposal, all to one colleague, one notification for the lot."""

from __future__ import annotations

import re

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import aliased

from sealbook.collaboration import service
from sealbook.collaboration.notifications import notify
from sealbook.ledger import write
from sealbook.ledger.contracts import EXTRACTED_PROPS, Prop, Tier
from sealbook.ledger.models import Acquisition, Payload, Statement, StatementTier
from sealbook.sources.data import DERIVED

MAX_PROPOSALS = 200
MAX_SOURCES = 5_000
SHA = re.compile(r"[0-9a-f]{64}")


def _shas(item) -> list[str]:
    shas = item.get("shas")
    if (
        not isinstance(shas, list)
        or not shas
        or not all(isinstance(s, str) for s in shas)
    ):
        raise ValueError("shas must be a list of source hashes")
    shas = list(dict.fromkeys(shas))
    if len(shas) > MAX_SOURCES:
        raise ValueError(f"at most {MAX_SOURCES:,} sources per request")
    if bad := next((s for s in shas if not SHA.fullmatch(s)), None):
        raise ValueError(f"malformed source {bad[:80]}")
    return shas


async def proposals(db, investigation, shas) -> list[Statement]:
    """Current pending proposals read from these sources that nobody was asked to
    review yet. Raises PermissionError for a source outside the investigation."""
    if not isinstance(investigation, str) or not investigation:
        raise ValueError("investigation is required")
    inside = set(
        await db.scalars(
            select(Acquisition.blob_sha256).where(
                Acquisition.blob_sha256.in_(shas),
                Acquisition.context["investigation"].astext == investigation,
            )
        )
    )
    if len(inside) != len(shas):
        raise PermissionError("a source is not in this investigation")
    reach = select(DERIVED.c.derived).where(DERIVED.c.source.in_(shas))
    asked = aliased(Statement)
    q = (
        select(Statement)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            Statement.prop.in_(EXTRACTED_PROPS),
            StatementTier.tier == Tier.UNCONFIRMED,
            or_(
                Statement.span_blob.in_(shas),
                Statement.span_blob.in_(reach),
                and_(
                    Statement.span_blob.is_(None),
                    Statement.subject.in_([f"blob:{s}" for s in shas]),
                ),
            ),
            write.current_clause(),
            ~exists().where(
                asked.subject == func.concat("statement:", Statement.id),
                asked.prop == Prop.REVIEW_REQUEST,
            ),
        )
        .order_by(Statement.recorded_at, Statement.id)
        .limit(MAX_PROPOSALS + 1)
    )
    return list(await db.scalars(q))


async def request(db, user, item) -> dict:
    shas = _shas(item)
    found = await proposals(db, item.get("investigation"), shas)
    if len(found) > MAX_PROPOSALS:
        raise ValueError(
            f"more than {MAX_PROPOSALS} open proposals in this selection; select fewer"
            " sources"
        )
    if item.get("dry_run") is True:
        return {"proposals": len(found), "sources": len(shas)}
    reviewer = item.get("reviewer")
    if not isinstance(reviewer, int) or isinstance(reviewer, bool):
        raise ValueError("reviewer must be a user id")
    if not found:
        raise ValueError("these sources have no open proposals to review")
    await write.lock_chain(db)
    for st in found:
        # ponytail: one request per proposal, each with its own existence check;
        # bounded by MAX_PROPOSALS.
        await service.request_review(db, st, user, reviewer, item.get("reason"))
    await db.commit()
    n = len(found)
    await notify(
        db,
        reviewer,
        "assigned",
        "Evidence review requested",
        f"{n} proposal{'s' if n != 1 else ''} from {len(shas)} source"
        f"{'s' if len(shas) != 1 else ''}",
        "/review",
        None,
    )
    return {"requested": n, "sources": len(shas)}
