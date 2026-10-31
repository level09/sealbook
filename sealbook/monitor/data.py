"""Read side of the evidence monitor: one row shape per ledger object, shared by every
panel, the side panel pages and the live stream."""

from __future__ import annotations

import math
import re
import uuid
from calendar import monthrange
from datetime import date
from itertools import combinations

from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from sealbook import blobs, method
from sealbook.cases.models import Case
from sealbook.ledger.blobmeta import span_check
from sealbook.ledger.contracts import (
    ASSESSED_PROPS,
    EXTRACTED_PROPS,
    PRIVATE_PROPS,
    Prop,
    Tier,
)
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Chain,
    Payload,
    Principal,
    Run,
    RunEvent,
    Statement,
    StatementTier,
)
from sealbook.ledger.write import sig_problem, subject_head
from sealbook.monitor import filter as mfilter
from sealbook.places import normalise
from sealbook.settings import Config
from sealbook.sources.data import title

# Personal data: masked by default when the source is sensitive.
PERSONAL = {
    Prop.NAME,
    Prop.BIRTH_DATE,
    Prop.PLACE_OF_BIRTH,
    Prop.FATHER_NAME,
    Prop.MOTHER_NAME,
    Prop.SEX,
    Prop.AGE,
    Prop.OCCUPATION,
    Prop.EVENT,
}
# Props whose value is a place a person was at, or was moved to.
LOCATED = {
    Prop.ARRESTED_AT,
    Prop.DETAINED_AT,
    Prop.DETENTION_BRANCH,
    Prop.TRANSFERRED_TO,
    Prop.SEEN_AT,
    Prop.LAST_SEEN,
}
# Where the person was at an instant: two of these on one day at different places
# conflict. Branch membership and transfer destinations do not.
POSITION = {Prop.ARRESTED_AT, Prop.DETAINED_AT, Prop.SEEN_AT, Prop.LAST_SEEN}
# Statements about statements: never cards, never map points.
META = {
    Prop.REVIEW,
    Prop.RETRACTION,
    Prop.ACCESS,
    Prop.REVIEW_REQUEST,
    Prop.REVIEW_COMMENT,
    Prop.FINGERPRINT,
}


def blob_text(blob: Blob) -> str | None:
    return (
        blobs.read(blob, Config.BLOB_DIR).decode()
        if blob.mime.startswith("text/")
        else None
    )


def mask(text: str) -> str:
    """Same length and line shape, no content, so span offsets still line up."""
    return re.sub(r"\S", "•", text)


def _plural(n: int, one: str) -> str:
    return f"{n} {one}{'' if n == 1 else 's'}"


RELATION_WORDS = {
    "family": "Family of another person",
    "cellmate": "Cellmate of another person",
    "command": "In a command relation with another person",
    "contains": "Contains another document",
    "contained_in": "An excerpt of another document",
    "related": "Related to another document",
    "same_event": "Reports the same event as another document",
}


