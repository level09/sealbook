"""The case study "One man, four names" told in acts (acceptance/cases/four-names).

The sheets go through the app's sheet intake with the case's column mapping, each
row answered from the case file; the five scans are read by the app, replaying
recorded answers. The linker's proposals wait for a person until act 7, where the
analyst accepts the true ones and declines the surveyor with her reason. The last
act builds a package, signed with a demo certificate, and checks it offline.

Anchoring here is the test path tests/test_packages.py uses (FakeAnchor: one real
Merkle batch with a partner-held root, no network); `stk demo-seed` runs the real
`stk anchor`. The figures are pinned here and in the case's README."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import stk.extensions as ext
from sqlalchemy import func, select

from sealbook.agents import read
from sealbook.cases import conclusions
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Payload, Run, Statement, StatementTier
from sealbook.ledger.write import verify_db
from sealbook.monitor.seed_study import path_of, seed_study
from sealbook.packages.cli import make_signing_cert
from sealbook.packages.models import Package
from sealbook.people import data as people
from sealbook.sheets.models import DatasetVersion
from tests.test_case_studies import Seeding
from tests.test_packages import FakeAnchor
from tests.test_people import POSTGRES

HERE = Path(__file__).parents[1] / "acceptance" / "cases" / "four-names"
CASE = path_of("four-names")
# The man's names as the app wrote them, one per desk's paper or row.
MAN = ("A. Qassar", "Adrian Qassar", "Adrian Khassar", "KASSAR / ADRIAN", "أدريان قصار")


def make():
    spec = importlib.util.spec_from_file_location("four_names_make", HERE / "make.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FourNamesActsTest(unittest.TestCase):
    def test_case_json_is_what_make_writes(self):
        before = CASE.read_bytes()
        make().main()  # rewrites case.json in place; it must not change
        self.assertEqual(CASE.read_bytes(), before)
        self.assertEqual(len(json.loads(before)["claims"]), 422)

    def test_acts_keep_the_decision_for_a_person(self):
        acts = json.loads(CASE.read_text())["acts"]
        self.assertEqual(len(acts), 9)
        steps = [
            [s if isinstance(s, str) else s["do"] for s in a["steps"]] for a in acts
        ]
        self.assertEqual(steps[5], ["agent"])  # the linker proposes, nothing else
        self.assertEqual(steps[6], ["links", "decline"])
        self.assertEqual(steps[8], ["accept", "package"])


@unittest.skipUnless(
    POSTGRES, "case seeding needs Postgres (set SQLALCHEMY_DATABASE_URI)"
)
class FourNamesSeedTest(Seeding):
    def setUp(self):
        super().setUp()
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        cert, key = make_signing_cert(tmp / "cert")
        self.app.config |= {"C2PA_SIGN_CERT": cert, "C2PA_SIGN_KEY": key}
        self.saved = tmp
        self.enterContext(mock.patch("sealbook.monitor.study.package.ROOT", tmp))
        fake = FakeAnchor()
        for name in ("anchored", "proofs"):
            self.enterContext(
                mock.patch(f"sealbook.anchor.service.{name}", getattr(fake, name))
            )

        async def no_network():
            return []

        self.enterContext(
            mock.patch("sealbook.monitor.study.package.anchor", no_network)
        )

    async def subjects(self, s, names) -> set[str]:
        return set(
            await s.scalars(
                select(Statement.subject)
                .join(Payload, Payload.commitment == Statement.commitment)
                .where(
                    Statement.prop == Prop.NAME, Payload.value["text"].astext.in_(names)
                )
            )
        )

    async def links(self, s) -> dict:
        q = (
            select(StatementTier.tier, func.count())
            .join(Statement, Statement.id == StatementTier.statement_id)
            .where(Statement.prop == Prop.SAME_AS)
            .group_by(StatementTier.tier)
        )
        return dict((await s.execute(q)).all())

    async def test_whole_case_ends_in_a_verified_package(self):
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, CASE)
        self.assertEqual(out["sources"], 14)
        # Nine sheets mapped, 1,445 rows released, 225 of them naming someone; every
        # planted sheet claim lands in its cell.
        self.assertEqual(
            out["sheets"],
            {
                "datasets": 9,
                "rows": 1445,
                "released": 1445,
                "named": 225,
                "claims": 400,
            },
        )
        # The scans, read by the app: 17 of the 22 planted claims. Missed: four of
        # the five "signed in person at" places and the passport's "M" (written, no
        # norm). The vendor form's place arrives since extraction asks where a
        # person was (2026-11-22).
        self.assertEqual(out["read"], {"planted": 22, "recovered": 17, "written": 19})
        self.assertNotIn("recognised", out)  # read in natural order, no step
        self.assertEqual(
            sorted(out["missed"]),
            sorted(
                ["agreement/consultant.seen_at"]
                + [
                    f"{r}.seen_at"
                    for r in ("guarantee_deed/guarantor", "licence/holder")
                ]
                + ["passport/holder.seen_at", "passport/holder.sex"]
            ),
        )
        self.assertEqual(out["claims"], 417)  # 416 before the vendor form's place
        self.assertEqual(out["linker proposals"], 7)
        self.assertEqual(out["declined"], 1)
        got = out["package"]
        self.assertEqual(got["sentences"], 5)
        self.assertEqual(got["problems"], [])
        # No sentence cites the passport copy, the one sensitive source, and the
        # links the conclusion rests on carry no file: the package reaches 8
        # ordinary files and ships them with their 4 OCR texts. A sensitive file
        # would be withheld unless its builder signed to include it.
        self.assertEqual(got["withheld"], [])
        self.assertEqual(got["files shipped"], 12)
        self.assertEqual(got["statements"], 54)
        self.assertEqual(got["c2pa"], "Valid")
        self.assertFalse(got["c2pa signer trusted"])  # the demo authority
        self.assertTrue((self.saved / got["saved"]).exists())
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            # Desks' five joins and the linker's six true links, accepted; the
            # surveyor's link declined with the analyst's reason.
            self.assertEqual(await self.links(s), {Tier.ACCEPTED: 11, Tier.REJECTED: 1})
            reason = await s.scalar(
                select(Payload.value["reason"].astext)
                .join(Statement, Statement.commitment == Payload.commitment)
                .where(
                    Statement.prop == Prop.REVIEW,
                    Payload.value["verdict"].astext == "reject",
                )
            )
            self.assertIn("19 August 1974 and Torvane", reason)
            case = await read.case(s, "case-four-names")
            man = {
                case.groups.get(people.pid(x), people.pid(x))
                for x in await self.subjects(s, MAN)
            }
            self.assertEqual(len(man), 1)
            # Sheet rows are written by extract_sheet, answers fixed and labelled.
            note = await s.scalar(
                select(Run.inputs["demo"].astext).where(Run.tool == "extract_sheet")
            )
            self.assertIn("no model ran", note)
            self.assertEqual(await s.scalar(select(func.count(DatasetVersion.id))), 9)
            c = await self.concluded(s, "case-four-names")
            self.assertEqual(len(c["rests_on"]), 11)
            package = await s.scalar(select(Package))
            self.assertIsNotNone(package.finalized_at)

    async def test_act_6_leaves_the_proposals_open(self):
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, CASE, until=6)
        self.assertEqual(out["linker proposals"], 7)
        self.assertNotIn("declined", out)
        async with ext.async_session_factory() as s:
            self.assertEqual(
                await self.links(s), {Tier.ACCEPTED: 5, Tier.UNCONFIRMED: 7}
            )
            got = await conclusions.listing(s, "case-four-names")
            self.assertEqual(got["status"]["status"], "open")

    async def test_act_1_is_procurement_alone(self):
        async with ext.async_session_factory() as s:
            out = await seed_study(s, self.app, CASE, until=1)
        self.assertEqual(out["sources"], 4)
        self.assertEqual(out["sheets"]["datasets"], 3)
        self.assertEqual(out["read"]["recovered"], 5)
        async with ext.async_session_factory() as s:
            self.assertEqual(await self.links(s), {Tier.ACCEPTED: 1})
