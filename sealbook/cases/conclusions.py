"""A case's conclusions and where the case stands. Both are signed ledger statements
about the case ("case:<slug>"), written by a person with the passkey gesture every
other decision uses; nothing here is a mutable row. A conclusion rests on accepted
claims, same-person links and findings of the case, states its confidence on the
team's scale (method.standards), names the alternatives set aside and what is not
established. A revision supersedes it, a withdrawal retracts it, a status change is
one more statement. A conclusion whose citations no longer stand says so."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import Text, case, cast, func, select, true
from sqlalchemy.orm import aliased
from stk.user.models import User

from sealbook import method
from sealbook.collaboration.service import check_reviewer, requests, signed_off
from sealbook.discovery import evidence
from sealbook.discovery.models import FindingKey
from sealbook.discovery.service import available
from sealbook.ledger import write
from sealbook.ledger.contracts import (
    ASSESSED_PROPS,
    EXTRACTED_PROPS,
    Prop,
    StatementIn,
    Tier,
)
from sealbook.ledger.models import Payload, Principal, Statement, StatementTier
from sealbook.monitor import data
from sealbook.monitor import filter as mfilter

STATUSES = ("open", "concluded", "closed")
TEXT_MAX = 400
REASON_MAX = 2000
ITEMS_MAX = 12
RESTS_MAX = 200
CITEABLE_MAX = 2000
LINKS = (Prop.SAME_AS, Prop.NOT_SAME)
# Claims a conclusion may rest on: what sources say about people, documents and
# events, and the first pass of the method. Hashes, content credentials and other
# custody records are accepted by rule, not facts about the case; same-person links
# and findings are listed on their own.
CLAIMS = EXTRACTED_PROPS | ASSESSED_PROPS | {Prop.RELATION}
# Why a cited statement no longer holds, in words.
GONE = {
    "retracted": "withdrawn",
    "rejected": "rejected in review",
    "erased": "erased",
    "replaced": "replaced by a correction",
    "unconfirmed": "no longer accepted",
    "missing": "no longer on record",
}


def _field(prop: str) -> str:
    from sealbook.collaboration.notifications import PROP

    return PROP.get(prop, prop.replace("_", " ").capitalize())


def ref(slug: str) -> str:
    return f"case:{slug}"


def _lang(text: str) -> str:
    return "ar" if re.search(r"[؀-ۿ]", text) else "en"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value, what: str, limit: int, required: bool = True) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if required and not text:
        raise ValueError(f"{what} is missing")
    if len(text) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return text


# --- what a conclusion may rest on -------------------------------------------------


def _persons(slug: str):
    """Person records with a claim from the case's sources."""
    return (
        select(Statement.subject)
        .where(
            Statement.subject.startswith("person:"),
            mfilter.statement_clause({"investigation": slug, "time": None}),
        )
        .distinct()
    )


async def records(db, persons: set[str]) -> dict[str, dict]:
    """Each person record's own name (not its cluster's) and the source it is from,
    so a link reads "A. Qassar, Consultants register". Masked names stay out."""
    subjects = sorted(f"person:{p.removeprefix('person:')}" for p in persons)
    if not subjects:
        return {}
    rows = await data.statement_rows(
        db,
        Statement.subject.in_(subjects),
        Statement.prop == Prop.NAME,
        StatementTier.tier != Tier.REJECTED,
        write.current_clause(),
        limit=5000,
        order=Statement.recorded_at,
    )
    out: dict[str, dict] = {}
    for r in rows:
        got = out.setdefault(
            r["subject"], {"name": None, "source": None, "masked": False}
        )
        got["name"] = got["name"] or r["display"]
        got["source"] = got["source"] or (r["doc"] or {}).get("title")
        got["masked"] = got["masked"] or r["masked"]
    return out


def _record(rec: dict | None) -> str:
    """A record in words. The name is isolated (U+2068..U+2069) so an Arabic name
    inside an English sentence keeps the sentence in order."""
    rec = rec or {}
    name = (
        f"⁨{rec['name']}⁩"
        if rec.get("name")
        else "a record named only in a sensitive source"
        if rec.get("masked")
        else "an unnamed record"
    )
    return f"{name} ({rec['source']})" if rec.get("source") else name


