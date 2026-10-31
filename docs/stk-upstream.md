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

- **Static asset versioning with one import map** (`sealbook/assets.py`): `asset()` adds a
  content hash, versioned responses are `immutable`, unversioned ones `no-cache`, and
  module imports are rewritten by one import map in the layout. Fixes stale JS after
  deploys without a build step.
- **Screen sense check** (`acceptance/check_sense.py`, largely in `stk smoke` since 17.2.0): a Playwright crawl that fails on
  raw ids, uuids, snake_case, internal vocabulary, raw scores, repeated rows, plural
  errors and broken values in visible text. Smoke tests pass pages that read badly; this
  catches them. The rule functions are generic; the page list and deny list are per app.
- **Graceful shutdown defaults and the `{item: {...}}` envelope**, above.
- **Parallel work in one repository**: one worktree per line of work and merges by
  one maintainer; temporary index tricks are not enough (see lessons, "Parallel work
  in one repository").

## Found moving to stk 17 (2026-10-25)

Sealbook now depends on `stkit>=17.1.0,<18` and edits no stk file. These are what
the move needed from the app side, and what stk 17.0.x could offer instead.

| Gap | What Sealbook does | For stk |
| --- | --- | --- |
| `stk verify` ran a check that `GET /` returns 200, which failed an app whose `/` redirects | Fixed in stk 17.0.2: the check follows redirects | Done |
| No documented way to replace stk's own `/` view | `sealbook/shell` swaps `app.view_functions["public.index"]` | Documented as the supported way in stk 17.0.2 |
| The layout's `stk_navigation()` always wraps app entries in stk's Dashboard, Account and Administration sections | `sealbook/shell` adds a `rail_navigation()` global returning `app.navigation` only | Expose the app's entries on their own |
| App commands landed under "Other" in `stk --help` | `STK_CLI_SECTIONS` in `sealbook/settings.py` | Added in stk 17.0.2 |
| `stk db revision` writes `pgvector.sqlalchemy.bit.BIT`, `pgvector.sqlalchemy.halfvec.HALFVEC` and app `UserDefinedType` columns by module path with no import, and drops column-level `CheckConstraint`s (an Alembic limitation) | `sealbook_0001` imports pgvector's types, spells PostGIS types out locally and adds the three checks by hand | Import the modules of rendered types; warn about column-level checks |
| `build_alembic_config(app, url)` needs an app, so a test that migrates a throwaway database before it builds its own app must import the module-level app | Tests import `sealbook.app.app` for that | Accept the app's import name or migrations directory |
| `url_for('static')` versions each file, but ES modules import each other by plain path, so a nested module stays stale after a deploy | `sealbook/assets.py` keeps one import map of versioned module URLs | Ship the import map with the layout |
| `stk.Config` no longer has `PROJECT_ROOT`, which apps used for paths under `instance/` | `sealbook/settings.py` defines its own | Expose the app's root or instance path in config |

## Found moving to stk 17.2 (2026-10-31)

Sealbook depends on `stkit>=17.3.1,<18`. It runs its own queue as `stk runner` (stk's
`worker` drains `stk_job`), uses `stk.utils.netguard` and `httpx2` throughout, and
keeps its own MCP, notifications and evidence storage.

| Gap | What Sealbook does | For stk |
| --- | --- | --- |
| stk 17.2.0 to 17.2.2 always registered blueprint `mcp`, `POST /mcp` and table `mcp_token`, which Sealbook already had: import and `stk db upgrade` failed | Stayed on 17.1 until 17.2.3 | Done in 17.2.3: MCP is opt-in, its table is `stk_mcp_token` |
| stk always registers `/api/notifications` and `/api/notifications/read`; Sealbook has its own at those paths, and Werkzeug matches the first rule added, so stk's answered silently (no role check, empty list) | Stayed on its own routes | Done in 17.2.4: notifications are opt-in, and `stk verify` fails when two endpoints share a path and method |
| Sealbook's MCP agent acts as its own ledger principal per OAuth grant; stk tools run as the token's user and stk has no OAuth | Keeps `sealbook/mcp` | An OAuth authorization server (client ID metadata documents, PKCE, rotating opaque tokens) with a hook that maps a grant to the identity a tool runs as |
| `notify()` stores title text; Sealbook stores a kind and short ids and builds the label when it is read, so erasure leaves nothing in notifications | Keeps its own notifications | Render titles at read time from a kind and refs |
| `stk.files` has no sensitivity, custody or erasure model | Keeps `sealbook/blobs.py` | None yet; type sniffing and thumbnails may be reusable later |
| `netguard.stream` raises `TimeoutError` past its deadline, not an `httpx2` error, and the deadline covers the whole body | Callers catch both; downloads pass `timeout=900` | One line in the netguard docs |
| Tests fake DNS by patching the private `netguard._resolve` | Patches `_resolve` | A public `resolve`, or a resolver hook |
| stk's `worker` command took the name of Sealbook's | Renamed to `stk runner` | Done; the changelog said so |
