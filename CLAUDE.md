# Sealbook

Read [docs/README.md](docs/README.md), then the current state, decisions, and lessons
linked there. Update those documents when behavior or a known limitation changes.
Keep pending work distinct from implemented behavior. Preserve the existing local
changes from this chat; they are not committed.

Use `uv` for Python, `rg` for search, and `bat -P` for file reads. Match the async
Quart, SQLAlchemy, Vue Options API and no-build frontend conventions in
[docs/development.md](docs/development.md). Run ruff after Python edits.

No production or partner evidence. Use synthetic fixtures.
Name a remote server or database environment and get confirmation before access.
Get explicit approval for destructive commands. Never commit secrets or fixture
tokens. Confirm the target branch before a push. Stage only changed files.
Do not use AI terms in commits or PRs, or `codex` in branch names.

Keep changes tied to the request. Prefer the smallest complete system. Use plain,
short prose with no em dash or en dash. No automatic acceptance of semantic or
perceptual matches. Retain human review, provenance, access logging and erasure
boundaries when adding projections or APIs.
