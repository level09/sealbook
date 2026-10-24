"""YouTube through yt-dlp: search without an API key, subtitles of a video without
its media, and the download of one picked video with its metadata, subtitles and
thumbnail. No cookies, no account, no config files: the API reads none unless told
to. A bot check or a sign-in wall is reported in plain words, never worked around.

The yt-dlp calls (`_search`, `_info`, `_fetch`, `_download`) are the only code that
touches the network; tests replace them with recorded fixtures."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yt_dlp
import yt_dlp.version
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.utils import DownloadCancelled, DownloadError

from stk.research.brave import Result

VERSION = yt_dlp.version.__version__
WATCH = "https://www.youtube.com/watch?v={}"
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
MAX_SUBTITLE_BYTES = 5_000_000
PREFERRED = ("ar", "en")
MEDIA_EXT = (".mp4", ".webm", ".mkv", ".m4a", ".mov")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "noplaylist": True,
    "socket_timeout": 20,
    "retries": 1,
    "cachedir": False,
}
# Floats are rounded for the ledger's canonical JSON; the info file keeps them.
INFO_KEYS = (
    "id",
    "webpage_url",
    "original_url",
    "title",
    "description",
    "channel",
    "channel_id",
    "channel_url",
    "uploader",
    "uploader_id",
    "upload_date",
    "timestamp",
    "duration",
    "view_count",
    "like_count",
    "comment_count",
    "tags",
    "categories",
    "availability",
    "live_status",
    "language",
    "format_id",
    "width",
    "height",
    "vcodec",
    "acodec",
    "extractor",
)
CONTEXT_KEYS = (
    "id",
    "webpage_url",
    "title",
    "channel",
    "channel_id",
    "upload_date",
    "duration",
    "extractor",
)


class YouTubeError(Exception):
    """YouTube did not answer as asked; the message is for the analyst."""


class BotCheck(YouTubeError):
    pass


class LoginRequired(YouTubeError):
    pass


class TimeLimit(DownloadCancelled):
    pass


@dataclass(frozen=True)
class Video(Result):
    video_id: str = ""
    channel: str | None = None
    channel_id: str | None = None
    duration: int | None = None
    views: int | None = None
    approximate: bool = False  # date read from "3 years ago", not the upload date


def plain(exc: Exception) -> YouTubeError:
    """yt-dlp's error as something an analyst can act on."""
    text = str(exc)
    low = text.lower()
    if "not a bot" in low or "429" in low or "too many requests" in low:
        return BotCheck(
            "YouTube asked for a bot check or refused for too many requests. Sealbook "
            "does not solve or bypass bot checks; try again later."
        )
    if any(w in low for w in ("sign in", "private video", "members-only", "cookies")):
        return LoginRequired(
            "YouTube asks to sign in to watch this video (private, members only or "
            "age restricted). Sealbook does not log in."
        )
    line = re.sub(
        r"^ERROR:\s*(\[[^\]]+\]\s*)*([\w-]+:\s)?", "", text.strip().splitlines()[0]
    )
    return YouTubeError(f"YouTube did not answer: {line[:200]}")


def upload_day(info: dict) -> tuple[str | None, bool]:
    """(YYYY-MM-DD, approximate). Flat search entries carry only a timestamp worked
    out from "3 years ago"; a full extraction carries the upload date."""
    if d := info.get("upload_date"):
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}", False
    if ts := info.get("timestamp"):
        return datetime.fromtimestamp(ts, UTC).date().isoformat(), True
    return None, False


def _video(row: dict) -> Video | None:
    vid = row.get("id") or ""
    if row.get("ie_key") not in (None, "Youtube") or not VIDEO_ID.fullmatch(vid):
        return None  # channels and playlists
    day, approx = upload_day(row)
    return Video(
        kind="youtube",
        url=WATCH.format(vid),
        title=row.get("title") or "",
        domain="youtube.com",
        date=day,
        description=row.get("description") or "",
        thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        media_url=None,
        video_id=vid,
        channel=row.get("channel") or row.get("uploader"),
        channel_id=row.get("channel_id"),
        duration=round(row["duration"]) if row.get("duration") else None,
        views=row.get("view_count"),
        approximate=approx,
    )


