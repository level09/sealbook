"""yt-dlp for the one video page an analyst picked: no playlists, no cookies, no
account, a size cap. A page that needs a login is refused, never worked around."""

from __future__ import annotations

import asyncio
from pathlib import Path

import yt_dlp
import yt_dlp.version

VERSION = yt_dlp.version.__version__

LOGIN = ("sign in", "log in", "login", "private video", "members-only", "cookies")


class LoginRequired(Exception):
    """The video is behind an account."""


def _download(page: str, directory: str, max_bytes: int) -> tuple[Path, dict]:
    opts = {
        "outtmpl": str(Path(directory) / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "playlist_items": "1",
        "format": "best[ext=mp4]/best",
        "max_filesize": max_bytes,
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 2,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(page, download=True)
            path = Path(ydl.prepare_filename(info))
    except yt_dlp.utils.DownloadError as exc:
        if any(w in str(exc).lower() for w in LOGIN):
            raise LoginRequired(str(exc)) from exc
        raise
    if not path.exists():
        raise ValueError("yt-dlp downloaded no file (larger than the size cap?)")
    keep = ("id", "extractor", "webpage_url", "title", "duration", "upload_date")
    # The ledger's canonical JSON refuses floats (duration is one).
    return path, {
        k: round(v) if isinstance(v, float) else v
        for k in keep
        if (v := info.get(k)) is not None
    }


async def download(page: str, directory: str, max_bytes: int) -> tuple[Path, dict]:
    return await asyncio.to_thread(_download, page, directory, max_bytes)
