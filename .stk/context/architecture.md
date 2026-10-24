# Architecture

This checkout is Sealbook, an evidence application on stk. It requires PostgreSQL,
pgvector and pg_trgm. The core runtime is Quart, async SQLAlchemy, Alembic,
quart-security, SSE/WebSockets, and Vue Options API plus Vuetify without a build.

Start at [docs/README.md](../../docs/README.md). Use
`uv run stk inspect context --json` to inspect actual routes/models.

Important boundaries:
- Request DB sessions live on `g.db_session`.
- CLI DB sessions use `stk.extensions.async_session_factory`.
- Models inherit from `stk.extensions.Base`.
- Blueprint routes are async.
- Frontend code uses Vue Options API with `${}` delimiters.
- Signed ledger statements are evidence; discovery tables are rebuildable projections.
- Review discussion is participant-only and excluded from general monitor/MCP reads.
- The supported evidence deployment is one process; notifications are process-local.
