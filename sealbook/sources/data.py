"""Sources list: one row per acquired blob, paged, sorted, filtered and counted in
SQL, so the table holds at tens of thousands of sources.

Two phases. The base rows (first acquisition, blob, collector) carry every cheap
filter and sort and are materialized once. Counts (claims, open findings, C2PA, run
state, assignment) are aggregates joined to them: for the page's rows only, unless a
sort or filter needs them across the whole result. Never a query per row."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, case, cast, func, or_, select, text, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import aliased
from stk.user.models import User

from sealbook import method
from sealbook.collaboration.models import Assignment
from sealbook.discovery.models import FindingKey
from sealbook.ledger.contracts import ASSESSED_PROPS, EXTRACTED_PROPS, Prop, Tier
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Principal,
    Run,
    Statement,
    StatementTier,
)

TYPES = ("image", "pdf", "audio", "video", "text", "web", "other")
C2PA = ("valid", "invalid", "absent", "unchecked", "unknown")
BASE_SORTS = ("type", "title", "origin", "acquired_at", "by", "size")
SORTS = (*BASE_SORTS, "c2pa", "claims", "findings", "assignee", "status")
# The platform caps per_page at 100 on every /api route (stk.security).
MAX_PAGE = 100
# ponytail: "select all matching" returns every sha in one response; move bulk
# actions server side by filter if a case passes this.
MAX_IDS = 50_000
# Group by the first output column: the same expression bound twice is two
# parameters to Postgres, which then refuses the GROUP BY.
FIRST = text("1")


def title(given, file, doc_type) -> str:
    """A name a person reads: the acquisition's title, else its file name, unless that
    is one bare word such as "list" or "note", where the document kind says more."""
    kind = None if doc_type in (None, "unknown") else doc_type.replace("_", " ")
    if given:
        return given
    if file and (not kind or not re.fullmatch(r"[a-z]+", file)):
        return file
    return (kind or "untitled source").capitalize()


def parse(args, user_id: int) -> dict:
    """Validate query args. Raises ValueError with a reason for the caller."""

    def listed(name, allowed):
        values = [v for v in args.get(name, "").split(",") if v]
        if bad := [v for v in values if v not in allowed]:
            raise ValueError(f"{name} must be among {', '.join(allowed)}: {bad[0]}")
        return values

    def flag(name):
        raw = args.get(name)
        if raw not in (None, "", "0", "1"):
            raise ValueError(f"{name} must be 0 or 1")
        return None if raw in (None, "") else raw == "1"

    def day(name):
        raw = args.get(name)
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            raise ValueError(f"{name} must be an ISO date") from None

    try:
        page = int(args.get("page", 1))
        per_page = int(args.get("per_page", 50))
    except ValueError:
        raise ValueError("page and per_page must be integers") from None
    if page < 1 or not 1 <= per_page <= MAX_PAGE:
        raise ValueError(f"page starts at 1; per_page is 1 to {MAX_PAGE}")
    sort = args.get("sort", "acquired_at")
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(SORTS)}")
    order = args.get("order", "desc")
    if order not in ("asc", "desc"):
        raise ValueError("order must be asc or desc")
    assignee = args.get("assignee") or None
    if assignee == "me":
        assignee = user_id
    elif assignee not in (None, "unassigned"):
        try:
            assignee = int(assignee)
        except ValueError:
            raise ValueError("assignee is me, unassigned or a user id") from None
    by = args.get("by") or None
    if by:
        try:
            by = uuid.UUID(by)
        except ValueError:
            raise ValueError("by must be a principal id") from None
    q = (args.get("q") or "").strip()
    if len(q) > 200:
        raise ValueError("q is at most 200 characters")
    lo, hi = day("from"), day("to")
    if lo and hi and lo > hi:
        raise ValueError("the date range ends before it starts")
    return {
        "investigation": args.get("investigation") or None,
        "batch": args.get("batch") or None,
        "page": page,
        "per_page": per_page,
        "sort": sort,
        "order": order,
        "type": listed("type", TYPES),
        "c2pa": listed("c2pa", C2PA),
        "sensitive": flag("sensitive"),
        "findings": flag("findings"),
        "assignee": assignee,
        "by": by,
        "from": lo,
        "to": hi,
        "q": q,
    }


def _sha(subject):
    """The sha of a "blob:<sha>" subject. Postgres' regex engine costs about 70 us per
    matching row on {64}, which alone made a 20,000 row page take a second."""
    return case((subject.startswith("blob:"), func.substr(subject, 6)))


def _blob(st):
    """The blob a statement hangs off: its span, or the blob it is about (the rule of
    monitor.filter.statement_blob, without the regex)."""
    return func.coalesce(st.span_blob, _sha(st.subject))


def _derived():
    """OCR text and transcripts are derived blobs: their claims belong to the source.
    One CTE object, so both phases of a listing share it."""
    d, dp = aliased(Statement), aliased(Payload)
    return (
        select(
            _sha(d.subject).label("derived"),
            func.min(dp.value["blob"].astext).label("source"),
        )
        .join(dp, dp.commitment == d.commitment)
        .where(d.prop == Prop.DERIVED_FROM)
        .group_by(FIRST)
        .cte("derived")
    )


DERIVED = _derived()


def base(f: dict):
    """One row per source in scope from its earliest acquisition (as the monitor's
    Context.doc), with every filter that needs no aggregate."""
    blob = Acquisition.blob_sha256
    first = select(
        blob,
        Acquisition.at,
        Acquisition.origin,
        Acquisition.doc_type,
        Acquisition.collector_id,
        func.coalesce(
            Acquisition.context["title"].astext,
            Acquisition.context["file"].astext,
            Acquisition.context["filename"].astext,
        ).label("file"),
        # Same rule as the monitor: any sensitive acquisition makes the source
        # sensitive. A window over the same sort as DISTINCT ON costs no extra pass.
        func.bool_or(Acquisition.sensitivity == "sensitive")
        .over(partition_by=blob)
        .label("sensitive"),
    ).distinct(blob)
    for key in ("investigation", "batch"):
        if f[key]:
            first = first.where(
                blob.in_(select(blob).where(Acquisition.context[key].astext == f[key]))
            )
    a = first.order_by(blob, Acquisition.at, Acquisition.id).subquery("a")
    mimes = {
        "image": Blob.mime.startswith("image/"),
        "pdf": Blob.mime == "application/pdf",
        "audio": Blob.mime.startswith("audio/"),
        "video": Blob.mime.startswith("video/"),
        "web": Blob.mime.startswith("text/html"),
        "text": Blob.mime.startswith("text/") & ~Blob.mime.startswith("text/html"),
    }
    kind = case(*((cond, name) for name, cond in mimes.items()), else_="other")
    sensitive = a.c.sensitive
    title = func.coalesce(a.c.file, func.left(a.c.blob_sha256, 12))
    q = (
        select(
            a.c.blob_sha256.label("sha"),
            kind.label("type"),
            Blob.mime,
            Blob.size,
            (Blob.storage_ref == "synthetic").label("synthetic"),
            title.label("title"),
            a.c.doc_type,
            a.c.origin,
            a.c.at.label("acquired_at"),
            a.c.collector_id,
            Principal.name.label("by"),
            sensitive.label("sensitive"),
        )
        .join(Blob, Blob.sha256 == a.c.blob_sha256)
        .join(Principal, Principal.id == a.c.collector_id)
    )
    if f["type"]:
        # Mime predicates, not the CASE label: Postgres estimates these from column
        # statistics, and a 1-row guess here sends every later join into nested loops.
        known = [mimes[t] for t in f["type"] if t in mimes]
        if "other" in f["type"]:
            known.append(~or_(*mimes.values()))
        q = q.where(or_(*known))
    if f["sensitive"] is not None:
        q = q.where(sensitive if f["sensitive"] else ~sensitive)
    if f["by"]:
        q = q.where(a.c.collector_id == f["by"])
    # Days are UTC days, as acquired_at is reported; a bare date would take the
    # database session's zone and shift the boundary.
    if f["from"]:
        q = q.where(a.c.at >= datetime.combine(f["from"], time.min, UTC))
    if f["to"]:
        q = q.where(
            a.c.at < datetime.combine(f["to"] + timedelta(days=1), time.min, UTC)
        )
    if f["q"]:
        q = q.where(
            or_(
                title.icontains(f["q"], autoescape=True),
                a.c.origin.icontains(f["q"], autoescape=True),
                a.c.blob_sha256.startswith(f["q"].lower(), autoescape=True),
            )
        )
    return q


# What each sortable or filterable count needs; status reads claims and run state.
NEEDS = {
    "c2pa": {"c2pa"},
    "claims": {"claims"},
    "findings": {"findings"},
    "assignee": {"assigned"},
    "status": {"claims", "runs"},
}
GROUPS = {"claims", "findings", "c2pa", "runs", "assigned"}


def with_counts(rows, investigation, groups: set, everything: bool):
    """`rows` (a CTE with a sha column) joined to the counts in `groups`. For one
    page, each aggregate reads only that page's statements through their indexes;
    across the whole result (a count sort or filter), one pass over each table beats
    20,000 lookups."""
    shas = select(rows.c.sha)
    subjects = select(func.concat("blob:", rows.c.sha))

    derived = DERIVED
    reach = shas.union(
        select(derived.c.derived).where(derived.c.source.in_(shas))
    ).scalar_subquery()

    def owner(st):
        return func.coalesce(derived.c.source, _blob(st))

    # Two indexed lookups, not one OR that Postgres can only answer with a seq scan.
    touched = (
        select(Statement.id)
        .where(Statement.span_blob.in_(reach))
        .union_all(
            select(Statement.id).where(
                Statement.span_blob.is_(None), Statement.subject.in_(subjects)
            )
        )
    )

    claims = (
        select(
            owner(Statement).label("sha"),
            func.count().filter(StatementTier.tier != Tier.REJECTED).label("claims"),
            func.count()
            .filter(StatementTier.tier == Tier.UNCONFIRMED)
            .label("pending"),
        )
        .select_from(Statement)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .outerjoin(derived, derived.c.derived == _blob(Statement))
        .where(
            Statement.prop.in_(EXTRACTED_PROPS),
            true() if everything else Statement.id.in_(touched),
        )
        .group_by(FIRST)
        .subquery("claims")
    )

    fs = aliased(Statement)
    member = func.jsonb_array_elements_text(FindingKey.members).table_valued("value")
    findings = (
        select(
            owner(fs).label("sha"),
            func.count(func.distinct(FindingKey.key)).label("findings"),
        )
        .select_from(FindingKey)
        .join(member, true())
        .join(fs, fs.id == cast(member.c.value, UUID))
        .outerjoin(derived, derived.c.derived == _blob(fs))
        .where(FindingKey.state == "open", true() if everything else fs.id.in_(touched))
        .group_by(FIRST)
        .subquery("findings")
    )

    cs, cp = aliased(Statement), aliased(Payload)
    c2pa = (
        select(
            _sha(cs.subject).label("sha"),
            # An erased verification payload leaves the state unknown, not unchecked.
            func.coalesce(
                cp.value["status"].astext, cp.value["result"].astext, "unknown"
            ).label("c2pa"),
        )
        .outerjoin(cp, cp.commitment == cs.commitment)
        .where(
            cs.prop == Prop.C2PA_VERIFY,
            true() if everything else cs.subject.in_(subjects),
        )
        .distinct(cs.subject)
        .order_by(cs.subject, cs.recorded_at.desc())
        .subquery("c2pa")
    )

    run_blob = func.jsonb_array_elements_text(Run.inputs["blobs"]).table_valued("value")
    runs = (
        select(
            run_blob.c.value.label("sha"),
            func.bool_or(Run.status == "failed").label("failed"),
            func.bool_or(Run.status.in_(("queued", "running"))).label("busy"),
        )
        .select_from(Run)
        .join(run_blob, true())
        .where(Run.status != "done")
        .group_by(FIRST)
        .subquery("runs")
    )

    open_ = (
        select(
            func.substr(Assignment.subject, 6).label("sha"),
            Assignment.assignee_id,
            Assignment.due,
        )
        .where(
            Assignment.status == "open",
            Assignment.subject.startswith("blob:")
            if everything
            else Assignment.subject.in_(subjects),
        )
        .distinct(Assignment.subject)
        .order_by(Assignment.subject, Assignment.updated_at.desc())
    )
    if investigation:
        open_ = open_.where(Assignment.investigation == investigation)
    open_ = open_.subquery("assigned")

    out = {
        "c2pa": [func.coalesce(c2pa.c.c2pa, "unchecked").label("c2pa")],
        "claims": [
            func.coalesce(claims.c.claims, 0).label("claims"),
            func.coalesce(claims.c.pending, 0).label("pending"),
        ],
        "findings": [func.coalesce(findings.c.findings, 0).label("findings")],
        "assigned": [
            open_.c.assignee_id,
            func.coalesce(User.name, User.email).label("assignee"),
            open_.c.due,
        ],
    }
    q = select(rows)
    for g in sorted(groups):
        q = q.add_columns(*out.get(g, []))
    if "runs" in groups:
        # Pending claims come from this call or from rows ranked by them already.
        pending = claims.c.pending if "claims" in groups else rows.c.pending
        status = case(
            (runs.c.failed, "failed"),
            (runs.c.busy, "processing"),
            (func.coalesce(pending, 0) > 0, "needs review"),
            else_="indexed",
        )
        q = q.add_columns(status.label("status"))
    joins = {
        "claims": (claims, claims.c.sha),
        "findings": (findings, findings.c.sha),
        "c2pa": (c2pa, c2pa.c.sha),
        "runs": (runs, runs.c.sha),
        "assigned": (open_, open_.c.sha),
    }
    for g in sorted(groups):
        table_, sha = joins[g]
        q = q.outerjoin(table_, sha == rows.c.sha)
    if "assigned" in groups:
        q = q.outerjoin(User, User.id == open_.c.assignee_id)
    return q.subquery("counted")


def count_filters(r, f: dict) -> list:
    conds = []
    if f["c2pa"]:
        conds.append(r.c.c2pa.in_(f["c2pa"]))
    if f["findings"] is not None:
        conds.append(r.c.findings > 0 if f["findings"] else r.c.findings == 0)
    if f["assignee"] == "unassigned":
        conds.append(r.c.assignee_id.is_(None))
    elif f["assignee"] is not None:
        conds.append(r.c.assignee_id == f["assignee"])
    return conds


def _needs(f: dict) -> set:
    """Count groups the sort or a filter reads across the whole result."""
    need = set(NEEDS.get(f["sort"], ()))
    if f["c2pa"]:
        need.add("c2pa")
    if f["findings"] is not None:
        need.add("findings")
    if f["assignee"] is not None:
        need.add("assigned")
    return need


def _order(r, f):
    key = r.c[f["sort"]]
    key = key.asc() if f["order"] == "asc" else key.desc()
    return key.nulls_last(), r.c.sha


def _row(m) -> dict:
    return {
        "sha256": m["sha"],
        "type": m["type"],
        "mime": m["mime"],
        "size": m["size"],
        "title": m["title"],
        "doc_type": m["doc_type"],
        "origin": m["origin"],
        "acquired_at": m["acquired_at"].isoformat(),
        "by": {"id": str(m["collector_id"]), "name": m["by"]},
        "sensitive": m["sensitive"],
        "synthetic": m["synthetic"],
        "c2pa": m["c2pa"],
        "claims": m["claims"],
        "pending": m["pending"],
        "findings": m["findings"],
        "status": m["status"],
        "assignee": {"id": m["assignee_id"], "name": m["assignee"]}
        if m["assignee_id"] is not None
        else None,
        "due": m["due"].isoformat() if m["due"] else None,
    }


async def listing(db, f: dict) -> dict:
    rows = base(f).cte("base").prefix_with("MATERIALIZED")
    need, inv = _needs(f), f["investigation"]
    ranked = with_counts(rows, inv, need, everything=True) if need else rows
    page = (
        select(ranked, func.count().over().label("total"))
        .where(and_(true(), *count_filters(ranked, f)))
        .order_by(*_order(ranked, f))
        .offset((f["page"] - 1) * f["per_page"])
        .limit(f["per_page"])
        .cte("page")
    )
    r = with_counts(page, inv, GROUPS - need, everything=False)
    found = list((await db.execute(select(r).order_by(*_order(r, f)))).mappings())
    if found:
        total = found[0]["total"]
    else:
        total = await db.scalar(
            select(func.count())
            .select_from(ranked)
            .where(and_(true(), *count_filters(ranked, f)))
        )
    items = [_row(m) for m in found]
    passes = await first_passes(db, [i["sha256"] for i in items])
    for item in items:
        item["pass"] = passes.get(item["sha256"])
        # A first-pass title is free text that may name people (monitor PERSONAL).
        if item["pass"] and item["sensitive"]:
            item["pass"] |= {"title": None, "title_ar": None}
    return {
        "items": items,
        "total": total,
        "page": f["page"],
        "per_page": f["per_page"],
    }


async def ids(db, f: dict) -> dict:
    """Every sha matching the filter, for "select all matching filter"."""
    rows = base(f).cte("base").prefix_with("MATERIALIZED")
    need = _needs({**f, "sort": "title"})
    r = with_counts(rows, f["investigation"], need, everything=True) if need else rows
    q = select(r.c.sha).where(and_(true(), *count_filters(r, f)))
    found = list((await db.execute(q.limit(MAX_IDS + 1))).scalars())
    if len(found) > MAX_IDS:
        raise ValueError(f"more than {MAX_IDS:,} sources match; narrow the filter")
    return {"ids": found, "total": len(found)}


async def collectors(db) -> list[dict]:
    q = (
        select(Principal.id, Principal.name)
        .where(Principal.id.in_(select(Acquisition.collector_id).distinct()))
        .order_by(Principal.name)
    )
    return [{"id": str(i), "name": n} for i, n in await db.execute(q)]


async def first_passes(db, shas: list[str]) -> dict[str, dict]:
    """Each item's first pass in brief, in one query: the analyst title, the
    priority its triage sets, and whether any part still waits for review."""
    if not shas:
        return {}
    rows = await db.execute(
        select(Statement.subject, Statement.prop, Payload.value, StatementTier.tier)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        # An erased value has no payload, so it drops out.
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            Statement.subject.in_([f"blob:{sha}" for sha in shas]),
            Statement.prop.in_(ASSESSED_PROPS),
            StatementTier.tier != Tier.REJECTED,
        )
        .order_by(Statement.recorded_at)
    )
    out: dict[str, dict] = {}
    for subject, prop, value, tier in rows:
        brief = out.setdefault(
            subject.removeprefix("blob:"),
            {"title": None, "title_ar": None, "priority": None, "open": False},
        )
        brief["open"] |= tier == Tier.UNCONFIRMED
        if prop == Prop.TITLE:
            brief["title"], brief["title_ar"] = value["title"], value.get("title_ar")
        elif prop == Prop.TRIAGE:
            brief["priority"] = method.priority(value["content"], value["reliability"])
    return out
