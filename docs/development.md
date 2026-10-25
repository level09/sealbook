# Development conventions

Sealbook runs on stk, with Quart, async SQLAlchemy, Vue Options API and Vuetify.
The evidence application requires PostgreSQL; SQLite supports only isolated
framework tests. Start with [architecture.md](architecture.md), [operations.md](operations.md)
and [lessons.md](lessons.md). The conventions below are inherited from stk.

stk is a dependency (`stkit>=17.0.2,<18`), not a fork. Sealbook's code is the
`sealbook/` package: `sealbook/app.py` composes `Stk(__name__, config=Config,
extensions=[...])` and `sealbook/settings.py` holds `class Config(stk.Config)`.
Paths under `stk/` below name files in the installed stk package; never edit them.
Each module in `sealbook/` with routes or commands is an extension whose
`init_app(app)` registers its blueprints and commands and appends its rail entry to
`app.navigation`. The `extensions` list in `sealbook/app.py` puts rail modules
first, in rail order. Templates and static files resolve from `sealbook/templates`
and `sealbook/static` first, then stk's; `stk/<name>` reaches an original.

## Commands

Everything runs through one command, `stk`, grouped by purpose in `stk --help`.
It finds the app from `[tool.stk] app` in `pyproject.toml`, so `QUART_APP` is never needed. `quart <command>`
still works for anything registered on `app.cli`.

```bash
./setup.sh                        # First-time setup (venv, deps, .env)
uv sync                           # Install, dev tools included
uv run stk --help                 # Command map, grouped by purpose
uv run stk run                    # Dev server at localhost:5000
uv run stk run --port 5001        # Alt port (macOS 5000 conflict)
uv run stk db upgrade             # stk's migrations, then Sealbook's
uv run stk install                # Create admin user
uv run ruff check --fix . && uv run ruff format .  # Lint + format
uv run python -m stk.checks       # Sanity checks against the app (not pytest)
docker compose up --build         # Full stack (Redis, PostgreSQL, Nginx)
```

Drop the `uv run` prefix by activating the venv once per shell:
`source .venv/bin/activate`, then `stk run`, `stk shell`, `stk verify`.

### Agent Operability

Prefer these over guessing from files. Structured agent context lives in `.stk/context/` (architecture, commands, verification, frontend).

```bash
uv run stk inspect routes --json   # Route map with auth and source info
uv run stk inspect context --json  # Routes + models in one contract
uv run stk doctor                  # Environment/app state, each line with its fix
uv run stk verify --watch          # Re-run affected checks on every save
uv run stk verify --json           # Lint, sanity, migration checks (exit 0/1)
uv run stk smoke --json            # Real-browser behavioral check (Playwright)
uv run stk report                 # Static project review artifact
uv run stk shell                  # Async REPL: app, live db session, models, top-level await (ptpython)
uv run stk shell -c "await count(User)"  # One-shot query, prints last expression
uv run stk new <module>            # Scaffold + migrate, ends at a URL you can open
uv run python -m unittest discover -s tests
```

### Commit hooks

`setup.sh` sets `git config core.hooksPath .githooks`. `commit-msg` and `pre-push`
refuse AI attribution lines (`Co-Authored-By` naming an AI, "Generated with Claude",
session links). Run that command once in an existing clone.

### Worktrees

Parallel work happens in git worktrees under `../sealbook-wt/`. A worktree has no
`.env` and no `instance/blobs`: link the main `.env` (ignored by git) and start the
server with `BLOB_DIR` set to the main checkout's `instance/blobs`, or search and
the sense check fail on missing source bytes.

### Reading the screens (`acceptance/check_sense.py`)

`stk smoke` and `acceptance.check_workflow` prove pages render and flows work. They
do not read the words. Run the sense check after any UI or display text change,
alongside `check_workflow`, against a server on a copy of the demo database:

