"""Human review: one passkey gesture per card. The assertion's challenge is the
review statement's own hash, so the signature covers exactly that verdict."""

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import orjson
from quart import Blueprint, Response, abort, g, render_template, request, url_for
from quart_security import auth_required, current_user
from sqlalchemy import func, select

from sealbook.collaboration import service as collaboration
from sealbook.discovery import service as discovery
from sealbook.discovery.models import FindingKey
from sealbook.ledger import attest, offline_sigs, write
from sealbook.ledger.contracts import Event, Prop, Refused, StatementIn
from sealbook.ledger.models import RunEvent, Statement, StatementTier
from sealbook.monitor import data
from sealbook.review import events, findings, firstpass, queue, todo

review = Blueprint("review", __name__)

ROLES = ("analyst", "admin")
TTL = 300

# Prepared statements waiting for their passkey assertion, keyed by (user, hash):
# one statement and its Prepared, or a Gesture and its list of Prepared. They hold
# the commitment salt, which never leaves the server.
# ponytail: in-process dict, one worker only; move to Redis or a table when the app
# runs more than one worker.
PENDING: dict[tuple[int, str], tuple[float, object, object]] = {}
# A finding's key follows its review, by whichever path the review came.
FINDING_STATE = {
    "review_accept": "accepted",
    "review_reject": "rejected",
    "review_undone": "open",
}


@dataclass(frozen=True)
class Gesture:
    """Several statements under one passkey assertion. Its challenge is
    offline_sigs.gesture_digest of their ordered hashes, and each stored sig carries
    that list as "batch", so every row verifies on its own."""

    id: str
    statements: list[StatementIn]
    hashes: list[str]


@review.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


def _refused(exc: Refused):
    return _json({"error": exc.reason}, 409)


def safe_next(target: str | None) -> str | None:
    """A same-origin relative path to return to, or None."""
    if not isinstance(target, str) or not target.startswith("/"):
        return None
    if target.startswith("//") or "\\" in target or any(ord(c) < 32 for c in target):
        return None
    parts = urlsplit(target)
    return None if parts.scheme or parts.netloc else target


def _no_passkey():
    """409 that tells the client to send the person to passkey setup and back."""
    ref = urlsplit(request.referrer or "")
    back = safe_next(
        (ref.path + (f"?{ref.query}" if ref.query else ""))
        if ref.netloc == request.host
        else None
    )
    setup = url_for("security.wan_register")
    return _json(
        {
            "error": "Decisions are signed with your passkey. You have not set one up yet.",
            "reason": "no_passkey",
            "setup_url": f"{setup}?next={quote(back, safe='')}" if back else setup,
        },
        409,
    )


async def passkey_gate(db):
    """The 409 no_passkey reply when the person cannot sign, else None. Asked after
    the request is validated and authorised (a 403 or 404 says more) and before
    anything is prepared or held."""
    if await attest.registered(db, current_user):
        return None
    return _no_passkey()


def _about(subject: str) -> uuid.UUID:
    return uuid.UUID(subject.removeprefix("statement:"))


async def _refuse_accept(db, sid: uuid.UUID, value: dict) -> None:
    """A stale subject passes only when the review names the current head: the
    reviewer saw the change, and the passkey signs that acknowledgement."""
    card = await data.card(db, sid)
    failed = [
        i
        for i in card["invariants"]
        if i["ok"] is False
        and not (
            "acknowledge" in i
            and "acknowledged_head" in value
            and value["acknowledged_head"] == i["acknowledge"]
        )
    ]
    if failed:
        raise Refused(f"cannot accept: {failed[0]['name']}, {failed[0]['note']}")


async def _target(db, sid: uuid.UUID, prop: Prop | None = None) -> Statement:
    st = await db.get(Statement, sid)
    if st is None:
        abort(404)
    if prop is not None and st.prop != prop:
        abort(400, description=f"statement is not a {prop}")
    if prop is None and st.prop in data.META:
        abort(400, description="review, retraction and access rows are not reviewed")
    return st