async def _finding_text(db, value: dict) -> str:
    from sealbook.review import findings

    places = await findings._places(db, set(value.get("places") or []))
    names = [p.get("en") or p.get("ar") or "a place" for p in places.values()] or [
        "a place"
    ]
    window = value.get("window") or [None]
    if not window[0]:
        return "An accepted finding"
    return findings.reason(
        value.get("kind", ""),
        len(value.get("people") or []),
        names,
        str(window[0])[:7],
        value.get("features"),
    )


async def _claims(db, *where, rejected: bool = False) -> list[dict]:
    """Current claims (CLAIMS) from available sources, as items; rejected ones
    only with `rejected`."""
    rows = await data.statement_rows(
        db,
        *where,
        true() if rejected else StatementTier.tier != Tier.REJECTED,
        write.current_clause(),
        Payload.value.is_not(None),
        Statement.prop.in_([str(p) for p in CLAIMS]),
        available(mfilter.source(mfilter.statement_blob())),
        limit=CITEABLE_MAX,
        order=Statement.recorded_at,
    )
    recs = await records(
        db, {r["subject"] for r in rows if r["subject"].startswith("person:")}
    )
    return [
        {
            "id": r["id"],
            "kind": "claim",
            "tier": r["tier"],
            "about": _record(recs.get(r["subject"]))
            if r["subject"].startswith("person:")
            else r["subject_label"],
            "field": _field(r["prop"]),
            "value": r["display"],
            "text": f"{_field(r['prop'])}: "
            + (r["display"] or "hidden until its sensitive source is revealed"),
            "masked": r["masked"],
            "when": r["when"],
            "source": (r["doc"] or {}).get("title"),
            "href": f"/evidence/{r['doc']['source_sha256']}" if r.get("doc") else None,
            "people": [r["subject"]] if r["subject"].startswith("person:") else [],
        }
        for r in rows
    ]


async def citeable(db, slug: str) -> dict:
    """What a conclusion on the case may rest on: accepted, current claims from its
    available sources; accepted same-person links between its person records; its
    accepted findings and matches. "said" is every current claim not rejected, the
    accounts a conclusion may name as the ones it contradicts, accepted or not.
    Each item carries its id, kind and the words a person reads."""
    accepted = (StatementTier.tier == Tier.ACCEPTED, write.current_clause())
    case_ = mfilter.statement_clause({"investigation": slug, "time": None})
    said = await _claims(db, case_)
    claims = await _claims(db, case_, StatementTier.tier == Tier.ACCEPTED)
    links = (
        await db.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                *accepted,
                Statement.prop.in_(LINKS),
                Statement.subject.in_(_persons(slug)),
            )
            .order_by(Statement.recorded_at)
        )
    ).all()
    found = (
        await db.execute(
            select(Statement, Payload.value)
            .join(FindingKey, FindingKey.statement_id == Statement.id)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(*accepted, FindingKey.investigation == slug)
            .order_by(Statement.recorded_at)
        )
    ).all()
    matches = await _matches(db, slug)
    persons = {st.subject for st, _ in links} | {
        f"person:{v['person']}" for _, v in links
    }
    recs = await records(db, persons)
    return {
        "claims": claims,
        "said": said,
        "links": [
            {
                "id": str(st.id),
                "kind": "link",
                "same": st.prop == Prop.SAME_AS,
                "text": f"{_record(recs.get(st.subject))} and "
                f"{_record(recs.get('person:' + v['person']))} are "
                + (
                    "the same person" if st.prop == Prop.SAME_AS else "different people"
                ),
                "people": [st.subject, f"person:{v['person']}"],
                "href": f"/people/{st.subject.removeprefix('person:')}",
            }
            for st, v in links
        ],
        "findings": [
            {
                "id": str(st.id),
                "kind": "finding",
                "text": await _finding_text(db, v),
                "people": [
                    p if str(p).startswith("person:") else f"person:{p}"
                    for p in v.get("people") or []
                ],
                "href": None,
            }
            for st, v in found
        ],
        "matches": matches,
    }


