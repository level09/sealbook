"""Findings in review: the queue an analyst works and the statements one signed
gesture writes when a finding is decided. Nothing here signs or commits; the review
views stage these statements and commit them under one passkey assertion."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Text, cast, select
from sqlalchemy.orm import aliased

from sealbook.blobs import read
from sealbook.collaboration.service import check_reviewer
from sealbook.discovery import evidence
from sealbook.discovery import findings as discovery
from sealbook.discovery.models import FindingKey
from sealbook.discovery.service import available
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop, StatementIn, Tier
from sealbook.ledger.models import Blob, Payload, Principal, Statement, StatementTier
from sealbook.ledger.offline_sigs import GESTURE_MAX
from sealbook.monitor import data
from sealbook.monitor import filter as mfilter
from sealbook.people import data as people
from sealbook.places.models import Place
from sealbook.settings import Config

ROLES = ("unspecified", "victim", "witness", "alleged_perpetrator", "other")
KINDS = {discovery.SHARED: "Shared presence", discovery.INCIDENT: "Candidate incident"}
INCIDENT_REF = re.compile(r"incident:[0-9a-f-]{36}")
# Two statements per kept member plus incident, place and review stay inside one
# signed gesture (offline_sigs.GESTURE_MAX).
MAX_KEPT = (GESTURE_MAX - 3) // 2
EXCERPT = 60  # characters of context either side of a span


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def month_label(month: str) -> str:
    return date.fromisoformat(f"{month}-01").strftime("%B %Y")


def metres(m: int) -> str:
    return f"{m} m" if m < 1000 else f"{m / 1000:.1f} km".replace(".0 km", " km")


def reason(kind: str, n: int, names: list[str], month: str, features=None) -> str:
    """The plain sentence a finding leads with. Shared presence of two reads "Both";
    two places within reach name their distance and how precisely each is known."""
    when = month_label(month)
    who = "Both" if kind == discovery.SHARED and n == 2 else f"{n} people"
    f = features or {}
    if "distance_m" in f and len(names) == 2:
        a, b = (metres(p) for p in f["precision_m"])
        return (
            f"{who} held {metres(f['distance_m'])} apart, at {names[0]} and "
            f"{names[1]}, in {when} (places known to within {a} and {b})"
        )
    return f"{who} held at {names[0]} in {when}"


def band(kind: str, features: dict) -> str:
    """Evidence strength as a band, never a probability: independent documents and,
    for shared presence, how much of the claimed time overlaps."""
    sources = features.get("sources", 0)
    if kind == discovery.SHARED:
        o = float(features.get("overlap", 0))
        if o >= 0.75 and sources >= 2:
            return "strong"
        return "moderate" if o >= 0.25 else "weak"
    return "strong" if sources >= 3 else "moderate"


def why(kind: str, f: dict) -> str:
    s = f.get("sources", 0)
    docs = f"{s} source{'s' if s != 1 else ''}"
    if kind == discovery.SHARED:
        days = f.get("days", 0)
        return f"{docs}, overlapping {days} day{'s' if days != 1 else ''}"
    return (
        f"{f.get('people', 0)} people from {docs} within {f.get('span_days', 0)} days"
    )


def _cluster(groups: dict, subject: str) -> str:
    pid = people.pid(subject)
    return f"person:{min(groups.get(pid, {pid}))}"


def _mine(user_id):
    return select(Principal.id).where(Principal.user_id == user_id)


async def _open(db, investigation: str | None, key: str | None = None):
    q = (
        select(FindingKey, Statement, Payload.value)
        .join(Statement, Statement.id == FindingKey.statement_id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(FindingKey.state == "open")
        .order_by(FindingKey.rank.desc(), FindingKey.created_at.desc(), FindingKey.key)
    )
    if investigation:
        q = q.where(FindingKey.investigation == investigation)
    if key:
        q = q.where(FindingKey.key == key)
    return (await db.execute(q)).all()


async def _members(db, ids: set[str]) -> dict[str, tuple]:
    """Member statements a reader may see: the value is not erased and the source
    is available. A missing id is not visible."""
    if not ids:
        return {}
    rows = await db.execute(
        select(Statement, Payload.value, StatementTier.tier)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.id.in_([uuid.UUID(i) for i in ids]),
            available(Statement.span_blob),
        )
    )
    return {str(st.id): (st, value, tier) for st, value, tier in rows}


async def _places(db, refs: set[str]) -> dict[str, dict]:
    ids = [uuid.UUID(r.removeprefix("place:")) for r in refs]
    if not ids:
        return {}
    rows = await db.execute(
        select(Place.id, Place.name_en, Place.name_ar).where(Place.id.in_(ids))
    )
    return {f"place:{i}": {"id": str(i), "en": en, "ar": ar} for i, en, ar in rows}


async def _seen(db, user, subjects: list[str]) -> set[str]:
    """Finding statements this user has opened, from their own access records."""
    if not subjects:
        return set()
    rows = await db.scalars(
        select(Payload.value["statements"])
        .join(Statement, Statement.commitment == Payload.commitment)
        .where(
            Statement.prop == Prop.ACCESS,
            Statement.subject.in_(subjects),
            Statement.author_id.in_(_mine(user.id)),
        )
    )
    return {i for ids in rows for i in ids or ()}


async def queue(db, user, investigation: str | None, key: str | None = None):
    """Open findings, strongest first. A finding is left out when any member is not
    visible: no partial findings (spec call 1)."""
    rows = await _open(db, investigation, key)
    members = await _members(db, {m for fk, _, _ in rows for m in fk.members or ()})
    places = await _places(db, {p for _, _, v in rows for p in v["places"]})
    seen = await _seen(db, user, [st.subject for _, st, _ in rows])
    groups = (await people.identity(db))[0] if rows else {}
    out = []
    for fk, st, value in rows:
        ids = value["members"]
        if not ids or any(m not in members for m in ids):
            continue
        names = [
            (places.get(p, {}).get("en") or places.get(p, {}).get("ar"))
            or "an unnamed place"
            for p in value["places"]
        ]
        # A finding across two near places says where each member was.
        at = value.get("at", {})
        rows_ = []
        for m in ids:
            ms, _, tier = members[m]
            rows_.append(
                {
                    "statement": m,
                    "subject": ms.subject,
                    # The identity cluster the finding names, as discovery does.
                    "cluster": _cluster(groups, ms.subject),
                    "prop": ms.prop,
                    "tier": tier,
                    "span": ms.span,
                    "source": await db.scalar(select(mfilter.source(ms.span_blob))),
                    "span_blob": ms.span_blob,
                    "when": data.when(ms.valid_from, ms.valid_to),
                    "place": places.get(at.get(m, value["places"][0]), {}),
                }
            )
        out.append(
            {
                "id": fk.key,
                "statement": str(st.id),
                "kind": fk.kind,
                "label": KINDS.get(fk.kind, fk.kind),
                "investigation": fk.investigation,
                "reason": reason(
                    fk.kind,
                    len(value["people"]),
                    names,
                    value["month"],
                    value["features"],
                ),
                "why": why(fk.kind, value["features"]),
                "band": band(fk.kind, value["features"]),
                "rank": fk.rank,
                "month": value["month"],
                "window": value["window"],
                "people": value["people"],
                "places": [places.get(p, {"id": p}) for p in value["places"]],
                "members": rows_,
                "features": value["features"],
                "new": str(st.id) not in seen,
                "at": st.recorded_at.isoformat(),
            }
        )

    # Shared presence and a candidate incident over the same people, places and
    # month are two readings of one set; each names the other.
    def same(f):
        return (
            tuple(sorted(f["people"])),
            tuple(sorted(p["id"] for p in f["places"])),
            f["month"],
        )

    for f in out:
        f["also"] = [
            {"id": g["id"], "label": g["label"]}
            for g in out
            if g["id"] != f["id"] and g["kind"] != f["kind"] and same(g) == same(f)
        ]
    return out


async def log_view(db, user, finding: dict) -> Statement:
    """Opening a finding is a use of its members: one access record on the finding
    listing them, which is also what ends "new since you last looked"."""
    who = await write.principal_for_user(db, user.id, user.name or user.email)
    return await write.append(
        db,
        StatementIn(
            subject=f"finding:{finding['id']}",
            prop=Prop.ACCESS,
            value={
                "action": "view",
                "at": datetime.now(UTC).isoformat(),
                "statements": [finding["statement"]]
                + [m["statement"] for m in finding["members"]],
            },
            author_id=who.id,
        ),
    )


async def _names(db, members: list[dict]) -> dict[tuple, dict]:
    """Each member person's name as written in the same source, if any."""
    rows = await db.execute(
        select(Statement.subject, Statement.span_blob, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.prop == Prop.NAME,
            Statement.subject.in_({m["subject"] for m in members}),
            Statement.span_blob.in_({m["span_blob"] for m in members}),
            StatementTier.tier != Tier.REJECTED,
        )
    )
    return {(s, b): v for s, b, v in rows}


