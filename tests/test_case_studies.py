"""Case studies (docs/case-studies.md): the fixed-mode score of "One man, four
names" is pinned, and `stk demo-seed --case` writes a case file through the ledger
write path with every claim pinned where it says."""

import asyncio
import json
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import click
import orjson
import stk.app  # noqa: F401  (registers the CLI before the seed module loads)
import stk.extensions as ext
from quart_security import hash_password
from sqlalchemy import func, select
from stk.user.models import Role, User

from acceptance import eval_agents
from sealbook import blobs
from sealbook.agents import read
from sealbook.cases import conclusions
from sealbook.collaboration.models import Assignment
from sealbook.discovery.evidence import copy_reason
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Run,
    Statement,
    StatementTier,
)
from sealbook.ledger.write import verify_db
from sealbook.monitor.seed_study import path_of, person, seed_study
from sealbook.people import data as people
from sealbook.places.models import Place
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
        self.assertEqual(self.report["claims"], 422)
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


class Seeding(unittest.IsolatedAsyncioTestCase):
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

    async def tiers(self, s, prop=None):
        q = select(StatementTier.tier, func.count()).join(
            Statement, Statement.id == StatementTier.statement_id
        )
        if prop:
            q = q.where(Statement.prop == prop)
        return dict((await s.execute(q.group_by(StatementTier.tier))).all())

    async def concluded(self, s, inv):
        """The case is concluded by one standing, peer-agreed conclusion signed by
        a person's name, and every item it cites still stands."""
        got = await conclusions.listing(s, inv)
        self.assertEqual(got["status"]["status"], "concluded")
        [c] = got["conclusions"]
        self.assertTrue(c["stands"], c["broken"])
        self.assertEqual(c["broken"], [])
        self.assertEqual(c["by"], "Ines Halvorsen")
        self.assertEqual(
            (c["peer"]["state"], c["peer"]["by"]), ("agreed", "Tomas Reyes")
        )
        self.assertEqual(c["peer"]["reviewer"], "Tomas Reyes")
        return c


