# Notes for the stk framework

Issues and pieces found while building Sealbook on [stk](https://github.com/level09/stk)
that are not Sealbook specific. Each entry says what happened, what Sealbook did, and
whether it belongs in stk core. Nothing here has been filed upstream yet; details and
root causes are in [lessons.md](lessons.md).

## Defects and sharp edges

| Issue | What Sealbook did | For stk core |
| --- | --- | --- |
| Ctrl+C hung while any tab was open: Hypercorn waits for every connection, and every page holds `/ws`; uvicorn waited forever on SSE streams | `stk run` serves through uvicorn with `--timeout-graceful-shutdown 3` | Yes, default the dev and prod launch lines |
| Killing `uv run stk run` left the reload child listening; the restarted server failed on the busy port while the old one kept serving cached templates | Stop dev servers by port (`lsof -tiTCP:<port> -sTCP:LISTEN`) | Yes, `stk run` should forward signals to its child and refuse a busy port loudly |
| The `${ }` Vue delimiter ends at the first `}`, so an object literal in an interpolation compiles to a broken render function and the page stays blank with only "Unexpected token ')'" | Move such expressions into methods | Yes, document it in the template guide or lint templates for `{` inside `${ }` |
| An empty Axios POST failed the global JSON body check | Every mutation sends `{item: {...}}` | Yes, either accept an empty body for bodiless actions or make the client helper always send the envelope |
| Tests inherit `.env`, so a developer's secret can turn a test into a real network call | Pin every model, provider and auth switch a test depends on | Yes, load a test settings profile that blanks secrets by default |
| A server started without debug keeps its Jinja template cache, so template edits look ignored | Restart before judging a template edit | Docs, or auto-reload templates in dev |
| `selectin` relations load on query, not on `add()`; touching an unloaded relation in async code raises `MissingGreenlet` | Fetch through the session first | Docs |
| `uv run alembic` can read a different config than `stk db` | Use `uv run stk db current|upgrade` | Yes, make `alembic.ini` and `stk db` share one source of the URL |
| Vuetify styles live in CSS layers, so any unlayered app rule wins (a scoped `button { color: inherit }` blacked out a drawer button) | Keep app rules scoped and layered, mount dialogs outside custom wrappers | Docs, or ship app CSS inside a layer |

## Pieces worth upstreaming

- **Static asset versioning with one import map** (`stk/assets.py`): `asset()` adds a
  content hash, versioned responses are `immutable`, unversioned ones `no-cache`, and
  module imports are rewritten by one import map in the layout. Fixes stale JS after
  deploys without a build step.
- **Screen sense check** (`acceptance/check_sense.py`): a Playwright crawl that fails on
  raw ids, uuids, snake_case, internal vocabulary, raw scores, repeated rows, plural
  errors and broken values in visible text. Smoke tests pass pages that read badly; this
  catches them. The rule functions are generic; the page list and deny list are per app.
- **Graceful shutdown defaults and the `{item: {...}}` envelope**, above.
- **Working with several coding agents in one repository**: one worktree per agent and
  merges by one owner; temporary index tricks are not enough (see lessons, "Working with
  agents and shared trees").
