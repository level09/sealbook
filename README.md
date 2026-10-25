<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/logo/dark.svg">
  <img src="docs/logo/light.svg" alt="Sealbook" height="56">
</picture>

# Sealbook

Evidence intelligence for defensible investigations. Investigate with AI, verify with
people, keep every source, and export findings as a signed package anyone can verify
offline.

Built on [stk](https://github.com/level09/stk).

Start with [docs/README.md](docs/README.md) for the current code state, architecture,
operations, decisions, lessons and pending work. The design is in
[docs/design.md](docs/design.md). Sealbook requires PostgreSQL with pgvector and
pg_trgm; it does not run its evidence migrations on SQLite.

```bash
./setup.sh                  # deps + secure .env
uv run stk db upgrade       # stk's migrations, then Sealbook's
uv run stk install          # admin user
uv run stk run              # localhost:5000
uv run stk verify --json    # gate before every merge
uv run stk smoke --json
```

See [local operations](docs/operations.md) before migration or model setup.
No live model quality or production readiness is implied by local test results.
