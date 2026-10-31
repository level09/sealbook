"""Same-event groups in Discover: open same_event proposals between documents, joined
into connected groups, each decided as one candidate incident. Accepting signs one
gesture: the incident, its place and window when the kept reports name exactly one
gazetteer town, each kept post's reports as support, and an accept review of every
proposal between kept posts. Proposals that touch a dropped post stay open."""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select

from sealbook.blobs import read
from sealbook.collaboration.service import check_reviewer
from sealbook.discovery import evidence
from sealbook.discovery.service import available
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop, StatementIn, Tier
from sealbook.ledger.models import Blob, Payload, Statement, StatementTier
from sealbook.ledger.offline_sigs import GESTURE_MAX
from sealbook.monitor import data
from sealbook.monitor import filter as mfilter
from sealbook.places import normalise, resolve
from sealbook.review.findings import _lang
from sealbook.settings import Config
from sealbook.sheets import dates

KIND = "event_group"
TEXT = 1200  # characters of a post shown on its card
ADMIN = {"governorate", "district", "subdistrict"}
CHOICES = 8


def _key(shas) -> str:
    return hashlib.sha256("|".join(sorted(shas)).encode()).hexdigest()[:16]


async def _proposals(db, investigation: str | None) -> list[tuple[Statement, dict]]:
    """Open same_event proposals whose two documents are both still available."""
    rows = (
        await db.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == Prop.RELATION,
                Payload.value["kind"].astext == "same_event",
                StatementTier.tier == Tier.UNCONFIRMED,
                mfilter.statement_clause(
                    {"investigation": investigation, "time": None}
                ),
            )
            .order_by(Statement.recorded_at)
        )
    ).all()
    shas = {s for st, v in rows for s in (st.subject.removeprefix("blob:"), v["blob"])}
    if not shas:
        return []
    ok = set(
        await db.scalars(
            select(Blob.sha256).where(Blob.sha256.in_(shas), available(Blob.sha256))
        )
    )
    return [
        (st, v)
        for st, v in rows
        if st.subject.removeprefix("blob:") in ok and v["blob"] in ok
    ]


def _components(links: list[tuple[Statement, dict]]) -> list[list[str]]:
    parent: dict[str, str] = {}

    def root(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for st, v in links:
        parent[root(st.subject.removeprefix("blob:"))] = root(v["blob"])
    out: dict[str, list[str]] = {}
    for x in list(parent):
        out.setdefault(root(x), []).append(x)
    return [sorted(v) for v in out.values()]


async def _reports(db, shas: list[str]) -> dict[str, list[tuple[Statement, dict]]]:
    rows = (
        await db.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == Prop.REPORTS,
                Statement.span_blob.in_(shas),
                StatementTier.tier != Tier.REJECTED,
            )
            .order_by(Statement.span_blob, Statement.recorded_at)
        )
    ).all()
    out: dict[str, list] = {}
    for st, v in rows:
        if v is not None:
            out.setdefault(st.span_blob, []).append((st, v))
    return out


async def _posted(db, shas: list[str]) -> dict[str, str]:
    """Each document's earliest stated date, as an ISO day."""
    rows = (
        await db.execute(
            select(Statement.subject, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.prop == Prop.DOCUMENT_DATE,
                Statement.subject.in_([f"blob:{s}" for s in shas]),
                StatementTier.tier != Tier.REJECTED,
            )
        )
    ).all()
    out: dict[str, str] = {}
    for subject, v in rows:
        if v and (d := dates.interval(v.get("text"))):
            sha = subject.removeprefix("blob:")
            out[sha] = min(out.get(sha, d[0].isoformat()), d[0].isoformat())
    return out


def _day(st: Statement) -> str | None:
    return st.valid_from.isoformat() if st.valid_from else None


def _summary(posts: list[dict]) -> dict:
    """Title words, town and window read from the posts' reports."""
    reps = [r for p in posts for r in p["reports"]]
    towns = Counter(normalise(r["town"]) for r in reps if r.get("town"))
    top = towns.most_common(1)[0][0] if towns else None
    town = next(
        (r["town"] for r in reps if normalise(r.get("town") or "") == top), None
    )
    kinds = Counter(r["kind"] for r in reps if r.get("kind") not in (None, "other"))
    days = sorted(p["when"] for p in posts if p["when"])
    return {
        "town": town,
        "kind": kinds.most_common(1)[0][0] if kinds else None,
        "window": [days[0], days[-1]] if days else None,
        "towns": len(towns),
    }


