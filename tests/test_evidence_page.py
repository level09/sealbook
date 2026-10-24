"""Evidence page read APIs: sensitive sources masked until the logged reveal, other
sources shown with one logged view per session, review rights by the review flow's
rule, frames only for their own source, and erasure hides it all."""

import asyncio
import unittest
import uuid

from sqlalchemy import select

import stk.extensions as ext
from stk import blobs
from stk.discovery import evidence
from stk.discovery.models import Segment
from stk.ledger.contracts import Prop, StatementIn
from stk.ledger.models import Payload, Run, Statement
from stk.ledger.write import append
from stk.settings import Config
from tests import test_review


class MomentsTest(unittest.TestCase):
    def test_sound_offset_places_both_sides(self):
        value = {"sound": {"offset_ms": 1500}}
        self.assertEqual(evidence.moments(value, True), ([0, 0], [1500, 1500]))
        self.assertEqual(evidence.moments(value, False), ([1500, 1500], [0, 0]))
        value = {"picture": {"query_ms": [0, 2000], "candidate_ms": [18000, 20000]}}
        self.assertEqual(evidence.moments(value, False), ([18000, 20000], [0, 2000]))


class EvidencePageTest(unittest.IsolatedAsyncioTestCase):
    setUp = test_review.ReviewTest.setUp
    asyncSetUp = test_review.ReviewTest.asyncSetUp
    asyncTearDown = test_review.ReviewTest.asyncTearDown
    client = test_review.ReviewTest.client
    propose = test_review.ReviewTest.propose

    async def reveal(self, client, sha):
        r = await client.post(
            f"/api/evidence/{sha}/reveal", headers=client.csrf, json={"item": {}}
        )
        self.assertEqual(r.status_code, 200)

    async def test_values_stay_masked_until_the_logged_reveal(self):
        subject = f"person:{uuid.uuid4()}"
        claim = await self.propose(subject, "يوسف")
        client = await self.client()
        page = await client.get(f"/evidence/{self.sha}")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("يوسف", await page.get_data(as_text=True))
        item = await (await client.get(f"/api/evidence/{self.sha}/item")).get_json()
        row = item["claims"][0]
        self.assertEqual(
            (row["id"], row["display"], row["masked"]), (str(claim.id), None, True)
        )
        self.assertEqual(row["span_blob"], self.sha)  # where Show in source counts
        self.assertFalse(item["revealed"])
        self.assertTrue(row["can_review"])
        self.assertEqual(
            (await client.get(f"/api/evidence/{self.sha}/read")).status_code, 403
        )
        await self.reveal(client, self.sha)
        read = await (await client.get(f"/api/evidence/{self.sha}/read")).get_json()
        self.assertEqual(read["values"][str(claim.id)], "يوسف")
        self.assertEqual(read["text"], test_review.TEXT)
        item = await (await client.get(f"/api/evidence/{self.sha}/item")).get_json()
        self.assertTrue(item["revealed"])
        self.assertIsNone(item["claims"][0]["display"])  # the item API never unmasks
        self.assertEqual(
            [c["display"] for c in item["custody"] if c["prop"] == "access"], ["reveal"]
        )

    async def test_review_rights_and_private_threads_follow_the_review_flow(self):
        from stk.user.models import User

        claim = await self.propose(f"person:{uuid.uuid4()}", "محمد")
        async with ext.async_session_factory() as s, s.begin():
            other = User(
                email="other@example.com", name="other", password="x", active=True
            )
            s.add(other)
            await s.flush()
            await append(
                s,
                StatementIn(
                    subject=f"statement:{claim.id}",
                    prop=Prop.REVIEW_REQUEST,
                    value={"reviewer": other.id, "reason": "check row 1"},
                    author_id=self.viewer.id,
                ),
            )
        client = await self.client()
        item = await (await client.get(f"/api/evidence/{self.sha}/item")).get_json()
        self.assertFalse(item["claims"][0]["can_review"])
        [thread] = item["threads"]
        self.assertEqual(thread["about"], str(claim.id))
        self.assertFalse(thread["visible"])
        self.assertIsNone(thread["reason"])

    async def test_frames_only_for_their_source_after_reveal(self):
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
            b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01"
            b"\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        async with ext.async_session_factory() as s, s.begin():
            frame = await blobs.put(s, png, "image/png", Config.BLOB_DIR)
            run = Run(principal_id=self.agent.id, tool="media_index", inputs={})
            s.add(run)
            await s.flush()
            segment = Segment(
                source_sha256=self.sha,
                kind="frame",
                ordinal=0,
                location={"ms": [0, 1]},
                artifact_sha256=frame.sha256,
                run_id=run.id,
            )
            s.add(segment)
        client = await self.client()
        path = f"/api/evidence/{self.sha}/segments/{segment.id}/image"
        self.assertEqual((await client.get(path)).status_code, 403)
        await self.reveal(client, self.sha)
        r = await client.get(path)
        self.assertEqual((r.status_code, r.mimetype), (200, "image/png"))
        self.assertIn("sandbox", r.headers["Content-Security-Policy"])
        wrong = f"/api/evidence/{frame.sha256}/segments/{segment.id}/image"
        self.assertEqual((await client.get(wrong)).status_code, 404)

    async def test_bad_side_by_side_and_erased_source_are_refused(self):
        from stk.people.data import erase

        subject = f"person:{uuid.uuid4()}"
        await self.propose(subject, "أحمد")
        client = await self.client()
        self.assertEqual(
            (await client.get(f"/evidence/{self.sha}?with=nope")).status_code, 400
        )
        self.assertEqual((await client.get(f"/evidence/{'0' * 64}")).status_code, 404)
        await self.reveal(client, self.sha)
        async with ext.async_session_factory() as s, s.begin():
            await erase(s, subject, self.viewer.id, "test erasure")
        for path in (
            f"/evidence/{self.sha}",
            f"/api/evidence/{self.sha}/item",
            f"/api/evidence/{self.sha}/read",
        ):
            self.assertEqual((await client.get(path)).status_code, 404, path)

    async def test_ordinary_source_shows_at_once_and_logs_one_view_per_session(self):
        from datetime import UTC, datetime

        from stk.ledger.write import record_acquisition

        async with ext.async_session_factory() as s, s.begin():
            sha = (
                await blobs.put(s, b"public bulletin", "text/plain", Config.BLOB_DIR)
            ).sha256
            await record_acquisition(
                s,
                blob_sha256=sha,
                origin="url:https://example.org/bulletin",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={"investigation": "case-r"},
                sensitivity="ordinary",
                doc_type="report",
            )

        async def views():
            async with ext.async_session_factory() as s:
                rows = await s.execute(
                    select(Payload.value)
                    .join(Statement, Statement.commitment == Payload.commitment)
                    .where(
                        Statement.subject == f"blob:{sha}",
                        Statement.prop == Prop.ACCESS,
                    )
                )
                return [v["action"] for v in rows.scalars()]

        client = await self.client()
        item = await (await client.get(f"/api/evidence/{sha}/item")).get_json()
        self.assertTrue(item["revealed"])
        self.assertEqual(await views(), [])  # opening the page logs nothing yet
        # content and text of a first open arrive together: still one view
        first = await asyncio.gather(
            client.get(f"/api/evidence/{sha}/content"),
            client.get(f"/api/evidence/{sha}/read"),
            client.get(f"/api/evidence/{sha}/content"),
        )
        self.assertEqual([r.status_code for r in first], [200, 200, 200])
        read = await (await client.get(f"/api/evidence/{sha}/read")).get_json()
        self.assertEqual(read["text"], "public bulletin")
        self.assertEqual(await views(), ["view"])
        other = await self.client()
        self.assertEqual(
            (await other.get(f"/api/evidence/{sha}/content")).status_code, 200
        )
        self.assertEqual(await views(), ["view", "view"])  # a new session is a new view

    async def test_sensitive_content_waits_for_the_reveal(self):
        client = await self.client()
        path = f"/api/evidence/{self.sha}/content"
        self.assertEqual((await client.get(path)).status_code, 403)
        await self.reveal(client, self.sha)
        self.assertEqual((await client.get(path)).status_code, 200)
