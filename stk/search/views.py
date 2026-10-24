"""The one search box: /search?q= renders the page, POST /api/search answers it."""

import time
from datetime import UTC, datetime

import httpx
import orjson
from quart import (
    Blueprint,
    Response,
    abort,
    current_app,
    g,
    render_template,
    request,
    session,
)
from quart_security import current_user

from stk.collaboration.views import guard, no_store
from stk.discovery import embeddings, service
from stk.ledger import write
from stk.ledger.contracts import Prop, StatementIn
from stk.monitor import data
from stk.monitor import filter as mfilter
from stk.search import find
from stk.settings import Config

bp = Blueprint("search", __name__)
bp.before_request(guard)
bp.after_request(no_store)


@bp.get("/search")
async def page():
    return await render_template(
        "search/index.html",
        boot={
            "investigations": await data.investigations(g.db_session),
            "embeddings": bool(Config.EMBEDDING_MODEL),
            "types": find.TYPES,
        },
    )


@bp.post("/api/search")
async def search():
    db = g.db_session
    item = ((await request.get_json(silent=True)) or {}).get("item", {})
    query, types, limit = (
        item.get("query"),
        item.get("types") or list(find.TYPES),
        item.get("limit", 8),
    )
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1000:
        abort(400, description="query must contain 1 to 1000 characters")
    if not isinstance(types, list) or not set(types) <= set(find.TYPES):
        abort(400, description=f"types must be a subset of {', '.join(find.TYPES)}")
    if type(limit) is not int or not 1 <= limit <= 50:
        abort(400, description="limit must be an integer from 1 to 50")
    try:
        f = mfilter.parse(orjson.dumps(item.get("filter", {})).decode())
    except ValueError as exc:
        abort(400, description=str(exc))
    query = query.strip()
    started = time.perf_counter()
    embedding, semantic = None, {"status": "off"}
    # A hash names one item; meaning has nothing to add to it.
    if (
        Config.EMBEDDING_MODEL
        and item.get("semantic", True)
        and not service.SHA.fullmatch(query.lower())
    ):
        try:
            embedding = await embeddings.query(db, query, Config.EMBEDDING_MODEL)
            semantic = {"status": "ok"}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # Exact matches still answer; the failure is shown, never silent.
            current_app.logger.warning("semantic search failed: %s", exc)
            semantic = {
                "status": "failed",
                "message": f"Local embedding service failed: {exc}",
            }
    # Same grant as evidence.granted: a logged, unexpired reveal in this session.
    revealed = {
        sha
        for sha, until in session.get("evidence_previews", {}).items()
        if until > time.time()
    }
    found = await find.find(
        db,
        query,
        f,
        types=types,
        limit=limit,
        embedding=embedding,
        revealed=revealed,
    )
    who = await write.principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    await write.append(
        db,
        StatementIn(
            subject="search:evidence",
            prop=Prop.ACCESS,
            value={
                "action": "search",
                "query": query,
                "mode": "hybrid" if embedding else "keyword",
                "filter": mfilter.dump(f),
                "at": datetime.now(UTC).isoformat(),
            },
            author_id=who.id,
        ),
    )
    await db.commit()
    return Response(
        orjson.dumps(
            {
                "query": query,
                "filter": mfilter.dump(f),
                "semantic": semantic,
                "ms": round((time.perf_counter() - started) * 1000),
                **found,
            }
        ),
        content_type="application/json",
    )
