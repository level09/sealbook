import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from stk.collaboration.notifications import admins, mentioned, notify, short
from stk.ledger import write
from stk.ledger.blobmeta import span_check
from stk.ledger.contracts import EXTRACTED_PROPS, Prop, Refused, StatementIn, Tier
from stk.ledger.models import Blob, Payload, Principal, Statement, StatementTier
from stk.monitor import data
from stk.user.models import User


async def eligible(db, user_id):
    user = await db.get(User, user_id)
    if (
        user is None
        or not user.active
        or not any(user.has_role(r) for r in ("analyst", "admin"))
    ):
        raise ValueError("reviewer must be an active analyst")
    return user


async def requests(db, proposal_id):
    return (
        await db.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(
                Statement.subject == f"statement:{proposal_id}",
                Statement.prop == Prop.REVIEW_REQUEST,
            )
            .order_by(Statement.recorded_at)
        )
    ).all()


async def check_reviewer(db, statement, user):
    assigned = await requests(db, statement.id)
    if not assigned:
        return
    if (await db.get(Principal, statement.author_id)).user_id == user.id:
        raise PermissionError("another analyst must review this proposal")
    if not user.has_role("admin") and user.id not in {
        v["reviewer"] for _, v in assigned
    }:
        raise PermissionError("this proposal is assigned to another analyst")


async def request_review(db, proposal, user, reviewer_id, reason):
    await eligible(db, reviewer_id)
    if reviewer_id == user.id:
        raise ValueError("choose another analyst")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
        raise ValueError("reason must contain 1 to 4000 characters")
    if (await db.get(StatementTier, proposal.id)).tier != Tier.UNCONFIRMED:
        raise ValueError("only pending proposals can be assigned")
    if await db.get(Payload, proposal.commitment) is None:
        raise ValueError("an erased proposal cannot be assigned")
    if await requests(db, proposal.id):
        raise Refused("proposal already has a review request")
    who = await write.principal_for_user(db, user.id, user.name or user.email)
    return await write.append(
        db,
        StatementIn(
            subject=f"statement:{proposal.id}",
            prop=Prop.REVIEW_REQUEST,
            value={"reviewer": reviewer_id, "reason": reason.strip()},
            author_id=who.id,
        ),
    )


async def suggest(db, original, user, item):
    from stk.discovery.service import available

    if original.prop not in EXTRACTED_PROPS:
        raise ValueError("corrections require a source-backed fact")
    if await db.get(Payload, original.commitment) is None or not await db.scalar(
        select(available(Blob.sha256)).where(Blob.sha256 == original.span_blob)
    ):
        raise ValueError("an erased source cannot be used for a correction")
    value = item.get("value")
    if (
        not isinstance(value, dict)
        or set(value) != {"text", "lang"}
        or not all(
            isinstance(value[k], str) and value[k].strip() for k in ("text", "lang")
        )
        or len(value["text"]) > 8000
        or len(value["lang"]) > 20
    ):
        raise ValueError("value needs text and lang")
    source_span = item.get("span", original.span)
    ok, why = span_check(
        await db.get(Blob, original.span_blob) if original.span_blob else None,
        source_span,
    )
    if not ok:
        raise ValueError(f"invalid source span: {why}")
    who = await write.principal_for_user(db, user.id, user.name or user.email)
    return await write.append(
        db,
        StatementIn(
            subject=original.subject,
            prop=Prop(original.prop),
            value=value,
            author_id=who.id,
            span_blob=original.span_blob,
            span=source_span,
            supersedes=original.id,
            based_on=await write.subject_head(db, original.subject),
            valid_from=original.valid_from,
            valid_to=original.valid_to,
        ),
    )


