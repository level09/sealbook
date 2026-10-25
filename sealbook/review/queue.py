"""Read side of the review workspace: each pending claim as a sentence a reviewer can
decide, with its source, location, proposer, request and due date, in a fixed number
of queries per page.

Masking follows the monitor and evidence page: personal values from a sensitive
source stay masked unless this session holds the logged reveal of that source."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from stk.user.models import User

from sealbook.collaboration.models import Assignment
from sealbook.discovery import evidence
from sealbook.discovery.models import FindingKey, Segment
from sealbook.ledger.contracts import PRIVATE_PROPS, Prop, Tier
from sealbook.ledger.models import (
    Blob,
    Payload,
    Principal,
    Run,
    Statement,
    StatementTier,
)
from sealbook.monitor import data
from sealbook.monitor import filter as mfilter
from sealbook.people import data as people
from sealbook.review import findings

BOXES = ("mine", "sent", "open")
PENDING = (Tier.UNCONFIRMED, Tier.CORROBORATED)
HIDDEN = data.META | PRIVATE_PROPS
IDENTITY = (Prop.SAME_AS, Prop.NOT_SAME)
# Proposals between two things, shown with the statements they rest on.
LINKS = (*IDENTITY, Prop.RELATION, Prop.INVOLVED)
LIMIT = 200
RELATED = 20
# Value keys that are bookkeeping, never shown as a field of an unknown prop.
QUIET = {"at", "gesture", "basis", "lang", "fingerprint", "why"}


def _ms(v: int) -> str:
    s = round(v / 1000)
    return f"{s // 60:02d}:{s % 60:02d}"


def _col(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _ref(v) -> str:
    """A subject ref as words, never a raw uuid."""
    kind, _, rest = str(v).partition(":")
    return f"{kind} {rest[:8]}" if rest else str(v)


def fields(value) -> list[list[str]]:
    """An unknown prop's value as labelled pairs, for the fallback list."""
    if not isinstance(value, dict):
        return [] if value is None else [["value", str(value)]]
    out = []
    for k, v in value.items():
        if k in QUIET or isinstance(v, (dict, list)):
            continue
        out.append([k.replace("_", " "), _ref(v) if ":" in str(v) else str(v)])
    return out


def where(span, sha, source, segments, timelines) -> dict:
    """The place in the source a reader is sent to: page, second, cell or region."""
    if not span:
        return {"label": "the whole item"}
    if "bbox" in span:
        return {
            "label": f"region on page {span.get('page', 1)}",
            "page": span.get("page", 1),
        }
    if "ms" in span:
        a, b = span["ms"]
        return {"label": f"{_ms(a)} to {_ms(b)}", "ms": [a, b]}
    if "row" in span:
        return {
            "label": f"row {span['row']}, cell {_col(span['column'])}{span['row']}",
            "row": span["row"],
        }
    if "start" in span:
        if sha != source and source in timelines:
            line = [t for t in timelines[source] if t[0] <= span["start"]]
            if line:
                return {"label": f"at {_ms(line[-1][1])}", "ms": line[-1][1:3]}
        page = segments.get(sha)
        if page:
            return {"label": f"page {page}", "page": page}
        return {"label": f"characters {span['start']} to {span['end']}"}
    return {"label": "the whole item"}


