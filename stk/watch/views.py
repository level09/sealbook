"""Watchlists: an analyst's watched persons and the matcher proposals for them."""

import re

import orjson
from quart import Blueprint, Response, abort, g, render_template, request
from quart_security import auth_required, current_user
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from stk.ledger.contracts import Prop, Tier
from stk.ledger.models import Payload, Run, Statement, StatementTier
from stk.monitor.data import Context, mask
from stk.people import data as people
from stk.watch import matcher
from stk.watch.models import Watch

bp_watch = Blueprint("watch", __name__)

ROLES = ("analyst", "admin")
PERSON = re.compile(
    r"person:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


@bp_watch.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@bp_watch.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


async def _cells(db, subjects: set[str]) -> dict[tuple[str, str], dict]:
    """(prop, text) -> the statement behind it, its text masked as the monitor
    masks it: a value from a sensitive (or unknown) source shows only its shape
    until revealed through the logged /api/monitor/reveal."""
    rows = (
        await db.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.subject.in_(subjects),
                Statement.prop.in_([Prop.NAME, *matcher.CORROBORATING]),
                StatementTier.tier != Tier.REJECTED,
            )
        )
    ).all()
    ctx = await Context.load(db, blobs=[st.span_blob for st, _ in rows])
    out = {}
    for st, value in rows:
        text = matcher.value_text(value)
        masked = st.span_blob is None or ctx.doc(st.span_blob)["sensitive"]
        cell = {
            "statement": str(st.id),
            "display": mask(text) if masked else text,
            "masked": masked,
        }
        # The same text from a public source is already public: show that one.
        if (st.prop, text) not in out or not masked:
            out[st.prop, text] = cell
    return out


async def feed(db, user_id: int, investigation: str | None = None) -> list[dict]:
    """The user's watched persons, each with the matcher's same_as proposals and
    the evidence that ties them, recomputed from the current draft projection.
    With an investigation, only watched persons with facts from its sources."""
    watches = (
        (
            await db.execute(
                select(Watch).where(Watch.user_id == user_id).order_by(Watch.id)
            )
        )
        .scalars()
        .all()
    )
    if investigation:
        groups, _ = await people.identity(db)
        here = await people.roster(db, investigation)
        watches = [
            w
            for w in watches
            if min(groups.get(people.pid(w.person), {people.pid(w.person)})) in here
        ]
    if not watches:
        return []
    uuids = [w.person.removeprefix("person:") for w in watches]
    rows = (
        await db.execute(
            select(Statement, Payload.value, StatementTier.tier)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .join(Run, Run.id == Statement.run_id)
            .where(
                Run.tool == matcher.TOOL,
                Statement.author_id == Run.principal_id,
                Statement.prop == Prop.SAME_AS,
                Payload.value["person"].astext.in_(uuids),
            )
            .order_by(Statement.recorded_at.desc())
        )
    ).all()
    label = await people.names(
        db,
        {people.pid(w.person) for w in watches}
        | {people.pid(r[0].subject) for r in rows},
    )
    out = []
    for w in watches:
        members = await matcher.cluster(db, w.person)
        known = await matcher.facts(db, members)
        mine = [(st, v, t) for st, v, t in rows if f"person:{v['person']}" == w.person]
        cells = await _cells(db, members | {st.subject for st, _, _ in mine})
        proposals = []
        for st, _, tier in mine:
            evidence = matcher.corroborate(await matcher.facts(db, {st.subject}), known)
            if evidence:
                evidence = {
                    "name": [cells[Prop.NAME, n] for n in evidence["name"]],
                    "fields": [
                        {"prop": f["prop"]} | cells[f["prop"], f["value"]]
                        for f in evidence["fields"]
                    ],
                }
            proposals.append(
                {
                    "id": str(st.id),
                    "mention": st.subject,
                    "mention_name": label.get(people.pid(st.subject)),
                    "document": st.span_blob,
                    "tier": tier,
                    "recorded_at": st.recorded_at.isoformat(),
                    # None when the evidence no longer holds (e.g. a field was rejected).
                    "evidence": evidence,
                }
            )
        out.append(
            {
                "person": w.person,
                "name": label.get(people.pid(w.person)),
                "names": [
                    cells[Prop.NAME, n] for n in sorted(set(known.get(Prop.NAME, [])))
                ],
                "since": w.created_at.isoformat(),
                "proposals": proposals,
            }
        )
    return out


@bp_watch.get("/watch")
async def page():
    return await render_template("watch/index.html")


@bp_watch.get("/api/watch")
async def items():
    return _json(
        {
            "items": await feed(
                g.db_session,
                current_user.id,
                request.args.get("investigation") or None,
            )
        }
    )


@bp_watch.post("/api/watch")
async def watch():
    db = g.db_session
    person = ((await request.get_json()) or {}).get("item", {}).get("person", "")
    if not PERSON.fullmatch(person):
        abort(400, description='person must be "person:<uuid>"')
    members = await matcher.cluster(db, person)
    named = await db.scalar(
        select(Statement.id)
        .where(Statement.subject.in_(members), Statement.prop == Prop.NAME)
        .limit(1)
    )
    if named is None:
        abort(404, description="no person mention with a name")
    await db.execute(
        pg_insert(Watch)
        .values(user_id=current_user.id, person=person)
        .on_conflict_do_nothing(constraint="uq_watch_user_person")
    )
    await db.commit()
    return _json({"person": person}, 201)


@bp_watch.delete("/api/watch/<path:person>")
async def unwatch(person):
    db = g.db_session
    gone = await db.execute(
        delete(Watch).where(Watch.user_id == current_user.id, Watch.person == person)
    )
    if not gone.rowcount:
        abort(404)
    await db.commit()
    return _json({"person": person})
