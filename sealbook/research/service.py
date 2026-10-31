"""Web research: the guardrails, sending queries, and queueing captures.

Every query reveals what it contains to the search provider, so: a person starts it,
sees each query before it is sent, a sensitive or switched-off case refuses it, each
query is an access statement written before the request leaves, and per-person hourly
limits apply. Results live in process memory (sealbook.research.store) only."""

from __future__ import annotations

import asyncio
import io
import re
import uuid
from datetime import UTC, date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx2
import numpy as np
import pdqhash
import stk.extensions as ext
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stk.utils import netguard
from stk.utils.ratelimit import consume_limit

from sealbook.cases.models import Case
from sealbook.discovery.models import MediaFrame
from sealbook.discovery.service import available
from sealbook.ingest.service import normalise_url, system_agent
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop, Sensitivity, StatementIn
from sealbook.ledger.models import Acquisition, Run
from sealbook.monitor import filter as mfilter
from sealbook.research import brave, browser, store, youtube
from sealbook.research.models import CaseSetting
from sealbook.research.queries import KINDS
from sealbook.search.arabic import normalize
from sealbook.settings import Config

MAX_QUERIES = 8
MAX_QUERY_CHARS = 300
THUMB_BYTES = 2 * 1024 * 1024
TRACKING = re.compile(
    r"utm_.*|fbclid|gclid|dclid|msclkid|igshid|mc_cid|mc_eid|_ga", re.I
)
SUBJECT_LABELS = {"Name", "Gazetteer name", "Incident place"}
PROVIDER = {"web": "brave", "images": "brave", "videos": "brave", "youtube": "youtube"}
MATCHES_PER_VIDEO = 3


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
            "youtube_subtitles": Config.RESEARCH_YOUTUBE_SUBTITLES,
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


def clean_window(start, end) -> tuple[str, str] | None:
    """The analyst's date window: both ends as YYYY-MM-DD, or neither."""
    if not start and not end:
        return None
    try:
        a, b = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    except ValueError as exc:
        raise ValueError("set both dates as YYYY-MM-DD, or neither") from exc
    if a > b:
        raise ValueError("the window starts after it ends")
    return a.isoformat(), b.isoformat()


def link_key(url: str | None) -> str | None:
    """One page, one key: scheme, www, trailing slash, fragment and tracking
    parameters do not tell two links apart; other query parameters do."""
    if not url:
        return None
    try:
        parts = urlsplit(normalise_url(url))
    except ValueError:
        return url
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not TRACKING.fullmatch(k)
    )
    host = (parts.netloc.removeprefix("www.")) + parts.path.rstrip("/")
    return host + ("?" + urlencode(query) if query else "")


def _tokens(text: str) -> set[str]:
    return {t.removeprefix("ال") for t in re.findall(r"\w+", normalize(text).lower())}


def _head(name: str) -> str | None:
    words = [
        t for t in re.findall(r"\w+", normalize(name).lower()) if t not in ("al", "el")
    ]
    return words[0].removeprefix("ال") if words else None


def subject_names(q: dict) -> list[str]:
    return [w["value"] for w in q.get("why") or [] if w.get("label") in SUBJECT_LABELS]


def spoken(qs: list[dict]) -> list[set[str]]:
    """What to listen for in subtitles: each recorded spelling of the subject, or
    for a typed query its own words; numbers are left out, since speech says them
    in words."""
    names = [n for q in qs for n in subject_names(q)] or [
        q["text"].replace('"', " ") for q in qs
    ]
    out = []
    for n in names:
        words = {t for t in _tokens(n) if not t.isdigit() and t not in ("al", "el")}
        if words and words not in out:
            out.append(words)
    return out


def heard(lines: list[tuple[int, str]], spellings: list[set[str]], video_id: str):
    """Subtitle lines that say a spelling, every word of it within one cue and the
    next (a name is often split across two), each with a link to that second."""
    out, last = [], -2
    for i, (at, text) in enumerate(lines):
        here = _tokens(text)
        window = here | (_tokens(lines[i + 1][1]) if i + 1 < len(lines) else set())
        if i == last + 1 or not any(s <= window and s & here for s in spellings):
            continue
        if not any(s <= here for s in spellings):
            text = f"{text} {lines[i + 1][1]}"
        out.append(
            {
                "at": at,
                "text": text[:200],
                "url": f"{youtube.WATCH.format(video_id)}&t={at}s",
            }
        )
        last = i
        if len(out) == MATCHES_PER_VIDEO:
            break
    return out


