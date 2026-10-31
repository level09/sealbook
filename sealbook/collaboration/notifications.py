"""Stored notifications. Texts are titles and links only: items are stored by kind
and short id, never by value, so erasure has nothing to remove here. Listing and
the live push name the claim and its source at read time, so an erased source
drops out of the label."""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select, update
from stk.user.models import User
from stk.websocket import broadcast

from sealbook.collaboration.models import NOTIFICATION_KINDS, Notification

MAX_LIMIT = 100
MAX_IDS = 500


def short(ref):
    return str(ref).split(":")[-1][:8]


BARE = re.compile(r"(Proposal|Request) [0-9a-f]{8}")
PROP = {
    "name": "Name",
    "father_name": "Father's name",
    "mother_name": "Mother's name",
    "birth_date": "Birth date",
    "place_of_birth": "Birthplace",
    "arrested_at": "Arrested at",
    "last_seen": "Last seen",
    "detention_branch": "Held by branch",
    "transferred_to": "Transferred to",
    "seen_at": "Seen at",
    "detained_at": "Detained at",
    "document_date": "Document date",
    "document_type": "Document type",
    "reports": "Reports",
    "triage": "Triage",
    "title": "Title",
    "description": "Description line",
    "label": "Label",
}
# These read as a question or a thing, not as "<field> claim".
WHOLE = {
    "same_as": "Same person",
    "media_match": "Media match",
    "finding": "Finding",
    "relation": "Relation",
    "involved": "Person in an incident",
}


async def describe(db, subjects) -> dict[str, str]:
    """subject -> the claim a notification is about, as the Inbox labels assigned
    work: the field and its source's title, never the value, so erasure and
    sensitivity have nothing to hide. A request subject points at its proposal."""
    # Imported here: desk.data reaches back into collaboration.
    from sealbook.desk.data import files
    from sealbook.ledger.contracts import Prop
    from sealbook.ledger.models import Statement
    from sealbook.sources.data import _blob

    ids = {
        uuid.UUID(s.removeprefix("statement:"))
        for s in subjects
        if s and s.startswith("statement:")
    }
    if not ids:
        return {}
    first = {
        r.id: r
        for r in await db.execute(
            select(Statement.id, Statement.prop, Statement.subject).where(
                Statement.id.in_(ids)
            )
        )
    }
    target = {
        i: uuid.UUID(r.subject.removeprefix("statement:"))
        if r.prop == Prop.REVIEW_REQUEST
        else i
        for i, r in first.items()
    }
    claims = {
        r.id: r
        for r in await db.execute(
            select(Statement.id, Statement.prop, _blob(Statement).label("sha")).where(
                Statement.id.in_(set(target.values()))
            )
        )
    }
    titles = await files(db, [c.sha for c in claims.values()])
    out = {}
    for i, t in target.items():
        c = claims.get(t)
        if c is None:
            continue
        what = (
            WHOLE.get(c.prop)
            or f"{PROP.get(c.prop, c.prop.replace('_', ' ').capitalize())} claim"
        )
        out[f"statement:{i}"] = (
            f"{what} in {titles[c.sha]}" if c.sha in titles else what
        )
    return out


async def notify(db, user_id, kind, title, subtitle, link, subject=None):
    """Store, commit, then push live. The one path; the socket is only a nudge."""
    if kind not in NOTIFICATION_KINDS:
        raise ValueError(f"unknown notification kind {kind}")
    row = Notification(
        user_id=user_id,
        kind=kind,
        title=title,
        subtitle=subtitle,
        link=link,
        subject=subject,
    )
    db.add(row)
    await db.commit()
    # Stored bare so a later erasure has nothing to remove; named when it is shown.
    if subject and BARE.fullmatch(subtitle):
        subtitle = (await describe(db, [subject])).get(subject, subtitle)
    await broadcast(
        {
            "type": "notification",
            "id": str(row.id),
            "kind": kind,
            "title": title,
            "subtitle": subtitle,
            "to": link,
            "isSeen": False,
        },
        str(user_id),
    )
    return row


def unread_query(user_id):
    return select(func.count()).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)
    )


def cursor(n):
    return f"{n.created_at.isoformat()}|{n.id}"


def parse_cursor(raw):
    try:
        at, _, last = raw.partition("|")
        at = datetime.fromisoformat(at)
        return (at if at.tzinfo else at.replace(tzinfo=UTC)), uuid.UUID(last)
    except ValueError:
        raise ValueError("before must be a cursor from a previous page") from None


async def listing(db, user_id, unread, limit, before):
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be 1 to {MAX_LIMIT}")
    q = select(Notification).where(Notification.user_id == user_id)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    if before is not None:
        at, last = before
        q = q.where(
            or_(
                Notification.created_at < at,
                and_(Notification.created_at == at, Notification.id < last),
            )
        )
    rows = (
        (
            await db.execute(
                q.order_by(
                    Notification.created_at.desc(), Notification.id.desc()
                ).limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    named = await describe(
        db, [n.subject for n in rows[:limit] if BARE.fullmatch(n.subtitle or "")]
    )
    return {
        "items": [
            {
                "id": str(n.id),
                "kind": n.kind,
                "title": n.title,
                "subtitle": named.get(n.subject, n.subtitle),
                "to": n.link,
                "at": n.created_at.isoformat(),
                "isSeen": n.read_at is not None,
            }
            for n in rows[:limit]
        ],
        "next": cursor(rows[limit - 1]) if len(rows) > limit else None,
        "unread": await db.scalar(unread_query(user_id)),
    }


async def mark_read(db, user_id, ids):
    """ids: a list of notification ids, or None for all. Other people's are ignored."""
    q = update(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)
    )
    if ids is not None:
        q = q.where(Notification.id.in_(ids))
    await db.execute(q.values(read_at=datetime.now(UTC)))
    await db.commit()
    return await db.scalar(unread_query(user_id))


def mentioned(text, candidates):
    """Candidate users named as `@` + display name or email local part."""
    found = set()
    for user in candidates:
        for name in {user.name, user.email.split("@")[0]} - {None, ""}:
            if re.search(rf"(?<!\w)@{re.escape(name)}(?!\w)", text, re.IGNORECASE):
                found.add(user.id)
    return found


async def admins(db):
    users = (await db.execute(select(User).where(User.active.is_(True)))).scalars()
    return [u for u in users if u.has_role("admin")]


def parse_ids(raw):
    if not isinstance(raw, list) or len(raw) > MAX_IDS:
        raise ValueError(f"send a list of at most {MAX_IDS} ids")
    try:
        return [uuid.UUID(i) for i in raw]
    except (TypeError, ValueError, AttributeError):
        raise ValueError("ids must be notification ids") from None
