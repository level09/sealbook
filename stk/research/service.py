"""Web research: the guardrails, sending queries, and queueing captures.

Every query reveals what it contains to the search provider, so: a person starts it,
sees each query before it is sent, a sensitive or switched-off case refuses it, each
query is an access statement written before the request leaves, and per-person hourly
limits apply. Results live in process memory (stk.research.store) only."""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import UTC, datetime

import httpx
import numpy as np
import pdqhash
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import stk.extensions as ext
from stk import netguard
from stk.cases.models import Case
from stk.discovery.models import MediaFrame
from stk.discovery.service import available
from stk.ingest.service import normalise_url, system_agent
from stk.ledger import write
from stk.ledger.contracts import Prop, Sensitivity, StatementIn
from stk.ledger.models import Acquisition, Run
from stk.monitor import filter as mfilter
from stk.research import brave, browser, store
from stk.research.models import CaseSetting
from stk.research.queries import KINDS
from stk.settings import Config
from stk.utils.ratelimit import consume_limit

MAX_QUERIES = 8
MAX_QUERY_CHARS = 300
THUMB_BYTES = 2 * 1024 * 1024


class Refusal(Exception):
    """Research may not run here; the message is for the analyst."""


# --- guardrails ------------------------------------------------------------------


def missing() -> dict[str, list[str]]:
    """Settings each half still needs: search, and capture (search plus rendering)."""
    search = [] if Config.BRAVE_API_KEY else ["BRAVE_API_KEY"]
    render = [] if browser.installed() else ["Chromium"]
    return {"search": search, "capture": search + render}


async def blocked(db: AsyncSession) -> set[str]:
    return set(
        (
            await db.execute(
                select(CaseSetting.investigation).where(
                    CaseSetting.sensitive | ~CaseSetting.research
                )
            )
        ).scalars()
    )


async def refusal(db: AsyncSession, investigation: str | None) -> str | None:
    if not investigation:
        return "Choose the case this research is for."
    known = await db.scalar(select(Case.id).where(Case.slug == investigation))
    if known is None:
        return f"There is no case named {investigation}."
    row = await db.get(CaseSetting, investigation)
    if row and row.sensitive:
        return (
            "This case is marked sensitive, so nothing from it is sent to a search "
            "or rendering service."
        )
    if row and not row.research:
        return "Web research is switched off for this case."
    return None


async def status(db: AsyncSession, investigation: str | None) -> dict:
    row = await db.get(CaseSetting, investigation) if investigation else None
    return {
        "missing": missing(),
        "refusal": await refusal(db, investigation),
        "case": {
            "sensitive": bool(row and row.sensitive),
            "research": not row or row.research,
        },
        "limits": {
            "queries_per_hour": Config.RESEARCH_QUERIES_PER_HOUR,
            "captures_per_hour": Config.RESEARCH_CAPTURES_PER_HOUR,
            "max_queries": MAX_QUERIES,
        },
    }


async def set_case(
    db: AsyncSession, investigation: str, by: uuid.UUID, **flags
) -> CaseSetting:
    row = await db.get(CaseSetting, investigation)
    if row is None:
        row = CaseSetting(investigation=investigation, updated_by=by)
        db.add(row)
    for key in ("sensitive", "research"):
        if key in flags:
            setattr(row, key, bool(flags[key]))
    row.updated_by, row.updated_at = by, datetime.now(UTC)
    await db.flush()
    return row


# --- search ----------------------------------------------------------------------


def clean(queries) -> list[dict]:
    """The queries exactly as the analyst confirmed them, or ValueError."""
    if not isinstance(queries, list) or not 1 <= len(queries) <= MAX_QUERIES:
        raise ValueError(f"send 1 to {MAX_QUERIES} queries")
    out = []
    for q in queries:
        text = " ".join(str((q or {}).get("text", "")).split())
        kinds = q.get("kinds") or []
        if not 0 < len(text) <= MAX_QUERY_CHARS:
            raise ValueError(f"a query must be 1 to {MAX_QUERY_CHARS} characters")
        if not kinds or any(k not in KINDS for k in kinds):
            raise ValueError(f"kinds must be some of {', '.join(KINDS)}")
        people = [p for p in q.get("people") or [] if str(p).startswith("person:")]
        out.append(
            {
                "text": text,
                "kinds": list(dict.fromkeys(kinds)),
                "why": q.get("why") or [],
                "people": people,
            }
        )
    return out