@unittest.skipUnless(
    POSTGRES, "case seeding needs Postgres (set SQLALCHEMY_DATABASE_URI)"
)
class SeedStudyTest(Seeding):
    async def test_four_names_seeds_and_waits_for_a_person(self):
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, FOUR_NAMES)
            with self.assertRaises(click.ClickException):
                await seed_study(s, self.app, FOUR_NAMES)
        self.assertEqual(out["sources"], 14)
        self.assertEqual(out["claims"], 422)
        # The fixed answer proposes every candidate, the namesake included.
        self.assertEqual(out["linker proposals"], 5)
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            # Every pin held: no claim was refused for its span.
            self.assertNotIn(Tier.REJECTED, await self.tiers(s))
            # The four true proposals are accepted for the conclusion; the
            # surveyor's stays for a person.
            links = await self.tiers(s, Prop.SAME_AS)
            self.assertEqual(links, {Tier.ACCEPTED: 9, Tier.UNCONFIRMED: 1})
            inv = "case-four-names"
            waiting, value = (
                await s.execute(
                    select(Statement.subject, Payload.value)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .where(
                        Statement.prop == Prop.SAME_AS,
                        StatementTier.tier == Tier.UNCONFIRMED,
                    )
                )
            ).one()
            self.assertIn(
                person(inv, "surveyors/S009"), {waiting, f"person:{value['person']}"}
            )
            case = await read.case(s, inv)
            desks = {
                case.groups.get(people.pid(person(inv, r)))
                for r in (
                    "vendor_form/owner",
                    "agreement/consultant",
                    "guarantee_deed/guarantor",
                    "licence/holder",
                )
            }
            self.assertEqual(len(desks), 1)
            c = await self.concluded(s, inv)
            self.assertEqual(len(c["rests_on"]), 9)

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

    def acted(self, slug: str) -> dict:
        """Two sources over two acts: a log arrives and is read, then a letter
        arrives, a person accepts and the places are confirmed."""
        claim = {"from": None, "to": None, "place": None, "tier": "unconfirmed"}
        return {
            "story": {"title": "Acts", "puzzle": "p", "fictional": True},
            "investigation": slug,
            "places": {},
            "sources": {
                k: {"title": t, "doc_type": "log", "sensitivity": "ordinary", "date": d}
                for k, t, d in (
                    ("log", "Gate log", "2023-01-02"),
                    ("letter", "Letter", "2023-01-05"),
                )
            },
            "records": {
                "a": {"source": "log", "person": "a"},
                "b": {"source": "letter", "person": "b"},
            },
            "clusters": [],
            "claims": [
                claim
                | {
                    "key": "a.name",
                    "record": "a",
                    "prop": "name",
                    "value": "Mira Holm",
                },
                claim
                | {"key": "b.name", "record": "b", "prop": "name", "value": "Ivo Lund"}
                | {"tier": "accepted"},
            ],
            "acts": [
                {
                    "title": "The log",
                    "day": "2023-01-02",
                    "story": "A log arrives.",
                    "shows": ["reading"],
                    "sources": ["log"],
                    "steps": ["claims"],
                    "see": ["/evidence/{source:log}", "/people/{person:a}"],
                },
                {
                    "title": "The letter",
                    "day": "2023-01-05",
                    "story": "A letter arrives and its name is accepted.",
                    "shows": ["reading"],
                    "sources": ["letter"],
                    "steps": ["claims", "accept", "places"],
                    "see": ["/people/{person:b}"],
                },
            ],
        }

    async def seed_spec(self, spec, until=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.json"
            path.write_text(json.dumps(spec))
            async with ext.async_session_factory() as s:
                return await seed_study(s, self.app, path, until)

    async def test_acts_stop_at_their_boundary(self):
        """Seeding to act 1 leaves the letter absent and nothing accepted; the full
        run equals the last boundary; `see` paths resolve to the seeded ids."""
        first = await self.seed_spec(self.acted("case-act-1"), until=1)
        last = await self.seed_spec(self.acted("case-act-2"), until=2)
        full = await self.seed_spec(self.acted("case-act-3"))
        self.assertEqual((first["sources"], first["claims"]), (1, 1))
        self.assertEqual([a["title"] for a in first["acts"]], ["The log"])
        self.assertTrue(first["acts"][0]["see"][0].startswith("/evidence/"))
        self.assertEqual(
            first["acts"][0]["see"][1],
            f"/people/{people.pid(person('case-act-1', 'a'))}",
        )
        strip = {k: v for k, v in last.items() if k not in ("investigation", "acts")}
        self.assertEqual(
            strip, {k: v for k, v in full.items() if k not in ("investigation", "acts")}
        )
        self.assertEqual((last["sources"], last["claims"]), (2, 2))
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            # Only the full runs accepted the letter's name: act 1 decided nothing.
            self.assertEqual(
                await self.tiers(s, Prop.NAME), {Tier.ACCEPTED: 2, Tier.UNCONFIRMED: 3}
            )

    async def test_acts_are_checked_before_anything_is_written(self):
        spec = self.acted("case-act-bad")
        spec["acts"][1]["steps"] = ["no such step"]
        with self.assertRaises(click.ClickException):
            await self.seed_spec(spec)
        spec = self.acted("case-act-bad")
        spec["acts"][1]["sources"] = []
        with self.assertRaises(click.ClickException):
            await self.seed_spec(spec)
        async with ext.async_session_factory() as s:
            self.assertEqual(await self.tiers(s), {})


@unittest.skipUnless(
    POSTGRES, "case seeding needs Postgres (set SQLALCHEMY_DATABASE_URI)"
)
@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class SeedCasesTest(Seeding):
    """The other three cases seed whole and their twist waits for a person: each
    runs the agents its planted truth asks about, and no other."""

    async def run_of(self, s, tool, inv):
        run = await s.scalar(
            select(Run).where(
                Run.tool == tool, Run.inputs["investigation"].astext == inv
            )
        )
        if run is None:
            return None
        return orjson.loads(
            blobs.read(await s.get(Blob, run.output_hash), Config.BLOB_DIR)
        )

    async def said(self, s, inv, record, prop, day=None):
        q = select(Statement.id).where(
            Statement.subject == person(inv, record), Statement.prop == prop
        )
        if day:
            q = q.where(Statement.valid_from == date.fromisoformat(day))
        return await s.scalar(q)

    async def blob_of(self, s, inv, title):
        return await s.scalar(
            select(Acquisition.blob_sha256).where(
                Acquisition.context["investigation"].astext == inv,
                Acquisition.context["title"].astext == title,
            )
        )

    async def test_never_there_is_read_by_the_app(self):
        """Every source is read by the worker's own tools, replaying recorded
        answers. The read-for-real figure is pinned beside the planted score: how
        many planted claims extraction recovered, and whether the planted
        contradictions were flagged on what was written. Two claims extraction did
        not propose are planted, labelled, as the act says."""
        inv, spec = "case-never-there", json.loads(path_of("never-there").read_text())
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, path_of("never-there"))
        self.assertEqual(out["sources"], 53)
        self.assertEqual(
            out["read"], {"planted": 152, "recovered": 118, "written": 129}
        )
        self.assertEqual(len(out["missed"]), 34)
        self.assertIn("mara_stmt.at", out["missed"])
        self.assertNotIn("mara_mq3.at", out["missed"])
        self.assertEqual(out["planted"], 2)
        self.assertEqual(out["contradictions proposals"], 3)
        self.assertEqual(out["planted contradictions flagged"], 4)
        self.assertEqual([len(a["see"]) for a in out["acts"]], [2, 2, 2, 2, 1, 1, 2, 1])
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            # Replayed runs name the model that answered and say it did not run.
            note = await s.scalar(
                select(Run.inputs["demo"].astext).where(
                    Run.tool == "extract", Run.model_served.is_not(None)
                )
            )
            self.assertIn("recorded once", note)
            # The photograph went through intake: geotag read its EXIF position.
            photo = await self.blob_of(s, inv, spec["sources"]["photo"]["title"])
            where = await s.scalar(
                select(Payload.value)
                .join(Statement, Statement.commitment == Payload.commitment)
                .where(
                    Statement.subject == f"blob:{photo}",
                    Statement.prop == Prop.PHOTOGRAPHED_AT,
                )
            )
            lon, lat = map(float, where["geometry"]["coordinates"])
            self.assertEqual((round(lon, 4), round(lat, 4)), (-27.4764, -31.6216))
            # The speech model's words, replayed: "Joe", as it heard the name.
            voice = await self.blob_of(s, inv, spec["sources"]["voice"]["title"])
            derived = await s.scalars(
                select(Statement.subject)
                .join(Payload, Payload.commitment == Statement.commitment)
                .where(
                    Statement.prop == Prop.DERIVED_FROM,
                    Payload.value["blob"].astext == voice,
                )
            )
            texts = [
                blobs.read(
                    await s.get(Blob, d.removeprefix("blob:")), Config.BLOB_DIR
                ).decode()
                for d in derived
            ]
            self.assertTrue(any("I'm still at the yard" in t for t in texts))
            c = await self.concluded(s, inv)
            self.assertEqual(len(c["rests_on"]), 2)
            # The colleague answered on the claim and marked the exhibits updated.
            updated = await s.scalar(
                select(func.count()).where(Assignment.status == "done")
            )
            self.assertEqual(updated, 2)

    async def test_never_there_stops_after_an_act(self):
        """To act 4 every source has arrived and been read, and nothing after it
        has happened: no linker, no hunter, no conclusion."""
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, path_of("never-there"), until=4)
        self.assertEqual((out["sources"], len(out["acts"])), (53, 4))
        self.assertNotIn("contradictions proposals", out)
        async with ext.async_session_factory() as s:
            self.assertIsNone(
                await self.run_of(s, "contradictions", "case-never-there")
            )
            got = await conclusions.listing(s, "case-never-there")
            self.assertEqual(got["status"]["status"], "open")

    async def test_a_read_case_without_its_recordings_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.json"
            path.write_text(path_of("never-there").read_text())
            async with ext.async_session_factory() as s:
                with self.assertRaisesRegex(click.ClickException, "recordings.json"):
                    await seed_study(s, self.app, path)

    async def test_unwritten_name_finds_the_driver_silence(self):
        inv = "case-unwritten-name"
        spec = json.loads(path_of("unwritten-name").read_text())
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, path_of("unwritten-name"))
        self.assertEqual((out["sources"], out["claims"]), (14, 651))
        self.assertEqual(out["gaps proposals"], 2)
        self.assertNotIn("contradictions proposals", out)
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            # Every sheet cell, quote, box and OCR quote held.
            self.assertNotIn(Tier.REJECTED, await self.tiers(s))
            found = await self.run_of(s, "gaps", inv)
            # The driver's 73 days off the logs and the open silence since Dana
            # was last seen (the finder's window starts on the last record).
            self.assertEqual(
                sorted(
                    (tuple(i["window"]) for i in found["items"]),
                    key=lambda w: w[1] or "",
                ),
                [("2026-03-14", None), ("2026-03-14", "2026-05-26")],
                found["items"],
            )
            self.assertEqual(len((await self.concluded(s, inv))["rests_on"]), 4)
            # The scanned statement is read through OCR, and the claims quote it.
            scan = await self.blob_of(s, inv, spec["sources"]["supervisor"]["title"])
            self.assertTrue(
                await s.scalar(
                    select(Statement.id)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .where(
                        Statement.prop == Prop.DERIVED_FROM,
                        Payload.value["blob"].astext == scan,
                    )
                )
            )
            case = await read.case(s, inv)
            self.assertEqual(
                {p.name_en for p in case.places.values()},
                {"Wenmere", "Wenmere coach station and depot", "Holt Cross depot"},
            )

    async def test_old_footage_ties_the_viral_clip_to_the_original(self):
        inv = "case-old-footage"
        spec = json.loads(path_of("old-footage").read_text())
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, path_of("old-footage"))
        self.assertEqual((out["sources"], out["claims"]), (36, 16))
        self.assertGreater(out["match proposals"], 0)
        self.assertEqual(
            [k for k in out if k.endswith("proposals")], ["match proposals"]
        )
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            self.assertNotIn(Tier.REJECTED, await self.tiers(s))
            # 36 clips and the 16 posts that came with a caption.
            self.assertEqual(
                await s.scalar(
                    select(func.count()).where(
                        Acquisition.context["investigation"].astext == inv
                    )
                ),
                52,
            )
            viral, original = [
                await self.blob_of(s, inv, spec["sources"][k]["title"])
                for k in ("copy_viral", "original")
            ]
            pair = (
                select(StatementTier.tier)
                .join(Statement, Statement.id == StatementTier.statement_id)
                .join(Payload, Payload.commitment == Statement.commitment)
                .where(Statement.prop == Prop.MEDIA_MATCH)
            )
            tiers = list(
                await s.scalars(
                    pair.where(
                        (
                            (Statement.subject == f"blob:{viral}")
                            & (Payload.value["blob"].astext == original)
                        )
                        | (
                            (Statement.subject == f"blob:{original}")
                            & (Payload.value["blob"].astext == viral)
                        )
                    )
                )
            )
            # The strong matches inside the family are accepted for the
            # conclusion; the weak ones wait for a person.
            self.assertEqual(tiers, [Tier.ACCEPTED])
            weak = [
                (tier, value)
                for tier, value in (
                    await s.execute(
                        select(StatementTier.tier, Payload.value)
                        .join(Statement, Statement.id == StatementTier.statement_id)
                        .join(Payload, Payload.commitment == Statement.commitment)
                        .where(Statement.prop == Prop.MEDIA_MATCH)
                    )
                ).all()
                if copy_reason(value)[2] == "weak"
            ]
            self.assertTrue(weak)
            self.assertEqual({tier for tier, _ in weak}, {Tier.UNCONFIRMED})
            # Lindmark has no coordinates, so nothing is placed.
            self.assertFalse(
                await s.scalar(select(Place.id).where(Place.name_en == "Lindmark"))
            )
            captions = await self.tiers(s, Prop.REPORTS)
            self.assertEqual(captions, {Tier.UNCONFIRMED: 14, Tier.ACCEPTED: 2})
            await self.concluded(s, inv)


if __name__ == "__main__":
    unittest.main()