class People:
    """Users and principals behind requests, assignments and authors, as the
    identity an actor chip shows: kind, name, role, and run detail for agents."""

    def __init__(self, users: dict, ctx: data.Context):
        self.users, self.ctx = users, ctx

    @staticmethod
    def role(user) -> str | None:
        names = {r.name for r in user.roles}
        return (
            "admin" if "admin" in names else "analyst" if "analyst" in names else None
        )

    def user(self, uid) -> dict | None:
        u = self.users.get(uid)
        if u is None:
            return None
        return {
            "kind": "human",
            "name": u.name or u.email,
            "role": self.role(u),
            "key": f"user:{u.id}",
            "user_id": u.id,
        }

    def author(self, author_id, run_id) -> dict:
        actor = self.ctx.actor(author_id, run_id)
        p, run = self.ctx.principals[author_id], self.ctx.runs.get(run_id)
        if actor["kind"] == "human":
            return self.user(p.user_id) or {
                "kind": "human",
                "name": p.name,
                "role": None,
                "key": f"principal:{p.id}",
            }
        if actor["kind"] == "agent":
            name, detail = p.name, run.model_served if run else None
        else:
            name, detail = actor["label"], actor["detail"]
        return {
            "kind": actor["kind"],
            "name": name,
            "role": f"for {actor['for']}" if actor.get("for") else None,
            "detail": detail,
            "key": f"principal:{p.id}",
        }


async def ids(db, user, box: str, investigation: str | None) -> list[uuid.UUID]:
    """Statement ids in one box, in the order the list shows them before the client
    sorts by due date."""
    if box not in BOXES:
        raise ValueError(f"box is one of {', '.join(BOXES)}")
    case = mfilter.statement_clause({"investigation": investigation, "time": None})
    pending = StatementTier.tier.in_(PENDING)
    if box == "open":
        used = data._used()
        q = (
            select(Statement.id)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                StatementTier.tier == Tier.UNCONFIRMED,
                Statement.prop.not_in(HIDDEN | {Prop.FINDING}),
                case,
            )
            .order_by(used.is_(None), Statement.recorded_at)
            .limit(LIMIT)
        )
        return list(await db.scalars(q))
    target = Statement.__table__.alias("target")
    req = (
        select(target.c.id, Statement.recorded_at)
        .select_from(Statement)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(Principal, Principal.id == Statement.author_id)
        .join(target, func.concat("statement:", target.c.id) == Statement.subject)
        .join(StatementTier, StatementTier.statement_id == target.c.id)
        .where(Statement.prop == Prop.REVIEW_REQUEST)
    )
    asg = select(Assignment.subject, Assignment.created_at).where(
        Assignment.status == "open", Assignment.subject.startswith("statement:")
    )
    if box == "mine":
        req = req.where(Payload.value["reviewer"].as_integer() == user.id, pending)
        asg = asg.where(Assignment.assignee_id == user.id)
    else:
        req = req.where(Principal.user_id == user.id)
        asg = asg.where(Assignment.assigned_by == user.id)
    found = {r[0]: r[1] for r in await db.execute(req.limit(LIMIT))}
    for subject, at in await db.execute(asg.limit(LIMIT)):
        found.setdefault(uuid.UUID(subject.removeprefix("statement:")), at)
    if not found:
        return []
    keep = (
        select(Statement.id)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(Statement.id.in_(found), Statement.prop.not_in(HIDDEN))
    )
    if box == "mine":
        keep = keep.where(pending)
    if investigation:
        keep = keep.where(case)
    kept = set(await db.scalars(keep))
    return sorted(kept, key=lambda i: found[i], reverse=True)