async def _excerpt(db, sha: str | None, span: dict | None) -> dict | None:
    if not sha or not span or "start" not in span:
        return None
    blob = await db.get(Blob, sha)
    if blob is None or not blob.mime.startswith("text/"):
        return None
    text = read(blob, Config.BLOB_DIR).decode()
    a, b = span["start"], span["end"]
    lo, hi = max(0, a - EXCERPT), min(len(text), b + EXCERPT)
    return {
        "before": ("…" if lo else "") + text[lo:a],
        "span": text[a:b],
        "after": text[b:hi] + ("…" if hi < len(text) else ""),
    }


async def detail(db, user, key: str) -> dict | None:
    """One finding with each member's value and, where this session may read the
    source, its span in context. Personal values from a sensitive source stay
    masked unless the session holds its logged reveal."""
    found = await queue(db, user, None, key)
    if not found:
        return None
    f = found[0]
    sts = await _members(db, {m["statement"] for m in f["members"]})
    ctx = await data.Context.load(
        db,
        blobs=[m[k] for m in f["members"] for k in ("span_blob", "source")],
        principals=[sts[m["statement"]][0].author_id for m in f["members"]],
        runs=[sts[m["statement"]][0].run_id for m in f["members"]],
    )
    names = await _names(db, f["members"])
    readable = {}
    for m in f["members"]:
        st, value, tier = sts[m["statement"]]
        row = ctx.statement(st, tier, "", value)
        sha = m["source"]
        if sha not in readable:
            readable[sha] = bool(sha) and await evidence.viewable(db, sha, user)
        open_ = readable[sha] or evidence.granted(m["span_blob"] or "")
        name = names.get((m["subject"], m["span_blob"]))
        m |= {
            "display": row["display"],
            # A transcript or page text is named by the recording or file it reads.
            "file": ctx.doc(sha)["title"] if sha else None,
            "sensitive": bool(row["doc"] and row["doc"]["sensitive"]),
            "inherited": row["inherited"],
            "agent": row["actor"]["agent"],
            "name": data.display(name) if name and open_ else None,
            "excerpt": await _excerpt(db, m["span_blob"], m["span"]) if open_ else None,
            "person": m["subject"].removeprefix("person:"),
        }
    # A member whose own span is masked still gets the best name seen elsewhere.
    best = await people.names(db, [m["person"] for m in f["members"]])
    for m in f["members"]:
        m["name"] = m["name"] or best.get(m["person"])
    request = await db.scalar(
        select(Statement.id).where(
            Statement.subject == f"statement:{f['statement']}",
            Statement.prop == Prop.REVIEW_REQUEST,
        )
    )
    f["request"] = str(request) if request else None
    return f


