"""`asset('js/x.js')` for templates: stk's versioned `url_for('static', ...)`.
Nested module imports are versioned by stk's own import map in the layout."""

from quart import url_for


def asset(path: str) -> str:
    return url_for("static", filename=path)


def register_assets(app):
    app.jinja_env.globals["asset"] = asset