async def log_query(
    db: AsyncSession, who, scope: str, investigation: str, q: dict, kind: str
) -> uuid.UUID:
    """The access record for one query, committed before the request leaves. The
    text is in the payload, which erasure can delete; the chain keeps only its
    salted commitment (decisions.md)."""
    st = await write.append(
        db,
        StatementIn(
            subject=scope,
            prop=Prop.ACCESS,
            value={
                "action": "research",
                "provider": "brave",
                "kind": kind,
                "query": q["text"],
                "investigation": investigation,
                "people": q["people"],
                "at": datetime.now(UTC).isoformat(),
            },
            author_id=who.id,
        ),
    )
    await db.commit()
    return st.id


def _pdq(data: bytes) -> tuple[str, int] | None:
    try:
        with Image.open(io.BytesIO(data)) as im:
            bits, quality = pdqhash.compute(
                np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
            )
    except (OSError, ValueError):
        return None
    return f"{int(np.packbits(bits).tobytes().hex(), 16):0256b}", int(quality)


async def _thumb(url: str, transport) -> tuple[bytes, str] | None:
    try:
        async with netguard.stream(url, timeout=10, transport=transport) as (resp, _):
            mime = resp.headers.get("content-type", "").split(";")[0].strip()
            if resp.status_code != 200 or not mime.startswith("image/"):
                return None
            data = b""
            async for chunk in resp.aiter_bytes():
                data += chunk
                if len(data) > THUMB_BYTES:
                    return None
            return data, mime
    except (httpx.HTTPError, netguard.Blocked):
        return None


async def _same_picture(db: AsyncSession, data: bytes, investigation: str):
    found = _pdq(data)
    if found is None or found[1] < Config.PDQ_QUALITY:
        return None
    distance = MediaFrame.hash.hamming_distance(found[0])
    f = {"investigation": investigation, "time": None}
    row = (
        await db.execute(
            select(MediaFrame.blob_sha256, distance.label("d"))
            .where(
                MediaFrame.quality >= Config.PDQ_QUALITY,
                mfilter.blob_clause(f, MediaFrame.blob_sha256),
                available(MediaFrame.blob_sha256),
            )
            .order_by(distance)
            .limit(1)
        )
    ).first()
    return row if row and row.d <= Config.PDQ_DISTANCE else None


async def _same_url(db: AsyncSession, urls: set[str], investigation: str) -> dict:
    origins = {}
    for u in urls:
        try:
            origins[f"url:{normalise_url(u)}"] = u
        except ValueError:
            continue
    rows = await db.execute(
        select(Acquisition.origin, Acquisition.blob_sha256, Acquisition.at)
        .where(
            Acquisition.origin.in_(list(origins)),
            Acquisition.context["investigation"].astext == investigation,
        )
        .order_by(Acquisition.at)
    )
    out: dict[str, dict] = {}
    for origin, sha, at in rows:
        out.setdefault(origins[origin], {"sha256": sha, "at": at.isoformat()})
    return out


