"""What left this server: one list joined from rows other modules already store.

Model calls are `run` rows on a model tool, web searches are research access
statements, web pages are capture runs, link intakes and liveness checks, and
timestamp requests are `anchor` rows. Nothing here logs anything new; a request
with no stored row cannot appear (docs/status.md names those)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from urllib.parse import urlsplit

from sqlalchemy import (
    String,
    and_,
    cast,
    exists,
    false,
    func,
    literal,
    null,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import aliased

from sealbook.anchor.models import Anchor, Batch
from sealbook.cases.models import Case
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop
from sealbook.ledger.gate import resolve
from sealbook.ledger.models import Acquisition, Payload, Principal, Run, Statement
from sealbook.research.brave import endpoint as brave_endpoint
from sealbook.sources.data import title as source_title

PER_PAGE = 25
KINDS = ("model", "web", "timestamp")

# What each model tool sends, in words a reader knows.
SENT = {
    "ocr": "A scanned page, to read its text",
    "extract": "Page text, to propose claims",
    "propose": "Page text, to propose claims",
    "assess": "A picture or video frames, to describe them",
    "transcribe": "A recording, to turn its speech into text",
    "embed": "A passage, picture or sound, to find it by meaning",
    "embed_actor": "A person's recorded details, to compare them with others",
    "extract_sheet": "Spreadsheet rows, to propose claims",
    "brief": "Claims from a new batch, to draft its brief",
    "link": "Claims across the case, to suggest links",
    "standing": "Claims about a person, to answer a standing question",
    "contradictions": "Claims about a person, to find where sources disagree",
    "gaps": "Claims about a person, to find silences in the record",
    "notice": "Two sources, to say why they resemble each other",
    "approximate": "Passages naming a place, to suggest where it is",
}
TASK = {
    "ocr": "reading text",
    "extract": "proposing claims",
    "propose": "proposing claims",
    "assess": "describing pictures",
    "transcribe": "speech to text",
    "embed": "search by meaning",
    "embed_actor": "comparing people",
    "extract_sheet": "spreadsheet rows",
    "brief": "intake briefs",
    "link": "the linker",
    "standing": "standing questions",
    "contradictions": "the contradiction hunter",
    "gaps": "the gap finder",
    "notice": "the noticer",
    "approximate": "the place finder",
}
ROUTE = {
    "local": "This server",
    "cloud-zdr": "Hosted model that keeps no data",
    "cloud": "Hosted model, open cloud",
    "anthropic": "Anthropic",
    "vision": "Google Cloud Vision",
}
HOST = {
    "cloud-zdr": "openrouter.ai",
    "cloud": "openrouter.ai",
    "anthropic": "api.anthropic.com",
    "vision": "vision.googleapis.com",
}
SEARCH = {
    "web": "A web search",
    "images": "An image search",
    "videos": "A video search",
    "youtube": "A YouTube search",
}
STAMP = {
    "ots": "OpenTimestamps calendars",
    "tsa": "Timestamp authority",
    "partner": "Partner copy",
}
REFUSED = "RouteError: route % not allowed%"


def _host(url: str | None) -> str | None:
    return urlsplit(url).hostname if url else None


def _sources():
    """Every outbound row as (kind, ref, at, investigation, sensitive, route,
    refused), one select per stored kind."""
    acq = aliased(Acquisition)
    other = aliased(Acquisition)
    batch_case = (
        select(other.context["investigation"].astext)
        .where(other.context["batch"].astext == Run.inputs["batch"].astext)
        .limit(1)
        .scalar_subquery()
    )
    sensitive_blob = exists().where(
        other.blob_sha256 == acq.blob_sha256, other.sensitivity == "sensitive"
    )
    refused = Run.error.like(REFUSED)
    models = (
        select(
            literal("model").label("kind"),
            cast(Run.id, String).label("ref"),
            func.coalesce(Run.finished_at, Run.created_at).label("at"),
            func.coalesce(
                Run.inputs["investigation"].astext,
                acq.context["investigation"].astext,
                batch_case,
            ).label("investigation"),
            and_(acq.id.is_not(None), sensitive_blob).label("sensitive"),
            Run.route.label("route"),
            refused.label("refused"),
        )
        .outerjoin(acq, acq.id == cast(Run.inputs["acquisition"].astext, UUID))
        .where(
            Run.tool.in_(SENT),
            or_(
                Run.status == "done",
                and_(
                    Run.status == "failed",
                    or_(refused, Run.route.is_distinct_from("local")),
                ),
            ),
        )
    )
    pages = (
        select(
            literal("web"),
            cast(Run.id, String),
            func.coalesce(Run.finished_at, Run.created_at),
            func.coalesce(
                Run.inputs["investigation"].astext,
                acq.context["investigation"].astext,
            ),
            func.coalesce(
                Run.inputs["sensitivity"].astext == "sensitive",
                and_(acq.id.is_not(None), sensitive_blob),
            ),
            null(),
            false(),
        )
        .outerjoin(acq, acq.id == cast(Run.inputs["acquisition"].astext, UUID))
        .where(
            Run.tool.in_(("capture", "url_check")),
            Run.status.in_(("done", "failed")),
        )
    )
    links = select(
        literal("web"),
        cast(acq.id, String),
        acq.at,
        acq.context["investigation"].astext,
        sensitive_blob,
        null(),
        false(),
    ).where(
        acq.origin.startswith("url:"),
        acq.context.has_key("fetched_at"),
        ~acq.context.has_key("capture"),
    )
    searches = (
        select(
            literal("web"),
            cast(Statement.id, String),
            Statement.recorded_at,
            Payload.value["investigation"].astext,
            false(),
            null(),
            false(),
        )
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            Statement.prop == Prop.ACCESS,
            Payload.value["action"].astext == "research",
        )
    )
    stamps = select(
        literal("timestamp"),
        cast(Anchor.id, String),
        Anchor.at,
        cast(null(), String),
        false(),
        null(),
        false(),
    )
    return union_all(models, pages, links, searches, stamps).subquery("out")


def _scope(out, readable: set[str] | None, f: dict) -> list:
    """Filters on the joined rows. `readable` None is an admin: every row."""
    where = []
    if readable is not None:
        # A timestamp request carries a hash of the whole chain and no case.
        where.append(
            or_(out.c.kind == "timestamp", out.c.investigation.in_(sorted(readable)))
        )
    if f["investigation"]:
        where.append(
            or_(out.c.kind == "timestamp", out.c.investigation == f["investigation"])
        )
    return where


def today_start() -> datetime:
    return datetime.combine(datetime.now(UTC).date(), time.min, UTC)


async def summary(db, readable: set[str] | None, f: dict) -> dict:
    out = _sources()
    rows = await db.execute(
        select(
            out.c.kind,
            out.c.route,
            out.c.refused,
            func.count(),
        )
        .where(*_scope(out, readable, f), out.c.at >= today_start())
        .group_by(out.c.kind, out.c.route, out.c.refused)
    )
    models: dict[str, int] = {}
    counts = {"web": 0, "timestamp": 0, "refused": 0}
    for kind, route, refused, n in rows:
        if refused:
            counts["refused"] += n
        elif kind == "model":
            # A run with no route recorded ran here, as its row says.
            models[route or "local"] = models.get(route or "local", 0) + n
        else:
            counts[kind] += n
    return {"models": models, **counts}


async def listing(db, readable: set[str] | None, f: dict) -> dict:
    out = _sources()
    where = _scope(out, readable, f)
    if f["kind"]:
        where.append(out.c.kind == f["kind"])
    if f["sensitive"]:
        where.append(out.c.sensitive.is_(True))
    total = await db.scalar(select(func.count()).select_from(out).where(true(), *where))
    found = (
        await db.execute(
            select(out)
            .where(true(), *where)
            .order_by(out.c.at.desc(), out.c.ref.desc())
            .offset((f["page"] - 1) * PER_PAGE)
            .limit(PER_PAGE)
        )
    ).all()
    items = await _hydrate(db, found)
    return {"items": items, "total": total, "page": f["page"], "per_page": PER_PAGE}


# --- rows -----------------------------------------------------------------------


async def _names(db, ids: set) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = await db.execute(
        select(Principal.id, Principal.name).where(Principal.id.in_(ids))
    )
    return dict(rows.all())


def _source(acq: Acquisition | None, sensitive: bool) -> dict | None:
    if acq is None:
        return None
    c = acq.context or {}
    return {
        "title": source_title(
            c.get("title"), c.get("file") or c.get("filename"), acq.doc_type
        ),
        "href": f"/evidence/{acq.blob_sha256}",
        "sensitive": bool(sensitive),
    }


def _allowed(policies, run: Run, acq: Acquisition | None) -> str:
    doc_type, sensitivity = (
        (acq.doc_type, acq.sensitivity) if acq else ("none", "sensitive")
    )
    try:
        p = resolve(policies, run.tool, doc_type, sensitivity)
    except LookupError:
        return "No policy covers this task today."
    sources = "any source" if p.sensitivity == "*" else f"{p.sensitivity} sources"
    task = "any model task" if p.capability == "*" else TASK.get(run.tool, run.tool)
    said = f"The policy for {task} on {sources} allows this route."
    if run.route not in p.routes:
        said = (
            "Allowed when it ran. Today the policy for "
            f"{task} on {sources} no longer allows this route."
        )
    return said


def _model(run: Run, acq, policies) -> dict:
    route = run.route or "local"
    error = run.error or ""
    refused = error.startswith("RouteError: route ") and " not allowed " in error
    row = {
        "what": SENT[run.tool],
        "to": ROUTE.get(route, route),
        "host": None,
        "model": None,
        "why": None,
        "tone": "ok",
    }
    if refused:
        sens = acq.sensitivity if acq else "sensitive"
        return row | {
            "what": "Refused. Nothing was sent.",
            "detail": SENT[run.tool],
            "host": "not contacted",
            "why": f"Not allowed for a {sens} source. "
            "The run failed and was not rerouted.",
            "tone": "warn",
        }
    if route == "local":
        return row | {
            "what": "Nothing. Read on this server.",
            "detail": SENT[run.tool],
            "host": "local model",
            "why": "Local work needs no outside service.",
        }
    provider = run.provider_served if run.provider_served != "local" else None
    row |= {
        "detail": None,
        "host": ", ".join(
            x
            for x in (
                _host(run.endpoint) or HOST.get(route),
                provider and f"served by {provider}",
            )
            if x
        ),
        "model": run.model_served or run.model_requested,
        "why": _allowed(policies, run, acq),
    }
    if run.status == "failed":
        row |= {
            "detail": "The call failed. Whether it reached the provider is not recorded.",
            "tone": "warn",
        }
    return row


def _page(run: Run, acq, names) -> dict:
    i = run.inputs or {}
    failed = run.status == "failed"
    if run.tool == "url_check":
        return {
            "what": "A web address, to check the page is still there",
            "detail": "The check failed." if failed else None,
            "to": "The page's own site",
            "host": _host(acq.origin.removeprefix("url:")) if acq else None,
            "why": "A liveness check on a source captured from this address.",
            "tone": "warn" if failed else "ok",
        }
    who = names.get(uuid.UUID(i["analyst"])) if i.get("analyst") else None
    video = i.get("kind") in ("videos", "youtube")
    return {
        "what": "A video page, to keep a copy"
        if video
        else "A web page, to keep a copy",
        "detail": "The capture failed." if failed else None,
        "to": "The page's own site",
        "host": i.get("domain") or _host(i.get("page")),
        "why": f"Picked from a web search by {who or 'an analyst'}.",
        "tone": "warn" if failed else "ok",
    }


async def _hydrate(db, found) -> list[dict]:
    ids = {k: [r.ref for r in found if r.kind == k] for k in KINDS}
    run_ids = [uuid.UUID(r) for r in ids["model"] + ids["web"] if _is_uuid(r)]
    runs = {
        str(r.id): r
        for r in (
            await db.scalars(select(Run).where(Run.id.in_(run_ids))) if run_ids else []
        )
    }
    acq_ids = {
        uuid.UUID(r.inputs["acquisition"])
        for r in runs.values()
        if _is_uuid((r.inputs or {}).get("acquisition"))
    } | {uuid.UUID(r) for r in ids["web"] if _is_uuid(r) and r not in runs}
    acqs = {
        str(a.id): a
        for a in (
            await db.scalars(select(Acquisition).where(Acquisition.id.in_(acq_ids)))
            if acq_ids
            else []
        )
    }
    statements = {
        str(s.id): (s, v)
        for s, v in (
            await db.execute(
                select(Statement, Payload.value)
                .join(Payload, Payload.commitment == Statement.commitment)
                .where(
                    Statement.id.in_(
                        [uuid.UUID(r) for r in ids["web"] if r not in runs]
                    )
                )
            )
            if ids["web"]
            else []
        )
    }
    anchors = {
        str(a.id): (a, b)
        for a, b in (
            await db.execute(
                select(Anchor, Batch)
                .join(Batch, Batch.id == Anchor.batch_id)
                .where(Anchor.id.in_([int(r) for r in ids["timestamp"]]))
            )
            if ids["timestamp"]
            else []
        )
    }
    names = await _names(
        db,
        {s.author_id for s, _ in statements.values()}
        | {a.collector_id for a in acqs.values()}
        | {
            uuid.UUID(r.inputs["analyst"])
            for r in runs.values()
            if _is_uuid((r.inputs or {}).get("analyst"))
        },
    )
    slugs = {r.investigation for r in found if r.investigation}
    cases = dict(
        (await db.execute(select(Case.slug, Case.name).where(Case.slug.in_(slugs))))
        .tuples()
        .all()
        if slugs
        else []
    )
    policies = await write.policies(db) if ids["model"] else []

    items = []
    for r in found:
        base = {
            "id": f"{r.kind}:{r.ref}",
            "kind": r.kind,
            "at": r.at.isoformat(),
            "case": {"slug": r.investigation, "name": cases.get(r.investigation)}
            if r.investigation
            else None,
            "source": None,
            "sensitive": bool(r.sensitive),
            "detail": None,
            "model": None,
        }
        if r.kind == "model":
            run = runs[r.ref]
            acq = acqs.get((run.inputs or {}).get("acquisition"))
            row = _model(run, acq, policies)
            row["source"] = _source(acq, r.sensitive)
            # The mark covers every upload of the file; the policy read this one.
            if r.sensitive and acq and acq.sensitivity != "sensitive" and row["why"]:
                row["why"] += (
                    " This upload came in as ordinary;"
                    " another upload of the same file is marked sensitive."
                )
        elif r.kind == "timestamp":
            a, b = anchors[r.ref]
            hosts = (
                ", ".join(filter(None, (_host(t) for t in a.target.split(","))))
                if a.kind != "partner"
                else "a folder held for a partner"
            )
            row = {
                "what": "A timestamp request",
                "detail": f"The root hash of {b.size} chain entries. "
                "A hash, no evidence."
                + (" It failed and will be retried." if a.error else ""),
                "to": STAMP[a.kind],
                "host": hosts,
                "why": "Hourly anchor. Hashes only.",
                "tone": "warn" if a.error else "ok",
            }
        elif r.ref in runs:
            run = runs[r.ref]
            acq = acqs.get((run.inputs or {}).get("acquisition"))
            row = _page(run, acq, names) | {"source": _source(acq, r.sensitive)}
        elif r.ref in acqs:
            acq = acqs[r.ref]
            row = {
                "what": "A web address, to fetch the page as a source",
                "to": "The page's own site",
                "host": _host(acq.origin.removeprefix("url:")),
                "why": f"Added from a link by {names.get(acq.collector_id) or 'someone'}.",
                "tone": "ok",
                "source": _source(acq, r.sensitive),
            }
        else:
            st, v = statements[r.ref]
            kind = v.get("kind", "web")
            youtube = v.get("provider") == "youtube"
            row = {
                "what": SEARCH.get(kind, "A web search"),
                "detail": "The words searched stay in the access record.",
                "to": "YouTube" if youtube else "Brave Search",
                "host": "youtube.com"
                if youtube
                else _host(brave_endpoint(kind)) or "api.search.brave.com",
                "why": f"Sent by {names.get(st.author_id) or 'an analyst'}. "
                "The case allows research and is not marked sensitive.",
                "tone": "ok",
            }
        items.append(base | row)
    return items


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return value is not None