def _search(query: str, count: int) -> dict:
    opts = OPTS | {
        "extract_flat": "in_playlist",
        "extractor_args": {"youtubetab": {"approximate_date": [""]}},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(f"ytsearch{count}:{query}", download=False)


async def search(query: str, count: int) -> list[Video]:
    try:
        found = await asyncio.to_thread(_search, query, count)
    except DownloadError as exc:
        raise plain(exc) from exc
    return [v for row in found.get("entries") or [] if (v := _video(row))]


# --- subtitles -------------------------------------------------------------------


def _vtt(formats: list[dict]) -> str | None:
    return next((f["url"] for f in formats if f.get("ext") == "vtt"), None)


def tracks(info: dict) -> list[dict]:
    """Manual subtitles, Arabic and English first (at most those two, else the first
    other language); else the automatic captions in the spoken language, never a
    machine translation of them."""
    manual = {
        k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"
    }

    def pref(lang):
        return next((i for i, p in enumerate(PREFERRED) if lang.split("-")[0] == p), 9)

    picked = [k for k in sorted(manual, key=pref) if pref(k) < 9][:2] or list(manual)[
        :1
    ]
    out = [
        {"lang": k, "auto": False, "url": u} for k in picked if (u := _vtt(manual[k]))
    ]
    if out:
        return out
    auto = info.get("automatic_captions") or {}
    for lang, formats in auto.items():
        url = _vtt(formats)
        # The spoken language is "<lang>-orig", or the only track without tlang=.
        if url and (lang.endswith("-orig") or "tlang=" not in url):
            return [{"lang": lang.removesuffix("-orig"), "auto": True, "url": url}]
    return []


_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s+-->")
_TAG = re.compile(r"<[^>]+>")


def cues(vtt: str) -> list[tuple[int, str]]:
    """(start second, new text) per cue. Automatic captions roll: each cue repeats
    the line before it, so a line already in the previous cue is dropped."""
    out, previous = [], set()
    for block in re.split(r"\n\s*\n", vtt.replace("\r", "")):
        lines = block.strip().split("\n")
        at = next((m for x in lines if (m := _TIME.match(x.strip()))), None)
        if not at:
            continue
        h, m, s = int(at[1] or 0), int(at[2]), int(at[3])
        body = [
            t
            for x in lines
            if not _TIME.match(x.strip())
            and (t := " ".join(_TAG.sub("", x).split()))
            and not t.isdigit()
        ]
        new = [t for t in body if t not in previous]
        previous = set(body)
        if new:
            out.append((h * 3600 + m * 60 + s, " ".join(new)))
    return out


def _info(video_id: str) -> dict:
    with yt_dlp.YoutubeDL(OPTS) as ydl:
        return ydl.extract_info(WATCH.format(video_id), download=False, process=False)


def _fetch(url: str) -> bytes:
    if not url.startswith("https://www.youtube.com/"):
        raise YouTubeError("subtitles offered from an unexpected host")
    try:
        with yt_dlp.YoutubeDL(OPTS) as ydl:
            data = ydl.urlopen(url).read(MAX_SUBTITLE_BYTES + 1)
    except RequestError as exc:
        raise plain(exc) from exc
    if len(data) > MAX_SUBTITLE_BYTES:
        raise YouTubeError("subtitles larger than the limit")
    return data


def _subtitles(video_id: str) -> dict:
    try:
        info = _info(video_id)
        picked = tracks(info)
        for t in picked:
            t["vtt"] = _fetch(t.pop("url")).decode("utf-8", "replace")
    except DownloadError as exc:
        raise plain(exc) from exc
    day, _ = upload_day(info)
    return {"upload_date": day, "tracks": picked}


async def subtitles(video_ids: list[str], seconds: float) -> dict[str, dict]:
    """Subtitles for these videos, all fetched at once and given `seconds` in all.
    A video still running at the deadline reads as not checked; its thread finishes
    on its own socket timeout."""
    tasks = {
        asyncio.ensure_future(asyncio.to_thread(_subtitles, v)): v for v in video_ids
    }
    if not tasks:
        return {}
    done, pending = await asyncio.wait(tasks, timeout=seconds)
    out = {}
    for t in done:
        exc = t.exception()
        out[tasks[t]] = (
            {"error": str(exc), "bot": isinstance(exc, BotCheck)} if exc else t.result()
        )
    for t in pending:
        t.cancel()
        out[tasks[t]] = {"error": "Not checked: the subtitle search ran out of time"}
    return out


# --- download --------------------------------------------------------------------


def _small(info: dict, keys) -> dict:
    return {
        k: round(v) if isinstance(v, float) else v
        for k in keys
        if (v := info.get(k)) is not None
    }


def _download(video_id: str, directory: str, max_bytes: int, seconds: int) -> dict:
    """Media, info, subtitles and thumbnail of one video into `directory`."""
    deadline = time.monotonic() + seconds

    def hook(_):
        if time.monotonic() > deadline:
            raise TimeLimit(f"the download took longer than {seconds} seconds")

    folder = Path(directory)
    opts = OPTS | {
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "max_filesize": max_bytes,
        "restrictfilenames": True,
        "writethumbnail": True,
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        raw = ydl.extract_info(WATCH.format(video_id), download=False, process=False)
        if raw.get("is_live") or raw.get("live_status") in ("is_live", "is_upcoming"):
            raise YouTubeError("This is a live stream; capture it after it has ended.")
        picked = tracks(raw)
        info = ydl.process_ie_result(raw, download=True)
    media = [p for p in folder.iterdir() if p.suffix in MEDIA_EXT]
    if not media:
        raise YouTubeError("yt-dlp downloaded no file (larger than the size cap?)")
    subs, skipped = [], []
    for t in picked:
        path = folder / f"{video_id}.{t['lang']}.vtt"
        try:
            path.write_bytes(_fetch(t["url"]))
        except YouTubeError as exc:
            skipped.append({"subtitles": t["lang"], "reason": str(exc)})
            continue
        subs.append({"path": path, "lang": t["lang"], "auto": t["auto"]})
    thumbs = [p for p in folder.iterdir() if p.suffix in IMAGE_EXT]
    return {
        "media": media[0],
        "thumbnail": thumbs[0] if thumbs else None,
        "subtitles": subs,
        "skipped": skipped,
        "info": _small(info, INFO_KEYS)
        | {"original_url": WATCH.format(video_id), "yt_dlp": VERSION},
        "context": _small(info, CONTEXT_KEYS),
    }


async def download(video_id: str, directory: str, max_bytes: int, seconds: int) -> dict:
    try:
        return await asyncio.to_thread(
            _download, video_id, directory, max_bytes, seconds
        )
    except TimeLimit as exc:
        raise YouTubeError(f"The download was stopped: {exc}") from exc
    except DownloadError as exc:
        raise plain(exc) from exc