def display(value, prop: str | None = None) -> str | None:
    """A statement value in words. Structured values get a label per shape, never
    a dict string or a raw person:, statement: or blob: id."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return str(value)
    if prop in ASSESSED_PROPS:
        return method.words(prop, value)
    for key in ("text", "date", "result", "hex", "verdict", "action"):
        if key in value:
            return str(value[key])
    if "title" in value:
        return display(value["title"])
    if prop == Prop.RELATION:
        return RELATION_WORDS.get(value.get("kind"), "Related to another record")
    if "person" in value and prop != Prop.INVOLVED:
        n = len(value.get("basis") or [])
        what = (
            "A different person from another mention"
            if prop == Prop.NOT_SAME
            else "The same person as another mention"
        )
        return f"{what}, based on {_plural(n, 'statement')}" if n else what
    if "blob" in value and ("sound" in value or "picture" in value):
        return "Matches another recording"
    if "name" in value and "place" in value:
        return str(value["name"])
    if "statement" in value:
        return "A supporting claim"
    if "role" in value:
        return f"Involved as {str(value['role']).replace('_', ' ')}"
    parts = [
        f"{k.replace('_', ' ')}: {v}"
        for k, v in value.items()
        if isinstance(v, (str, int, float))
        and k not in ("at", "gesture", "lang", "fingerprint", "session")
        and not re.match(r"^(person|statement|blob|place|incident|finding):", str(v))
        and not re.fullmatch(r"[0-9a-f]{32,64}|[0-9a-f-]{36}", str(v))
    ]
    return ", ".join(parts) or "A structured value"


def when(valid_from: date | None, valid_to: date | None) -> str | None:
    if valid_from is None:
        return None
    if valid_to in (None, valid_from):
        return valid_from.isoformat()
    last = monthrange(valid_to.year, valid_to.month)[1]
    if (valid_from.day, valid_to.day) == (
        1,
        last,
    ) and valid_from.month == valid_to.month:
        return valid_from.strftime("%Y-%m")
    return f"{valid_from.isoformat()} to {valid_to.isoformat()}"


def subject_label(subject: str, files: dict) -> str:
    kind, _, ref = subject.partition(":")
    if kind == "blob":
        return files.get(ref, f"blob {ref[:8]}")
    return f"{kind} {ref[:6]}"


TOOL_WORDS = {
    "extract": "AI extraction",
    "assess": "AI first pass",
    "extract_sheet": "AI sheet extraction",
    "transcribe": "AI transcription",
    "embed": "AI embedding",
    "propose": "AI proposal",
    "match": "name matching",
    "discover": "discovery",
    "hash": "hashing",
    "fingerprint": "fingerprinting",
    "c2pa_verify": "content credentials check",
    "url_check": "link check",
    "pdf": "PDF text reading",
    "ocr": "text recognition",
    "asr": "AI transcription",
    "embed_actor": "AI profile embedding",
    "index": "search indexing",
    "thumbnail": "thumbnails",
    "geotag": "camera location reading",
    "capture": "web capture",
    "brief": "intake brief",
    "link": "linker",
    "standing": "standing questions",
    "gaps": "gap finder",
    "contradictions": "contradiction hunter",
    "notice": "noticer",
    "approximate": "place finder",
    "notice_link": "noticer link proposal",
    "import": "import",
}
# Run event stages and drop reasons are storage tokens; read them as words.
EVENT_WORDS = {
    "claimed": "started",
    "review_accept": "accepted in review",
    "review_reject": "rejected in review",
    "not_utf8": "not UTF-8 text",
    "ocr_empty": "no text recognised",
}


def work(tool: str | None) -> str:
    """A run's work in plain words: "search indexing", never "index run"."""
    return TOOL_WORDS.get(tool) or (tool or "unknown work").replace("_", " ")


def event_words(token: str | None) -> str | None:
    return token and EVENT_WORDS.get(token, token.replace("_", " "))