async def search(
    db: AsyncSession,
    user_id: int,
    who,
    scope: str,
    investigation: str,
    queries: list[dict],
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    if missing()["search"]:
        raise Refusal("Search is not configured: " + ", ".join(missing()["search"]))
    if reason := await refusal(db, investigation):
        raise Refusal(reason)
    sent, results, first = [], [], True
    for q in queries:
        for kind in q["kinds"]:
            if not await consume_limit(
                ext.async_session_factory,
                f"research:query:{user_id}",
                Config.RESEARCH_QUERIES_PER_HOUR,
                3600,
            ):
                sent.append(q | {"kind": kind, "error": "Hourly query limit reached"})
                continue
            if not first:
                await asyncio.sleep(Config.RESEARCH_SEARCH_INTERVAL)
            first = False
            access = await log_query(db, who, scope, investigation, q, kind)
            try:
                found = await brave.search(
                    kind, q["text"], Config.RESEARCH_RESULTS, transport
                )
            except brave.SearchError as exc:
                sent.append(q | {"kind": kind, "error": str(exc), "count": 0})
                continue
            sent.append(q | {"kind": kind, "count": len(found)})
            results += [(r, q, access) for r in found]

    urls = {r.url for r, _, _ in results} | {
        r.media_url for r, _, _ in results if r.media_url
    }
    held = await _same_url(db, urls, investigation)
    thumbs = await asyncio.gather(
        *(
            _thumb(r.thumbnail, transport) if r.thumbnail else _none()
            for r, _, _ in results
        )
    )
    groups: dict[str, list] = {k: [] for k in KINDS}
    seen = set()
    for (r, q, access), thumb in zip(results, thumbs, strict=True):
        if (r.kind, r.url, r.media_url) in seen:
            continue
        seen.add((r.kind, r.url, r.media_url))
        in_case = []
        for u in (r.url, r.media_url):
            if u in held:
                in_case.append(
                    {
                        "kind": "same_link",
                        "sha256": held[u]["sha256"],
                        "why": f"This link was captured on {held[u]['at'][:10]}",
                    }
                )
        if thumb and r.kind in ("images", "videos"):
            near = await _same_picture(db, thumb[0], investigation)
            if near:
                in_case.append(
                    {
                        "kind": "same_picture",
                        "sha256": near.blob_sha256,
                        "why": f"The thumbnail matches a picture in this case "
                        f"(PDQ distance {near.d}); a lead, not a confirmed copy",
                    }
                )
        candidate = {
            "kind": r.kind,
            "url": r.url,
            "media_url": r.media_url,
            "title": r.title,
            "domain": r.domain,
            "date": r.date,
            "description": r.description,
            "query": q["text"],
            "access": str(access),
            "investigation": investigation,
            "scope": scope,
            "thumb": thumb,
            "found_at": datetime.now(UTC).isoformat(),
        }
        cid = store.put(user_id, candidate)
        groups[r.kind].append(
            {
                "id": cid,
                "kind": r.kind,
                "url": r.url,
                "media_url": r.media_url,
                "title": r.title,
                "domain": r.domain,
                "date": r.date,
                "description": r.description,
                "thumb": bool(thumb),
                "in_case": in_case,
                "why": {"query": q["text"], "from": q["why"]},
            }
        )
    return {"sent": sent, "groups": groups}


async def _none():
    return None


# --- capture ---------------------------------------------------------------------


async def queue_capture(
    db: AsyncSession,
    user_id: int,
    who,
    ids: list[str],
    sensitivity: str,
    doc_type: str,
    batch: str | None = None,
) -> dict:
    """Queue one capture run per picked result, into `batch` when the caller has an
    intake batch open (Add evidence), else a new one."""
    if missing()["capture"]:
        raise Refusal("Capture is not configured: " + ", ".join(missing()["capture"]))
    sensitivity = str(Sensitivity(sensitivity))
    if not 1 <= len(ids) <= 20:
        raise ValueError("pick 1 to 20 results")
    candidates = []
    for cid in ids:
        c = store.get(user_id, cid)
        if c is None:
            raise LookupError("A picked result has expired; search again.")
        candidates.append(c)
    for inv in {c["investigation"] for c in candidates}:
        if reason := await refusal(db, inv):
            raise Refusal(reason)
    batch = str(uuid.UUID(batch)) if batch else str(uuid.uuid4())
    agent = await system_agent(db, "capture")
    runs = []
    for c in candidates:
        if not await consume_limit(
            ext.async_session_factory,
            f"research:capture:{user_id}",
            Config.RESEARCH_CAPTURES_PER_HOUR,
            3600,
        ):
            raise Refusal("Hourly capture limit reached; the rest were not queued.")
        run = Run(
            principal_id=agent.id,
            tool="capture",
            route="local",
            # No query text here: run inputs are chained and cannot be erased. The
            # access statement holds it and erasure can reach it.
            inputs={
                "page": normalise_url(c["url"]),
                "kind": c["kind"],
                "media_url": c["media_url"],
                "title": c["title"],
                "domain": c["domain"],
                "date": c["date"],
                "found_at": c["found_at"],
                "query_access": c["access"],
                "provider": {"name": "brave", "endpoint": brave.endpoint(c["kind"])},
                "investigation": c["investigation"],
                "sensitivity": sensitivity,
                "doc_type": doc_type,
                "batch": batch,
                "analyst": str(who.id),
            },
        )
        db.add(run)
        await db.flush()
        runs.append(str(run.id))
    return {"batch": batch, "runs": runs}


async def capture_status(db: AsyncSession, batch: str) -> list[dict]:
    rows = (
        await db.execute(
            select(Run)
            .where(Run.tool == "capture", Run.inputs["batch"].astext == batch)
            .order_by(Run.id)
        )
    ).scalars()
    return [
        {
            "run": str(r.id),
            "page": r.inputs["page"],
            "title": r.inputs.get("title"),
            "status": r.status,
            "error": (r.error or "").split(": ", 1)[-1][:300] or None,
        }
        for r in rows
    ]
