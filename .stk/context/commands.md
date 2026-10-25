# Commands

Everything runs through `stk` (see `uv run stk --help` for the grouped map).
The app factory is baked in, so `QUART_APP` is never required.

Use `uv` for Python commands.
Read [local operations](../../docs/operations.md) for database, model and media
requirements. The evidence application requires PostgreSQL.

Common commands:
- `uv run stk run`
- `uv run stk db upgrade`
- `uv run stk db current`
- `uv run stk db history`
- `uv run ruff check .`
- `uv run ruff format .`
- `uv run python -m stk.checks`
- `uv sync`
- `uv run playwright install chromium`
- `./vendor.sh` (re-download pinned frontend assets listed in `sealbook/static/VERSIONS.txt`)

Agent-operability commands:
- `uv run stk inspect routes --json`
- `uv run stk inspect context --json`
- `uv run stk doctor` (env, secrets, database, migrations, admin, assets, agent login; every problem names its fix)
- `uv run stk verify` (ruff + stk's checks + migration drift)
- `uv run stk verify --watch` (re-runs only the checks a changed file can break; leave it running while you edit)
- `uv run stk verify --json`
- `uv run stk smoke`
- `uv run stk smoke --json`
- `uv run stk report`
- `uv run stk worker` (durable evidence jobs)
- `uv run stk enrich --investigation case-name --limit 100` (local discovery jobs)
- `uv run stk enrich --investigation case-name --limit 100 --after ACQUISITION_UUID` (next acquisition batch)
- `uv run python -m acceptance.check_workflow` (populated local search/review browser check)
- `uv run stk shell` (async REPL: app, live `db` session, all models, top-level await; ptpython gives highlighting and completion when the dev extra is installed)
- `uv run stk shell -c "await count(User)"` (one-shot query, prints the last expression)

Run `uv run stk smoke` for frontend-touching changes. It is the behavioral browser check for this no-build Vue/Vuetify frontend.
