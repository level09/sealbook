"""Case suggestion after a batch is read: names and places on overlapping dates tie
a batch to another case, a case the person cannot open is never named, and
recording a source there adds an acquisition and leaves the original. Synthetic
data on a throwaway Postgres; no model is called."""

import unittest
import uuid
from datetime import UTC, date, datetime
from unittest import mock

import stk.extensions as ext
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select

from sealbook import blobs
from sealbook.agents import tasks
from sealbook.cases.service import ensure as ensure_case
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Acquisition, Run
from sealbook.ledger.write import append, record_acquisition
from sealbook.settings import Config
from tests import test_add_evidence, test_review


@unittest.skipUnless(test_add_evidence.POSTGRES, "needs Postgres")
class CaseSuggestionTest(unittest.IsolatedAsyncioTestCase):
    setUp = test_review.ReviewTest.setUp
    asyncTearDown = test_review.ReviewTest.asyncTearDown
    client = test_review.ReviewTest.client

    async def asyncSetUp(self):
        await test_review.ReviewTest.asyncSetUp(self)
        async with ext.async_session_factory() as s, s.begin():
            for slug in ("case-r", "case-x", "case-hidden"):
                await ensure_case(s, slug)

    async def source(self, case, text, claims, batch=None):
        """A source in `case` with its claims: (subject, prop, value text, date)."""
        async with ext.async_session_factory() as s, s.begin():
            sha = (
                await blobs.put(s, text.encode(), "text/plain", Config.BLOB_DIR)
            ).sha256
            await record_acquisition(
                s,
                blob_sha256=sha,
                origin="device:scanner",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={"investigation": case, "filename": f"{text[:12]}.txt"}
                | ({"batch": batch} if batch else {}),
                sensitivity="ordinary",
                doc_type="letter",
            )
            run = Run(
                principal_id=self.agent.id,
                tool="extract",
                inputs={"blobs": [sha]},
                pubkey=self.key.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                ),
            )
            s.add(run)
            await s.flush()
            for subject, prop, value, when in claims:
                start = text.index(value)
                await append(
                    s,
                    StatementIn(
                        subject=subject,
                        prop=prop,
                        value={"text": value, "lang": "en"},
                        author_id=self.agent.id,
                        run_id=run.id,
                        span_blob=sha,
                        span={"start": start, "end": start + len(value)},
                        valid_from=when[0] if isinstance(when, tuple) else when,
                        valid_to=when[1] if isinstance(when, tuple) else None,
                    ),
                    sign=lambda h: {
                        "kind": "ed25519",
                        "sig": self.key.sign(h.encode()).hex(),
                    },
                )
            return sha

    async def test_suggests_an_open_case_and_records_on_click(self):
        p = lambda: f"person:{uuid.uuid4()}"  # noqa: E731
        may = date(2013, 5, 2)
        await self.source(
            "case-r",
            "Old note on Rami Saad Taha",
            [(p(), Prop.NAME, "Rami Saad Taha", None)],
        )
        await self.source(
            "case-x",
            "Yousef Khaled Al-Ali seen in Douma",
            [
                (p(), Prop.NAME, "Yousef Khaled Al-Ali", None),
                (p(), Prop.LAST_SEEN, "Douma", may),
            ],
        )
        # The hidden case ties all three batch sources; it must never be named.
        await self.source(
            "case-hidden",
            "Yousef Khaled Al-Ali, Douma, Lina Omar Najjar, Harasta",
            [
                (p(), Prop.NAME, "Yousef Khaled Al-Ali", None),
                (p(), Prop.LAST_SEEN, "Douma", may),
                (p(), Prop.NAME, "Lina Omar Najjar", None),
                (p(), Prop.ARRESTED_AT, "Harasta", date(2014, 2, 11)),
            ],
        )
        a = await self.source(
            "case-r",
            "Yousef Khaled al-Ali was taken",
            [(p(), Prop.NAME, "Yousef Khaled al-Ali", None)],
            "b1",
        )
        b = await self.source(
            "case-r",
            "Last seen in Douma in May",
            [(p(), Prop.LAST_SEEN, "Douma", (date(2013, 5, 1), date(2013, 5, 31)))],
            "b1",
        )
        # Same place, other dates: a place alone does not tie it.
        c = await self.source(
            "case-r",
            "Arrested in Harasta in 2011",
            [(p(), Prop.ARRESTED_AT, "Harasta", date(2011, 1, 3))],
            "b1",
        )

        real = tasks.can_read

        async def can_read(db, user_id, slug):
            return slug != "case-hidden" and await real(db, user_id, slug)

        client = await self.client()
        with mock.patch.object(tasks, "can_read", can_read):
            got = await (await client.get("/ingest/batch/b1/case")).get_json()
            self.assertNotIn("case-hidden", str(got))
            e = got["elsewhere"]
            self.assertEqual((got["read"], e["case"]), (3, "case-x"))
            ties = {s["sha256"]: s["ties"][0] for s in e["sources"]}
            self.assertEqual(set(ties), {a, b})
            self.assertEqual(ties[a]["kind"], "person")
            self.assertEqual(ties[a]["where"], "characters 0 to 20")
            self.assertEqual((ties[b]["kind"], ties[b]["when"]), ("place", "May 2013"))
            self.assertEqual([s["sha256"] for s in e["unmatched"]], [c])
            self.assertIsNone(got["new_case"])

            async def record(case):
                r = await client.post(
                    "/ingest/batch/b1/record",
                    json={"item": {"case": case, "sources": [a]}},
                    headers=client.csrf,
                )
                return r.status_code, await r.get_json()

            self.assertEqual((await record("case-hidden"))[0], 404)
            self.assertEqual(
                await record("case-x"), (200, {"recorded": 1, "already": 0})
            )
            self.assertEqual(
                await record("case-x"), (200, {"recorded": 0, "already": 1})
            )

            # A batch that ties to nothing gets a case name from its place and month.
            for text in ("Shelling in Al Hamra", "Al Hamra market burned"):
                await self.source(
                    "case-r",
                    text,
                    [(p(), Prop.SEEN_AT, "Al Hamra", date(2015, 3, 3))],
                    "b2",
                )
            got = await (await client.get("/ingest/batch/b2/case")).get_json()
            self.assertIsNone(got["elsewhere"])
            self.assertEqual(got["new_case"]["name"], "Al Hamra, March 2015")
            self.assertEqual(len(got["new_case"]["sources"]), 2)
        async with ext.async_session_factory() as s:
            homes = sorted(
                (
                    await s.scalars(
                        select(Acquisition.context["investigation"].astext).where(
                            Acquisition.blob_sha256 == a
                        )
                    )
                ).all()
            )
        self.assertEqual(homes, ["case-r", "case-x"])