```bash
createdb sealbook_sense_check && pg_dump sealbook | psql -q sealbook_sense_check
SQLALCHEMY_DATABASE_URI=postgresql+asyncpg:///sealbook_sense_check \
  STK_ENABLE_AGENT_LOGIN=1 uv run stk run --port 5023
uv run python -m acceptance.check_sense --base http://localhost:5023
dropdb sealbook_sense_check   # after stopping that server
```

It logs in as `admin@example.com`, opens every analyst page of `case-demo-02` at
desktop and phone width (Desk, Inbox, Discover, Review, Sources, Add evidence, People and five
person pages, Places and three place pages, incidents, Stories, Watching, Packages, Guide,
the case map, one evidence page per source type with its Read, Copies and Relations
tabs and sensitive items shown, and Arabic, English and hash searches), and runs
text rules over what is visible: raw hex ids and uuids, snake_case tokens, storage
words (`<prop> values`, `<tool> run`, model ids, built from `Prop`, the worker's
`TOOLS` and the seeds), raw scores, a row repeated 3+ times in one list, "1
<plural>", stray separators, empty labels, and `undefined`, `null`, `NaN`,
`[object Object]`. It also checks that each visited person has one count of facts
and documents on the People list, the person page and in search. It prints page, rule, snippet and count, writes
`instance/check_sense.json` with each page's text, and exits 1 on any finding.

