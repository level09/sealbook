"""Search results for one person for a short while, in process memory only, so a
result list is never written to the database or to disk. Capture takes a candidate
id from here, which keeps "Add as evidence" to pages this person was shown."""

from __future__ import annotations

import secrets
import time

from stk.settings import Config

PER_USER = 600
# ponytail: process memory like pending passkeys; one process only (decisions.md),
# move to a table with expiry when the app runs more than one web process.
_store: dict[int, dict[str, tuple[float, dict]]] = {}


def _live(user_id: int) -> dict[str, tuple[float, dict]]:
    now = time.monotonic()
    mine = {k: v for k, v in _store.get(user_id, {}).items() if v[0] > now}
    _store[user_id] = mine
    return mine


def put(user_id: int, candidate: dict) -> str:
    mine = _live(user_id)
    while len(mine) >= PER_USER:
        mine.pop(next(iter(mine)))
    cid = secrets.token_urlsafe(12)
    mine[cid] = (time.monotonic() + Config.RESEARCH_CANDIDATE_SECONDS, candidate)
    return cid


def get(user_id: int, cid: str) -> dict | None:
    found = _live(user_id).get(cid)
    return found[1] if found else None


def clear(user_id: int | None = None) -> None:
    if user_id is None:
        _store.clear()
    else:
        _store.pop(user_id, None)
