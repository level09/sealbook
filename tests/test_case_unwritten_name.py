"""The case study "The name nobody wrote down", scored in fixed mode and played
in acts.

The gap finder must flag the driver's 73-day silence and the missing woman's open
silence, and none of the four planted silences that are not gaps. Seeded in acts,
the standing question on the driver must find nothing in April's logs and his
name again in May's, and discovery must propose the night at the depot for a
person to accept. The numbers are pinned so any change has to be read and written
down in the case's README."""

import asyncio
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import stk.app  # noqa: F401  (registers the CLI before the seed module loads)
import stk.extensions as ext
from quart_security import hash_password
from sqlalchemy import func, select
from stk.user.models import Role, User

from acceptance import eval_agents
from sealbook.agents import gaps, read
from sealbook.agents.models import AgentTask
from sealbook.cases import conclusions
from sealbook.collaboration.models import Assignment
from sealbook.discovery.models import FindingKey
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Statement, StatementTier
from sealbook.ledger.write import verify_db
from sealbook.monitor import study
from sealbook.monitor.seed_study import seed_study
from sealbook.settings import Config
from tests.test_case_studies import stood_in
from tests.test_people import POSTGRES, _app

CASE = (
    Path(__file__).parents[1] / "acceptance" / "cases" / "unwritten-name" / "case.json"
)


class UnwrittenNameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(CASE.read_text(encoding="utf-8"))
        cls.report = asyncio.run(eval_agents.evaluate(live=False, path=CASE))
        cls.first = cls.report["runs"][0]

    def test_story_is_fictional_and_rests_on_the_gap_finder(self):
        story = self.spec["story"]
        self.assertTrue(story["fictional"])
        self.assertIn("gap finder", story["rests_on"])
        self.assertEqual(self.spec["investigation"], "case-unwritten-name")

    def test_gap_finder_flags_both_planted_gaps_and_no_planted_non_gap(self):
        gaps = self.first["gaps"]
        self.assertEqual(gaps["recall"], 1.0)
        self.assertEqual(gaps["false_gaps_on_planted_non_gaps"], 0)
        self.assertEqual(gaps["extra_in_planted_people"], 0)
        self.assertEqual(gaps["detected_elsewhere"], 0)
        self.assertEqual((gaps["detected"], gaps["reported"]), (2, 2))
        self.assertEqual(self.report["planted_deferred"]["gaps"], 0)
        # The supervisor's account starts the driver's watch, so the stopped
        # routine is not found a second time.
        self.assertEqual(gaps["routine_stops"], 0)

    def test_the_driver_silence_is_found_without_the_supervisor_statement(self):
        """The logs alone, in their three dated batches: his routine stops on 12
        March, the last line naming him, and he is named again on 26 May. Each
        batch is its own source, so the place they all record carries the
        routine. Nobody else's routine is flagged, Petra Coyle's leave included."""
        spec = json.loads(CASE.read_text(encoding="utf-8"))
        gone = {r for r, v in spec["records"].items() if v["source"] == "supervisor"}
        spec["claims"] = [c for c in spec["claims"] if c["record"] not in gone]
        spec["records"] = {r: v for r, v in spec["records"].items() if r not in gone}
        spec["clusters"] = [[r for r in c if r not in gone] for c in spec["clusters"]]
        del spec["sources"]["supervisor"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            _, case, events, _ = eval_agents.load(path)
        found = gaps.detect(case.claims, events, date.fromisoformat(spec["today"]))
        stops = [g for g in found if "routine" in g]
        self.assertEqual(len(stops), 1, stops)
        (aron,) = stops
        gate = f"person:{eval_agents._id('aron_gate1')}"
        self.assertEqual(aron["person"], read.cluster_of(case.groups, gate))
        self.assertEqual(aron["window"], ["2026-03-12", "2026-05-26"])
        self.assertEqual(aron["days"], 75)
        routine = aron["routine"]
        self.assertEqual(
            (routine["source"], routine["place"]), (None, "Wenmere depot gate")
        )
        self.assertEqual(
            gaps.routine_words(routine, aron["window"], routine["place"]),
            "Recorded at Wenmere depot gate about 5 days a week from 2 February 2026 "
            "to 12 March 2026, then in no record at all for 75 days, while it went on",
        )
        # Dana's open silence after the notice still stands.
        self.assertEqual(len(found), 2)

    def test_the_driver_silence_is_honestly_over_the_threshold(self):
        aron = next(
            g for g in self.spec["truth"]["gaps"] if g["record"] == "aron_gate1"
        )
        days = (date.fromisoformat(aron["to"]) - date.fromisoformat(aron["from"])).days
        self.assertEqual(days, 73)
        self.assertGreater(days, eval_agents.gaps.GAP_DAYS)

    def test_logs_hold_several_hundred_dated_lines(self):
        seen = [c for c in self.spec["claims"] if c["prop"] == "seen_at"]
        self.assertEqual(len(self.spec["claims"]), 671)
        self.assertGreater(len(seen), 500)

    def test_the_logs_arrive_in_dated_batches_and_the_acts_are_playable(self):
        study.load()
        study.check(self.spec, self.spec["acts"])
        arrive = {
            k: n for n, act in enumerate(self.spec["acts"]) for k in act["sources"]
        }
        self.assertLess(arrive["gate1"], arrive["gate2"])
        self.assertLess(arrive["gate2"], arrive["gate3"])
        days = {
            c["from"]
            for c in self.spec["claims"]
            if self.spec["records"][c["record"]]["source"] == "gate2" and c["from"]
        }
        self.assertEqual((min(days), max(days)), ("2026-04-01", "2026-04-30"))
        # April names no Aron Vesk: the standing question has nothing to report.
        april = {r for r, v in self.spec["records"].items() if v["source"] == "gate2"}
        self.assertNotIn("aron_gate2", april)

    def test_no_contradiction_or_same_person_is_planted_or_offered(self):
        linker, found = self.first["linker"], self.first["contradictions"]
        self.assertEqual(found["rule_candidates"]["candidates"], [])
        self.assertEqual(found["items"], [])
        self.assertEqual(linker["false_merges"], 0)
        self.assertEqual(linker["other_false_proposals"], 0)


@unittest.skipUnless(
    POSTGRES, "case seeding needs Postgres (set SQLALCHEMY_DATABASE_URI)"
)
class UnwrittenNameActsTest(unittest.IsolatedAsyncioTestCase):
    """A throwaway database with an owner, no embedding and no model routes."""

    def setUp(self):
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        self.enterContext(mock.patch.object(Config, "RUN_MODELS", {}))
        self.app = _app(self)

    async def asyncSetUp(self):
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

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def test_until_april_the_question_has_found_nothing_new(self):
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, CASE, until=6)
        self.assertEqual(out["sources"], 16)
        checks = out["question checks"]
        # The first check reads what the case holds: five papers name him.
        self.assertEqual([c["matches"] for c in checks], [5, 0])
        self.assertEqual(checks[1]["reason"], "No new claims matched this question.")
        self.assertNotIn("gaps proposals", out)
        async with ext.async_session_factory() as s:
            self.assertEqual(await s.scalar(select(func.count(FindingKey.key))), 0)

    async def test_the_whole_case_in_acts(self):
        inv = "case-unwritten-name"
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, CASE)
        self.assertEqual((out["sources"], out["claims"]), (18, 671))
        async with ext.async_session_factory() as s:
            self.assertEqual(await stood_in(s), 0)
        # The question: five records at first, none in April, the two May and
        # June logs that name him again.
        self.assertEqual([c["matches"] for c in out["question checks"]], [5, 0, 2])
        self.assertEqual(out["gaps proposals"], 2)
        self.assertEqual(
            out["discovery"],
            {
                "proposed": {"candidate_incident": 1, "shared_presence": 1},
                "incident members": 14,
                "kept": 6,
                "people kept": 3,
                "rejected": 1,
            },
        )
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            task = await s.scalar(select(AgentTask))
            self.assertEqual((task.kind, task.every_seconds), ("standing", None))
            states = dict(
                (
                    await s.execute(
                        select(FindingKey.kind, FindingKey.state).where(
                            FindingKey.investigation == inv
                        )
                    )
                ).all()
            )
            self.assertEqual(
                states,
                {"candidate_incident": "accepted", "shared_presence": "rejected"},
            )
            incidents = await s.scalar(
                select(func.count())
                .select_from(Statement)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .where(
                    Statement.prop == Prop.INCIDENT, StatementTier.tier == Tier.ACCEPTED
                )
            )
            self.assertEqual(incidents, 1)
            done = list(await s.scalars(select(Assignment.status)))
            self.assertEqual(done, ["done", "done"])
            got = await conclusions.listing(s, inv)
            self.assertEqual(got["status"]["status"], "concluded")
            [c] = got["conclusions"]
            self.assertTrue(c["stands"], c["broken"])


if __name__ == "__main__":
    unittest.main()
