"""Versioned static URLs: `?v=<content hash>` so a changed file is always refetched.

Templates call `asset('js/x.js')`. ES modules import each other by plain path, so
`import_map()` emits an import map that rewrites every module URL to its versioned
form, covering imports nested at any depth with no edits to the JS.
"""

import hashlib
import json
from pathlib import Path

from markupsafe import Markup
from quart import request

STATIC = Path(__file__).parent / "static"
MODULE_DIRS = ("js", "pdfjs", "maplibre")
LONG = "public, max-age=31536000, immutable"

_seen: dict[str, tuple[int, str]] = {}


def version(path: str, recheck: bool) -> str:
    """Short content hash, computed once per file; re-stat'd on each call in dev."""
    hit = _seen.get(path)
    if hit and not recheck:
        return hit[1]
    file = STATIC / path
    mtime = file.stat().st_mtime_ns
    if hit and hit[0] == mtime:
        return hit[1]
    digest = hashlib.sha256(file.read_bytes()).hexdigest()[:10]
    _seen[path] = (mtime, digest)
    return digest


def register_assets(app):
    def asset(path: str) -> str:
        return f"/static/{path}?v={version(path, app.debug)}"

    def import_map() -> Markup:
        imports = {
            f"/static/{p.relative_to(STATIC)}": asset(str(p.relative_to(STATIC)))
            for d in MODULE_DIRS
            for p in sorted((STATIC / d).rglob("*"))
            if p.suffix == ".mjs" or (p.suffix == ".js" and ".min." not in p.name)
        }
        body = json.dumps({"imports": imports}).replace("</", "<\\/")
        return Markup(f'<script type="importmap">{body}</script>')

    app.jinja_env.globals["asset"] = asset
    app.jinja_env.globals["import_map"] = import_map

    @app.after_request
    async def _static_cache(response):
        if request.path.startswith("/static/") and response.status_code in (200, 304):
            response.headers["Cache-Control"] = (
                LONG if "v" in request.args else "no-cache"
            )
        return response