async def incidents(db, investigation: str | None) -> list[dict]:
    """Current incidents, by title, that some accepted support ties to the
    investigation. Without one, every current incident."""
    rows = (
        await db.execute(
            select(Statement.subject, Payload.value, Statement.recorded_at)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == Prop.INCIDENT,
                StatementTier.tier == Tier.ACCEPTED,
            )
            .order_by(Statement.recorded_at.desc())
        )
    ).all()
    if investigation and rows:
        member = aliased(Statement)
        support = (
            select(Statement.subject)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .join(member, cast(member.id, Text) == Payload.value["statement"].astext)
            .where(
                Statement.prop == Prop.SUPPORTED_BY,
                StatementTier.tier == Tier.ACCEPTED,
                mfilter.statement_clause(
                    {"investigation": investigation, "time": None}, member
                ),
            )
        )
        keep = set(await db.scalars(support))
        rows = [r for r in rows if r[0] in keep]
    return [
        {"id": s, "title": data.display(v.get("title")), "at": at.isoformat()}
        for s, v, at in rows
    ]


def _lang(text: str) -> str:
    return "ar" if re.search(r"[؀-ۿ]", text) else "en"


async def _finding(db, user, key: str) -> tuple[dict, Statement]:
    found = await queue(db, user, None, key)
    if not found:
        raise LookupError("no open finding with that key that you may see")
    st = await db.get(Statement, uuid.UUID(found[0]["statement"]))
    await check_reviewer(db, st, user)
    return found[0], st


