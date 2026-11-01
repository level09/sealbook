"""Live work state across server processes, over the Postgres channel the run
stream already listens on.

A `work` event says which items changed hands or stage (assigned, updated,
returned, first pass decided, sent for peer review); open pages reload what they
show, so the event carries no content, only subjects. Presence says who has an
evidence page open: a tab announces itself on opening, again each heartbeat and
in answer to a newcomer, and says when it leaves; pages drop a viewer not heard
from for two heartbeats. Nothing is stored."""

import asyncio
import time

import orjson
import stk.extensions as ext
from sqlalchemy import text

from sealbook.monitor.stream import CHANNEL, HEARTBEAT, authorized

# pg_notify takes 8000 bytes; past this many subjects the event says "anything".
MAX_SUBJECTS = 50


async def _send(db, payload: dict):
    await db.execute(
        text("select pg_notify(:c, :p)"),
        {"c": CHANNEL, "p": orjson.dumps(payload).decode()},
    )


async def work(db, subjects, by: int, investigation: str | None = None):
    """Queue a work event in the caller's transaction: Postgres sends it at commit,
    and not at all on rollback."""
    subjects = sorted(set(subjects))
    await _send(
        db,
        {
            "kind": "work",
            "investigation": investigation,
            "subjects": subjects if len(subjects) <= MAX_SUBJECTS else None,
            "by": by,
        },
    )


async def _presence(sha: str, user: dict, state: str):
    async with ext.async_session_factory() as db:
        await _send(db, {"kind": "presence", "sha": sha, "state": state, **user})
        await db.commit()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {orjson.dumps(data).decode()}\n\n"


async def stream(hub, user: dict, sha: str | None, uniquifier: str, token: str | None):
    """Work events for every open page; presence for the evidence page `sha`."""
    try:
        async with hub.subscribe(("work", "presence")) as queue:
            yield "retry: 3000\n\n"
            if sha:
                await _presence(sha, user, "open")
            checked = time.monotonic()
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), HEARTBEAT)
                    except TimeoutError:
                        msg = "ping"
                    if time.monotonic() - checked >= HEARTBEAT:
                        if not await authorized(uniquifier, token):
                            return
                        checked = time.monotonic()
                        if sha:
                            await _presence(sha, user, "here")
                    if msg is None:
                        return
                    if msg == "ping":
                        yield ": ping\n\n"
                    elif msg["kind"] == "work":
                        yield _sse("work", msg)
                    elif sha and msg["sha"] == sha and msg["user"] != user["user"]:
                        if msg["state"] == "open":
                            await _presence(sha, user, "here")
                        yield _sse("presence", msg)
            finally:
                if sha:
                    await _presence(sha, user, "left")
    except asyncio.CancelledError:
        return