async def log_query(
    db: AsyncSession,
    who,
    scope: str,
    investigation: str,
    q: dict,
    kind: str,
    window: tuple[str, str] | None = None,
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
                "provider": PROVIDER[kind],
                "kind": kind,
                "query": q["text"],
                "investigation": investigation,
                "people": q["people"],
                "at": datetime.now(UTC).isoformat(),
                **({"window": list(window)} if window and kind == "web" else {}),
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
        async with netguard.stream(
            url, timeout=10, max_bytes=THUMB_BYTES, transport=transport
        ) as (resp, _):
            mime = resp.headers.get("content-type", "").split(";")[0].strip()
            if resp.status_code != 200 or not mime.startswith("image/"):
                return None
            return await resp.aread(), mime
    except (httpx2.HTTPError, netguard.Blocked, TimeoutError):
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


async def _same_video(db: AsyncSession, ids: set[str], investigation: str) -> dict:
    """Video ids captured before in this case, from any page that led to them."""
    vid = Acquisition.context["capture"]["video"]["id"].astext
    rows = await db.execute(
        select(vid, Acquisition.blob_sha256, Acquisition.at)
        .where(
            vid.in_(list(ids)),
            Acquisition.context["capture"]["role"].astext == "video",
            Acquisition.context["investigation"].astext == investigation,
        )
        .order_by(Acquisition.at)
    )
    out: dict[str, dict] = {}
    for v, sha, at in rows:
        out.setdefault(v, {"sha256": sha, "at": at.isoformat()})
    return out


def _grounded(heads: set[str], r) -> bool | None:
    return bool(heads & _tokens(f"{r.title} {r.description}")) if heads else None


async def search(
    db: AsyncSession,
    user_id: int,
    who,
    scope: str,
    investigation: str,
    queries: list[dict],
    transport: httpx2.AsyncBaseTransport | None = None,
    window: tuple[str, str] | None = None,
) -> dict:
    if missing()["search"] and any(
        PROVIDER[k] == "brave" for q in queries for k in q["kinds"]
    ):
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
            access = await log_query(db, who, scope, investigation, q, kind, window)
            try:
                if kind == "youtube":
                    found = await youtube.search(q["text"], Config.RESEARCH_RESULTS)
                else:
                    found = await brave.search(
                        kind, q["text"], Config.RESEARCH_RESULTS, transport, window
                    )
            except (brave.SearchError, youtube.YouTubeError) as exc:
                sent.append(q | {"kind": kind, "error": str(exc), "count": 0})
                continue
            sent.append(q | {"kind": kind, "count": len(found)})
            results += [(r, q, access, rank) for rank, r in enumerate(found)]

    # Copies of one page (or one YouTube video) found by several queries become one
    # result, ranked by its best place in any of them.
    merged: dict[tuple, dict] = {}
    for r, q, access, rank in results:
        key = (
            (r.kind, r.video_id)
            if r.kind == "youtube"
            else (r.kind, link_key(r.url), link_key(r.media_url))
        )
        m = merged.setdefault(
            key, {"r": r, "q": q, "qs": [], "accesses": [], "rank": rank}
        )
        if q["text"] not in [x["text"] for x in m["qs"]]:
            m["qs"].append(q)
            m["accesses"].append(access)
        m["rank"] = min(m["rank"], rank)
    results = list(merged.values())
    names = [n for q in queries for n in subject_names(q)]
    heads = {h for n in names if (h := _head(n))}

    # Subtitles of the leading YouTube results, read for the subject's spellings.
    videos = sorted(
        (m for m in results if m["r"].kind == "youtube"),
        key=lambda m: (_grounded(heads, m["r"]) is False, m["rank"]),
    )[: Config.RESEARCH_YOUTUBE_SUBTITLES]
    subs = await youtube.subtitles(
        [m["r"].video_id for m in videos], Config.RESEARCH_YOUTUBE_SUBTITLE_SECONDS
    )

    urls = {m["r"].url for m in results} | {
        m["r"].media_url for m in results if m["r"].media_url
    }
    held = await _same_url(db, urls, investigation)
    held_videos = await _same_video(
        db,
        {m["r"].video_id for m in results if m["r"].kind == "youtube"},
        investigation,
    )
    thumbs = await asyncio.gather(
        *(
            _thumb(m["r"].thumbnail, transport) if m["r"].thumbnail else _none()
            for m in results
        )
    )
    groups: dict[str, list] = {k: [] for k in KINDS}
    for m, thumb in zip(results, thumbs, strict=True):
        r, q = m["r"], m["q"]
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
        if r.kind == "youtube" and r.video_id in held_videos and r.url not in held:
            h = held_videos[r.video_id]
            in_case.append(
                {
                    "kind": "same_video",
                    "sha256": h["sha256"],
                    "why": f"This video was captured on {h['at'][:10]}",
                }
            )
        if thumb and r.kind in ("images", "videos", "youtube"):
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
        row = {
            "kind": r.kind,
            "url": r.url,
            "media_url": r.media_url,
            "title": r.title,
            "domain": r.domain,
            "date": r.date,
            "description": r.description,
        }
        grounded = _grounded(heads, r)
        if r.kind == "youtube":
            row |= {
                "video_id": r.video_id,
                "channel": r.channel,
                "channel_id": r.channel_id,
                "duration": r.duration,
                "views": r.views,
                "approximate": r.approximate,
            }
            found = subs.get(r.video_id)
            if found is None:
                row["subtitles"] = {"status": "not_checked"}
            elif "error" in found:
                row["subtitles"] = {
                    "status": "failed",
                    "message": found["error"],
                    "bot": bool(found.get("bot")),
                }
            else:
                if found["upload_date"]:
                    row |= {"date": found["upload_date"], "approximate": False}
                said = [
                    line | {"lang": t["lang"], "auto": t["auto"]}
                    for t in found["tracks"]
                    for line in heard(
                        youtube.cues(t["vtt"]), spoken(m["qs"]), r.video_id
                    )
                ][:MATCHES_PER_VIDEO]
                row["subtitles"] = {
                    "status": "said"
                    if said
                    else "none"
                    if found["tracks"]
                    else "absent",
                    "tracks": [
                        {"lang": t["lang"], "auto": t["auto"]} for t in found["tracks"]
                    ],
                    "lines": said,
                }
                if said and heads:
                    grounded = True
        candidate = row | {
            "query": q["text"],
            "access": str(m["accesses"][0]),
            "accesses": [str(a) for a in m["accesses"]],
            "investigation": investigation,
            "scope": scope,
            "thumb": thumb,
            "found_at": datetime.now(UTC).isoformat(),
        }
        cid = store.put(user_id, candidate)
        # Never dropped: an ungrounded result sorts below the grounded ones.
        groups[r.kind].append(
            row
            | {
                "id": cid,
                "thumb": bool(thumb),
                "in_case": in_case,
                "why": {"query": q["text"], "from": q["why"]},
                "queries": [x["text"] for x in m["qs"]],
                "rank": m["rank"],
                "grounded": grounded,
                "subject": next(iter(subject_names(q)), None),
            }
        )
    for rows in groups.values():
        # Pages keep the order they were found in; YouTube keeps its relevance.
        rows.sort(
            key=lambda x: (x["grounded"] is False, x["rank"] if "video_id" in x else 0)
        )
    every = [x for k in KINDS for x in groups[k]]
    nothing = bool(heads and every and not any(x["grounded"] for x in every))
    return {
        "sent": sent,
        "groups": groups,
        "window": list(window) if window else None,
        "subject": names[0] if names else None,
        "closest": every[0]["id"] if nothing else None,
    }


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
    sensitivity = str(Sensitivity(sensitivity))
    if not 1 <= len(ids) <= 20:
        raise ValueError("pick 1 to 20 results")
    candidates = []
    for cid in ids:
        c = store.get(user_id, cid)
        if c is None:
            raise LookupError("A picked result has expired; search again.")
        candidates.append(c)
    # A YouTube capture is yt-dlp alone; any other result needs the browser.
    if missing()["capture"] and any(c["kind"] != "youtube" for c in candidates):
        raise Refusal("Capture is not configured: " + ", ".join(missing()["capture"]))
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
                "query_accesses": c["accesses"],
                "provider": (
                    {
                        "name": "youtube",
                        "endpoint": "ytsearch",
                        "yt_dlp": youtube.VERSION,
                    }
                    if c["kind"] == "youtube"
                    else {"name": "brave", "endpoint": brave.endpoint(c["kind"])}
                ),
                **({"video_id": c["video_id"]} if c["kind"] == "youtube" else {}),
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
