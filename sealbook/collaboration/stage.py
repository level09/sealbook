"""Where an item stands in the people's work, in the words Bayanat analysts use:
read by a machine, assigned, updated by its analyst, sent for peer review, returned,
finalized. Derived from the first pass, its review requests and the latest
assignment, so no second status can drift from the ledger."""

from sqlalchemy import Text, func, select
from sqlalchemy.orm import aliased
from stk.user.models import User

from sealbook.collaboration.models import Assignment
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Statement, StatementTier

# Done means its analyst finished it ("Updated" in Bayanat); returned means a
# reviewer sent it back.
WORDS = {"open": "Assigned", "done": "Updated", "returned": "Returned"}


async def stages(db, investigation: str | None, shas) -> dict[str, dict]:
    """{sha: {"stage", "assignment"}} for the items given, in a fixed number of
    queries."""
    shas = list(dict.fromkeys(shas))
    if not shas:
        return {}
    subjects = [f"blob:{s}" for s in shas]
    latest = (
        select(Assignment, func.coalesce(User.name, User.email))
        .join(User, User.id == Assignment.assignee_id)
        .where(Assignment.subject.in_(subjects))
        .distinct(Assignment.subject)
        .order_by(Assignment.subject, Assignment.updated_at.desc())
    )
    if investigation:
        latest = latest.where(Assignment.investigation == investigation)
    work = {a.subject: (a, name) for a, name in (await db.execute(latest)).all()}
    # The newest first pass on each item, and whether a peer review of it is open:
    # its triage line, or for a pass imported from Bayanat (which has no triage) its
    # title or a label.
    request = aliased(Statement)
    asked = (
        select(request.id)
        .where(
            request.prop == Prop.REVIEW_REQUEST,
            request.subject == func.concat("statement:", Statement.id.cast(Text)),
        )
        .exists()
    )
    passes = {
        subject: (tier, review)
        for subject, tier, review in (
            await db.execute(
                select(Statement.subject, StatementTier.tier, asked)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .where(
                    Statement.prop.in_((Prop.TRIAGE, Prop.TITLE, Prop.LABEL)),
                    Statement.subject.in_(subjects),
                )
                .distinct(Statement.subject)
                .order_by(
                    Statement.subject,
                    (Statement.prop == Prop.TRIAGE).desc(),
                    Statement.recorded_at.desc(),
                )
            )
        ).all()
    }
    out = {}
    for sha, subject in zip(shas, subjects, strict=True):
        a, name = work.get(subject, (None, None))
        tier, review = passes.get(subject, (None, False))
        if tier == Tier.ACCEPTED:
            stage = "Finalized"
        elif review and tier == Tier.UNCONFIRMED:
            stage = "Peer review"
        elif a is not None:
            stage = WORDS[a.status]
        elif tier is not None:
            stage = "Machine created"
        else:
            stage = "New"
        out[sha] = {
            "stage": stage,
            "assignment": a
            and {
                "id": str(a.id),
                "assignee": {"id": a.assignee_id, "name": name},
                "status": a.status,
                "priority": a.priority,
                "due": a.due and a.due.isoformat(),
            },
        }
    return out
