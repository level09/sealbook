"""Stored notifications against a throwaway Postgres database migrated to head."""

import asyncio
import unittest
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import quote

import stk.extensions as ext
from sqlalchemy import select

from sealbook.collaboration import intake
from sealbook.collaboration.models import Assignment, Notification
from sealbook.ledger.models import Payload, Run
from sealbook.ledger.write import principal_for_user, record_acquisition
from sealbook.monitor.stream import events
from tests.collab_case import POSTGRES, CollabCase


@unittest.skipUnless(POSTGRES, "needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class NotificationTest(CollabCase):
    async def send(self, sender, to, reason="Please check"):
        r = await sender.post(
            "/api/collaboration/requests",
            headers=sender.csrf,
            json={
                "item": {
                    "statement": str(self.proposal.id),
                    "reviewer": self.ids[to],
                    "reason": reason,
                }
            },
        )
        self.assertEqual(r.status_code, 201, await r.get_data())
        return (await r.get_json())["id"]

    async def inbox(self, client, query=""):
        return await (await client.get(f"/api/notifications{query}")).get_json()

    async def test_a_request_is_stored_counted_and_marked_read(self):
        alice, bob = await self.client("alice"), await self.client("bob")
        rid = await self.send(alice, "bob")
        mine = await self.inbox(bob)
        [n] = mine["items"]
        self.assertEqual(
            (n["kind"], n["to"]), ("assigned", f"/inbox?item=request:{rid}")
        )
        self.assertFalse(n["isSeen"])
        # Named by the claim, never by an id.
        self.assertRegex(n["subtitle"], r"^[A-Z][\w' ]+ claim( in .+)?$")
        self.assertEqual(mine["unread"], 1)
        counts = await (await bob.get("/api/nav/counts")).get_json()
        self.assertEqual(counts["notifications"], 1)
        self.assertEqual((await self.inbox(alice))["items"], [])

        # Someone else's id is ignored; the owner's marks it read.
        r = await alice.post(
            "/api/notifications/read",
            headers=alice.csrf,
            json={"item": {"ids": [n["id"]]}},
        )
        self.assertEqual((await r.get_json())["unread"], 0)
        self.assertEqual((await self.inbox(bob, "?unread=1"))["unread"], 1)
        r = await bob.post(
            "/api/notifications/read", headers=bob.csrf, json={"item": {"all": True}}
        )
        self.assertEqual((await r.get_json())["unread"], 0)
        self.assertEqual((await self.inbox(bob, "?unread=1"))["items"], [])
        self.assertEqual(len((await self.inbox(bob))["items"]), 1)
        bad = await bob.post(
            "/api/notifications/read", headers=bob.csrf, json={"item": {}}
        )
        self.assertEqual(bad.status_code, 400)

    async def test_a_page_is_newest_first_and_continues(self):
        alice, bob = await self.client("alice"), await self.client("bob")
        await self.send(alice, "bob")
        async with ext.async_session_factory() as s, s.begin():
            for i in range(3):
                s.add(
                    Notification(
                        user_id=self.ids["bob"],
                        kind="reply",
                        title=f"t{i}",
                        subtitle="",
                        link="/review",
                    )
                )
        first = await self.inbox(bob, "?limit=2")
        self.assertEqual([i["title"] for i in first["items"]], ["t2", "t1"])
        rest = await self.inbox(bob, f"?limit=5&before={quote(first['next'])}")
        self.assertEqual([i["title"] for i in rest["items"]][0], "t0")
        self.assertEqual((await bob.get("/api/notifications?limit=0")).status_code, 400)

    async def test_equal_timestamps_are_not_skipped_between_pages(self):
        bob = await self.client("bob")
        at = datetime.now(UTC)
        async with ext.async_session_factory() as s, s.begin():
            for i in range(5):
                s.add(
                    Notification(
                        user_id=self.ids["bob"],
                        kind="reply",
                        title=f"t{i}",
                        subtitle="",
                        link="/review",
                        created_at=at,
                    )
                )
        seen, query = [], "?limit=2"
        while True:
            page = await self.inbox(bob, query)
            seen += [i["id"] for i in page["items"]]
            if not page["next"]:
                break
            query = f"?limit=2&before={quote(page['next'])}"
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)

    async def test_read_takes_at_most_500_ids(self):
        bob = await self.client("bob")
        r = await bob.post(
            "/api/notifications/read",
            headers=bob.csrf,
            json={"item": {"ids": [str(uuid.uuid4()) for _ in range(501)]}},
        )
        self.assertEqual(r.status_code, 400)

    async def test_shutdown_cancels_the_monitor_stream_quietly(self):
        class Hub:
            @asynccontextmanager
            async def subscribe(self):
                yield asyncio.Queue()

        async def drain():
            async for _ in events(
                Hub(), {"investigation": None, "time": None}, None, "u", None
            ):
                pass

        task = asyncio.create_task(drain())
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.wait_for(task, 3)

    async def test_replies_and_mentions_reach_only_people_who_can_see_the_item(self):
        alice, bob = await self.client("alice"), await self.client("bob")
        rid = await self.send(alice, "bob")
        url = f"/api/collaboration/requests/{rid}/comments"
        # Eve is a user but not a participant, admin or analyst: never resolved.
        r = await alice.post(
            url, headers=alice.csrf, json={"item": {"text": "cc @Eve Out and @dan"}}
        )
        self.assertEqual(r.status_code, 201)
        async with ext.async_session_factory() as s:
            rows = (await s.execute(select(Notification))).scalars().all()
        by_user = {r.user_id: r.kind for r in rows if r.kind != "assigned"}
        self.assertEqual(
            by_user, {self.ids["bob"]: "reply", self.ids["dan"]: "mention"}
        )
        r = await bob.post(
            url, headers=bob.csrf, json={"item": {"text": "@Alice Hart seen"}}
        )
        self.assertEqual(r.status_code, 201)
        async with ext.async_session_factory() as s:
            kinds = (
                (
                    await s.execute(
                        select(Notification.kind).where(
                            Notification.user_id == self.ids["alice"]
                        )
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual(kinds, ["mention"])
        # A comment notice points at the request; its label still names the claim.
        [reply] = [n for n in (await self.inbox(bob))["items"] if n["kind"] == "reply"]
        self.assertRegex(reply["subtitle"], r" claim( in .+)?$")

    async def test_titles_never_carry_values_so_erasure_has_nothing_to_clear(self):
        alice = await self.client("alice")
        await self.send(alice, "bob", reason="secret reason text")
        r = await alice.post(
            "/api/assignments/bulk",
            headers=alice.csrf,
            json={
                "item": {
                    "investigation": "inv-a",
                    "subjects": [f"statement:{self.proposal.id}"],
                    "assignee": self.ids["bob"],
                }
            },
        )
        self.assertEqual(r.status_code, 201)

        # Really erase the person the proposal is about, in a thread: the command
        # runs its own event loop.
        from sealbook.people.cli import erase_cmd

        await ext.engine.dispose()
        out = await asyncio.to_thread(
            self.app.test_cli_runner().invoke,
            erase_cmd,
            [
                self.proposal.subject,
                "--reason",
                "family request",
                "--by",
                "alice@example.com",
            ],
        )
        self.assertEqual(out.exit_code, 0, out.output)
        await ext.engine.dispose()

        async with ext.async_session_factory() as s:
            payload = await s.get(Payload, self.proposal.commitment)
            self.assertIsNone(payload)  # the value is gone
            rows = (await s.execute(select(Notification))).scalars().all()
            assigned = (await s.execute(select(Assignment))).scalars().all()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(assigned), 1)
        for n in rows:
            text = " ".join([n.title, n.subtitle, n.link, n.subject or ""])
            for value in ("synthetic", "secret reason text"):
                self.assertNotIn(value, text)
        self.assertIn(f"statement:{self.proposal.id}", {n.subject for n in rows})

    async def test_intake_done_fires_once_when_the_last_run_of_a_batch_ends(self):
        async with ext.async_session_factory() as s, s.begin():
            who = await principal_for_user(s, self.ids["alice"], "Alice Hart")
            runs = []
            for sha in self.blobs["inv-a"]:
                acq = await record_acquisition(
                    s,
                    blob_sha256=sha,
                    origin="device:batch",
                    collector_id=who.id,
                    at=datetime.now(UTC),
                    context={"investigation": "inv-a", "batch": "b1"},
                )
                run = Run(
                    principal_id=who.id,
                    tool="extract",
                    inputs={"acquisition": str(acq.id)},
                )
                s.add(run)
                runs.append(run)
            await s.flush()
            ids = [r.id for r in runs]
            runs[0].status = "done"
        await intake.finished(ext.async_session_factory, ids[0])
        async with ext.async_session_factory() as s:
            self.assertEqual((await s.execute(select(Notification))).all(), [])
        async with ext.async_session_factory() as s, s.begin():
            (await s.get(Run, ids[1])).status = "failed"
        for run_id in ids:
            await intake.finished(ext.async_session_factory, run_id)
        async with ext.async_session_factory() as s:
            [n] = (await s.execute(select(Notification))).scalars().all()
        self.assertEqual(
            (n.user_id, n.kind, n.subtitle),
            (self.ids["alice"], "intake_done", "2 runs done, 1 failed"),
        )


if __name__ == "__main__":
    unittest.main()
