"""Versioned module imports on top of stk's versioned static URLs.

Templates call `asset('js/x.js')`, which is `url_for('static', ...)`: stk adds a
content hash (`?v=...`) and caches versioned files forever. ES modules import
each other by plain path, so `import_map()` emits an import map that rewrites
every module URL to its versioned form, covering imports nested at any depth
with no edits to the JS.
"""

import json
from pathlib import Path

from markupsafe import Markup
from quart import url_for

STATIC = Path(__file__).parent / "static"
MODULE_DIRS = ("js", "pdfjs", "maplibre")


def asset(path: str) -> str:
    return url_for("static", filename=path)


def import_map() -> Markup:
    imports = {
        f"/static/{p.relative_to(STATIC)}": asset(str(p.relative_to(STATIC)))
        for d in MODULE_DIRS
        for p in sorted((STATIC / d).rglob("*"))
        if p.suffix == ".mjs" or (p.suffix == ".js" and ".min." not in p.name)
    }
    body = json.dumps({"imports": imports}).replace("</", "<\\/")
    return Markup(f'<script type="importmap">{body}</script>')


def register_assets(app):
    app.jinja_env.globals["asset"] = asset
    app.jinja_env.globals["import_map"] = import_map
