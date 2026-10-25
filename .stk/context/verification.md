# Verification

Use the smallest verification set that catches the risk introduced by the change.

Start with `uv run stk doctor` when anything behaves oddly: it reports env, secrets,
database, migration state, admin user, vendored assets, and agent-login exposure, and
each non-ok line carries the exact command that fixes it. `--json` for machine use.

While editing, leave `uv run stk verify --watch` running. It re-runs only the checks a
changed file can break (models touch migration drift, templates do not) and prints the
remedy under any failure.

Default checks:
- `uv run python -m unittest discover -s tests`
- `uv run ruff check .`
- `uv run python -m stk.checks`
- `uv run stk verify`

For frontend-touching changes, also run:
- `uv run stk smoke`

For model or migration changes, also run:
- `uv run stk db check` (fails if models drifted away from migrations; part of `stk verify`)
- `uv run stk db current`

For auth or route changes, also run:
- `uv run stk inspect routes --json`
- `uv run stk inspect context --json`
- `uv run stk report`

## Browser Feature Testing

For authenticated browser testing in development:

```bash
STK_ENV=development STK_ENABLE_AGENT_LOGIN=1 uv run stk browser-token create --user admin@example.com --ttl 60 --next /dashboard/
```

Open the returned `/_test/login?token=...` path in the browser. The route creates a normal authenticated session and redirects to the requested local path.

Use browser tests only for workflows where rendering, navigation, or interaction matters. Prefer API scenarios for CRUD and permission checks.

## Browser Smoke

The default real-browser check is:

```bash
uv run stk smoke
```

Install the dev dependency and Chromium once per machine:

```bash
uv sync
uv run playwright install chromium
```

The smoke command starts a temporary development app with a disposable database of
the configured dialect. Sealbook needs PostgreSQL. It creates `smoke@example.com`
with the admin role, logs in through the agent login route, and visits the fixed
framework pages plus parameterless application pages. It captures browser errors,
failed requests and contrast issues and writes `.stk/smoke/dashboard.png`.

See [current status](../../docs/status.md) for completed checks and
[operations](../../docs/operations.md) for test environment requirements. Smoke
with empty pages does not replace a populated search/review scenario.

Run `uv run python -m acceptance.check_workflow` for the populated synthetic
colleague, logged media, Arabic text, PDF page and coverage scenario. Model protocol
and migration backfill checks are in `tests/test_search_workflow.py`. They do not
evaluate live embedding quality.