async def _matches(db, slug: str) -> list[dict]:
    """Accepted, current matches between two available files, one of them the
    case's: the two sources by title, the kind and strength the fingerprints
    measured, and who accepted it."""
    accepter = aliased(Principal)
    review, review_value = aliased(Statement), aliased(Payload)
    other = Payload.value["blob"].astext
    rows = (
        await db.execute(
            select(Statement, Payload.value, accepter.name, review_value.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .join(
                review,
                (review.subject == func.concat("statement:", cast(Statement.id, Text)))
                & (review.prop == Prop.REVIEW),
            )
            .join(review_value, review_value.commitment == review.commitment)
            .join(accepter, accepter.id == review.author_id)
            .where(
                StatementTier.tier == Tier.ACCEPTED,
                write.current_clause(),
                Statement.prop == Prop.MEDIA_MATCH,
                review_value.value["verdict"].astext == "accept",
                mfilter.statement_clause({"investigation": slug, "time": None}),
                available(mfilter.statement_blob()),
                available(other),
            )
            .order_by(Statement.recorded_at, review.recorded_at.desc())
        )
    ).all()
    shas = {st.subject.removeprefix("blob:") for st, *_ in rows} | {
        v["blob"] for _, v, *_ in rows
    }
    ctx = await data.Context.load(db, blobs=shas)
    out: dict[str, dict] = {}
    for st, v, name, decided in rows:
        if str(st.id) in out:
            continue  # the latest accepting review names who accepted it
        here, there = st.subject.removeprefix("blob:"), v["blob"]
        kind, why, strength = evidence.copy_reason(v)
        a, b = ctx.doc(here)["title"], ctx.doc(there)["title"]
        as_ = (decided or {}).get("relationship")
        out[str(st.id)] = {
            "id": str(st.id),
            "kind": "match",
            "text": f"⁨{a}⁩ and ⁨{b}⁩: {kind}, {strength}",
            "match": kind,
            "strength": strength,
            "why": why,
            "sources": [
                {"sha256": here, "title": a, "href": f"/evidence/{here}"},
                {"sha256": there, "title": b, "href": f"/evidence/{there}"},
            ],
            "accepted_by": name,
            "accepted_as": as_.replace("_", " ") if as_ else None,
            "about": f"{why}. Accepted by {name}"
            + (f" as {as_.replace('_', ' ')}" if as_ else ""),
            "people": [],
            "href": f"/evidence/{here}",
        }
    return list(out.values())


# Each citeable kind and the list citeable() returns it in.
KINDS = {"claim": "claims", "link": "links", "finding": "findings", "match": "matches"}


def _flat(found: dict) -> dict[str, dict]:
    return {i["id"]: i for k in KINDS.values() for i in found[k]}


# --- whether what it cited still stands ---------------------------------------------


async def cited(db, ids: list[str], said: bool = False) -> dict[str, str]:
    """Each cited statement's state: "stands", or why it no longer does (a key of
    GONE). A statement may be retracted, rejected, erased (its value or its source),
    replaced by an accepted correction, or no longer accepted after an undo. A claim
    cited as what was said (`said`, the account a conclusion contradicts) stands
    while it is current and not rejected, accepted or not."""
    uids = [uuid.UUID(i) for i in ids]
    blob = mfilter.statement_blob()
    rows = (
        await db.execute(
            select(
                Statement.id,
                StatementTier.tier,
                StatementTier.reason,
                Payload.commitment,
                write.current_clause().label("current"),
                case(
                    (blob.is_(None), true()),
                    # A match holds only while both files it joins do.
                    (
                        Statement.prop == Prop.MEDIA_MATCH,
                        available(mfilter.source(blob))
                        & available(Payload.value["blob"].astext),
                    ),
                    else_=available(mfilter.source(blob)),
                ).label("available"),
            )
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .outerjoin(Payload, Payload.commitment == Statement.commitment)
            .where(Statement.id.in_(uids))
        )
    ).all()
    out = dict.fromkeys(ids, "missing")
    for sid, tier, reason, commitment, current, ok in rows:
        out[str(sid)] = (
            "erased"
            if commitment is None or not ok
            else ("retracted" if reason == "retracted" else "rejected")
            if tier == Tier.REJECTED
            else "replaced"
            if not current
            else "stands"
            if tier == Tier.ACCEPTED or said
            else "unconfirmed"
        )
    return out


async def hidden(db, ids: list[str]) -> list[dict]:
    """Sensitive sources behind the cited statements (and the claims a link or a
    finding was built on) that this session has not revealed. A conclusion is masked
    by its citations, like a brief line: shown only once they are revealed."""
    uids = [uuid.UUID(i) for i in ids]
    rows = (
        await db.execute(
            select(Statement.prop, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(Statement.id.in_(uids))
        )
    ).all()
    more, other = set(), set()
    for prop, v in rows:
        if prop == Prop.MEDIA_MATCH:
            other.add(v["blob"])  # the second file a match joins
        elif isinstance(v, dict):
            for r in [*(v.get("basis") or []), *(v.get("members") or [])]:
                try:
                    more.add(uuid.UUID(str(r).removeprefix("statement:")))
                except ValueError:
                    continue
    shas = (
        set(
            await db.scalars(
                select(mfilter.source(mfilter.statement_blob())).where(
                    Statement.id.in_(set(uids) | more),
                    mfilter.statement_blob().is_not(None),
                )
            )
        )
        | other
    )
    ctx = await data.Context.load(db, blobs=shas)
    return [
        {"sha256": sha, "title": ctx.doc(sha)["title"]}
        for sha in sorted(shas)
        if ctx.doc(sha)["sensitive"] and not evidence.granted(sha)
    ]


# --- reading ---------------------------------------------------------------------


async def status(db, slug: str) -> dict:
    """Where the case stands: the latest accepted status statement, else open."""
    rows = await history(db, slug)
    return (
        rows[-1]
        if rows
        else {"status": "open", "reason": "", "at": None, "by": None, "id": None}
    )


async def history(db, slug: str) -> list[dict]:
    rows = (
        await db.execute(
            select(Statement, Payload.value, Principal.name)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(Principal, Principal.id == Statement.author_id)
            .where(
                Statement.subject == ref(slug),
                Statement.prop == Prop.CASE_STATUS,
                StatementTier.tier == Tier.ACCEPTED,
            )
            .order_by(Statement.recorded_at)
        )
    ).all()
    return [
        {
            "id": str(st.id),
            "status": v["status"],
            "reason": v.get("reason", ""),
            "at": st.recorded_at.isoformat(),
            "by": name,
        }
        for st, v, name in rows
    ]


async def _peer(db, sid: uuid.UUID) -> dict:
    asked = await requests(db, sid)
    if not asked:
        return {"state": "none"}
    st, v = asked[0]
    reviewer = await db.get(User, v["reviewer"])
    out = {
        "state": "asked",
        "request": str(st.id),
        "reviewer": (reviewer.name or reviewer.email) if reviewer else None,
        "reviewer_id": v["reviewer"],
        "asked_at": st.recorded_at.isoformat(),
    }
    if await signed_off(db, sid):
        rev = (
            await db.execute(
                select(Statement, Payload.value, Principal.name)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .join(Principal, Principal.id == Statement.author_id)
                .outerjoin(Payload, Payload.commitment == Statement.commitment)
                .where(
                    Statement.subject == f"statement:{sid}",
                    Statement.prop == Prop.REVIEW,
                    StatementTier.tier == Tier.ACCEPTED,
                )
                .order_by(Statement.recorded_at.desc())
                .limit(1)
            )
        ).first()
        out |= {
            "state": "agreed",
            "by": rev[2],
            "at": rev[0].recorded_at.isoformat(),
            "note": (rev[1] or {}).get("reason", ""),
        }
    return out


def _shape(st, tier, reason, value, name, current) -> dict:
    """One conclusion statement as stored, before its citations are checked."""
    state = (
        "erased"
        if value is None
        else "withdrawn"
        if tier == Tier.REJECTED
        else "revised"
        if not current
        else "standing"
    )
    out = {
        "id": str(st.id),
        "state": state,
        "by": name,
        "author": str(st.author_id),
        "at": st.recorded_at.isoformat(),
        "revises": str(st.supersedes) if st.supersedes else None,
    }
    if value is None:
        return out
    key = value.get("standard")
    scale = method.standards().get(key, {})
    return out | {
        "text": value["text"],
        "lang": value.get("lang", "en"),
        "standard": {
            "key": key,
            # The words it was signed with; the meaning from today's scale.
            "label": value.get("standard_label") or scale.get("label") or key,
            "meaning": scale.get("meaning", ""),
        },
        "rests_on": list(value.get("rests_on") or []),
        "contradicts": list(value.get("contradicts") or []),
        "alternatives": list(value.get("alternatives") or []),
        "not_established": list(value.get("not_established") or []),
        "gesture": value.get("gesture"),
    }


async def _rows(db, slug: str, sid: uuid.UUID | None = None):
    q = (
        select(
            Statement,
            StatementTier.tier,
            StatementTier.reason,
            Payload.value,
            Principal.name,
            write.current_clause().label("current"),
        )
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .outerjoin(Payload, Payload.commitment == Statement.commitment)
        .join(Principal, Principal.id == Statement.author_id)
        .where(Statement.prop == Prop.CONCLUSION)
        .order_by(Statement.recorded_at)
    )
    q = q.where(Statement.id == sid) if sid else q.where(Statement.subject == ref(slug))
    return (await db.execute(q)).all()


async def _checked(db, c: dict, user=None) -> dict:
    if c["state"] == "erased":
        return c | {"stands": False, "broken": [], "peer": {"state": "none"}}
    states = await cited(db, c["rests_on"]) | await cited(
        db, c["contradicts"], said=True
    )
    broken = [
        {"id": i, "state": s, "words": GONE[s]}
        for i, s in states.items()
        if s != "stands"
    ]
    out = c | {
        "stands": c["state"] == "standing" and not broken,
        "broken": broken,
        "peer": await _peer(db, uuid.UUID(c["id"])),
    }
    if user is not None:
        locked = await hidden(db, [*c["rests_on"], *c["contradicts"]])
        if locked:
            out |= {
                "locked": locked,
                "text": None,
                "alternatives": [],
                "not_established": [],
            }
        else:
            out["against"] = await against(db, c["contradicts"])
        out["mine"] = await _mine(db, user, c["author"])
        out["reviewing"] = (
            out["peer"].get("reviewer_id") == user.id
            and out["peer"]["state"] == "asked"
        )
    return out


async def _mine(db, user, author: str) -> bool:
    p = await db.get(Principal, uuid.UUID(author))
    return bool(p and p.user_id == user.id)


async def listing(db, slug: str, user=None) -> dict:
    """The case's status, its standing conclusions (each with whether it still
    stands on what it cited) and erased ones, which keep their place in the record."""
    rows = [_shape(*r) for r in await _rows(db, slug)]
    shown = [c for c in rows if c["state"] in ("standing", "erased")]
    return {
        "status": await status(db, slug),
        "history": await history(db, slug),
        "conclusions": [await _checked(db, c, user) for c in shown],
        "earlier": sum(c["state"] in ("revised", "withdrawn") for c in rows),
        "scale": standards(),
    }


def standards() -> list[dict]:
    return [{"key": k} | v for k, v in method.standards().items()]


async def detail(db, sid: uuid.UUID, user=None) -> dict | None:
    """One conclusion with what it rests on, item by item, and its revisions."""
    found = await _rows(db, "", sid)
    if not found:
        return None
    st = found[0][0]
    slug = st.subject.removeprefix("case:")
    c = await _checked(db, _shape(*found[0]), user)
    items = []
    if c.get("rests_on") and c.get("text") is not None:
        known = _flat(await citeable(db, slug))
        states = await cited(db, c["rests_on"])
        for i in c["rests_on"]:
            item = known.get(i) or {"id": i, "kind": "gone"}
            items.append(item | {"state": states[i], "words": GONE.get(states[i])})
    chain_ = [_shape(*r) for r in await _rows(db, slug)]
    return {
        "investigation": slug,
        "conclusion": c,
        "items": items,
        "against": c.get("against", []),
        "versions": [
            {k: v.get(k) for k in ("id", "state", "by", "at", "text", "revises")}
            for v in chain_
            if v["id"] in _lineage(chain_, c["id"])
        ],
        "status": await status(db, slug),
    }


async def against(db, ids: list[str]) -> list[dict]:
    """The claims a conclusion contradicts, each as what was said and its state;
    one rejected since stays listed as gone."""
    if not ids:
        return []
    found = {
        i["id"]: i
        for i in await _claims(
            db, Statement.id.in_([uuid.UUID(x) for x in ids]), rejected=True
        )
    }
    states = await cited(db, ids, said=True)
    return [
        (found.get(i) or {"id": i, "kind": "gone"})
        | {"state": states[i], "words": GONE.get(states[i])}
        for i in ids
    ]


def _lineage(rows: list[dict], sid: str) -> set[str]:
    """The conclusion, what it revised, and what revised it."""
    by = {r["id"]: r for r in rows}
    out, cur = {sid}, by.get(sid)
    while cur and cur.get("revises") and cur["revises"] not in out:
        out.add(cur["revises"])
        cur = by.get(cur["revises"])
    grew = True
    while grew:
        grew = False
        for r in rows:
            if r.get("revises") in out and r["id"] not in out:
                out.add(r["id"])
                grew = True
    return out


async def log_view(db, user, slug: str, ids: list[str]) -> None:
    """One access record per reader, login session and set of statements shown, on
    the case: who read which conclusions and the claims under them."""
    if not ids:
        return
    me = await write.principal_for_user(db, user.id, user.name or user.email)
    key = evidence.view_session()
    shown = sorted(set(ids))
    seen = await db.scalar(
        select(Statement.id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            Statement.subject == ref(slug),
            Statement.prop == Prop.ACCESS,
            Statement.author_id == me.id,
            # A set already logged in this session covers any part of it.
            Payload.value.contains({"session": key, "statements": shown}),
        )
        .limit(1)
    )
    if seen:
        return
    await write.append(
        db,
        StatementIn(
            subject=ref(slug),
            prop=Prop.ACCESS,
            value={"action": "view", "at": _now(), "session": key, "statements": shown},
            author_id=me.id,
        ),
    )


# --- writing ---------------------------------------------------------------------


async def _case(db, slug):
    from sealbook.cases.service import require

    try:
        return await require(db, slug)
    except LookupError as exc:
        raise LookupError(str(exc)) from None


def _resolve(rests_on, known: dict[str, dict]) -> list[str]:
    """Statement ids, or the words "links", "findings", "claims", "matches" for
    every citeable item of that kind (what a seeder plants)."""
    if not isinstance(rests_on, list) or not rests_on:
        raise ValueError("choose at least one accepted claim, link, match or finding")
    out = []
    for r in rests_on:
        if r in KINDS.values():
            out += [i for i, x in known.items() if KINDS[x["kind"]] == r]
        else:
            out.append(str(r))
    out = list(dict.fromkeys(out))
    if not out:
        raise ValueError("there is nothing accepted of that kind in this case")
    if len(out) > RESTS_MAX:
        raise ValueError(f"a conclusion rests on at most {RESTS_MAX} items")
    if missing := [i for i in out if i not in known]:
        raise ValueError(
            f"{len(missing)} of the chosen items "
            f"{'is' if len(missing) == 1 else 'are'} not an accepted claim, link, "
            "match or finding of this case"
        )
    return out


def _contradicts(raw, found: dict, rests_on: list[str]) -> list[str]:
    """The claims of the case the conclusion finds against: cited as what was said,
    accepted or not, never among what it rests on."""
    if not isinstance(raw, list):
        raise ValueError("what it contradicts must be a list")
    out = list(dict.fromkeys(map(str, raw)))
    said = {i["id"] for i in found["said"]}
    if len(out) > RESTS_MAX:
        raise ValueError(f"a conclusion contradicts at most {RESTS_MAX} claims")
    if [i for i in out if i not in said]:
        raise ValueError(
            "a conclusion contradicts only claims of this case that are not rejected"
        )
    if set(out) & set(rests_on):
        raise ValueError(
            "a claim cannot be both what it rests on and what it contradicts"
        )
    return out


def _alternatives(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("alternatives must be a list")
    out = []
    for a in raw:
        if not isinstance(a, dict):
            raise ValueError("each alternative has a text and why it was set aside")
        text = _text(a.get("text"), "an alternative", TEXT_MAX, required=False)
        why = _text(a.get("why"), "why it was set aside", REASON_MAX, required=False)
        if not text and not why:
            continue
        if not text or not why:
            raise ValueError(
                "each alternative needs what it is and why it was set aside"
            )
        out.append({"text": text, "why": why})
    if not out:
        raise ValueError("name at least one alternative and why it was set aside")
    if len(out) > ITEMS_MAX:
        raise ValueError(f"at most {ITEMS_MAX} alternatives")
    return out


def _open_points(raw) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("what is not established must be a list")
    out = [
        _text(x, "what is not established", TEXT_MAX)
        for x in raw
        if isinstance(x, str) and x.strip()
    ]
    if len(out) > ITEMS_MAX:
        raise ValueError(f"at most {ITEMS_MAX} lines of what is not established")
    return out


async def plan(db, item: dict, author) -> tuple[str, list[StatementIn]]:
    """The statements writing a conclusion signs under one gesture: the conclusion
    and, unless the case is already concluded or the writer keeps it open, the
    case's status as concluded. `revises` names a standing conclusion of the case it
    replaces. ValueError for a bad request, LookupError for an unknown case."""
    case_ = await _case(db, item.get("investigation"))
    text = _text(item.get("text"), "the conclusion", TEXT_MAX)
    if "\n" in text:
        raise ValueError("a conclusion is one sentence")
    scale = method.standards()
    standard = item.get("standard")
    if standard not in scale:
        raise ValueError("choose a confidence level from the scale")
    found = await citeable(db, case_.slug)
    known = _flat(found)
    rests_on = _resolve(item.get("rests_on"), known)
    contradicts = _contradicts(item.get("contradicts") or [], found, rests_on)
    alternatives = _alternatives(item.get("alternatives") or [])
    open_points = _open_points(item.get("not_established") or [])
    supersedes = None
    if item.get("revises"):
        try:
            supersedes = uuid.UUID(str(item["revises"]))
        except ValueError:
            raise ValueError("revises must be a conclusion id") from None
        standing = {
            c["id"]
            for c in (_shape(*r) for r in await _rows(db, case_.slug))
            if c["state"] == "standing"
        }
        if str(supersedes) not in standing:
            raise ValueError("only a standing conclusion of this case can be revised")
    said = {i["id"]: i for i in found["said"]}
    people = sorted(
        {p for i in rests_on for p in known[i].get("people") or []}
        | {p for i in contradicts for p in said[i]["people"]}
    )
    gesture = str(uuid.uuid4())
    out = [
        StatementIn(
            subject=ref(case_.slug),
            prop=Prop.CONCLUSION,
            value={
                "text": text,
                "lang": _lang(text),
                "standard": standard,
                "standard_label": scale[standard]["label"],
                "rests_on": rests_on,
                "contradicts": contradicts,
                "people": people,
                "alternatives": alternatives,
                "not_established": open_points,
                "gesture": gesture,
            },
            author_id=author,
            supersedes=supersedes,
        )
    ]
    now = await status(db, case_.slug)
    if item.get("conclude", True) and now["status"] != "concluded":
        out.append(_status(case_.slug, "concluded", "", gesture, author))
    return gesture, out


def _status(slug, status_, reason, gesture, author) -> StatementIn:
    return StatementIn(
        subject=ref(slug),
        prop=Prop.CASE_STATUS,
        value={"status": status_, "reason": reason, "at": _now(), "gesture": gesture},
        author_id=author,
    )


async def plan_status(db, item: dict, author) -> tuple[str, list[StatementIn]]:
    """Close a case without a conclusion, or reopen a concluded or closed one; both
    need a reason. A case is concluded only by writing a conclusion."""
    case_ = await _case(db, item.get("investigation"))
    want = item.get("status")
    if want not in ("open", "closed"):
        raise ValueError(
            "a case is reopened or closed here; write a conclusion to conclude it"
        )
    reason = _text(item.get("reason"), "the reason", REASON_MAX)
    now = (await status(db, case_.slug))["status"]
    if want == now:
        raise ValueError(f"the case is already {now}")
    gesture = str(uuid.uuid4())
    return gesture, [_status(case_.slug, want, reason, gesture, author)]


async def _standing(db, sid) -> tuple[Statement, dict]:
    try:
        sid = uuid.UUID(str(sid))
    except ValueError:
        raise ValueError("conclusion must be an id") from None
    found = await _rows(db, "", sid)
    if not found:
        raise LookupError("no conclusion with that id")
    c = _shape(*found[0])
    if c["state"] != "standing":
        raise LookupError(f"this conclusion is {c['state']}")
    return found[0][0], c


async def plan_withdraw(db, user, item: dict, author) -> tuple[str, list[StatementIn]]:
    """Its author withdraws a standing conclusion with a reason; a concluded case
    left with no standing conclusion is reopened in the same gesture."""
    st, _ = await _standing(db, item.get("conclusion"))
    if st.author_id != author:
        raise PermissionError(
            "only the analyst who signed a conclusion can withdraw it"
        )
    reason = _text(item.get("reason"), "the reason", REASON_MAX)
    slug = st.subject.removeprefix("case:")
    gesture = str(uuid.uuid4())
    out = [
        StatementIn(
            subject=f"statement:{st.id}",
            prop=Prop.RETRACTION,
            value={"reason": reason, "at": _now(), "gesture": gesture},
            author_id=author,
        )
    ]
    others = [
        c
        for c in (_shape(*r) for r in await _rows(db, slug))
        if c["state"] == "standing" and c["id"] != str(st.id)
    ]
    if not others and (await status(db, slug))["status"] == "concluded":
        out.append(
            _status(slug, "open", f"Conclusion withdrawn: {reason}", gesture, author)
        )
    return gesture, out


async def plan_agree(db, user, item: dict, author) -> tuple[str, list[StatementIn]]:
    """The colleague asked to peer review a conclusion signs that they agree; any
    disagreement goes in the review thread, and the author revises or withdraws."""
    st, _ = await _standing(db, item.get("conclusion"))
    if not await requests(db, st.id):
        raise LookupError("this conclusion was not sent for peer review")
    await check_reviewer(db, st, user)
    if await signed_off(db, st.id):
        raise LookupError("this peer review is already signed")
    note = _text(item.get("note"), "the note", REASON_MAX, required=False)
    gesture = str(uuid.uuid4())
    return gesture, [
        StatementIn(
            subject=f"statement:{st.id}",
            prop=Prop.REVIEW,
            value={
                "verdict": "accept",
                "reason": note or "agree",
                "at": _now(),
                "gesture": gesture,
            },
            author_id=author,
        )
    ]


# --- seeding ---------------------------------------------------------------------


async def seed(db, investigation: str, spec: dict, author, sign) -> list[Statement]:
    """Plant a signed conclusion, for seeders. `spec` is the form's item: "text",
    "standard", "rests_on" (statement ids, or "links", "findings", "claims",
    "matches" for all accepted ones of that kind), optional "contradicts"
    (statement ids of claims it finds against), "alternatives" [{"text", "why"}]
    and "not_established" [text]. `author` is a human principal and `sign(hash)` its
    passkey assertion (Seeder.assertion), so the rows verify like a browser's."""
    _, statements = await plan(db, spec | {"investigation": investigation}, author)
    return [await write.append(db, s, sign=sign) for s in statements]


async def seed_peer_review(db, conclusion: Statement, asker, reviewer, sign, note=""):
    """Ask `reviewer` (a principal with a user) to peer review and sign their
    agreement with `sign`, as the request and the review gesture would."""
    reviewer_user = (await db.get(Principal, reviewer)).user_id
    await write.append(
        db,
        StatementIn(
            subject=f"statement:{conclusion.id}",
            prop=Prop.REVIEW_REQUEST,
            value={
                "reviewer": reviewer_user,
                "reason": "Peer review of the conclusion",
            },
            author_id=asker,
        ),
    )
    return await write.append(
        db,
        StatementIn(
            subject=f"statement:{conclusion.id}",
            prop=Prop.REVIEW,
            value={"verdict": "accept", "reason": note or "agree", "at": _now()},
            author_id=reviewer,
        ),
        sign=sign,
    )