def _review(finding_st: Statement, verdict: str, why_: str, gesture: str, author):
    return StatementIn(
        subject=f"statement:{finding_st.id}",
        prop=Prop.REVIEW,
        value={
            "verdict": verdict,
            "reason": why_,
            "at": datetime.now(UTC).isoformat(),
            "gesture": gesture,
        },
        author_id=author,
    )


async def plan(
    db,
    user,
    key: str,
    kept: list[str],
    roles: dict[str, str],
    title: str,
    incident: str | None,
    author,
) -> tuple[str, list[StatementIn]]:
    """The statements accepting `key` writes, all carrying one gesture id: a new
    incident (title, place and kept window) unless `incident` names a current one,
    each kept person as involved, each kept statement as support, and the review
    accepting the finding. ValueError for a bad request, LookupError when the
    finding is gone, PermissionError when it is assigned to someone else."""
    f, st = await _finding(db, user, key)
    ids = [m["statement"] for m in f["members"]]
    if not isinstance(kept, list) or not kept or len(set(kept)) != len(kept):
        raise ValueError("keep at least one member, each once")
    if len(kept) > MAX_KEPT:
        raise ValueError(f"keep at most {MAX_KEPT} members in one gesture")
    if any(k not in ids for k in kept):
        raise ValueError("kept statements must be members of the finding")
    # Members must still stand (accepted or corroborated, current, resolved, dated).
    # ponytail: rereads the investigation's points; filter to the kept ids in SQL
    # if a case passes a few thousand position claims.
    pts = {p["id"]: p for p in await discovery.points(db, f["investigation"])}
    gone = [k for k in kept if k not in pts]
    if gone:
        raise ValueError(f"member {gone[0][:8]} no longer stands; run discovery again")
    involved = list(dict.fromkeys(pts[k]["person"] for k in kept))
    roles = roles or {}
    if not isinstance(roles, dict) or any(p not in involved for p in roles):
        raise ValueError("roles are keyed by a kept person")
    if any(r not in ROLES for r in roles.values()):
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    title = title.strip() if isinstance(title, str) else ""
    gesture = str(uuid.uuid4())
    out = []
    if incident:
        if not isinstance(incident, str) or not INCIDENT_REF.fullmatch(incident):
            raise ValueError("incident must be incident:<uuid>")
        if not any(
            i["id"] == incident for i in await incidents(db, f["investigation"])
        ):
            raise ValueError("no current incident with that id in this investigation")
        subject = incident
    else:
        if not title or len(title) > 300:
            raise ValueError("a new incident needs a title of 1 to 300 characters")
        subject = f"incident:{uuid.uuid4()}"
        out.append(
            StatementIn(
                subject=subject,
                prop=Prop.INCIDENT,
                value={
                    "title": {"text": title, "lang": _lang(title)},
                    "kind": "unspecified",
                    "gesture": gesture,
                },
                author_id=author,
            )
        )
        start = min(pts[k]["interval"][0] for k in kept)
        end = max(pts[k]["interval"][1] for k in kept)
        for place in sorted({pts[k]["place"] for k in kept}):
            out.append(
                StatementIn(
                    subject=subject,
                    prop=Prop.OCCURRED_AT,
                    value={"place": place, "gesture": gesture},
                    author_id=author,
                    valid_from=start,
                    valid_to=end,
                )
            )
    for p in involved:
        out.append(
            StatementIn(
                subject=subject,
                prop=Prop.INVOLVED,
                value={
                    "person": p,
                    "role": roles.get(p, "unspecified"),
                    "gesture": gesture,
                },
                author_id=author,
            )
        )
    for k in kept:
        out.append(
            StatementIn(
                subject=subject,
                prop=Prop.SUPPORTED_BY,
                value={"statement": k, "gesture": gesture},
                author_id=author,
            )
        )
    out.append(_review(st, "accept", "", gesture, author))
    return gesture, out


async def reject(db, user, key: str, why_: str, author) -> tuple[str, list]:
    """One review rejecting the finding; its key then never returns."""
    if not isinstance(why_, str) or len(why_) > 4000:
        raise ValueError("reason must be text of at most 4000 characters")
    _, st = await _finding(db, user, key)
    gesture = str(uuid.uuid4())
    return gesture, [_review(st, "reject", why_.strip(), gesture, author)]