async def rows(db, user, wanted: list[uuid.UUID]) -> list[dict]:
    """Each statement in `wanted` as a review item, in that order."""
    if not wanted:
        return []
    src = mfilter.source(mfilter.statement_blob())
    found = {
        st.id: (st, tier, reason, value, source)
        for st, tier, reason, value, source in await db.execute(
            select(
                Statement,
                StatementTier.tier,
                StatementTier.reason,
                Payload.value,
                src,
            )
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .outerjoin(Payload, Payload.commitment == Statement.commitment)
            .where(Statement.id.in_(wanted), Statement.prop.not_in(HIDDEN))
        )
    }
    order = [i for i in wanted if i in found]
    persons = set()
    for st, _, _, value, _ in found.values():
        if st.subject.startswith("person:"):
            persons.add(st.subject)
        if st.prop in LINKS and value and "person" in value:
            persons.add(f"person:{value['person'].removeprefix('person:')}")
    names: dict[str, tuple] = {}
    if persons:
        for subject, value, tier, source in await db.execute(
            select(Statement.subject, Payload.value, StatementTier.tier, src)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == Prop.NAME,
                Statement.subject.in_(persons),
                StatementTier.tier != Tier.REJECTED,
            )
            .order_by(Statement.recorded_at)
        ):
            # An accepted name wins over a pending one; otherwise the first read.
            if subject not in names or (
                tier == Tier.ACCEPTED and names[subject][1] != Tier.ACCEPTED
            ):
                names[subject] = (value, tier, source)
    shas = {r[4] for r in found.values() if r[4]} | {
        n[2] for n in names.values() if n[2]
    }
    media = {
        st.id: value["blob"]
        for st, _, _, value, _ in found.values()
        if st.prop in (Prop.MEDIA_MATCH, Prop.RELATION) and value and "blob" in value
    }
    shas |= set(media.values())
    ctx = await data.Context.load(
        db,
        blobs=shas,
        runs=[r[0].run_id for r in found.values()],
        principals=[r[0].author_id for r in found.values()],
    )
    mimes = (
        dict(
            (
                await db.execute(
                    select(Blob.sha256, Blob.mime).where(Blob.sha256.in_(shas))
                )
            ).all()
        )
        if shas
        else {}
    )
    derived = {
        r[0].span_blob
        for r in found.values()
        if r[0].span_blob and r[4] and r[0].span_blob != r[4]
    }
    segments = (
        {
            t: loc.get("page")
            for t, loc in await db.execute(
                select(Segment.text_sha256, Segment.location).where(
                    Segment.text_sha256.in_(derived)
                )
            )
        }
        if derived
        else {}
    )
    timelines = {
        sha: await evidence.transcript_timeline(db, sha)
        for sha in {r[4] for r in found.values() if r[0].span_blob in derived}
        if mimes.get(sha, "").startswith(("audio/", "video/"))
    }
    subjects = [f"statement:{i}" for i in order]
    requests = {}
    for st, value, sender, sender_name in await db.execute(
        select(Statement, Payload.value, Principal.user_id, Principal.name)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(Principal, Principal.id == Statement.author_id)
        .where(Statement.prop == Prop.REVIEW_REQUEST, Statement.subject.in_(subjects))
        .order_by(Statement.recorded_at)
    ):
        requests.setdefault(st.subject, (st, value, sender, sender_name))
    comments = (
        dict(
            (
                await db.execute(
                    select(Statement.subject, func.count())
                    .where(
                        Statement.prop == Prop.REVIEW_COMMENT,
                        Statement.subject.in_(
                            [f"statement:{r[0].id}" for r in requests.values()]
                        ),
                    )
                    .group_by(Statement.subject)
                )
            ).all()
        )
        if requests
        else {}
    )
    assigned = {
        a.subject: a
        for a in await db.scalars(
            select(Assignment).where(
                Assignment.subject.in_(subjects), Assignment.status == "open"
            )
        )
    }
    uids = {user.id}
    for _, value, sender, _ in requests.values():
        uids |= {value["reviewer"], sender}
    for a in assigned.values():
        uids |= {a.assignee_id, a.assigned_by}
    uids |= {p.user_id for p in ctx.principals.values() if p.user_id}
    users = {
        u.id: u
        for u in await db.scalars(select(User).where(User.id.in_(uids - {None})))
    }
    who = People(users, ctx)
    # Any spelling the reader may already see labels a person whose name here is
    # masked (people.names never uses a masked or erased name).
    readable_names = (
        await people.names(db, [p.removeprefix("person:") for p in persons])
        if persons
        else {}
    )
    can = await evidence.reviewable(db, [str(i) for i in order], user)
    findings = (
        dict(
            (
                await db.execute(
                    select(FindingKey.statement_id, FindingKey.key).where(
                        FindingKey.statement_id.in_(order)
                    )
                )
            ).all()
        )
        if any(found[i][0].prop == Prop.FINDING for i in order)
        else {}
    )
    admin = user.has_role("admin")
    involved = {found[i][0].subject for i in order if found[i][0].prop == Prop.INVOLVED}
    incidents = (
        {
            subject: data.display(value.get("title"))
            for subject, value in await db.execute(
                select(Statement.subject, Payload.value)
                .join(Payload, Payload.commitment == Statement.commitment)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .where(
                    Statement.subject.in_(involved),
                    Statement.prop == Prop.INCIDENT,
                    StatementTier.tier == Tier.ACCEPTED,
                )
            )
        }
        if involved
        else {}
    )

    def shown(prop, value, source) -> dict:
        doc = ctx.doc(source) if source else None
        masked = (
            prop in data.PERSONAL
            and (doc is None or doc["sensitive"])
            and not (source and evidence.granted(source))
        )
        return {
            "text": None if masked else data.display(value, prop),
            "masked": masked and value is not None,
            "erased": value is None,
        }

    def person(subject) -> dict:
        name = names.get(subject)
        out = {"id": subject.removeprefix("person:")}
        if name is None:
            return out | {"name": None, "masked": False}
        v = shown(Prop.NAME, name[0], name[2])
        if v["masked"] and out["id"] in readable_names:
            return out | {"name": readable_names[out["id"]], "masked": False}
        return out | {"name": v["text"], "masked": v["masked"]}

    def source_of(sha) -> dict | None:
        if not sha:
            return None
        doc = ctx.doc(sha)
        acqs = ctx.acqs.get(sha) or []
        mime = mimes.get(sha, "")
        return {
            "sha256": sha,
            "file": doc["title"],
            "mime": mime,
            "kind": evidence.kind_of(mime),
            "doc_type": doc["doc_type"],
            "sensitive": doc["sensitive"],
            "revealed": evidence.granted(sha) or not doc["sensitive"],
            "origins": doc["origins"],
            "investigation": acqs[0].context.get("investigation") if acqs else None,
        }

    out = []
    for i in order:
        st, tier, reason, value, source = found[i]
        key = f"statement:{st.id}"
        lo, hi = st.valid_from, st.valid_to or st.valid_from
        if lo is None and source in ctx.doc_dates:
            lo, hi = ctx.doc_dates[source]
        item = {
            "id": str(st.id),
            "prop": st.prop,
            "subject": st.subject,
            "subject_kind": st.subject.partition(":")[0],
            "person": person(st.subject) if st.subject.startswith("person:") else None,
            "value": shown(st.prop, value, source),
            "fields": [] if st.prop in data.PERSONAL else fields(value),
            "from": lo.isoformat() if lo else None,
            "to": hi.isoformat() if hi else None,
            "inherited": st.valid_from is None and lo is not None,
            "tier": tier,
            "tier_reason": reason,
            "proposer": who.author(st.author_id, st.run_id),
            "agent": ctx.principals[st.author_id].kind == "agent",
            "run_id": str(st.run_id) if st.run_id else None,
            "evidence": st.evidence or [],
            "source": source_of(source),
            "span_blob": st.span_blob,
            "span": st.span,
            "where": where(st.span, st.span_blob, source, segments, timelines),
            "supersedes": str(st.supersedes) if st.supersedes else None,
            "can_review": can.get(str(st.id), False),
            "recorded_at": st.recorded_at.isoformat(),
            "request": None,
            "assignment": None,
            "due": None,
        }
        if st.prop in LINKS and value and "person" in value:
            other = person(f"person:{value['person'].removeprefix('person:')}")
            item["identity" if st.prop in IDENTITY else "other"] = other
        if st.prop == Prop.INVOLVED and value:
            item["incident"] = {
                "id": st.subject.removeprefix("incident:"),
                "title": incidents.get(st.subject),
            }
        if st.prop == Prop.RELATION and value:
            item["relation"] = value.get("kind")
        if st.id in media and st.prop == Prop.RELATION:
            item["document"] = source_of(media[st.id])
        elif st.id in media:
            item["media"] = {
                "other": source_of(media[st.id]),
                "sound": value.get("sound"),
                "picture": value.get("picture"),
            }
        if st.id in findings:
            item["finding"] = findings[st.id]
        if key in requests:
            r, rv, sender, sender_name = requests[key]
            visible = admin or user.id in (sender, rv["reviewer"])
            item["request"] = {
                "id": str(r.id),
                "visible": visible,
                "at": r.recorded_at.isoformat(),
                "mine": rv["reviewer"] == user.id,
                "sent": sender == user.id,
            } | (
                {
                    "by": who.user(sender)
                    or {"kind": "human", "name": sender_name, "key": str(sender)},
                    "to": who.user(rv["reviewer"]),
                    "reason": rv["reason"],
                    "comments": comments.get(f"statement:{r.id}", 0),
                }
                if visible
                else {}
            )
        if key in assigned:
            a = assigned[key]
            item["assignment"] = {
                "id": str(a.id),
                "to": who.user(a.assignee_id),
                "by": who.user(a.assigned_by),
                "due": a.due.isoformat() if a.due else None,
                "priority": a.priority,
            }
            item["due"] = item["assignment"]["due"]
        out.append(item)
    return out