async def _statement_in(db, item: dict, author: uuid.UUID) -> StatementIn:
    at = datetime.now(UTC).isoformat()
    try:
        if "undo" in item:
            # Undo retracts the review itself; the reviewed statement's tier follows.
            st = await _target(db, uuid.UUID(item["undo"]), Prop.REVIEW)
            try:
                await collaboration.check_reviewer(
                    db, await db.get(Statement, _about(st.subject)), current_user
                )
            except PermissionError as exc:
                abort(403, description=str(exc))
            prop, value = Prop.RETRACTION, {"reason": "undo", "at": at}
        else:
            st = await _target(db, uuid.UUID(item["statement"]))
            try:
                await collaboration.check_reviewer(db, st, current_user)
            except PermissionError as exc:
                abort(403, description=str(exc))
            verdict = item["verdict"]
            if verdict not in ("accept", "reject"):
                abort(400, description="verdict is accept or reject")
            # "at" makes each gesture its own statement, so a review given again
            # after an undo is not collapsed into the retracted one.
            prop = Prop.REVIEW
            value = {"verdict": verdict, "reason": item.get("reason", ""), "at": at}
            if st.prop == Prop.MEDIA_MATCH and verdict == "accept":
                relationship = item.get("relationship")
                if relationship not in (
                    "same_recording",
                    "contains",
                    "contained_in",
                    "related",
                ):
                    abort(
                        400,
                        description="choose the media relationship before accepting",
                    )
                value["relationship"] = relationship
            if "acknowledged_head" in item:
                value["acknowledged_head"] = item["acknowledged_head"]
            if verdict == "accept":
                await _refuse_accept(db, st.id, value)
    except (KeyError, ValueError):
        abort(400, description="item needs statement and verdict, or undo")
    return StatementIn(
        subject=f"statement:{st.id}", prop=prop, value=value, author_id=author
    )


def _hold(digest: str, s, prepared) -> None:
    now = time.monotonic()
    for key in [k for k, v in PENDING.items() if v[0] < now]:
        del PENDING[key]
    PENDING[(current_user.id, digest)] = (now + TTL, s, prepared)


async def stage(db, s: StatementIn):
    """Prepare `s` and ask for the passkey assertion that will sign it; the commit
    route below finishes it. A person without a passkey is told so before anything
    is prepared or held."""
    if gate := await passkey_gate(db):
        return gate
    try:
        prepared = await write.prepare(db, s)
        opts = await attest.options(db, current_user, prepared.hash)
    except Refused as exc:
        return _refused(exc)
    _hold(prepared.hash, s, prepared)
    await db.commit()
    row = {
        "prop": s.prop,
        "subject": queue._ref(s.subject),
        "summary": _summary(s),
        "hash": prepared.hash,
    }
    return _json({"hash": prepared.hash, "options": opts, "rows": [row]})


def _summary(s: StatementIn) -> str:
    """One prepared row in words, as the reviewer reads it before signing."""
    v = s.value
    if s.prop == Prop.REVIEW:
        why = f", because {v['reason']}" if v.get("reason") else ""
        how = (
            f" as {v['relationship'].replace('_', ' ')}" if "relationship" in v else ""
        )
        return f"{v['verdict']} this claim{how}{why}"
    if s.prop == Prop.RETRACTION:
        return "undo your decision"
    return s.prop.replace("_", " ")


async def stage_gesture(db, gesture: str, statements: list[StatementIn]):
    """Prepare every statement and ask for one assertion over all of them; the same
    commit route writes them in one transaction. The reply lists each prepared row,
    so the reviewer sees exactly what the passkey will sign before the prompt."""
    if gate := await passkey_gate(db):
        return gate
    try:
        if not 0 < len(statements) <= offline_sigs.GESTURE_MAX:
            raise Refused(f"a gesture signs 1 to {offline_sigs.GESTURE_MAX} rows")
        prepared = [await write.prepare(db, s) for s in statements]
        if any(p.existing for p in prepared):
            raise Refused("part of this gesture is already written")
        hashes = [p.hash for p in prepared]
        digest = offline_sigs.gesture_digest(hashes)
        opts = await attest.options(db, current_user, digest)
    except Refused as exc:
        return _refused(exc)
    rows = await findings.describe(db, statements)
    _hold(digest, Gesture(gesture, statements, hashes), prepared)
    await db.commit()
    return _json(
        {
            "hash": digest,
            "options": opts,
            "gesture": gesture,
            "rows": [r | {"hash": h} for r, h in zip(rows, hashes, strict=True)],
        }
    )


@review.post("/api/review/options")
async def options():
    db = g.db_session
    item = (await request.get_json())["item"]
    who = await write.principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    try:
        s = await _statement_in(db, item, who.id)
    except Refused as exc:
        return _refused(exc)
    return await stage(db, s)


async def _gesture(db, target: Statement, stmt: Statement, stage: str) -> None:
    """The only place a run event carries human_gesture: after the assertion
    verified. It lands on the run that produced the reviewed statement."""
    if target.run_id is None:
        return  # a human-authored statement has no run feed to show it on
    seq = (
        await db.execute(
            select(func.coalesce(func.max(RunEvent.seq), 0)).where(
                RunEvent.run_id == target.run_id
            )
        )
    ).scalar()
    await write.emit(
        db,
        Event(
            run_id=target.run_id,
            seq=seq + 1,
            stage=stage,
            counts={"statements": 1},
            causation_id=stmt.id,
            correlation_id=target.id,
            human_gesture=True,
        ),
    )


