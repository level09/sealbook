"""The Desk: a case at a glance. Each table is paged in SQL and labelled in one batch
per page, never a query per row. Rows name sources by file name and claims by kind,
never by value, so nothing here needs a reveal or survives an erasure."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, case, cast, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import UUID, array_agg
from sqlalchemy.orm import aliased
from stk.user.models import User

from sealbook.collaboration.models import Assignment, Notification
from sealbook.collaboration.notifications import BARE, describe
from sealbook.discovery import service
from sealbook.discovery.models import FindingKey
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Principal,
    Run,
    Statement,
    StatementTier,
)
from sealbook.people import data as people
from sealbook.places.models import Place
from sealbook.review.findings import band
from sealbook.sources import data as sources
from sealbook.sources.data import DERIVED, _blob, title

# Bookkeeping, not work: reads, fingerprints and hashes would drown the changes.
QUIET = (Prop.ACCESS, Prop.FINGERPRINT, Prop.SHA256, Prop.MD5, Prop.DERIVED_FROM)
ACQUIRED = "acquisition"
MAX_PAGE = 100
SAMPLES = 3


def _paging(page, per_page):
    if page < 1 or not 1 <= per_page <= MAX_PAGE:
        raise ValueError(f"page starts at 1; per_page is 1 to {MAX_PAGE}")
    return (page - 1) * per_page


def in_case(sha, investigation):
    if not investigation:
        return true()
    return sha.in_(
        select(Acquisition.blob_sha256).where(
            Acquisition.context["investigation"].astext == investigation
        )
    )


def _ref(st):
    """The statement a review, request or comment is about."""
    return case(
        (st.subject.startswith("statement:"), cast(func.substr(st.subject, 11), UUID))
    )


def _mine(user_id):
    return select(Principal.id).where(Principal.user_id == user_id)


async def files(db, shas) -> dict[str, str]:
    """Title per available source; erased sources are left out."""
    return {sha: title(*rest) for sha, *rest, _ in await _named(db, shas)}


async def _named(db, shas):
    """(sha, given title, file key, doc type, uploaded file name) per available
    source. The file key is the importer's handle for matching; only the uploaded
    file name is something a reader can check."""
    shas = sorted({s for s in shas if s})
    if not shas:
        return []
    q = (
        select(
            Acquisition.blob_sha256,
            Acquisition.context["title"].astext,
            func.coalesce(
                Acquisition.context["file"].astext,
                Acquisition.context["filename"].astext,
            ),
            Acquisition.doc_type,
            Acquisition.context["filename"].astext,
        )
        .join(Blob, Blob.sha256 == Acquisition.blob_sha256)
        .where(Acquisition.blob_sha256.in_(shas), service.available(Blob.sha256))
        .distinct(Acquisition.blob_sha256)
        .order_by(Acquisition.blob_sha256, Acquisition.at)
    )
    return (await db.execute(q)).all()


async def since(db, user) -> tuple[datetime, str]:
    """When the analyst last left: their last logged access before this sign-in,
    else the previous sign-in, else a week ago. Login times are naive UTC."""
    began = user.current_login_at and user.current_login_at.replace(tzinfo=UTC)
    if began:
        left = await db.scalar(
            select(func.max(Statement.recorded_at)).where(
                Statement.prop == Prop.ACCESS,
                Statement.author_id.in_(_mine(user.id)),
                Statement.recorded_at < began,
            )
        )
        if left:
            return left, "access"
    if user.last_login_at:
        return user.last_login_at.replace(tzinfo=UTC), "login"
    return datetime.now(UTC) - timedelta(days=7), "week"


async def changes(db, user, investigation, page, per_page) -> dict:
    """What other people and agents did since the analyst left, one row per actor
    and kind of work, newest first."""
    offset = _paging(page, per_page)
    at, basis = await since(db, user)
    s, t, t2 = Statement, aliased(Statement), aliased(Statement)
    blob = func.coalesce(_blob(s), _blob(t), _blob(t2))
    derived = DERIVED
    # A decision reads as what it decided: "accepted 4 claims", not "decided".
    verdict = Payload.value["verdict"].astext
    what = case(
        (s.prop == Prop.REVIEW, func.coalesce("review_" + verdict, s.prop)),
        else_=s.prop,
    )
    said = (
        select(
            s.author_id.label("who"),
            what.label("what"),
            func.coalesce(derived.c.source, blob).label("sha"),
            s.recorded_at.label("at"),
        )
        .select_from(s)
        .outerjoin(t, t.id == _ref(s))
        .outerjoin(t2, t2.id == _ref(t))
        .outerjoin(derived, derived.c.derived == blob)
        .outerjoin(
            Payload, (Payload.commitment == s.commitment) & (s.prop == Prop.REVIEW)
        )
        .where(
            s.recorded_at > at,
            s.prop.not_in(QUIET),
            s.author_id.not_in(_mine(user.id)),
        )
    )
    acquired = select(
        Acquisition.collector_id,
        literal(ACQUIRED),
        Acquisition.blob_sha256,
        Acquisition.at,
    ).where(Acquisition.at > at, Acquisition.collector_id.not_in(_mine(user.id)))
    ev = said.union_all(acquired).cte("events")
    grouped = (
        select(
            ev.c.who,
            ev.c.what,
            func.count().label("n"),
            func.count(func.distinct(ev.c.sha)).label("items"),
            func.max(ev.c.at).label("last"),
            array_agg(func.distinct(ev.c.sha))[1:SAMPLES].label("sample"),
        )
        .where(in_case(ev.c.sha, investigation))
        .group_by(ev.c.who, ev.c.what)
        .subquery("grouped")
    )
    rows = (
        await db.execute(
            select(grouped, Principal.name, Principal.kind, func.count().over())
            .join(Principal, Principal.id == grouped.c.who)
            .order_by(grouped.c.last.desc(), grouped.c.what)
            .offset(offset)
            .limit(per_page)
        )
    ).all()
    names = await files(db, [sha for r in rows for sha in r.sample or ()])
    return {
        "since": at.isoformat(),
        "basis": basis,
        "items": [
            {
                "who": {"id": str(r.who), "name": r.name, "agent": r.kind == "agent"},
                "what": r.what,
                "n": r.n,
                "items": r.items,
                "at": r.last.isoformat(),
                "sources": [
                    {"sha256": sha, "title": names[sha]}
                    for sha in r.sample or ()
                    if sha in names
                ],
            }
            for r in rows
        ],
        "total": rows[0][-1] if rows else 0,
    }


async def _concat(db, parts, offset, per_page):
    """Page across lists shown one after another: count each, fetch only the slice
    that falls on this page."""
    totals = [
        await db.scalar(select(func.count()).select_from(q.subquery()))
        for q, _ in parts
    ]
    items, start = [], offset
    for (q, shape), n in zip(parts, totals, strict=True):
        if start < n and len(items) < per_page:
            items += await shape(
                (await db.execute(q.offset(start).limit(per_page - len(items)))).all()
            )
        start = max(0, start - n)
    return items, sum(totals)


async def needs(db, user, investigation, page, per_page) -> dict:
    """Review requests the analyst is in (asked of them first), unread mentions and
    replies, then open work assigned to them."""
    offset = _paging(page, per_page)
    me = user.id

    rq, proposal, sender = Statement, aliased(Statement), aliased(Principal)
    reviewer = cast(Payload.value["reviewer"].astext, Integer)
    asks_me = reviewer == me
    reviewer_user = aliased(User)
    requests = (
        select(
            rq.id,
            rq.recorded_at,
            Payload.value["reason"].astext.label("reason"),
            asks_me.label("mine"),
            sender.name.label("sender"),
            func.coalesce(reviewer_user.name, reviewer_user.email).label("reviewer"),
            proposal.prop,
            _blob(proposal).label("sha"),
        )
        .join(Payload, Payload.commitment == rq.commitment)
        .join(sender, sender.id == rq.author_id)
        .join(proposal, proposal.id == _ref(rq))
        .join(StatementTier, StatementTier.statement_id == proposal.id)
        .outerjoin(reviewer_user, reviewer_user.id == reviewer)
        .where(
            rq.prop == Prop.REVIEW_REQUEST,
            StatementTier.tier == Tier.UNCONFIRMED,
            or_(asks_me, sender.user_id == me),
            in_case(_blob(proposal), investigation),
        )
        .order_by(asks_me.desc(), rq.recorded_at.desc(), rq.id)
    )

    async def shape_requests(rows):
        names = await files(db, [r.sha for r in rows])
        return [
            {
                "kind": "review",
                "id": str(r.id),
                "prop": r.prop,
                "source": {"sha256": r.sha, "title": names.get(r.sha)}
                if r.sha
                else None,
                "detail": f"{r.sender}: {r.reason}" if r.reason else r.sender,
                "status": "asks you" if r.mine else f"waiting on {r.reviewer}",
                "mine": r.mine,
                "href": f"/review?request={r.id}",
                "at": r.recorded_at.isoformat(),
            }
            for r in rows
        ]

    req, about = aliased(Statement), aliased(Statement)
    mentions = select(Notification).where(
        Notification.user_id == me,
        Notification.kind.in_(("mention", "reply")),
        Notification.read_at.is_(None),
    )
    if investigation:
        mentions = (
            mentions.join(
                req, cast(func.substr(Notification.subject, 11), UUID) == req.id
            )
            .join(about, about.id == _ref(req))
            .where(
                Notification.subject.startswith("statement:"),
                in_case(_blob(about), investigation),
            )
        )
    mentions = mentions.order_by(Notification.created_at.desc(), Notification.id)

    async def shape_mentions(rows):
        # Stored as "Request 1fbaac41"; named at read time as the bell names them.
        named = await describe(
            db, [n.subject for (n,) in rows if BARE.fullmatch(n.subtitle or "")]
        )
        return [
            {
                "kind": n.kind,
                "id": str(n.id),
                "title": n.title,
                "detail": named.get(n.subject, n.subtitle),
                "status": "mentions you" if n.kind == "mention" else "new reply",
                "mine": n.kind == "mention",
                "href": n.link,
                "at": n.created_at.isoformat(),
            }
            for (n,) in rows
        ]

    work = select(Assignment).where(
        Assignment.assignee_id == me, Assignment.status == "open"
    )
    if investigation:
        work = work.where(Assignment.investigation == investigation)
    work = work.order_by(
        Assignment.due.asc().nulls_last(), Assignment.updated_at.desc(), Assignment.id
    )

    async def shape_work(rows):
        found = [a for (a,) in rows]
        by = {"blob": [], "statement": [], "finding": []}
        for a in found:
            kind, _, ref = a.subject.partition(":")
            by[kind].append(ref)
        claims = {}
        if by["statement"]:
            q = select(Statement.id, Statement.prop, _blob(Statement)).where(
                Statement.id.in_([uuid.UUID(i) for i in by["statement"]])
            )
            claims = {str(i): (p, sha) for i, p, sha in await db.execute(q)}
        found_kinds = {}
        if by["finding"]:
            q = select(FindingKey.key, FindingKey.kind).where(
                FindingKey.key.in_(by["finding"])
            )
            found_kinds = dict((await db.execute(q)).all())
        names = await files(db, by["blob"] + [sha for _, sha in claims.values() if sha])
        out = []
        for a in found:
            kind, _, ref = a.subject.partition(":")
            item = {
                "kind": "assignment",
                "id": str(a.id),
                "subject": kind,
                "status": f"due {a.due.isoformat()}" if a.due else "assigned",
                "priority": a.priority,
                "mine": False,
                "at": a.updated_at.isoformat(),
                "investigation": a.investigation,
            }
            if kind == "blob":
                item |= {"source": {"sha256": ref, "title": names.get(ref)}}
            elif kind == "statement":
                prop, sha = claims.get(ref, (None, None))
                item |= {
                    "prop": prop,
                    "source": {"sha256": sha, "title": names.get(sha)} if sha else None,
                }
            else:
                item |= {"finding": found_kinds.get(ref)}
            out.append(item)
        return out

    items, total = await _concat(
        db,
        [(requests, shape_requests), (mentions, shape_mentions), (work, shape_work)],
        offset,
        per_page,
    )
    asks = await db.scalar(
        select(func.count()).select_from(
            requests.where(asks_me).order_by(None).subquery()
        )
    )
    return {"items": items, "total": total, "asks": asks}


async def findings(db, investigation, page, per_page) -> dict:
    """Open discovery findings, strongest first, with their places by name."""
    offset = _paging(page, per_page)
    q = (
        select(FindingKey, Payload.value)
        .outerjoin(Statement, Statement.id == FindingKey.statement_id)
        .outerjoin(Payload, Payload.commitment == Statement.commitment)
        .where(FindingKey.state == "open")
    )
    ran = select(func.count()).where(Run.tool == "discover")
    if investigation:
        q = q.where(FindingKey.investigation == investigation)
        ran = ran.where(Run.inputs["investigation"].astext == investigation)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = (
        await db.execute(
            q.order_by(
                FindingKey.rank.desc(), FindingKey.created_at.desc(), FindingKey.key
            )
            .offset(offset)
            .limit(per_page)
        )
    ).all()
    place_ids = {
        uuid.UUID(p.removeprefix("place:"))
        for _, v in rows
        for p in (v or {}).get("places", ())
    }
    places = {}
    if place_ids:
        places = {
            f"place:{i}": en or ar
            for i, en, ar in await db.execute(
                select(Place.id, Place.name_en, Place.name_ar).where(
                    Place.id.in_(place_ids)
                )
            )
        }
    named = await people.names(
        db,
        sorted(
            {
                p.removeprefix("person:")
                for _, v in rows
                for p in (v or {}).get("people", ())
            }
        ),
    )
    items = [
        {
            "key": fk.key,
            "kind": fk.kind,
            "investigation": fk.investigation,
            "rank": fk.rank,
            "band": band(fk.kind, fk.features or {}),
            "features": fk.features or {},
            "members": len(fk.members or ()),
            "people": [p.removeprefix("person:") for p in (v or {}).get("people", ())],
            "places": [places.get(p) for p in (v or {}).get("places", ())],
            "place_ids": [
                p.removeprefix("place:") for p in (v or {}).get("places", ())
            ],
            "month": (v or {}).get("month"),
            "at": fk.created_at.isoformat(),
        }
        for fk, v in rows
    ]

    # Two kinds over the same people, places and month are one set read twice.
    def same(i):
        return (tuple(sorted(i["people"])), tuple(sorted(i["place_ids"])), i["month"])

    for i in items:
        i["also"] = next(
            (j["kind"] for j in items if j["kind"] != i["kind"] and same(j) == same(i)),
            None,
        )
    return {
        "names": named,
        "items": items,
        "total": total,
        "ran": bool(await db.scalar(ran)),
    }


def mark_new(items, at: datetime):
    for r in items:
        r["new"] = datetime.fromisoformat(r["acquired_at"]) > at
    return items


async def recent(db, user, f: dict) -> dict:
    """The Sources listing for the page, plus case and newness the Desk shows."""
    out = await sources.listing(db, f)
    shas = [r["sha256"] for r in out["items"]]
    if shas:
        cases = dict(
            (
                await db.execute(
                    select(
                        Acquisition.blob_sha256,
                        func.min(Acquisition.context["investigation"].astext),
                    )
                    .where(Acquisition.blob_sha256.in_(shas))
                    .group_by(Acquisition.blob_sha256)
                )
            ).all()
        )
        names = {
            sha: (title(*rest), filename)
            for sha, *rest, filename in await _named(db, shas)
        }
        for r in out["items"]:
            r["investigation"] = cases.get(r["sha256"])
            r["title"], file = names.get(r["sha256"], (r["title"], None))
            r["file"] = file if file != r["title"] else None
    at, _ = await since(db, user)
    mark_new(out["items"], at)
    return out
