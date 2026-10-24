"""Review workspace read side, the no-passkey refusal on every staging route, and the
passkey setup round trip, against a throwaway Postgres database."""

import unittest
import uuid

import orjson
from sqlalchemy import select

import stk.extensions as ext
import tests.test_review as base
from stk.monitor.data import display
from stk.review.views import safe_next
from stk.user.models import User
from tests.passkey import SoftPasskey


class Pure(unittest.TestCase):
    def test_display_never_prints_a_structure(self):
        same = {"basis": ["statement:1", "statement:2"], "person": "ab12"}
        self.assertEqual(
            display(same, "same_as"),
            "The same person as another mention, based on 2 statements",
        )
        self.assertNotIn("{", display({"blob": "a" * 64, "sound": {}}, "media_match"))
        odd = display({"kind": "x", "ref": f"person:{uuid.uuid4()}", "nested": {}})
        self.assertEqual(odd, "kind: x")

    def test_next_is_a_same_origin_path_only(self):
        self.assertEqual(safe_next("/review?statement=1"), "/review?statement=1")
        for bad in (
            "https://evil.example/x",
            "//evil.example",
            "review",
            "/\\evil",
            None,
        ):
            self.assertIsNone(safe_next(bad), bad)


@unittest.skipUnless(base.POSTGRES, "review needs Postgres")
class ReviewPageTest(unittest.IsolatedAsyncioTestCase):
    setUp = base.ReviewTest.setUp
    asyncSetUp = base.ReviewTest.asyncSetUp
    asyncTearDown = base.ReviewTest.asyncTearDown
    propose = base.ReviewTest.propose
    client = base.ReviewTest.client
    colleague = base.ReviewTest.colleague

    async def ask(self):
        """The analyst asks a colleague to check an agent's name claim."""
        st = await self.propose(f"person:{uuid.uuid4()}", "يوسف")
        reviewer = await self.colleague()
        sender = await self.client()
        r = await sender.post(
            "/api/collaboration/requests",
            headers=sender.csrf,
            json={
                "item": {
                    "statement": str(st.id),
                    "reviewer": reviewer,
                    "reason": "Second entry?",
                }
            },
        )
        self.assertEqual(r.status_code, 201)
        return st, (await r.get_json())["id"], sender

    async def test_queue_reads_as_a_sentence_with_source_requester_and_masking(self):
        st, rid, sender = await self.ask()
        other = await self.client("colleague@example.com")
        r = await other.get("/api/review/queue?box=mine")
        body = await r.get_json()
        self.assertEqual(r.status_code, 200, body)
        self.assertEqual(body["counts"]["mine"], 1)
        item = body["items"][0]
        self.assertEqual(item["id"], str(st.id))
        # A name from a sensitive source stays masked, as on the evidence page.
        self.assertTrue(item["value"]["masked"])
        self.assertIsNone(item["value"]["text"])
        self.assertTrue(item["person"]["masked"])
        self.assertEqual(item["source"]["sha256"], self.sha)
        self.assertTrue(item["where"]["label"].startswith("characters"))
        self.assertEqual(item["proposer"]["kind"], "extractor")
        self.assertEqual(item["proposer"]["name"], "AI extraction")
        self.assertEqual(item["request"]["by"]["name"], "analyst")
        self.assertEqual(item["request"]["to"]["role"], "analyst")
        self.assertTrue(item["request"]["mine"])
        self.assertTrue(item["can_review"])
        raw = orjson.dumps(body).decode()
        self.assertNotIn("يوسف", raw)
        sent = await (await sender.get("/api/review/queue?box=sent")).get_json()
        self.assertEqual([i["id"] for i in sent["items"]], [str(st.id)])
        self.assertFalse(sent["items"][0]["can_review"])
        bad = await sender.get("/api/review/queue?box=everything")
        self.assertEqual(bad.status_code, 400)

    async def test_claim_detail_unmasks_after_the_logged_reveal(self):
        st, rid, _ = await self.ask()
        other = await self.client("colleague@example.com")
        claim = await (await other.get(f"/api/review/claim/{st.id}")).get_json()
        self.assertEqual(
            [c["name"] for c in claim["checks"]],
            ["span exists", "date in range", "subject not stale"],
        )
        self.assertEqual(claim["run"]["tool"], "extract")
        self.assertIsNone(claim["excerpt"])
        self.assertEqual(claim["thread"], [])
        by_request = await (await other.get(f"/api/review/request/{rid}")).get_json()
        self.assertEqual(by_request["id"], str(st.id))
        r = await other.post(
            f"/api/evidence/{self.sha}/reveal", headers=other.csrf, json={"item": {}}
        )
        self.assertEqual(r.status_code, 200)
        claim = await (await other.get(f"/api/review/claim/{st.id}")).get_json()
        self.assertEqual(claim["value"]["text"], "يوسف")
        self.assertEqual(claim["excerpt"]["span"], "يوسف")
        missing = await other.get(f"/api/review/claim/{uuid.uuid4()}")
        self.assertEqual(missing.status_code, 404)

    async def test_reveal_all_logs_one_access_per_source_and_unblinds(self):
        from stk.ledger.contracts import Prop
        from stk.ledger.models import Payload, Statement

        st, _, _ = await self.ask()
        other = await self.client("colleague@example.com")
        claim = await (await other.get(f"/api/review/claim/{st.id}")).get_json()
        self.assertEqual([h["sha256"] for h in claim["hidden_sources"]], [self.sha])
        r = await other.post(
            f"/api/review/claim/{st.id}/reveal", headers=other.csrf, json={"item": {}}
        )
        claim = await r.get_json()
        self.assertEqual(r.status_code, 200, claim)
        self.assertEqual(claim["hidden_sources"], [])
        self.assertEqual(claim["value"]["text"], "يوسف")
        # Revealing again finds nothing hidden and logs nothing more.
        await other.post(
            f"/api/review/claim/{st.id}/reveal", headers=other.csrf, json={"item": {}}
        )
        async with ext.async_session_factory() as s:
            reveals = (
                await s.scalars(
                    select(Payload.value)
                    .join(Statement, Statement.commitment == Payload.commitment)
                    .where(
                        Statement.subject == f"blob:{self.sha}",
                        Statement.prop == Prop.ACCESS,
                    )
                )
            ).all()
        self.assertEqual([v["action"] for v in reveals], ["reveal"])

    async def test_staging_shows_the_rows_the_passkey_signs(self):
        st = await self.propose(f"person:{uuid.uuid4()}", "محمد")
        client = await self.client()
        r = await client.post(
            "/api/review/options",
            headers=client.csrf,
            json={
                "item": {"statement": str(st.id), "verdict": "reject", "reason": "x"}
            },
        )
        body = await r.get_json()
        self.assertEqual(r.status_code, 200, body)
        self.assertEqual(body["rows"][0]["summary"], "reject this claim, because x")
        self.assertEqual(body["rows"][0]["hash"], body["hash"])

    async def test_staging_refuses_a_person_without_a_passkey(self):
        st = await self.propose(f"person:{uuid.uuid4()}", "محمد")
        uid = await self.colleague()
        client = await self.client("colleague@example.com")
        headers = {**client.csrf, "Referer": "http://localhost/review?box=open"}
        r = await client.post(
            "/api/review/options",
            headers=headers,
            json={"item": {"statement": str(st.id), "verdict": "accept"}},
        )
        body = await r.get_json()
        self.assertEqual(r.status_code, 409, body)
        self.assertEqual(body["reason"], "no_passkey")
        self.assertEqual(body["setup_url"], "/wan-register?next=%2Freview%3Fbox%3Dopen")
        # Nothing is held for a prompt that cannot happen.
        from stk.review.views import PENDING

        # PENDING is process-wide; other tests may leave their own entries.
        self.assertFalse([key for key in PENDING if key[0] == uid])

    async def test_passkey_setup_returns_to_the_page_that_asked(self):
        uid = await self.colleague()
        client = await self.client("colleague@example.com")
        await client.get("/wan-register?next=https://evil.example/")
        async with client.session_transaction() as sess:
            self.assertNotIn("passkey_next", sess)
        await client.get("/wan-register?next=/review%3Fstatement%3Dabc")
        async with client.session_transaction() as sess:
            self.assertEqual(sess["passkey_next"], "/review?statement=abc")
        # Without a passkey yet, the registration response keeps its own redirect.
        r = await client.post(
            "/wan-register-response",
            headers=client.csrf,
            form={"csrf_token": client.csrf["X-CSRFToken"]},
        )
        self.assertEqual(r.status_code, 302)
        self.assertNotEqual(r.headers["Location"], "/review?statement=abc")
        async with ext.async_session_factory() as s, s.begin():
            user = await s.scalar(select(User).where(User.id == uid))
            s.add(SoftPasskey().register(user))
        r = await client.post(
            "/wan-register-response",
            headers=client.csrf,
            form={"csrf_token": client.csrf["X-CSRFToken"]},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/review?statement=abc")


if __name__ == "__main__":
    unittest.main()