async def counts(db, user, investigation) -> dict:
    return {b: len(await ids(db, user, b, investigation)) for b in BOXES}


async def _basis_ids(db, st: Statement, value) -> list[uuid.UUID]:
    refs = (value or {}).get("basis")
    if not refs and st.run_id:
        run = await db.get(Run, st.run_id)
        refs = (run.inputs or {}).get("basis") if run else None
    return [uuid.UUID(r.removeprefix("statement:")) for r in refs or []]


def compare(a: list[dict], b: list[dict]) -> list[dict]:
    """Field by field across the two people of an identity proposal: agree, differ,
    or unknown when a side is masked or missing. Pure."""
    props = list(dict.fromkeys(r["prop"] for r in a + b))
    out = []
    for p in props:
        left = [r for r in a if r["prop"] == p]
        right = [r for r in b if r["prop"] == p]
        texts = [
            {(r["value"]["text"] or "").strip() for r in side if r["value"]["text"]}
            for side in (left, right)
        ]
        masked = any(r["value"]["masked"] for r in left + right)
        if not left or not right or masked or not all(texts):
            state = "unknown"
        else:
            state = "agree" if texts[0] & texts[1] else "differ"
        out.append({"prop": p, "a": left, "b": right, "state": state})
    return out


async def claim(db, user, sid: uuid.UUID) -> dict | None:
    """One claim as the decision pane shows it: the list item plus checks, run
    provenance, other claims about the same person and prop, the thread, and for
    identity proposals the basis compared field by field."""
    found = await rows(db, user, [sid])
    if not found:
        return None
    item = found[0]
    st = await db.get(Statement, sid)
    value = await db.scalar(
        select(Payload.value).where(Payload.commitment == st.commitment)
    )
    card = await data.card(db, sid)
    item["checks"] = card["invariants"]
    run = await db.get(Run, st.run_id) if st.run_id else None
    item["run"] = (
        {
            "id": str(run.id),
            "tool": run.tool,
            "route": run.route,
            "model_requested": run.model_requested,
            "model_served": run.model_served,
            "provider_served": run.provider_served,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
        if run
        else None
    )
    reasoning = next(
        (
            value[k]
            for k in ("reasoning", "rationale", "why")
            if isinstance(value, dict) and isinstance(value.get(k), str)
        ),
        None,
    )
    item["reasoning"] = reasoning
    # The words the span covers, in context, under the same rule as the evidence
    # page's text: a sensitive source needs the logged reveal, any other source is
    # read at once and its first read in this session is logged.
    src = item["source"]
    readable = src and (
        evidence.granted(src["sha256"])
        or (not src["sensitive"] and await evidence.viewable(db, src["sha256"], user))
    )
    item["excerpt"] = (
        await findings._excerpt(db, st.span_blob, st.span)
        if readable and st.span and "start" in st.span and "row" not in st.span
        else None
    )
    related = []
    if st.subject.startswith("person:") and st.prop not in IDENTITY:
        groups, _ = await people.identity(db)
        cluster = people._cluster(groups, st.subject)
        related = list(
            await db.scalars(
                select(Statement.id)
                .where(
                    Statement.subject.in_([f"person:{p}" for p in cluster]),
                    Statement.prop == st.prop,
                    Statement.id != st.id,
                )
                .order_by(Statement.recorded_at)
                .limit(RELATED)
            )
        )
    elif st.subject.startswith("blob:"):
        related = list(
            await db.scalars(
                select(Statement.id)
                .where(
                    Statement.subject == st.subject,
                    Statement.prop == st.prop,
                    Statement.id != st.id,
                )
                .limit(RELATED)
            )
        )
    item["related"] = await rows(db, user, related)
    item["corrects"] = (
        (await rows(db, user, [st.supersedes]) or [None])[0] if st.supersedes else None
    )
    if st.prop in IDENTITY:
        basis = await rows(db, user, await _basis_ids(db, st, value))
        other = f"person:{value['person']}" if value else None
        item["basis"] = basis
        item["compare"] = compare(
            [r for r in basis if r["subject"] == st.subject],
            [r for r in basis if r["subject"] == other],
        )
    elif isinstance(value, dict) and value.get("basis"):
        item["basis"] = await rows(db, user, await _basis_ids(db, st, value))
    thread = None
    request = item["request"]
    if request and request["visible"]:
        thread = [
            {
                "id": str(c.id),
                "text": v["text"],
                "by": name,
                "at": c.recorded_at.isoformat(),
            }
            for c, v, name in await db.execute(
                select(Statement, Payload.value, Principal.name)
                .join(Payload, Payload.commitment == Statement.commitment)
                .join(Principal, Principal.id == Statement.author_id)
                .where(
                    Statement.subject == f"statement:{request['id']}",
                    Statement.prop == Prop.REVIEW_COMMENT,
                )
                .order_by(Statement.recorded_at)
            )
        ]
    item["thread"] = thread
    item["hidden_sources"] = hidden(item)
    # An agent's reason may name what its basis says, so it waits for the same reveal.
    if item["reasoning"] and item["hidden_sources"]:
        item["reasoning"], item["reasoning_hidden"] = None, True
    me = await db.scalar(select(Principal.id).where(Principal.user_id == user.id))
    item["my_review"] = (
        str(
            await db.scalar(
                select(Statement.id)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .where(
                    Statement.subject == f"statement:{sid}",
                    Statement.prop == Prop.REVIEW,
                    Statement.author_id == me,
                    StatementTier.tier == Tier.ACCEPTED,
                )
                .order_by(Statement.recorded_at.desc())
                .limit(1)
            )
            or ""
        )
        or None
        if me
        else None
    )
    return item


def hidden(item: dict) -> list[dict]:
    """The sensitive sources a decision on `item` rests on that this session has not
    revealed: its own source, an identity proposal's basis, the other recording of a
    media match and the claim a correction replaces. Accept and reject wait for them,
    so no one signs what they could not read."""
    rests = [
        item["source"],
        *(r["source"] for r in item.get("basis") or []),
        (item.get("media") or {}).get("other"),
        item.get("document"),
        (item.get("corrects") or {}).get("source"),
    ]
    out: dict[str, dict] = {}
    for s in rests:
        if s and s["sensitive"] and not evidence.granted(s["sha256"]):
            out.setdefault(s["sha256"], {"sha256": s["sha256"], "file": s["file"]})
    return list(out.values())


async def for_request(db, rid: uuid.UUID) -> uuid.UUID | None:
    subject = await db.scalar(
        select(Statement.subject).where(
            Statement.id == rid, Statement.prop == Prop.REVIEW_REQUEST
        )
    )
    return uuid.UUID(subject.removeprefix("statement:")) if subject else None