Fix the screen, not the check. `acceptance/sense_allow.json` holds only content
that is meant to be there (a digest shown as a hash statement's value), each entry
with a rule, page and match pattern and a reason. Rule changes come with a case in
`tests/test_check_sense.py`, which tests the rules on plain text without a browser.

### Database Migrations (Alembic)

```bash
uv run stk db upgrade [revision]              # Apply migrations (default: heads)
uv run stk db downgrade <revision>            # Rollback (e.g. -1 for one step)
uv run stk db revision -m "description"       # Autogenerate in Sealbook's lineage
uv run stk db revision -m "desc" --empty      # Empty revision for manual SQL
uv run stk db current                         # Show current revision
uv run stk db history                         # Show migration history
uv run stk db stamp heads                     # Adopt Alembic on existing DB
uv run stk db check                           # Fail if models drifted from migrations
```

Two lineages share one version table. stk's revisions ship in the package (`stk_0001`, branch `stk`); Sealbook's live in `sealbook/migrations/` (branch `sealbook`) and each depends on stk's head. `sealbook_0001` is the whole schema as of the move to stk 17, with the extensions, append-only triggers and reference rows that autogenerate cannot see; a database built by the stk 16 fork's 28 revisions is not upgraded. Autogenerate leaves out column-level `CheckConstraint`s and writes custom column types by their module path: check both in a new revision. SQLite uses batch mode automatically for ALTER TABLE support.

SQLite migration traps, each hit while building an app on stk:
- DDL is not transactional there: a failed upgrade leaves the tables it already created. Fix the revision, drop only those new empty tables, then upgrade again.
- A new NOT NULL column on a table with rows needs `server_default` in the migration and the model, or the upgrade fails.
- Constraints are named by the convention on `Base.metadata` (`stk/extensions.py`); keep it, because batch mode cannot add an unnamed foreign key.

## Architecture

### Async SQLAlchemy (not flask-sqlalchemy)

Engine and session factory live in `stk/extensions.py` as module-level globals (`ext.engine`, `ext.async_session_factory`). No `db` object. Models inherit from `Base` (plain `DeclarativeBase`), not `db.Model`.

Request-scoped sessions via `g.db_session`, created in `before_request`, closed in `after_request` (see `stk/app.py`).

```python
# In request handlers: use g.db_session
from quart import g
from sqlalchemy import select
result = await g.db_session.execute(select(User).where(User.active == True))
users = result.scalars().all()

# In CLI commands: use ext.async_session_factory directly
import stk.extensions as ext
async with ext.async_session_factory() as session:
    ...
```

All relationships must use `lazy="selectin"` for async compatibility. Selectin loads only on query: reading a relationship on a row you just `add()`ed (or got back from the identity map without it loaded) raises `MissingGreenlet`. Load it with `await session.get(...)` or a query instead.

### CLI Commands

Sync click commands wrapping `asyncio.run()` (`stk.cli.base.run_async`). Quart CLI doesn't support async click commands. stk's own commands live in its `stk/cli/` package. Sealbook's live beside their module (`sealbook/runs/cli.py` for `worker`, `sealbook/discovery/cli.py`, `sealbook/places/cli.py`, `sealbook/monitor/seed.py` for `demo-seed`, and so on), and each extension's `init_app` adds them with `app.cli.add_command`. `stk --help` lists them under Other.

### Blueprints

- `stk/public/` - unauthenticated routes, OAuth callbacks (Google, GitHub)
- `stk/user/` - auth, login, registration, OAuth, WebAuthn, 2FA, session management
- `stk/portal/` - protected dashboard (blueprint-level `@auth_required`)
- `stk/websocket.py` - WebSocket blueprint (releases DB session early for long-lived connections)

### Auth (quart-security)

`SQLAlchemyUserDatastore` with session factory callable (`lambda: g.db_session`). Key decorators: `@auth_required("session")`, `@roles_required('admin')`.

**Security policy:**
- Public password registration is disabled by default.
- TOTP seeds are encrypted at rest. Use `stk protect-mfa` on upgrade and preserve the MFA keyring.
- All mutations require CSRF; Axios uses the shared layout token. Third-party webhooks without a session opt out per view with `@csrf_exempt` (`stk.security`) placed under the route decorator, and must authenticate the caller themselves (e.g. HMAC signature).
- OAuth uses single-use state and PKCE, requires verified email, and enforces local MFA. It does not auto-link matching emails.
- WebSockets require an allowed origin, recheck session validity, bound resources, and require an explicit broadcast recipient.
- See `SECURITY.md` for production settings and upgrade requirements.

**Features enabled:**
- Session auth with tracking (IP, device, browser via `Session` model)
- 2FA via TOTP authenticator (`SECURITY_TWO_FACTOR = True`)
- WebAuthn as first or multi-factor (`SECURITY_WEBAUTHN = True`)
- OAuth (Google, GitHub) via AuthLib `AsyncOAuth2Client`
- Password hashing: Argon2id, min 12 chars; legacy hashes upgrade on login
- Account lockout: `failed_login_count` + `locked_until` on User model
- Recovery codes (3 codes, hashed at rest, displayed only once at generation)
- Session freshness: 60-minute window, enforced on 2FA/recovery/passkey management routes (stale session gets 401)
- Logout is POST-only (`layoutMixin.logout()` submits the form; plain links 405)

**Signal handlers** (all async, in `stk/user/views.py`):
- `@user_authenticated.connect` - creates session record, tracks IP changes
- `@user_logged_out.connect` - deactivates session
- `@password_changed.connect` - logs change, marks password as user-set
- `@tf_profile_changed.connect` - logs 2FA modifications

**Rate limiting**: atomic SQL windows in `stk/utils/ratelimit.py`, shared across workers and auth endpoints. Defaults are 10 attempts and 120 auth requests per IP per minute. OAuth requests are included; logout remains available.

### Models (`stk/user/models.py`)

- **User** - UserMixin. Auth fields, login tracking, lockout, 2FA, WebAuthn handle. Methods: `from_dict()`, `to_dict()`, `random_password()`, `logout_other_sessions()`, `get_active_sessions()`.
- **Role** - RoleMixin. Many-to-many with User via `roles_users`.
- **WebAuthn** - credential storage, FK to User via `fs_webauthn_user_handle`.
- **OAuth** - provider accounts linked to users. Unique on `(provider, provider_user_id)`.
- **Activity** - audit log. `register()` stores events without broadcasting.
- **Session** - tracks active sessions with IP, device meta, expiry.

### Background Tasks

No Celery. `stk/tasks.py` provides:
- `run_in_background(coro)` - fire-and-forget with exception logging
- `run_with_session(coro_factory)` - provides fresh DB session to coroutine
- `cleanup_expired_sessions()` - deactivates expired, deletes 30+ day old records

### Frontend

Vue 3 + Vuetify loaded from static files (no build step). **Options API** (`data()`, `methods`, `mounted()`), NOT Composition API. Versions are pinned in `sealbook/static/VERSIONS.txt` and refreshed with `./vendor.sh`; see `.stk/context/frontend.md` before bumping Vuetify.

- **Delimiters:** `${}` via `config.delimiters` (avoids Jinja `{{}}` conflicts)
- **Layout mixin:** Every app uses `mixins: [layoutMixin]` for the shell (case switcher, search, Cmd-K palette, rail, hint bar, theme), WebSocket and notifications; its contract is in `.stk/context/frontend.md`
- **Component registration:** Call `registerStkComponents(app)` before `.mount('#app')`
- **Vuetify init:** `createVuetify(config.vuetifyConfig)` (config is in `static/js/config.js`)
- **Icons:** Tabler Icons (`ti ti-pencil`, `ti ti-plus`, etc.), NOT Material Design Icons
- **Server data:** Pass via `<script type="application/json" id="...">{{ data|tojson|safe }}</script>` placed in `{% block js %}`, never in `{% block content %}`: Vue strips `<script>` tags inside the `#app` mount element, so `getElementById(...)` returns null.
- **Navigation:** Rail entries are `app.navigation.append({...})` in each extension's `init_app` (`title`, `icon`, `to`, `match`, optional `badge` naming a key of `/api/nav/counts`, `role: 'admin'` for access control). The layout reads them through `rail_navigation()`, without stk's Dashboard and Account sections
- **JSON responses:** List endpoints use `orjson` via `import orjson as json` and `Response(json.dumps(data), content_type="application/json")`
- **Request body:** Frontend sends mutations as `{item: {...}}`, extract with `json_data.get("item", {})`
- **Template globals:** Vue template expressions cannot see page globals (a `const labels = {...}` in a script is undefined inside `${...}` and directives). Expose shared helpers with `app.config.globalProperties.labels = labels` before mount. The production Vue build fails such lookups silently (the cell renders nothing, columns shift), so `stk smoke` cannot catch it; check the rendered table.

## Key Gotchas

- `User.from_dict()`, `Activity.register()`, `Session.create_session()` are all async.
- Signal handlers (`@user_authenticated.connect` etc.) are async.
- Pagination is manual: `offset().limit()` + `select(func.count())`.
- WebSocket connections release their DB session early to avoid pool starvation.
- Session backend: Redis if `REDIS_URL` is set, otherwise cookie-based.
- `DISABLE_MULTIPLE_SESSIONS` config controls single-session enforcement.
- Renaming the product: set `APP_NAME` / `APP_TAGLINE` / `APP_DESCRIPTION` and replace `sealbook/templates/_brand.html` and the favicon. Never edit names across templates.
- Brand files (app lockups, favicons, docs logos, the site header mark) come from `docs/logo/build.py`. Change the seal there and rerun it; do not hand-edit the generated SVGs. The app shows `sealbook-dark.svg` under `body.dark-mode` instead of filtering the light file.
- Tests: Config subclasses inherit everything `.env` sets (`load_dotenv` runs at import). Pin every env-driven switch a test depends on in the test config (e.g. a provider key that changes request auth), or a developer's `.env` silently changes the code path.
- Test configs are `class TestConfig(stk.TestingConfig, Config)` and build their app with `sealbook.app.create_app(TestConfig)`. Postgres tests migrate a throwaway database with `build_alembic_config(sealbook_app, url, quiet=True)` to `heads`: two lineages, so `head` is ambiguous.