class Context:
    """Principals, runs, acquisitions and document dates behind a set of rows,
    loaded once per request."""

    def __init__(self):
        self.principals: dict = {}
        self.runs: dict = {}
        self.acqs: dict = {}
        self.doc_dates: dict = {}
        self.source: dict = {}  # derived text blob -> the acquired blob it was read from

    @classmethod
    async def load(cls, db: AsyncSession, blobs=(), runs=(), principals=()):
        ctx = cls()
        blobs = {b for b in blobs if b}
        if runs := {r for r in runs if r}:
            ctx.runs = {
                r.id: r
                for r in (
                    await db.execute(select(Run).where(Run.id.in_(runs)))
                ).scalars()
            }
            for run in ctx.runs.values():
                blobs.update(run.inputs.get("blobs", []))
        principals = {p for p in principals if p} | {
            r.principal_id for r in ctx.runs.values()
        }
        if blobs:
            # Page, OCR and transcript text has no acquisition: it is its source's.
            ctx.source = {
                sha: src
                for sha, src in await db.execute(
                    select(Blob.sha256, mfilter.source(Blob.sha256)).where(
                        Blob.sha256.in_(blobs)
                    )
                )
                if src != sha
            }
            blobs |= set(ctx.source.values())
            for a in (
                await db.execute(
                    select(Acquisition)
                    .where(Acquisition.blob_sha256.in_(blobs))
                    .order_by(Acquisition.at)
                )
            ).scalars():
                ctx.acqs.setdefault(a.blob_sha256, []).append(a)
                principals.add(a.collector_id)
            dated = await db.execute(
                select(Statement.subject, Statement.valid_from, Statement.valid_to)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .where(
                    Statement.prop == Prop.DOCUMENT_DATE,
                    Statement.subject.in_([f"blob:{b}" for b in blobs]),
                    Statement.valid_from.is_not(None),
                    StatementTier.tier != Tier.REJECTED,
                )
            )
            for subject, lo, hi in dated:
                ctx.doc_dates[subject.removeprefix("blob:")] = (lo, hi or lo)
        while principals:
            found = (
                await db.execute(select(Principal).where(Principal.id.in_(principals)))
            ).scalars()
            principals = set()
            for p in found:
                ctx.principals[p.id] = p
                if p.on_behalf_of and p.on_behalf_of not in ctx.principals:
                    principals.add(p.on_behalf_of)
        return ctx

    @property
    def files(self) -> dict:
        return {sha: self.doc(sha)["title"] for sha in self.acqs}

    def doc(self, sha: str | None) -> dict:
        src = self.source.get(sha, sha)
        acqs = self.acqs.get(src, [])
        first = acqs[0] if acqs else None
        return {
            "sha256": sha,
            # The acquired file behind derived text (OCR, PDF page text, transcript),
            # which carries the case and sensitivity; `sha` itself otherwise.
            "source_sha256": src,
            "file": (
                (first.context.get("file") or first.context.get("filename"))
                if first
                else None
            )
            or (sha or "")[:12],
            "title": title(
                first.context.get("title"),
                first.context.get("file") or first.context.get("filename"),
                first.doc_type,
            )
            if first
            else (sha or "")[:12],
            # The name the file arrived under, for display; `file` is a match key.
            "filename": first.context.get("filename") if first else None,
            "doc_type": first.doc_type if first else None,
            "origins": sorted({a.origin for a in acqs}),
            # No acquisition means no sensitivity was ever set: sensitive by default.
            "sensitive": not acqs or any(a.sensitivity == "sensitive" for a in acqs),
            "date": when(*self.doc_dates[src]) if src in self.doc_dates else None,
            "date_to": self.doc_dates[src][1].isoformat()
            if src in self.doc_dates
            else None,
        }

    def principal(self, pid) -> dict | None:
        p = self.principals.get(pid)
        if p is None:
            return None
        return {"id": str(p.id), "kind": p.kind, "name": p.name}

    def actor(self, author_id, run_id) -> dict:
        """Who stands behind a row: human, extractor run, external agent (on behalf
        of whom) or import."""
        author, run = self.principals[author_id], self.runs.get(run_id)
        base = {"principal": str(author.id), "agent": author.kind == "agent"}
        if author.kind == "human":
            return base | {"kind": "human", "label": author.name}
        if run is not None and run.tool == "import":
            return base | {
                "kind": "import",
                "label": "imported with the source",
                "detail": f"import by {author.name}",
            }
        if author.on_behalf_of:
            boss = self.principals[author.on_behalf_of]
            return base | {
                "kind": "agent",
                "label": f"{author.name} for {boss.name}",
                "for": boss.name,
            }
        # The work in plain words; the model is secondary detail, never the label.
        tool = run.tool if run else None
        return base | {
            "kind": "extractor",
            "label": work(tool),
            "detail": run.model_served if run else None,
        }

    @staticmethod
    def blob_of(st: Statement) -> str | None:
        return st.span_blob or (
            st.subject.removeprefix("blob:") if st.subject.startswith("blob:") else None
        )

    def masked(self, st: Statement) -> bool:
        """Personal data from a sensitive (or unknown) source stays masked."""
        sha = self.blob_of(st)
        return st.prop in PERSONAL and (sha is None or self.doc(sha)["sensitive"])

    def statement(self, st: Statement, tier: str, reason: str, value) -> dict:
        sha = self.blob_of(st)
        doc = self.doc(sha) if sha else None
        masked = self.masked(st)
        own = st.valid_from is not None
        lo, hi = (
            (st.valid_from, st.valid_to or st.valid_from)
            if own
            else self.doc_dates.get(sha, (None, None))
        )
        return {
            "id": str(st.id),
            "subject": st.subject,
            "subject_label": subject_label(st.subject, self.files),
            "prop": st.prop,
            "display": None if masked else display(value, st.prop),
            # The other person of a relation, an id the person page turns into a name.
            **(
                {"other": value["person"]}
                if st.prop == Prop.RELATION
                and isinstance(value, dict)
                and "person" in value
                else {}
            ),
            "masked": masked,
            "erased": value is None,
            "tier": tier,
            "reason": reason,
            "actor": self.actor(st.author_id, st.run_id),
            "run_id": str(st.run_id) if st.run_id else None,
            "doc": doc,
            "span": st.span,
            "from": lo.isoformat() if lo else None,
            "to": hi.isoformat() if hi else None,
            "when": when(lo, hi),
            "inherited": bool(lo) and not own,
            "evidence": st.evidence,
            "recorded_at": st.recorded_at.isoformat(),
        }


def _statement_query():
    return (
        select(Statement, StatementTier.tier, StatementTier.reason, Payload.value)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .outerjoin(Payload, Payload.commitment == Statement.commitment)
    )


