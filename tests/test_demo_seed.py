"""`stk demo-seed --full` on a throwaway Postgres database: every analyst view gets
content, the worker finds the planted media duplicates, the chain verifies."""

import unittest
from datetime import datetime
from unittest import mock

import click
from quart_security import hash_password
from sqlalchemy import func, select

import stk.app  # noqa: F401  (registers the CLI before the seed module loads)
import stk.extensions as ext
from stk.collaboration import service as collaboration
from stk.collaboration.models import Assignment, Notification
from stk.discovery.evidence import transcript_timeline
from stk.discovery.models import FindingKey
from stk.ledger.contracts import Prop
from stk.ledger.models import Acquisition, Payload, Run, Statement, StatementTier
from stk.ledger.write import verify_db
from stk.monitor.seed import seed
from stk.monitor.seed_full import P1, R1, seed_full
from stk.monitor.seed_more import CASES, seed_more
from stk.packages.models import Sentence
from stk.people import data as people
from stk.places.models import Place
from stk.review import findings
from stk.runs.worker import work_once
from stk.settings import Config
from stk.sheets.models import DatasetVersion
from stk.sources import scale
from stk.stories.data import story
from stk.user.models import Role, User
from stk.watch.views import feed
from tests.test_people import POSTGRES, _app


@unittest.skipUnless(POSTGRES, "demo seed needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class DemoSeedTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        self.enterContext(mock.patch.object(Config, "RUN_MODELS", {}))
        # Speech to text has its own tests; here it would need the downloaded model.
        self.enterContext(mock.patch.object(Config, "SPEECH_MODEL", ""))
        self.app = _app(self)

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def test_full_seed_populates_every_view(self):
        async with ext.async_session_factory() as s, s.begin():
            owner = User(
                email="owner@example.com",
                name="owner",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            owner.roles.append(Role(name="admin"))  # as `stk install` creates it
            s.add(owner)
        async with ext.async_session_factory() as s:
            await seed(s)
            out = await seed_full(s, self.app)
            with self.assertRaises(click.ClickException):
                await seed_full(s, self.app)
        self.assertEqual(out["counts"]["watch hits"], 4)
        self.assertEqual(out["counts"]["open findings"], 4)
        while await work_once(ext.async_session_factory):
            pass
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            failed = await s.scalar(select(func.count()).where(Run.status == "failed"))
            self.assertEqual(failed, 0)
            matches = await s.scalar(
                select(func.count()).where(Statement.prop == Prop.MEDIA_MATCH)
            )
            self.assertGreaterEqual(matches, 3)
            colleague = await s.scalar(
                select(User).where(User.email == "analyst-2@example.com")
            )
            self.assertTrue(colleague.active)
            requests = (
                await s.execute(
                    select(Statement).where(Statement.prop == Prop.REVIEW_REQUEST)
                )
            ).scalars()
            for r in requests:
                task = await collaboration.task(s, r.id, colleague)
                self.assertTrue(task["comments"])
            tiers = dict(
                (
                    await s.execute(
                        select(StatementTier.reason, func.count()).group_by(
                            StatementTier.reason
                        )
                    )
                ).all()
            )
            self.assertIn("human_accept", tiers)
            watches = await feed(s, 1)
            self.assertEqual(len(watches), 3)
            self.assertTrue(all(w["proposals"] for w in watches))
            self.assertTrue((await people.person(s, P1))["places"])
            self.assertEqual(await s.scalar(select(func.count(Sentence.id))), 2)
            await self.check_flows(s)

    async def check_flows(self, s):
        """Every flow the guided tour in docs/demo.md walks through has data."""
        owner = await s.scalar(select(User).where(User.email == "owner@example.com"))
        case = {"investigation": "case-demo-02", "time": None}
        # case-demo-01 is indexed like any intake, so it is searchable.
        indexed = await s.scalar(
            select(func.count())
            .select_from(Run)
            .join(
                Acquisition,
                Acquisition.id
                == Run.inputs["acquisition"].astext.cast(Acquisition.id.type),
            )
            .where(
                Run.tool == "index",
                Run.status == "done",
                Acquisition.context["investigation"].astext == "case-demo-01",
            )
        )
        self.assertGreaterEqual(indexed, 5)
        # Discover: open findings from the real run, one rejected with its reason.
        queue = await findings.queue(s, owner, "case-demo-02")
        self.assertEqual(
            sorted(f["kind"] for f in queue),
            ["candidate_incident", *["shared_presence"] * 3],
        )
        self.assertTrue(
            any(
                f["kind"] == "candidate_incident" and "Branch 999" in f["reason"]
                for f in queue
            )
        )
        states = dict(
            (
                await s.execute(
                    select(FindingKey.kind + ":" + FindingKey.state, func.count())
                    .where(FindingKey.investigation == "case-demo-02")
                    .group_by(FindingKey.kind, FindingKey.state)
                )
            ).all()
        )
        self.assertEqual(states["candidate_incident:accepted"], 1)
        self.assertEqual(states["shared_presence:rejected"], 1)
        # Stories: Yousef has a gap and a fork, the incident has its people, the
        # place story for Branch 999 has points.
        yousef = await story(s, owner, "person", R1.removeprefix("person:"), case)
        self.assertTrue(yousef["rows"][0]["gaps"])
        days = [
            p["from"]
            for p in yousef["points"]
            if p["prop"] in ("arrested_at", "last_seen")
        ]
        self.assertEqual(days.count("2013-05-02"), 2)
        incident = (await findings.incidents(s, "case-demo-02"))[0]
        band = await story(s, owner, "incident", incident["id"], case)
        self.assertEqual(len(band["rows"]), 2)
        branch = await s.scalar(select(Place.id).where(Place.name_en == "Branch 999"))
        place = await story(s, owner, "place", str(branch), case)
        self.assertGreaterEqual(len({p["subject"] for p in place["points"]}), 6)
        # Evidence: a sheet with two versions and cell proposals, a transcript
        # with a time per line, and claims on the PDF's page text.
        versions = (await s.scalars(select(DatasetVersion))).all()
        self.assertEqual(sorted(v.number for v in versions), [1, 2])
        self.assertTrue(all(v.diff for v in versions))
        cells = await s.scalar(
            select(func.count()).where(Statement.span["row"].is_not(None))
        )
        self.assertGreater(cells, 50)
        witness = await s.scalar(
            select(Acquisition.blob_sha256).where(
                Acquisition.context["file"].astext == "witness",
                Acquisition.context["investigation"].astext == "case-demo-02",
            )
        )
        self.assertEqual(len(await transcript_timeline(s, witness)), 5)
        provenance = await s.scalar(
            select(func.count()).where(Run.model_served == "sealbook-demo-fixed-output")
        )
        self.assertEqual(provenance, 3)
        # Team work: notifications for every active analyst, overdue work.
        for email in ("analyst-2@example.com", "analyst-3@example.com"):
            user = await s.scalar(select(User).where(User.email == email))
            self.assertTrue(
                await s.scalar(
                    select(func.count()).where(Notification.user_id == user.id)
                )
            )
        kinds = set(
            await s.scalars(
                select(Notification.kind).where(Notification.user_id == owner.id)
            )
        )
        self.assertLessEqual({"assigned", "mention", "reply", "intake_done"}, kinds)
        overdue = await s.scalar(
            select(func.count()).where(
                Assignment.assignee_id == owner.id,
                Assignment.status == "open",
                Assignment.due < func.current_date(),
            )
        )
        self.assertEqual(overdue, 1)
        self.assertTrue(
            await s.scalar(
                select(func.count())
                .select_from(Payload)
                .where(
                    Payload.value["reason"].astext.contains("One man recorded twice")
                )
            )
        )


@unittest.skipUnless(POSTGRES, "demo seed needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class MoreCasesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        self.enterContext(mock.patch.object(Config, "RUN_MODELS", {}))
        self.enterContext(mock.patch.object(Config, "SPEECH_MODEL", ""))
        self.app = _app(self)

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def test_more_cases_and_filler_sources(self):
        async with ext.async_session_factory() as s, s.begin():
            owner = User(
                email="owner@example.com",
                name="owner",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            owner.roles.append(Role(name="admin"))
            s.add(owner)
        async with ext.async_session_factory() as s:
            out = await seed_more(s, self.app)
            with self.assertRaises(click.ClickException):
                await seed_more(s, self.app)
            await scale.fillers(s, 300)
            await s.commit()
        self.assertEqual(sorted(out["cases"]), sorted(CASES))
        while await work_once(ext.async_session_factory):
            pass
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            self.assertEqual(
                await s.scalar(select(func.count()).where(Run.status == "failed")), 0
            )
            owner = await s.scalar(
                select(User).where(User.email == "owner@example.com")
            )
            for case_id, case in CASES.items():
                sources = await s.scalar(
                    select(func.count())
                    .select_from(Acquisition)
                    .where(Acquisition.context["investigation"].astext == case_id)
                )
                self.assertTrue(8 <= sources <= 20, (case_id, sources))
                self.assertEqual(sources, len(case["docs"]) + len(case["photos"]))
                self.assertTrue(await findings.queue(s, owner, case_id), case_id)
                self.assertTrue(
                    await s.scalar(
                        select(func.count())
                        .select_from(Assignment)
                        .where(Assignment.investigation == case_id)
                    ),
                    case_id,
                )
            # Accepted incidents, from the real discovery run and a signed gesture.
            for case_id in ("case-demo-03", "case-demo-05", "case-demo-06"):
                self.assertTrue(await findings.incidents(s, case_id), case_id)
            # Every person is written in more than one spelling across the case.
            spellings = await s.scalar(
                select(func.count(func.distinct(Payload.value["text"].astext)))
                .select_from(Statement)
                .join(Payload, Payload.commitment == Statement.commitment)
                .where(
                    Statement.prop == Prop.NAME,
                    Statement.subject.like("person:%"),
                )
            )
            people = sum(len(c["people"]) for c in CASES.values())
            self.assertGreater(spellings, people)
            assignees = set(
                await s.scalars(
                    select(Assignment.assignee_id).where(
                        Assignment.investigation.like("case-demo-0%")
                    )
                )
            )
            self.assertGreaterEqual(len(assignees), 4)
            # Fillers: chained, labelled, and nothing queued for them.
            filler = Acquisition.context["investigation"].astext == scale.DEMO
            self.assertEqual(
                await s.scalar(
                    select(func.count()).select_from(Acquisition).where(filler)
                ),
                300,
            )
            self.assertFalse(
                await s.scalar(
                    select(func.count())
                    .select_from(Run)
                    .where(
                        Run.inputs["blobs"][0].astext.in_(
                            select(Acquisition.blob_sha256).where(filler)
                        )
                    )
                )
            )
