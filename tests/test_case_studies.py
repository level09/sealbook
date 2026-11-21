"""Case studies (docs/case-studies.md): the fixed-mode score of "One man, four
names" is pinned, and `stk demo-seed --case` writes a case file through the ledger
write path with every claim pinned where it says."""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import click
import stk.app  # noqa: F401  (registers the CLI before the seed module loads)
import stk.extensions as ext
from quart_security import hash_password
from sqlalchemy import func, select
from stk.user.models import Role, User

from acceptance import eval_agents
from sealbook.agents import read
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Statement, StatementTier
from sealbook.ledger.write import verify_db
from sealbook.monitor.seed_study import path_of, person, seed_study
from sealbook.people import data as people
from sealbook.settings import Config
from tests.test_people import POSTGRES, _app

FOUR_NAMES = path_of("four-names")


class FourNamesScoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = asyncio.run(eval_agents.evaluate(live=False, path=FOUR_NAMES))
        cls.linker = cls.report["runs"][0]["linker"]

    def test_story_is_marked_fictional(self):
        story = json.loads(FOUR_NAMES.read_text())["story"]
        self.assertIs(story["fictional"], True)
        self.assertEqual(story["kind"], "fraud")

    def test_rule_layer_is_pinned(self):
        """Four of the six cross-desk pairs are candidates. The two the rules rule
        out join the licence, whose father إلياس keys as ياس (the article ال
        stripped) against Elias; the consultant's records, which name no father,
        still bridge the licence to the other three."""
        self.assertEqual(self.report["claims"], 417)
        self.assertTrue(self.report["complete"])
        self.assertEqual(self.linker["recall"], 0.667)
        missed = [
            p["records"] for p in self.linker["same_person"] if not p["candidate"]
        ]
        self.assertEqual(
            missed,
            [
                ["licence/holder", "passport/holder"],
                ["licence/holder", "vendor_form/owner"],
            ],
        )
        merged = [p["records"] for p in self.linker["twins"] if p["proposed"]]
        self.assertEqual(merged, [["agreement/consultant", "surveyors/S009"]])
        self.assertEqual(self.linker["other_false_proposals"], 0)
        self.assertEqual(self.linker["relations_written"], 0)

    def test_nothing_else_is_planted_or_found(self):
        run = self.report["runs"][0]
        self.assertIsNone(self.report["targets"]["gap_recall == 1.0 in every trial"])
        self.assertEqual(run["gaps"]["detected"], 0)
        self.assertEqual(run["contradictions"]["items"], [])
        self.assertIsNone(self.report["metrics"]["canary_held"]["mean"])


@unittest.skipUnless(
    POSTGRES, "case seeding needs Postgres (set SQLALCHEMY_DATABASE_URI)"
)
class SeedStudyTest(unittest.IsolatedAsyncioTestCase):
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

    async def tiers(self, s, prop=None):
        q = select(StatementTier.tier, func.count()).join(
            Statement, Statement.id == StatementTier.statement_id
        )
        if prop:
            q = q.where(Statement.prop == prop)
        return dict((await s.execute(q.group_by(StatementTier.tier))).all())

    async def test_four_names_seeds_and_waits_for_a_person(self):
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, FOUR_NAMES)
            with self.assertRaises(click.ClickException):
                await seed_study(s, self.app, FOUR_NAMES)
        self.assertEqual(out["sources"], 14)
        self.assertEqual(out["claims"], 417)
        # The fixed answer proposes every candidate, the namesake included.
        self.assertEqual(out["linker proposals"], 5)
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            # Every pin held: no claim was refused for its span.
            self.assertNotIn(Tier.REJECTED, await self.tiers(s))
            links = await self.tiers(s, Prop.SAME_AS)
            self.assertEqual(links, {Tier.ACCEPTED: 5, Tier.UNCONFIRMED: 5})
            case = await read.case(s, "case-four-names")
            inv = "case-four-names"
            desks = {
                case.groups.get(people.pid(person(inv, r)))
                for r in ("vendor_form/owner", "agreement/consultant")
            }
            self.assertEqual(len(desks), 2)
            self.assertEqual(len(case.links), 5)

    async def test_stand_ins_events_and_places(self):
        """A case without files: text stand-ins, an event and a confirmed place."""
        spec = {
            "story": {"title": "Stand-in", "puzzle": "p", "fictional": True},
            "investigation": "case-stand-in",
            "places": {
                "quay": {
                    "name_en": "North Quay",
                    "name_ar": "",
                    "lon": -30.0,
                    "lat": 10.0,
                    "precision_m": 200,
                }
            },
            "sources": {
                "log": {
                    "title": "Gate log",
                    "doc_type": "log",
                    "sensitivity": "ordinary",
                    "date": "2023-01-02",
                }
            },
            "records": {"a": {"source": "log", "person": "a"}},
            "clusters": [],
            "claims": [
                {
                    "key": "a.name",
                    "record": "a",
                    "prop": "name",
                    "value": "Mira Holm",
                    "from": None,
                    "to": None,
                    "place": None,
                    "tier": "accepted",
                },
                {
                    "key": "a.seen",
                    "record": "a",
                    "prop": "seen_at",
                    "value": "North Quay",
                    "from": "2023-01-02",
                    "to": "2023-01-02",
                    "place": "quay",
                    "tier": "unconfirmed",
                },
                {
                    "key": "a.missing",
                    "record": "a",
                    "prop": "event",
                    "value": "Missing",
                    "from": "2023-01-03",
                    "to": None,
                    "place": None,
                    "tier": "unconfirmed",
                    "event": "missing",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.json"
            path.write_text(json.dumps(spec))
            async with ext.async_session_factory() as s:
                out = await seed_study(s, self.app, path)
        self.assertEqual(out["claims"], 3)
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            self.assertNotIn(Tier.REJECTED, await self.tiers(s))
            self.assertEqual(await self.tiers(s, Prop.NAME), {Tier.ACCEPTED: 1})
            case = await read.case(s, "case-stand-in")
            seen = next(c for c in case.claims if c.prop == Prop.SEEN_AT)
            self.assertEqual(case.places[seen.id].name_en, "North Quay")


if __name__ == "__main__":
    unittest.main()