async def statement_rows(db: AsyncSession, *where, limit=500, order=None) -> list[dict]:
    q = (
        _statement_query()
        .where(*where, Statement.prop.not_in(PRIVATE_PROPS))
        .order_by(order if order is not None else Statement.recorded_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(q)).all()
    ctx = await Context.load(
        db,
        blobs=[r[0].span_blob for r in rows]
        + [
            r[0].subject.removeprefix("blob:")
            for r in rows
            if r[0].subject.startswith("blob:")
        ],
        runs=[r[0].run_id for r in rows],
        principals=[r[0].author_id for r in rows],
    )
    return [ctx.statement(*r) for r in rows]


async def feed_rows(
    db: AsyncSession, f: dict, *, after: int | None = None, ids=None, limit=200
) -> list[dict]:
    """Run events matching the filter: newest first, or ascending after a cursor
    (replay), or exactly `ids` (live matching)."""
    q = (
        select(RunEvent)
        .join(Run, Run.id == RunEvent.run_id)
        .where(mfilter.run_clause(f, Run.inputs))
    )
    if ids is not None:
        q = q.where(RunEvent.id.in_(ids)).order_by(RunEvent.id)
    elif after is not None:
        q = q.where(RunEvent.id > after).order_by(RunEvent.id).limit(limit)
    else:
        q = q.order_by(RunEvent.id.desc()).limit(limit)
    events = (await db.execute(q)).scalars().all()
    runs = {
        r.id: r
        for r in (
            await db.execute(select(Run).where(Run.id.in_({e.run_id for e in events})))
        ).scalars()
    }
    first = {rid: r.inputs.get("blobs", [None])[0] for rid, r in runs.items()}
    # Index and embed runs read derived text; name the source it came from.
    source = (
        dict(
            (
                await db.execute(
                    select(Blob.sha256, mfilter.source(Blob.sha256)).where(
                        Blob.sha256.in_({b for b in first.values() if b})
                    )
                )
            ).all()
        )
        if any(first.values())
        else {}
    )
    ctx = await Context.load(db, blobs=source.values(), runs=list(runs))
    rows = []
    for e in events:
        run = ctx.runs[e.run_id]
        sha = first[e.run_id]
        rows.append(
            {
                "id": e.id,
                "at": e.at.isoformat(),
                "stage": event_words(e.stage),
                "seq": e.seq,
                "counts": e.counts,
                "drop_reason": event_words(e.drop_reason),
                "human_gesture": e.human_gesture,
                "run_id": str(run.id),
                "tool": run.tool,
                "work": work(run.tool),
                "status": run.status,
                "actor": ctx.actor(run.principal_id, run.id),
                "doc": ctx.doc(source.get(sha, sha)) if sha else None,
            }
        )
    return rows


async def document_rows(db: AsyncSession, f: dict, limit=500) -> list[dict]:
    has_acq = select(Acquisition.blob_sha256).distinct()
    shas = (
        (
            await db.execute(
                select(Blob.sha256)
                .where(Blob.sha256.in_(has_acq), mfilter.blob_clause(f, Blob.sha256))
                .order_by(Blob.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    if not shas:
        return []
    ctx = await Context.load(db, blobs=shas)
    blob = mfilter.statement_blob()
    counts = await db.execute(
        select(blob, StatementTier.tier, func.count())
        .select_from(Statement)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(blob.in_(shas), Statement.prop.not_in(META))
        .group_by(blob, StatementTier.tier)
    )
    tiers: dict = {}
    for sha, tier, n in counts:
        tiers.setdefault(sha, {})[tier] = n
    return [ctx.doc(sha) | {"tiers": tiers.get(sha, {})} for sha in shas]


async def investigations(db: AsyncSession) -> list[str]:
    """Case keys from the case table, the one list every switcher uses."""
    return list((await db.execute(select(Case.slug).order_by(Case.slug))).scalars())


# --- tracks -------------------------------------------------------------------


# Last seen somewhere, then arrested, is one plausible day, not two places at once.
SEQUENCE = frozenset({Prop.LAST_SEEN, Prop.ARRESTED_AT})


def _apart(a: dict, b: dict) -> bool:
    """Two same-day positions that cannot both hold: different places, not a
    last-seen and arrest pair, and farther apart than the larger of their
    precisions (the rule discovery's `near()` uses for shared presence)."""
    if (a["place_id"] or normalise(a["place"])) == (
        b["place_id"] or normalise(b["place"])
    ):
        return False
    if {a["prop"], b["prop"]} == SEQUENCE:
        return False
    reach = max(a.get("precision_m") or 0, b.get("precision_m") or 0)
    if a.get("precision_m") and b.get("precision_m") and a.get("lonlat"):
        return _metres(a["lonlat"], b["lonlat"]) > reach
    return True


def places_apart(prop: str, a, b) -> bool:
    """Two resolved places (Place rows) that cannot both hold on one day, by the rule
    above: a quarter and its city do not; two towns farther apart than their
    precision do."""

    def point(p):
        lonlat = [float(p.lon), float(p.lat)] if p.lon is not None else None
        return {
            "place_id": str(p.id),
            "place": "",
            "prop": prop,
            "lonlat": lonlat,
            "precision_m": p.precision_m,
        }

    return _apart(point(a), point(b))


def _metres(a, b) -> float:
    """Great-circle distance between two [lon, lat] points."""
    (lon1, lat1), (lon2, lat2) = (map(math.radians, p) for p in (a, b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_008.8 * math.asin(math.sqrt(h))


def track(points: list[dict]) -> dict:
    """One subject's located statements as a sequence of dated steps. Pure.

    `points` carry "id", "place", "place_id", "from" (ISO date), "prop" and
    "inherited". Points on one date form a step; a step whose position props name
    places that cannot both hold (`_apart`) is a fork. Places compare by resolved id,
    so two spellings of one place never fork; an unresolved place compares by its
    normalised text.
    Silences between steps and after the last one are gaps, never smoothed."""
    steps: dict = {}
    for p in sorted(points, key=lambda p: (p["from"], p["id"])):
        steps.setdefault(p["from"], []).append(p)
    out, prev = [], None
    for day, pts in steps.items():
        places = sorted({p["place"] for p in pts})
        positions = [p for p in pts if p["prop"] in POSITION and not p["inherited"]]
        out.append(
            {
                "date": day,
                "places": places,
                "ids": [p["id"] for p in pts],
                "fork": any(_apart(a, b) for a, b in combinations(positions, 2)),
                "gap_days": (date.fromisoformat(day) - prev).days if prev else None,
            }
        )
        prev = date.fromisoformat(day)
    return {"steps": out, "gap_after": out[-1]["date"] if out else None}


def tracks(points: list[dict], key: str = "subject") -> dict[str, dict]:
    """`track()` for each value of `key`, so one story holds many rows. Pure."""
    by: dict = {}
    for p in points:
        by.setdefault(p[key], []).append(p)
    return {k: track(pts) for k, pts in by.items()}


def map_layer(rows: list[dict], places: dict) -> dict:
    """Located rows at their resolved places. `places` maps a statement id (UUID) to
    its Place or None; rows without a place are listed as unresolved, not dropped."""
    points, unresolved, by_subject = [], {}, {}
    label_of: dict = {}  # (claim text, place id) -> unique label on this map
    for r in rows:
        if r["prop"] not in LOCATED or r["masked"] or not r["display"]:
            continue
        place = places.get(uuid.UUID(r["id"]))
        if place is None:
            if r["tier"] != Tier.REJECTED:
                seen = unresolved.setdefault(
                    r["display"], {"id": r["id"], "place": r["display"], "count": 0}
                )
                seen["count"] += 1
            continue
        if not r["from"]:
            continue
        # One text confirmed to two places must stay two places for tracks and labels.
        key = (r["display"], place.id)
        if key not in label_of:
            taken = any(k[0] == r["display"] for k in label_of)
            label_of[key] = (
                f"{r['display']} ({place.id.hex[:6]})" if taken else r["display"]
            )
        label = label_of[key]
        point = {
            "id": r["id"],
            "subject": r["subject"],
            "subject_label": r["subject_label"],
            "prop": r["prop"],
            "place": label,
            "place_id": str(place.id),
            "lonlat": [float(place.lon), float(place.lat)],
            "precision_m": place.precision_m,
            "from": r["from"],
            "when": r["when"],
            "inherited": r["inherited"],
            "tier": r["tier"],
            "agent": r["actor"]["agent"],
        }
        points.append(point)
        if r["tier"] != Tier.REJECTED:
            by_subject.setdefault(r["subject"], []).append(point)
    tracks = {
        s: track(pts) | {"label": pts[0]["subject_label"]}
        for s, pts in by_subject.items()
    }
    return {
        "points": points,
        "tracks": tracks,
        "places": {p["place"]: p["lonlat"] for p in points},
        "unresolved": list(unresolved.values()),
        "attribution": "GeoNames (CC BY 4.0)",
    }


# --- review cards ---------------------------------------------------------------


async def invariants(db: AsyncSession, st: Statement, row: dict) -> list[dict]:
    """The checks that keep Accept disabled: span exists in the source bytes (or,
    for identity, its basis stands), date inside the document's range, subject not
    stale. ok is True, False, or None when it cannot be checked yet. A failed stale
    check carries `acknowledge`, the head a reviewer may accept against."""
    if st.prop == Prop.MEDIA_MATCH:
        from sealbook.discovery.service import available

        value = await db.get(Payload, st.commitment)
        shas = [st.subject.removeprefix("blob:"), value.value["blob"]] if value else []
        ok = bool(shas) and all(
            [
                await db.scalar(
                    select(available(Blob.sha256)).where(Blob.sha256 == sha)
                )
                for sha in shas
            ]
        )
        span = {
            "name": "sources available",
            "ok": ok,
            "note": "both sources retained" if ok else "a source is missing or erased",
        }
    elif st.prop in (Prop.SAME_AS, Prop.NOT_SAME):
        span = {"name": "basis stands"} | await _basis(db, st)
    elif st.prop in EXTRACTED_PROPS or st.span_blob or st.span:
        ok, why = span_check(
            await db.get(Blob, st.span_blob) if st.span_blob else None, st.span
        )
        span = {
            "name": "span exists",
            "ok": ok,
            "note": "inside the source" if ok else why,
        }
    else:
        span = {"name": "span exists", "ok": True, "note": "no span needed"}

    dated = {"name": "date in range"}
    doc = row["doc"] or {}
    if st.valid_from is None:
        dated |= {"ok": True, "note": "no date claimed"}
    elif not doc.get("date_to"):
        dated |= {"ok": None, "note": "document date unknown"}
    else:
        late = st.valid_from > date.fromisoformat(doc["date_to"])
        dated |= {
            "ok": not late,
            "note": f"{'after' if late else 'within'} the document date {doc['date']}",
        }

    # Stale means a value for this prop was accepted after the head the proposal saw.
    # Other props moving on (a name accepted, a view logged) do not touch this card.
    seq = select(func.max(Chain.seq)).join(
        Statement,
        (Chain.kind == "statement") & (Chain.ref == cast(Statement.id, Text)),
    )
    accepted = [
        str(i)
        for i in (
            await db.execute(
                select(Statement.id)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .where(
                    Statement.subject == st.subject,
                    Statement.prop == st.prop,
                    Statement.id != st.id,
                    StatementTier.tier == Tier.ACCEPTED,
                )
            )
        ).scalars()
    ]
    # A sibling became accepted when it was written or when a review accepted it,
    # whichever came later: an old proposal accepted today is a new rival.
    rival = accepted and await db.scalar(
        seq.where(
            Statement.id.in_(accepted)
            | (
                (Statement.prop == Prop.REVIEW)
                & Statement.subject.in_([f"statement:{i}" for i in accepted])
            )
        )
    )
    seen = (
        await db.scalar(seq.where(Statement.hash == st.based_on)) if st.based_on else 0
    )
    stale = {"name": "subject not stale", "ok": (rival or 0) <= (seen or 0)}
    stale["note"] = (
        "subject unchanged" if stale["ok"] else "subject changed since proposal"
    )
    if not stale["ok"]:
        # The reviewer accepts against the head they were shown; commit re-checks it.
        stale["acknowledge"] = await subject_head(db, st.subject)
    return [span, dated, stale]


async def _basis(db: AsyncSession, st: Statement) -> dict:
    """Identity proposals carry no span. They name their basis statements in the
    value (the watch matcher) or in the run's chained inputs (propose_identity)."""
    value = await db.scalar(
        select(Payload.value).where(Payload.commitment == st.commitment)
    )
    run = await db.get(Run, st.run_id) if st.run_id else None
    refs = (value or {}).get("basis") or (
        (run.inputs or {}).get("basis") if run else None
    )
    if not refs:
        return {"ok": False, "note": "no basis statements named"}
    ids = [uuid.UUID(r.removeprefix("statement:")) for r in refs]
    tiers = dict(
        (
            await db.execute(
                select(Statement.id, StatementTier.tier)
                .outerjoin(StatementTier, StatementTier.statement_id == Statement.id)
                .where(Statement.id.in_(ids))
            )
        ).all()
    )
    if len(tiers) != len(ids):
        return {"ok": False, "note": "a basis statement does not exist"}
    if Tier.REJECTED in tiers.values():
        return {"ok": False, "note": "a basis statement was rejected"}
    return {"ok": True, "note": f"{len(ids)} basis statements stand"}


def _used():
    """When a pending statement was last in use: an access record lists it, or its
    subject was viewed."""
    access = aliased(Statement)
    return (
        select(func.max(access.recorded_at))
        .join(Payload, Payload.commitment == access.commitment)
        .where(
            access.prop == Prop.ACCESS,
            (access.subject == Statement.subject)
            | Payload.value["statements"].contains(
                func.jsonb_build_array(cast(Statement.id, Text))
            ),
        )
        .scalar_subquery()
    )


async def _cards(db: AsyncSession, q) -> list[dict]:
    rows = (await db.execute(q)).all()
    ctx = await Context.load(
        db,
        blobs=[r[0].span_blob for r in rows],
        runs=[r[0].run_id for r in rows],
        principals=[r[0].author_id for r in rows],
    )
    cards = []
    for st, tier, reason, value, used in rows:
        row = ctx.statement(st, tier, reason, value)
        row["used"] = used is not None
        row["invariants"] = await invariants(db, st, row)
        cards.append(row)
    return cards


async def queue_cards(db: AsyncSession, f: dict, limit=200) -> list[dict]:
    """Pending statements, ordered by use and never by confidence: anything a person
    view, brief or export touched comes first, then oldest first."""
    used = _used()
    q = (
        _statement_query()
        .add_columns(used)
        .where(
            StatementTier.tier == Tier.UNCONFIRMED,
            Statement.prop.not_in(META),
            mfilter.statement_clause(f),
        )
        .order_by(used.is_(None), Statement.recorded_at)
        .limit(limit)
    )
    return await _cards(db, q)


async def card(db: AsyncSession, sid: uuid.UUID) -> dict | None:
    """One statement as a review card, whatever its tier."""
    q = _statement_query().add_columns(_used()).where(Statement.id == sid)
    found = await _cards(db, q)
    return found[0] if found else None


# --- side panel pages -------------------------------------------------------------


async def _verify_sig(db: AsyncSession, st: Statement) -> dict:
    sig = st.sig or {}
    if not sig:
        return {"kind": None, "verifies": None, "by": "unsigned"}
    problem = await sig_problem(db, st)
    return {
        "kind": sig["kind"],
        "verifies": problem is None,
        "problem": problem,
        "by": "run key" if sig["kind"] == "ed25519" else "passkey",
        "demo": bool(sig.get("demo")),
    }


async def chain_entry(db: AsyncSession, kind: str, ref: str) -> dict | None:
    e = (
        await db.execute(select(Chain).where(Chain.kind == kind, Chain.ref == ref))
    ).scalar()
    return {"seq": e.seq, "hash": e.hash, "at": e.at.isoformat()} if e else None


async def statement_page(db: AsyncSession, sid: uuid.UUID) -> dict | None:
    found = (await db.execute(_statement_query().where(Statement.id == sid))).first()
    if found is None or found[0].prop in PRIVATE_PROPS:
        return None
    st = found[0]
    ctx = await Context.load(
        db,
        blobs=[st.span_blob, st.subject.removeprefix("blob:")],
        runs=[st.run_id],
        principals=[st.author_id],
    )
    row = ctx.statement(*found)
    about = await statement_rows(
        db, Statement.subject == f"statement:{st.id}", order=Statement.recorded_at
    )
    media = None
    if st.prop == Prop.MEDIA_MATCH:
        media = await media_pair(db, st)
        row["display"] = (
            f"{media['left']['title']} and {media['right']['title']}"
            if media
            else "A source was erased. This match cannot be used."
        )
    return row | {
        "media": media,
        "hash": st.hash,
        "based_on": st.based_on,
        "head": await subject_head(db, st.subject),
        "supersedes": str(st.supersedes) if st.supersedes else None,
        "signature": await _verify_sig(db, st),
        "signer": ctx.principal(st.author_id),
        "chain": await chain_entry(db, "statement", str(st.id)),
        "about": about,
    }


async def media_pair(db: AsyncSession, st: Statement) -> dict | None:
    """Both sources of a media match with their mime, or None when a source is
    erased or the match payload is gone. Callers must not render a None pair."""
    from sealbook.discovery.service import available

    match = await db.get(Payload, st.commitment)
    if match is None:
        return None
    left, right = st.subject.removeprefix("blob:"), match.value["blob"]
    mimes = {}
    for sha in (left, right):
        if not await db.scalar(
            select(available(Blob.sha256)).where(Blob.sha256 == sha)
        ):
            return None
        mimes[sha] = (await db.get(Blob, sha)).mime
    ctx = await Context.load(db, blobs=[left, right])
    return {
        "left": ctx.doc(left) | {"mime": mimes[left]},
        "right": ctx.doc(right) | {"mime": mimes[right]},
        "picture": match.value.get("picture"),
        "sound": match.value.get("sound"),
    }


async def blob_page(db: AsyncSession, sha: str) -> dict | None:
    blob = await db.get(Blob, sha)
    if blob is None:
        return None
    ctx = await Context.load(db, blobs=[sha])
    acqs = [
        {
            "id": str(a.id),
            "origin": a.origin,
            "at": a.at.isoformat(),
            "collector": ctx.principal(a.collector_id),
            "sensitivity": a.sensitivity,
            "doc_type": a.doc_type,
            "partner": a.partner,
            "chain": await chain_entry(db, "acquisition", str(a.id)),
        }
        for a in ctx.acqs.get(sha, [])
    ]
    return ctx.doc(sha) | {
        "md5": blob.md5,
        "size": blob.size,
        "mime": blob.mime,
        "storage_ref": blob.storage_ref,
        "acquisitions": acqs,
    }


async def document_page(
    db: AsyncSession, sha: str, revealed: bool = False
) -> dict | None:
    page = await blob_page(db, sha)
    if page is None:
        return None
    blob = await db.get(Blob, sha)
    try:
        text = blob_text(blob)
        error = None
    except (RuntimeError, OSError) as exc:
        text, error = None, str(exc)
    masked = text is not None and page["sensitive"] and not revealed
    statements = await statement_rows(
        db,
        mfilter.statement_blob() == sha,
        Statement.prop.not_in(META),
        order=Statement.recorded_at,
    )
    return page | {
        "text": mask(text) if masked else text,
        "text_masked": masked,
        "bytes_error": error,
        "statements": statements,
    }


async def run_page(db: AsyncSession, rid: uuid.UUID) -> dict | None:
    run = await db.get(Run, rid)
    if run is None:
        return None
    ctx = await Context.load(db, runs=[rid])
    events = (
        (
            await db.execute(
                select(RunEvent).where(RunEvent.run_id == rid).order_by(RunEvent.seq)
            )
        )
        .scalars()
        .all()
    )
    n = (await db.execute(select(func.count()).where(Statement.run_id == rid))).scalar()
    return {
        "id": str(run.id),
        "tool": run.tool,
        "work": work(run.tool),
        "actor": ctx.actor(run.principal_id, run.id),
        "principal": ctx.principal(run.principal_id),
        "route": run.route,
        "endpoint": run.endpoint,
        "model_requested": run.model_requested,
        "model_served": run.model_served,
        "provider_served": run.provider_served,
        "prompt_hash": run.prompt_hash,
        "output_hash": run.output_hash,
        "status": run.status,
        "error": run.error,
        "pubkey": run.pubkey.hex() if run.pubkey else None,
        "inputs": [ctx.doc(b) for b in run.inputs.get("blobs", [])],
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "chain": await chain_entry(db, "run", str(run.id)),
        "statement_count": n,
        "events": [
            {
                "seq": e.seq,
                "stage": event_words(e.stage),
                "counts": e.counts,
                "drop_reason": event_words(e.drop_reason),
                "at": e.at.isoformat(),
            }
            for e in events
        ],
    }


async def principal_page(db: AsyncSession, pid: uuid.UUID) -> dict | None:
    p = await db.get(Principal, pid)
    if p is None:
        return None
    boss = await db.get(Principal, p.on_behalf_of) if p.on_behalf_of else None
    runs = (
        await db.execute(select(func.count()).where(Run.principal_id == pid))
    ).scalar()
    n = (
        await db.execute(
            select(func.count()).where(
                Statement.author_id == pid, Statement.prop.not_in(META)
            )
        )
    ).scalar()
    return {
        "id": str(p.id),
        "kind": p.kind,
        "name": p.name,
        "user_id": p.user_id,
        "on_behalf_of": {"id": str(boss.id), "name": boss.name, "kind": boss.kind}
        if boss
        else None,
        "pubkey": p.pubkey.hex() if p.pubkey else None,
        "run_count": runs,
        "statement_count": n,
    }


def statements_where(args) -> list:
    """Statement-list page selectors: by run, author, blob or ids. Keyed off the number
    that was clicked, so the list always matches its count."""
    where = [Statement.prop.not_in(META)]
    if run := args.get("run"):
        where.append(Statement.run_id == uuid.UUID(run))
    if author := args.get("author"):
        where.append(Statement.author_id == uuid.UUID(author))
    if blob := args.get("blob"):
        where.append(mfilter.statement_blob() == blob)
    if ids := args.get("ids"):
        where.append(Statement.id.in_([uuid.UUID(i) for i in ids.split(",")]))
    if tier := args.get("tier"):
        where.append(StatementTier.tier == Tier(tier))
    if len(where) == 1:
        raise ValueError("pick statements by run, author, blob or ids")
    return where