async def _recheck(db, s: StatementIn) -> None:
    """Under the chain lock: the reviewer may still decide, and an accept still
    holds, since the subject may have moved while the passkey prompt was open."""
    if not s.subject.startswith("statement:"):
        return
    checked = await db.get(Statement, _about(s.subject))
    if s.prop == Prop.REVIEW or (
        s.prop == Prop.RETRACTION and checked.prop == Prop.REVIEW
    ):
        target = (
            checked
            if s.prop == Prop.REVIEW
            else await db.get(Statement, _about(checked.subject))
        )
        try:
            await collaboration.check_reviewer(db, target, current_user)
        except PermissionError as exc:
            abort(403, description=str(exc))
    if s.prop == Prop.REVIEW and s.value["verdict"] == "accept":
        await _refuse_accept(db, _about(s.subject), s.value)


async def _finding_state(db, target: Statement, label: str) -> None:
    if target.prop != Prop.FINDING or label not in FINDING_STATE:
        return
    fk = await db.scalar(
        select(FindingKey).where(FindingKey.statement_id == target.id).with_for_update()
    )
    if fk is None:
        return
    want = ("accepted", "rejected") if label == "review_undone" else ("open",)
    if fk.state not in want:
        raise Refused(f"this finding is already {fk.state}")
    fk.state = FINDING_STATE[label]


async def _after(db, s: StatementIn, stmt: Statement) -> Statement | None:
    """What one committed statement moves: the finding it decides, the run feed of
    the statement it reviews, and that person's actor profile."""
    if not s.subject.startswith("statement:"):
        return None
    about = await db.get(Statement, _about(s.subject))
    if s.prop == Prop.RETRACTION and about.prop == Prop.REVIEW:
        target = await db.get(Statement, _about(about.subject))
        label = "review_undone"
    elif s.prop == Prop.RETRACTION:
        target, label = about, "retracted"
    elif s.prop == Prop.LOCATED_AT:
        target, label = about, "place_confirmed"
    else:
        target, label = about, f"review_{s.value['verdict']}"
    await _finding_state(db, target, label)
    await _gesture(db, target, stmt, label)
    await discovery.actor_changed(db, target)
    return target


@review.post("/api/review/commit")
async def commit():
    db = g.db_session
    item = (await request.get_json())["item"]
    pending = PENDING.pop((current_user.id, item["hash"]), None)
    if pending is None or pending[0] < time.monotonic():
        return _json({"error": "review expired, start it again"}, 409)
    _, s, prepared = pending
    many = isinstance(s, Gesture)
    statements, preps = (s.statements, prepared) if many else ([s], [prepared])
    try:
        sig = await attest.verify(db, current_user, item["hash"], item["credential"])
        # Held through commit, so no other accept can move the subject between
        # these checks and the writes.
        await write.lock_chain(db)
        for one in statements:
            await _recheck(db, one)
        written, targets = [], []
        for one, p in zip(statements, preps, strict=True):
            stmt = await write.commit(
                db, p, {**sig, "batch": s.hashes} if many else sig
            )
            written.append(stmt)
            targets.append(await _after(db, one, stmt))
    except Refused as exc:
        await db.rollback()
        return _refused(exc)
    target = targets[0]
    tier = (
        target
        and (
            await db.execute(
                select(StatementTier.tier).where(
                    StatementTier.statement_id == target.id
                )
            )
        ).scalar()
    )
    await db.commit()
    for t in {t.id: t for t in targets if t}.values():
        await collaboration.reviewed(db, t.id)
    if many:
        return _json(
            {
                "gesture": s.id,
                "statements": [
                    {"id": str(st.id), "prop": st.prop, "subject": st.subject}
                    for st in written
                ],
            }
        )
    return _json(
        {"statement": str(target.id), "review": str(written[0].id), "tier": tier}
    )


# --- findings ----------------------------------------------------------------


def _private(payload, status=200):
    r = _json(payload, status)
    r.headers["Cache-Control"] = "no-store"
    return r


async def _who(db):
    return (
        await write.principal_for_user(
            db, current_user.id, current_user.name or current_user.email
        )
    ).id


async def _planned(make):
    """Turn a findings plan into a staged gesture, mapping its refusals."""
    db = g.db_session
    try:
        gesture, statements = await make(db, await _who(db))
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    except LookupError as exc:
        return _json({"error": str(exc)}, 404)
    except PermissionError as exc:
        return _json({"error": str(exc)}, 403)
    return await stage_gesture(db, gesture, statements)


async def _item() -> dict:
    item = ((await request.get_json(silent=True)) or {}).get("item")
    if not isinstance(item, dict):
        abort(400, description="send {item: {...}}")
    return item


@review.get("/todo")
async def todo_page():
    return await render_template("todo/index.html")


