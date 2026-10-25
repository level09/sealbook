"""Work assignment against a throwaway Postgres database migrated to head."""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

import stk.extensions as ext
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from sealbook.collaboration.models import Assignment, AssignmentEvent, Notification
from tests.collab_case import POSTGRES, CollabCase


@unittest.skipUnless(POSTGRES, "needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class AssignmentTest(CollabCase):
    async def bulk(self, client, subjects, to="bob", inv="inv-a", **extra):
        r = await client.post(
            "/api/assignments/bulk",
            headers=client.csrf,
            json={
                "item": {
                    "investigation": inv,
                    "subjects": subjects,
                    "assignee": self.ids[to],
                    **extra,
                }
            },
        )
        return r.status_code, await r.get_json()

    def blob_subjects(self, inv="inv-a"):
        return [f"blob:{s}" for s in self.blobs[inv]]

    async def test_bulk_assigns_with_one_notification_per_assignee(self):
        alice = await self.client("alice")
        subjects = [*self.blob_subjects(), f"statement:{self.proposal.id}"]
        status, body = await self.bulk(alice, subjects, due="2030-01-01")
        self.assertEqual((status, body["assigned"]), (201, 3))
        async with ext.async_session_factory() as s:
            notes = (await s.execute(select(Notification))).scalars().all()
        self.assertEqual(
            [(n.user_id, n.kind) for n in notes], [(self.ids["bob"], "assigned")]
        )
        self.assertEqual(notes[0].subtitle, "3 items")
        bob = await self.client("bob")
        listed = await (
            await bob.get(
                f"/api/assignments?investigation=inv-a&assignee={self.ids['bob']}"
            )
        ).get_json()
        self.assertEqual(listed["total"], 3)
        self.assertEqual({i["status"] for i in listed["items"]}, {"open"})

    async def test_due_today_is_allowed_and_the_past_is_not(self):
        alice = await self.client("alice")
        today = datetime.now(UTC).date()
        past = (today - timedelta(days=2)).isoformat()
        self.assertEqual(
            (await self.bulk(alice, self.blob_subjects(), due=past))[0], 400
        )
        due = today.isoformat()
        self.assertEqual(
            (await self.bulk(alice, self.blob_subjects(), due=due))[0], 201
        )

    async def test_due_today_west_of_utc_is_allowed(self):
        """At 03:00 UTC it is still yesterday evening in New York."""
        alice = await self.client("alice")

        class At(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 25, 3, tzinfo=UTC)

        with mock.patch("sealbook.collaboration.views.datetime", At):
            ok = await self.bulk(alice, self.blob_subjects(), due="2026-10-24")
            late = await self.bulk(alice, self.blob_subjects(), due="2026-10-23")
        self.assertEqual((ok[0], late[0]), (201, 400))

    async def test_validation(self):
        alice = await self.client("alice")
        self.assertEqual((await self.bulk(alice, ["blob:" + "0" * 64]))[0], 400)
        self.assertEqual((await self.bulk(alice, ["nonsense"]))[0], 400)
        self.assertEqual((await self.bulk(alice, []))[0], 400)
        self.assertEqual(
            (await self.bulk(alice, self.blob_subjects(), priority="x"))[0], 400
        )
        self.assertEqual(
            (await self.bulk(alice, self.blob_subjects(), due="soon"))[0], 400
        )
        # Eve is not an analyst, so she cannot take work.
        self.assertEqual(
            (await self.bulk(alice, self.blob_subjects(), to="outsider"))[0], 400
        )
        # A real blob from another investigation is refused, not assigned.
        self.assertEqual((await self.bulk(alice, self.blob_subjects("inv-b")))[0], 403)
        distinct = [f"blob:{i:064x}" for i in range(501)]
        status, body = await self.bulk(alice, distinct)
        self.assertEqual(status, 400)
        self.assertIn("500", body["error"])
        async with ext.async_session_factory() as s:
            self.assertEqual(len((await s.execute(select(AssignmentEvent))).all()), 0)

    async def test_reassign_logs_an_event_and_the_log_cannot_be_rewritten(self):
        alice, bob = await self.client("alice"), await self.client("bob")
        await self.bulk(alice, self.blob_subjects()[:1], to="bob")
        listed = await (await alice.get("/api/assignments")).get_json()
        aid = listed["items"][0]["id"]
        r = await bob.patch(
            f"/api/assignments/{aid}",
            headers=bob.csrf,
            json={"item": {"assignee": self.ids["dan"]}},
        )
        self.assertEqual((await r.get_json())["assignee"], self.ids["dan"])
        r = await bob.patch(
            f"/api/assignments/{aid}",
            headers=bob.csrf,
            json={"item": {"status": "done"}},
        )
        self.assertEqual((await r.get_json())["status"], "done")
        bad = await bob.patch(
            f"/api/assignments/{aid}", headers=bob.csrf, json={"item": {"status": "x"}}
        )
        self.assertEqual(bad.status_code, 400)
        async with ext.async_session_factory() as s:
            events = (
                (await s.execute(select(AssignmentEvent).order_by(AssignmentEvent.id)))
                .scalars()
                .all()
            )
            self.assertEqual(
                [(e.action, e.actor_id) for e in events],
                [
                    ("assigned", self.ids["alice"]),
                    ("reassigned", self.ids["bob"]),
                    ("status", self.ids["bob"]),
                ],
            )
            self.assertEqual(events[1].detail["from"], self.ids["bob"])
        with self.assertRaises(DBAPIError):
            async with ext.async_session_factory() as s, s.begin():
                await s.execute(text("delete from assignment_event"))

    async def test_assigning_an_open_subject_again_reassigns_instead_of_duplicating(
        self,
    ):
        alice = await self.client("alice")
        await self.bulk(alice, self.blob_subjects()[:1], to="bob")
        status, body = await self.bulk(alice, self.blob_subjects()[:1], to="dan")
        self.assertEqual((status, body["assigned"]), (201, 1))
        listed = await (await alice.get("/api/assignments?status=open")).get_json()
        self.assertEqual([i["assignee"] for i in listed["items"]], [self.ids["dan"]])
        status, body = await self.bulk(alice, self.blob_subjects()[:1], to="dan")
        self.assertEqual(body["unchanged"], 1)

    async def test_patch_validates_priority_on_its_own(self):
        alice = await self.client("alice")
        await self.bulk(alice, self.blob_subjects()[:1])
        aid = (await (await alice.get("/api/assignments")).get_json())["items"][0]["id"]
        url = f"/api/assignments/{aid}"
        r = await alice.patch(
            url, headers=alice.csrf, json={"item": {"priority": "urgent"}}
        )
        self.assertEqual(r.status_code, 400)
        r = await alice.patch(
            url, headers=alice.csrf, json={"item": {"priority": "high"}}
        )
        self.assertEqual((await r.get_json())["priority"], "high")

    async def test_concurrent_bulk_calls_never_500_or_duplicate(self):
        clients = [await self.client(w) for w in ("alice", "bob", "dan", "alice")]
        results = await asyncio.gather(
            *(
                self.bulk(c, self.blob_subjects(), to=t)
                for c, t in zip(clients, ("bob", "dan", "alice", "dan"), strict=True)
            )
        )
        self.assertTrue({s for s, _ in results} <= {201, 400}, results)
        async with ext.async_session_factory() as s:
            rows = (await s.execute(select(Assignment))).scalars().all()
        self.assertEqual(len(rows), 2)

    async def test_workload_counts_open_and_overdue(self):
        alice = await self.client("alice")
        yesterday = (datetime.now(UTC) - timedelta(days=2)).date()
        await self.bulk(alice, self.blob_subjects()[:1], to="bob", due="2999-01-01")
        async with ext.async_session_factory() as s, s.begin():
            await s.execute(
                update(Assignment)
                .where(Assignment.subject == self.blob_subjects()[0])
                .values(due=yesterday)
            )
        await self.bulk(alice, self.blob_subjects()[1:], to="bob", due="2999-01-01")
        load = await (
            await alice.get("/api/assignments/workload?investigation=inv-a")
        ).get_json()
        self.assertEqual(
            load["items"], [{"assignee": self.ids["bob"], "open": 2, "overdue": 1}]
        )
        picker = await (await alice.get("/api/analysts?investigation=inv-a")).get_json()
        by = {a["id"]: a for a in picker["items"]}
        self.assertEqual(
            (by[self.ids["bob"]]["open"], by[self.ids["bob"]]["overdue"]), (2, 1)
        )
        self.assertNotIn(self.ids["outsider"], by)

    async def test_a_non_analyst_is_refused(self):
        eve = await self.client("outsider")
        for method, path in (
            ("GET", "/api/assignments"),
            ("GET", "/api/assignments/workload"),
            ("GET", "/api/analysts"),
            ("GET", "/api/notifications"),
        ):
            r = await eve.get(path)
            self.assertEqual(r.status_code, 403, (method, path))
        status, _ = await self.bulk(eve, self.blob_subjects())
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