async def task(db, request_id, user):
    row = await db.get(Statement, request_id)
    if row is None or row.prop != Prop.REVIEW_REQUEST:
        raise LookupError("review request not found")
    payload = await db.get(Payload, row.commitment)
    if payload is None:
        raise LookupError("review request was erased")
    sender = await db.get(Principal, row.author_id)
    if user.id not in (sender.user_id, payload.value["reviewer"]) and not user.has_role(
        "admin"
    ):
        raise PermissionError("review request is private to its participants")
    proposal_id = uuid.UUID(row.subject.removeprefix("statement:"))
    proposal = await data.card(db, proposal_id)
    statement = await db.get(Statement, proposal_id)
    try:
        await check_reviewer(db, statement, user)
        can_review = True
    except PermissionError:
        can_review = False
    comments = (
        await db.execute(
            select(Statement, Payload.value, Principal.name)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(Principal, Principal.id == Statement.author_id)
            .where(
                Statement.subject == f"statement:{row.id}",
                Statement.prop == Prop.REVIEW_COMMENT,
            )
            .order_by(Statement.recorded_at)
        )
    ).all()
    media = None
    if proposal["prop"] == Prop.MEDIA_MATCH:
        media = await data.media_pair(db, statement)
        if media is None:
            proposal["display"] = "A source was erased. This match cannot be used."
            proposal["invariants"].append(
                {
                    "name": "sources available",
                    "ok": False,
                    "note": "a source was erased",
                }
            )
    return {
        "id": str(row.id),
        "sender": {"id": sender.user_id, "name": sender.name},
        "reviewer": payload.value["reviewer"],
        "reason": payload.value["reason"],
        "proposal": proposal,
        "can_review": can_review,
        "media": media,
        "at": row.recorded_at.isoformat(),
        "comments": [
            {
                "id": str(st.id),
                "text": val["text"],
                "author": name,
                "at": st.recorded_at.isoformat(),
            }
            for st, val, name in comments
        ],
    }


def link(request_id):
    return f"/inbox?item=request:{request_id}"


async def assigned(db, reviewer_id, request_id, proposal_id):
    await notify(
        db,
        reviewer_id,
        "assigned",
        "Evidence review requested",
        f"Proposal {short(proposal_id)}",
        link(request_id),
        f"statement:{proposal_id}",
    )


async def reviewed(db, proposal_id):
    for row, value in await requests(db, proposal_id):
        sender = await db.get(Principal, row.author_id)
        for user_id in {sender.user_id, value["reviewer"]}:
            await notify(
                db,
                user_id,
                "decision",
                "Evidence review decided",
                f"Proposal {short(proposal_id)}",
                link(row.id),
                f"statement:{proposal_id}",
            )


async def comment(db, request_id, user, text):
    await write.lock_chain(db)
    found = await task(db, request_id, user)
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("comment must contain 1 to 4000 characters")
    who = await write.principal_for_user(db, user.id, user.name or user.email)
    await write.append(
        db,
        StatementIn(
            subject=f"statement:{request_id}",
            prop=Prop.REVIEW_COMMENT,
            value={"text": text.strip(), "at": datetime.now(UTC).isoformat()},
            author_id=who.id,
        ),
    )
    return found


async def comment_notices(db, request_id, found, user, text):
    """Replies go to the thread's people; an @mention goes only to someone who may
    see the request (its sender, reviewer and admins) and replaces the reply."""
    authors = await db.execute(
        select(Principal.user_id)
        .join(Statement, Statement.author_id == Principal.id)
        .where(
            Statement.subject == f"statement:{request_id}",
            Statement.prop == Prop.REVIEW_COMMENT,
        )
    )
    thread = {found["sender"]["id"], found["reviewer"], *authors.scalars()} - {None}
    users = {
        u.id: u
        for u in [await db.get(User, i) for i in thread] + await admins(db)
        if u is not None and u.active
    }
    named = mentioned(text, users.values())
    for user_id in (thread | named) & users.keys() - {user.id}:
        mention = user_id in named
        await notify(
            db,
            user_id,
            "mention" if mention else "reply",
            "You were mentioned in a review" if mention else "New review comment",
            f"Request {short(request_id)}",
            link(request_id),
            f"statement:{request_id}",
        )