@review.get("/api/todo")
async def todo_list():
    found = await todo.todo(
        g.db_session, current_user, request.args.get("investigation") or None
    )
    return _private(found)


@review.get("/discover")
async def discover_page():
    return await render_template("discover/index.html")


@review.get("/api/discover/findings")
async def finding_queue():
    db = g.db_session
    items = await findings.queue(
        db, current_user, request.args.get("investigation") or None
    )
    return _private({"items": items})


@review.get("/api/discover/findings/<key>")
async def finding_detail(key):
    db = g.db_session
    found = await findings.detail(db, current_user, key)
    if found is None:
        abort(404, description="no open finding with that key that you may see")
    await findings.log_view(db, current_user, found)
    await db.commit()
    return _private(found)


@review.get("/api/discover/incidents")
async def incident_list():
    items = await findings.incidents(
        g.db_session, request.args.get("investigation") or None
    )
    return _private({"items": items})


@review.get("/api/discover/groups")
async def group_queue():
    items = await events.groups(
        g.db_session, current_user, request.args.get("investigation") or None
    )
    return _private({"items": items})


@review.get("/api/discover/groups/<key>")
async def group_detail(key):
    db = g.db_session
    found = await events.detail(db, current_user, key)
    if found is None:
        abort(404, description="no open group with that key; it may have been decided")
    await events.log_view(db, current_user, found)
    await db.commit()
    return _private(found)


@review.post("/api/review/groups/options")
async def group_accept():
    item = await _item()
    return await _planned(
        lambda db, who: events.plan(
            db,
            current_user,
            item.get("group"),
            item.get("kept"),
            item.get("title"),
            who,
            item.get("place"),
        )
    )


@review.post("/api/review/groups/reject")
async def group_reject():
    item = await _item()
    return await _planned(
        lambda db, who: events.reject(
            db, current_user, item.get("group"), item.get("reason", ""), who
        )
    )


@review.post("/api/review/firstpass/options")
async def first_pass_accept():
    item = await _item()
    return await _planned(
        lambda db, who: firstpass.plan(db, current_user, item.get("sha"), who)
    )


@review.post("/api/review/incidents/violation/options")
async def incident_violation():
    item = await _item()
    return await _planned(
        lambda db, who: findings.violation(
            db, item.get("incident"), item.get("kind"), item.get("category"), who
        )
    )


@review.post("/api/review/findings/options")
async def finding_accept():
    item = await _item()
    return await _planned(
        lambda db, who: findings.plan(
            db,
            current_user,
            item.get("finding"),
            item.get("kept"),
            item.get("roles"),
            item.get("title"),
            item.get("incident"),
            who,
        )
    )


@review.post("/api/review/findings/reject")
async def finding_reject():
    item = await _item()
    return await _planned(
        lambda db, who: findings.reject(
            db, current_user, item.get("finding"), item.get("reason", ""), who
        )
    )


@review.post("/api/review/undo")
async def undo_gesture():
    item = await _item()

    async def make(db, who):
        gesture = item.get("gesture")
        return gesture, await findings.undo(
            db, current_user, gesture, item.get("statements"), who
        )

    return await _planned(make)


# --- review workspace ----------------------------------------------------------


@review.get("/api/review/queue")
async def review_queue():
    """One box of the review workspace (mine, sent or open), each claim with its
    value, source, location, proposer, request and due date; counts for every box."""
    db = g.db_session
    investigation = request.args.get("investigation") or None
    try:
        wanted = await queue.ids(
            db, current_user, request.args.get("box", "mine"), investigation
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _private(
        {
            "items": await queue.rows(db, current_user, wanted),
            "counts": await queue.counts(db, current_user, investigation),
        }
    )


@review.get("/api/review/claim/<uuid:sid>")
async def review_claim(sid):
    found = await queue.claim(g.db_session, current_user, sid)
    if found is None:
        abort(404, description="no reviewable claim with that id")
    return _private(found)


@review.post("/api/review/claim/<uuid:sid>/reveal")
async def review_reveal(sid):
    """Show the sensitive values one decision rests on: the evidence page's own logged
    reveal for each source still hidden in this session, one access record each."""
    from sealbook.discovery.views import reveal

    db = g.db_session
    found = await queue.claim(db, current_user, sid)
    if found is None:
        abort(404, description="no reviewable claim with that id")
    for src in found["hidden_sources"]:
        await reveal(src["sha256"])
    return await review_claim(sid)


@review.get("/api/review/request/<uuid:rid>")
async def review_request(rid):
    """The claim a review request is about, so /review?request= deep links select it."""
    sid = await queue.for_request(g.db_session, rid)
    if sid is None:
        abort(404, description="review request not found")
    return await review_claim(sid)