async def undo(db, user, gesture: str, only: list[str] | None, author) -> list:
    """Retractions for the gesture's statements, or for `only` among them. A review
    in the gesture is undone the review way (its retraction), so the finding it
    decided returns to the queue."""
    try:
        uuid.UUID(gesture)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("gesture must be a UUID") from None
    # ponytail: unindexed JSON lookup; add an expression index on value->>'gesture'
    # when the ledger is large enough for undo to be slow.
    # Accepted only: an undone statement or review is already rejected (retracted).
    rows = list(
        await db.scalars(
            select(Statement)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Payload.value["gesture"].astext == gesture,
                Statement.prop != Prop.RETRACTION,
                StatementTier.tier == Tier.ACCEPTED,
            )
            .order_by(Statement.recorded_at)
        )
    )
    if only is not None:
        if not isinstance(only, list) or not all(isinstance(i, str) for i in only):
            raise ValueError("statements must be a list of ids")
        rows = [r for r in rows if str(r.id) in only]
        if len(rows) != len(set(only)):
            raise ValueError("every statement must belong to the gesture and stand")
    if not rows:
        raise LookupError("nothing in that gesture is left to undo")
    # The gate honours any attested retraction, so authorship is checked here.
    if any(r.author_id != author for r in rows):
        raise PermissionError("only the analyst who signed a gesture can undo it")
    at = datetime.now(UTC).isoformat()
    for r in rows:
        if r.prop == Prop.REVIEW:
            await check_reviewer(
                db,
                await db.get(
                    Statement, uuid.UUID(r.subject.removeprefix("statement:"))
                ),
                user,
            )
    return [
        StatementIn(
            subject=f"statement:{r.id}",
            prop=Prop.RETRACTION,
            value={"reason": "undo", "at": at, "undoes": gesture},
            author_id=author,
        )
        for r in rows
    ]


REVIEWED = {
    Prop.FINDING: "the finding",
    Prop.RELATION: "the same-event link",
    Prop.TRIAGE: "the triage",
    Prop.TITLE: "the title",
    Prop.DESCRIPTION: "a description line",
    Prop.LABEL: "a label",
}


def _about(subject: str) -> uuid.UUID:
    return uuid.UUID(subject.removeprefix("statement:"))


async def describe(db, statements: list[StatementIn]) -> list[dict]:
    """Each statement of a gesture as a reviewer reads it before signing."""
    places = await _places(
        db,
        {
            s.value["place"]
            for s in statements
            if s.prop in (Prop.OCCURRED_AT, Prop.LOCATED_AT)
        },
    )
    named = await people.names(
        db,
        [
            s.value["person"].removeprefix("person:")
            for s in statements
            if s.prop == Prop.INVOLVED
        ],
    )

    reviewed = [_about(s.subject) for s in statements if s.prop == Prop.REVIEW]
    props = dict(
        (
            await db.execute(
                select(Statement.id, Statement.prop).where(Statement.id.in_(reviewed))
            )
        ).all()
    )

    def short(ref: str) -> str:
        kind, _, rest = ref.partition(":")
        return f"{kind} {rest[:8]}"

    def summary(s: StatementIn) -> str:
        v = s.value
        if s.prop == Prop.INCIDENT:
            return f"title \u201c{v['title']['text']}\u201d"
        if s.prop == Prop.OCCURRED_AT:
            place = places.get(v["place"], {})
            name = place.get("en") or place.get("ar") or short(v["place"])
            return f"{name}, {data.when(s.valid_from, s.valid_to)}"
        if s.prop == Prop.LOCATED_AT:
            place = places.get(v["place"], {})
            name = place.get("en") or place.get("ar") or short(v["place"])
            return f"“{v['name']}” is {name}"
        if s.prop == Prop.INVOLVED:
            who = short(v["person"])
            if name := named.get(v["person"].removeprefix("person:")):
                who = f"{name} ({who})"
            return f"{who} as {v['role'].replace('_', ' ')}"
        if s.prop == Prop.SUPPORTED_BY:
            return f"claim {v['statement'][:8]}"
        if s.prop == Prop.REVIEW:
            why_ = f": {v['reason']}" if v.get("reason") else ""
            what = REVIEWED.get(props.get(_about(s.subject)), "the claim")
            return f"{v['verdict']} {what}{why_}"
        return f"undo {short(s.subject)}"

    return [
        {"prop": s.prop, "subject": short(s.subject), "summary": summary(s)}
        for s in statements
    ]