async def _build(db, investigation: str | None, key: str | None = None) -> list[dict]:
    links = await _proposals(db, investigation)
    comps = _components(links)
    if key:
        comps = [c for c in comps if _key(c) == key]
    shas = [s for c in comps for s in c]
    reports = await _reports(db, shas)
    posted = await _posted(db, shas)
    out = []
    for c in comps:
        inside = set(c)
        posts = []
        for sha in c:
            reps = [
                {
                    "statement": str(st.id),
                    "kind": v.get("kind"),
                    "text": v.get("text"),
                    "town": v.get("town"),
                    "place": v.get("place"),
                    "date": _day(st),
                    "span": st.span,
                }
                for st, v in reports.get(sha, [])
            ]
            when = next((r["date"] for r in reps if r["date"]), None) or posted.get(sha)
            posts.append(
                {"sha": sha, "posted": posted.get(sha), "when": when, "reports": reps}
            )
        group_links = [
            {
                "statement": str(st.id),
                "a": st.subject.removeprefix("blob:"),
                "b": v["blob"],
                "why": v.get("why", ""),
            }
            for st, v in links
            if st.subject.removeprefix("blob:") in inside
        ]
        s = _summary(posts)
        out.append(
            {
                "id": _key(c),
                "kind": KIND,
                "label": "Same event",
                "investigation": investigation,
                "posts": posts,
                "links": group_links,
                "summary": s,
            }
        )
    out.sort(key=lambda g: (-len(g["posts"]), g["id"]))
    return out


async def _places(db, towns: set[str]) -> dict[str, list]:
    return await resolve.candidates_many(db, {normalise(t) for t in towns if t})


def _place_row(p) -> dict:
    gov = p.governorate
    return {
        "id": str(p.id),
        "en": p.name_en,
        "ar": p.name_ar,
        "kind": p.kind,
        "in": (gov.name_en or gov.name_ar) if gov else None,
    }


def _one(cands: list) -> object | None:
    """The single place a town name means once admin areas that share the name (a
    governorate named after its capital) are set aside, else None."""
    local = [c for c in cands if c.kind not in ADMIN] or cands
    return local[0] if len(local) == 1 else None


async def groups(db, user, investigation: str | None) -> list[dict]:
    """Open same-event groups, largest first, as queue rows."""
    found = await _build(db, investigation)
    places = await _places(db, {g["summary"]["town"] for g in found})
    for g in found:
        town = g["summary"]["town"]
        cands = places.get(normalise(town), []) if town else []
        one = _one(cands)
        g["summary"]["place"] = _place_row(one) if one else None
        g["count"] = {"posts": len(g["posts"]), "links": len(g["links"])}
        del g["posts"], g["links"]
    return found


def _marked(text: str, spans: list[dict]) -> list[dict]:
    """`text` as runs, each report span marked; overlapping spans merge."""
    cuts = sorted(
        (s["start"], s["end"])
        for s in spans
        if s and "start" in s and s["end"] <= len(text)
    )
    out, at = [], 0
    for a, b in cuts:
        a = max(a, at)
        if b <= a:
            continue
        if a > at:
            out.append({"t": text[at:a]})
        out.append({"t": text[a:b], "mark": True})
        at = b
    if at < len(text):
        out.append({"t": text[at:]})
    return out


async def detail(db, user, key: str) -> dict | None:
    """One group with each post's text and its reports marked, where this session
    may read the source; a sensitive source stays closed until its logged reveal."""
    found = await _build(db, None, key)
    if not found:
        return None
    g = found[0]
    ctx = await data.Context.load(db, blobs=[p["sha"] for p in g["posts"]])
    places = await _places(db, {r["town"] for p in g["posts"] for r in p["reports"]})
    for p in g["posts"]:
        sha = p["sha"]
        open_ = await evidence.viewable(db, sha, user) or evidence.granted(sha)
        doc = ctx.doc(sha)
        p["title"] = doc["title"]
        p["sensitive"] = bool(doc.get("sensitive"))
        p["text"] = None
        if open_:
            blob = await db.get(Blob, sha)
            if blob and blob.mime.startswith("text/"):
                text = read(blob, Config.BLOB_DIR).decode(errors="replace")
                cut = text[:TEXT]
                p["text"] = _marked(cut, [r["span"] for r in p["reports"]])
                p["more"] = len(text) > TEXT
        for r in p["reports"]:
            cands = places.get(normalise(r["town"]), []) if r["town"] else []
            r["places"] = [_place_row(c) for c in cands[:3]]
            if not open_:
                r["text"] = r["place"] = None
    town = g["summary"]["town"]
    cands = places.get(normalise(town), []) if town else []
    one = _one(cands)
    g["summary"]["place"] = _place_row(one) if one else None
    g["summary"]["candidates"] = len(cands)
    # A name that means several places: the analyst says which before signing.
    g["summary"]["choices"] = [] if one else [_place_row(c) for c in cands[:CHOICES]]
    return g


