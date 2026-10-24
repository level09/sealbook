"""Live monitor stream. One asyncpg connection per process LISTENs on the `sealbook`
channel and fans event ids out to every open tab; each tab re-reads the event
through its own filter, so it only ever sees rows its filter and session allow."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import asyncpg
import orjson
from quart_security import SecurityState
from sqlalchemy import select
from sqlalchemy.engine import make_url

import stk.extensions as ext
from stk.monitor.data import feed_rows
from stk.user.models import User

CHANNEL = "sealbook"
HEARTBEAT = 15  # seconds; also how often the session is rechecked
QUEUE_SIZE = 256
REPLAY_LIMIT = 500


class Hub:
    """Fan-out of NOTIFY payloads. The listener connection opens with the first
    subscriber and closes with the last, so no connection outlives its tabs."""

    def __init__(self, database_uri: str):
        self.dsn = (
            make_url(database_uri)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        self.queues: set[asyncio.Queue] = set()
        self.conn = None
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(self):
        queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        async with self.lock:
            if self.conn is None:
                self.conn = await asyncpg.connect(self.dsn)
                await self.conn.add_listener(CHANNEL, self._on_notify)
                self.conn.add_termination_listener(self._on_lost)
            self.queues.add(queue)
        try:
            yield queue
        finally:
            async with self.lock:
                self.queues.discard(queue)
                if not self.queues and self.conn is not None:
                    conn, self.conn = self.conn, None
                    await conn.close()

    def _on_notify(self, _conn, _pid, _channel, payload):
        msg = orjson.loads(payload)
        if msg.get("kind") != "run_event":
            return
        for queue in self.queues:
            try:
                queue.put_nowait(msg["id"])
            except asyncio.QueueFull:
                # A slow tab is cut off; it reconnects and replays from Last-Event-ID.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)

    def _on_lost(self, _conn):
        self.conn = None
        for queue in self.queues:
            queue.put_nowait(None)


def sse(row: dict) -> str:
    return f"id: {row['id']}\nevent: run_event\ndata: {orjson.dumps(row).decode()}\n\n"


async def authorized(uniquifier: str, token: str | None) -> bool:
    """The session behind a long-lived stream is still live and its user active."""
    async with ext.async_session_factory() as db:
        state = await db.scalar(
            select(SecurityState.payload).where(
                SecurityState.token == token,
                SecurityState.expires_at > int(time.time()),
            )
        )
        user = await db.scalar(
            select(User.id).where(
                User.fs_uniquifier == uniquifier, User.active.is_(True)
            )
        )
    return bool(user and state and state.get("user_id") == uniquifier)


async def events(
    hub: Hub, f: dict, last_id: int | None, uniquifier: str, token: str | None
):
    """The SSE body. A shutdown cancels it; that ends the stream quietly, since the
    client reconnects with Last-Event-ID."""
    try:
        async for chunk in _events(hub, f, last_id, uniquifier, token):
            yield chunk
    except asyncio.CancelledError:
        return


async def _events(
    hub: Hub, f: dict, last_id: int | None, uniquifier: str, token: str | None
):
    """Replay after `last_id`, then live rows that match `f`."""
    async with hub.subscribe() as queue:
        yield "retry: 3000\n\n"
        # Subscribed before replaying, so an event committed meanwhile arrives twice
        # at most; `replayed` drops the second copy.
        replayed = set()
        if last_id is not None:
            async with ext.async_session_factory() as db:
                for row in await feed_rows(db, f, after=last_id, limit=REPLAY_LIMIT):
                    replayed.add(row["id"])
                    yield sse(row)
        checked = time.monotonic()
        while True:
            try:
                event_id = await asyncio.wait_for(queue.get(), HEARTBEAT)
            except TimeoutError:
                event_id = "ping"
            if time.monotonic() - checked >= HEARTBEAT:
                if not await authorized(uniquifier, token):
                    return
                checked = time.monotonic()
            if event_id is None:
                return
            if event_id == "ping":
                yield ": ping\n\n"
                continue
            if event_id in replayed:
                continue
            async with ext.async_session_factory() as db:
                for row in await feed_rows(db, f, ids=[event_id]):
                    yield sse(row)
