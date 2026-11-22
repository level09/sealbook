"""The case film, served by the app from the public site's own files.

site/media holds the one copy of the film, its poster and captions, and
site/film_board.tsv says where each story starts. The app reads both in place, so
a new render reaches the app with nothing to copy; the Docker image and a git
deploy both carry site/. Nothing is fetched from another host."""

import hashlib
from functools import cache
from pathlib import Path

from quart import Blueprint, abort, send_file
from quart_security import auth_required

SITE = Path(__file__).resolve().parents[2] / "site"
FILES = {
    "case_film.mp4": "video/mp4",
    "case_film.jpg": "image/jpeg",
    "case_film.vtt": "text/vtt",
}

bp_film = Blueprint("film", __name__)


@cache
def board() -> list[dict]:
    """The storyboard rows in film order: start second, label and, for a case
    study, its case slug and how long its part runs (to the next row's start)."""
    lines = (SITE / "film_board.tsv").read_text(encoding="utf-8").splitlines()
    body = [line for line in lines if line and not line.startswith("#")][1:]
    rows = []
    for line in body:
        seek, _still, label, *case = line.split("\t")
        rows.append(
            {
                "at": float(seek),
                "label": label,
                "case": f"case-{case[0]}" if case and case[0] else None,
            }
        )
    for row, after in zip(rows, rows[1:] + [None], strict=True):
        row["length"] = round(after["at"] - row["at"]) if after else None
    return rows


def chapters() -> list[dict]:
    """The four case studies, each a chapter of the film."""
    return [r for r in board() if r["case"]]


def for_case(slug: str) -> dict | None:
    """Where the film tells this case, or None for every case it does not."""
    return next((r for r in chapters() if r["case"] == slug), None)


@cache
def version(name: str) -> str:
    """A short hash of the file, so a new render is never served from a cache."""
    return hashlib.sha1((SITE / "media" / name).read_bytes()).hexdigest()[:8]


def urls() -> dict:
    return {
        name.rpartition(".")[2]: f"/film/{name}?v={version(name)}" for name in FILES
    }


@bp_film.get("/film/<name>")
@auth_required("session")
async def media(name):
    if name not in FILES:
        abort(404)
    # Range requests (seeking) and conditional gets come from send_file. The
    # versioned URL changes with the bytes, so the browser may keep it.
    response = await send_file(
        SITE / "media" / name, mimetype=FILES[name], conditional=True
    )
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response