async def log_view(db, user, g: dict) -> Statement:
    """Opening a group is a use of its posts' reports and links: one access record."""
    who = await write.principal_for_user(db, user.id, user.name or user.email)
    return await write.append(
        db,
        StatementIn(
            subject=f"group:{g['id']}",
            prop=Prop.ACCESS,
            value={
                "action": "view",
                "at": datetime.now(UTC).isoformat(),
                "statements": [r["statement"] for p in g["posts"] for r in p["reports"]]
                + [lk["statement"] for lk in g["links"]],
            },
            author_id=who.id,
        ),
    )


async def _group(db, user, key: str) -> dict:
    found = await _build(db, None, key)
    if not found:
        raise LookupError("no open group with that key; it may have been decided")
    g = found[0]
    for lk in g["links"]:
        await check_reviewer(
            db, await db.get(Statement, uuid.UUID(lk["statement"])), user
        )
    return g


def _review(sid: str, verdict: str, why_: str, gesture: str, author) -> StatementIn:
    return StatementIn(
        subject=f"statement:{sid}",
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
    db, user, key: str, kept: list[str], title: str, author, place: str | None = None
):
    """The statements accepting group `key` with the posts in `kept` writes, all
    under one gesture id. ValueError for a bad request, LookupError when the group
    is gone, PermissionError when a link is assigned to someone else."""
    g = await _group(db, user, key)
    shas = [p["sha"] for p in g["posts"]]
    if not isinstance(kept, list) or len(set(kept)) != len(kept) or len(kept) < 2:
        raise ValueError("keep at least two posts, each once")
    if any(k not in shas for k in kept):
        raise ValueError("kept posts must belong to the group")
    title = title.strip() if isinstance(title, str) else ""
    if not title or len(title) > 300:
        raise ValueError("a new incident needs a title of 1 to 300 characters")
    keep = set(kept)
    posts = [p for p in g["posts"] if p["sha"] in keep]
    links = [lk for lk in g["links"] if lk["a"] in keep and lk["b"] in keep]
    if not links:
        raise ValueError("no proposed link joins the kept posts")
    support = [r["statement"] for p in posts for r in p["reports"]]
    if not support:
        raise ValueError("the kept posts carry no report to support an incident")
    gesture = str(uuid.uuid4())
    subject = f"incident:{uuid.uuid4()}"
    s = _summary(posts)
    out = [
        StatementIn(
            subject=subject,
            prop=Prop.INCIDENT,
            value={
                "title": {"text": title, "lang": _lang(title)},
                "kind": s["kind"] or "unspecified",
                "gesture": gesture,
            },
            author_id=author,
        )
    ]
    cands = (
        (await _places(db, {s["town"]})).get(normalise(s["town"]), [])
        if s["town"]
        else []
    )
    one = _one(cands)
    if place is not None:
        one = next((c for c in cands if str(c.id) == place), None)
        if one is None:
            raise ValueError("the place must be one the reports' town can mean")
    elif one is None and len(cands) > 1:
        # The day is recorded with the place, so signing here would lose both.
        raise ValueError(f"choose which place {s['town']} means")
    if one and s["window"]:
        lo, hi = (datetime.fromisoformat(d).date() for d in s["window"])
        out.append(
            StatementIn(
                subject=subject,
                prop=Prop.OCCURRED_AT,
                value={"place": f"place:{one.id}", "gesture": gesture},
                author_id=author,
                valid_from=lo,
                valid_to=hi,
            )
        )
    out += [
        StatementIn(
            subject=subject,
            prop=Prop.SUPPORTED_BY,
            value={"statement": sid, "gesture": gesture},
            author_id=author,
        )
        for sid in support
    ]
    out += [_review(lk["statement"], "accept", "", gesture, author) for lk in links]
    if len(out) > GESTURE_MAX:
        raise ValueError(
            f"keep fewer posts: one gesture signs at most {GESTURE_MAX} rows"
        )
    return gesture, out


async def reject(db, user, key: str, why_: str, author):
    """Not one event: a reject review of every proposal in the group."""
    if not isinstance(why_, str) or len(why_) > 4000:
        raise ValueError("reason must be text of at most 4000 characters")
    g = await _group(db, user, key)
    if len(g["links"]) > GESTURE_MAX:
        raise ValueError(f"a gesture signs at most {GESTURE_MAX} rows")
    gesture = str(uuid.uuid4())
    return gesture, [
        _review(lk["statement"], "reject", why_.strip(), gesture, author)
        for lk in g["links"]
    ]
