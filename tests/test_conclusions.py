"""Conclusions on a case: the signed write path, status changes, the rule that a
conclusion whose citations fall no longer stands, who may do what, erasure, the
drafter's fixed run and the package opening. Synthetic case on a throwaway Postgres."""

import re
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from unittest import mock

import stk
import stk.app  # noqa: F401 (registers the CLI before the seed module loads)
import stk.extensions as ext
from alembic import command
from quart_security import hash_password
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import build_alembic_config, throwaway_database_url
from stk.user.models import Role, User

from sealbook import blobs
from sealbook.agents import conclude, read
from sealbook.app import create_app
from sealbook.cases import conclusions
from sealbook.cases.service import ensure
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Blob, Payload, Run, Statement, StatementTier
from sealbook.ledger.write import (
    principal_for_user,
    record_acquisition,
    verify_db,
)
from sealbook.monitor.seed import Seeder
from sealbook.packages import service as packages
from sealbook.packages.models import Brief, Sentence
from sealbook.people.data import erase
from sealbook.runs.worker import process
from sealbook.settings import Config
from tests.passkey import SoftPasskey

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
CASE = "case-conclusions"
TEXTS = {
    "vendors": "Ari Example born 1990, son of Bel.",
    "consultants": "A. Example born 1990, son of Bel.",
    "surveyors": "Ari Example born 1991, son of Cor.",
}


@unittest.skipUnless(
    POSTGRES, "conclusions need Postgres (set SQLALCHEMY_DATABASE_URI)"
)
class ConclusionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict("stk.websocket._clients", {}, clear=True))
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        self.enterContext(mock.patch.object(Config, "AGENT_ROUTE", "local"))
        self.enterContext(mock.patch.object(Config, "AGENT_MODEL", read.FIXED))
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        command.upgrade(build_alembic_config("sealbook", url, quiet=True), "heads")
        db_url = (
            make_url(url)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False)
        )

        class TestConfig(stk.TestingConfig, Config):
            SQLALCHEMY_DATABASE_URI = db_url
            TESTING = True
            SESSION_TYPE = None
            STK_ENV = "testing"
            STK_ENABLE_AGENT_LOGIN = True
            STK_AGENT_LOGIN_MAX_TTL_SECONDS = 60
            SECURITY_WAN_RP_ID = None
            SECURITY_WAN_EXPECTED_ORIGIN = None

        self.app = create_app(TestConfig)
        blob_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(Config, "BLOB_DIR", blob_dir))

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def asyncSetUp(self):
        self.keys = {}
        async with ext.async_session_factory() as s:
            analyst = await s.scalar(select(Role).where(Role.name == "analyst"))
            self.users = {}
            for name in ("author", "peer", "other", "outsider"):
                user = User(
                    email=f"{name}@example.com",
                    name=name,
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                if name != "outsider":
                    user.roles.append(analyst)
                s.add(user)
                await s.flush()
                self.keys[name] = SoftPasskey()
                s.add(self.keys[name].register(user))
                self.users[name] = user.id
            me = await principal_for_user(s, self.users["author"], "author")
            self.author = me.id
            await ensure(s, CASE, me.id)
            seed = self.seed = Seeder(s)
            for key, body in TEXTS.items():
                blob = await blobs.put(s, body.encode(), "text/plain", Config.BLOB_DIR)
                await record_acquisition(
                    s,
                    blob_sha256=blob.sha256,
                    origin=f"channel:synthetic/{key}",
                    collector_id=me.id,
                    at=datetime.now(UTC),
                    context={"investigation": CASE, "title": key.title()},
                    sensitivity="ordinary",
                    doc_type="register",
                )
                seed.blobs[key], seed.texts[key] = blob.sha256, body
            extractor = await seed.principal("Synthetic extractor")
            linker = await seed.principal("Synthetic linker")
            self.human = await seed.analyst("clerk", CASE)
            self.person = {k: f"person:{uuid.uuid4()}" for k in TEXTS}
            self.claims = {}
            for doc, body in TEXTS.items():
                run = await seed.run(extractor, "extract", [doc])
                name, year = body.split(" born ")[0], body.split(" born ")[1][:4]
                for prop, value in ((Prop.NAME, name), (Prop.BIRTH_DATE, year)):
                    st = await seed.say(
                        run,
                        doc,
                        self.person[doc],
                        prop,
                        {"text": value, "lang": "en"},
                        value,
                    )
                    self.claims[(doc, prop)] = st
                await seed.done(run, 2)
            await seed.review(
                self.human, self.claims[("vendors", Prop.NAME)], "accept", "read it"
            )
            run = await seed.run(linker, "link", list(TEXTS))
            self.link = await seed.say(
                run,
                "consultants",
                self.person["vendors"],
                Prop.SAME_AS,
                {"person": self.person["consultants"].removeprefix("person:")},
            )
            self.open_link = await seed.say(
                run,
                "surveyors",
                self.person["consultants"],
                Prop.SAME_AS,
                {"person": self.person["surveyors"].removeprefix("person:")},
            )
            await seed.done(run, 2)
            self.link_review = await seed.review(self.human, self.link, "accept", "ok")
            await s.commit()

    # --- helpers ---------------------------------------------------------------

    async def client(self, who="author"):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(f"{who}@example.com", "/desk")
        await client.get(f"/_test/login?token={token}")
        page = await (await client.get("/desk")).get_data(as_text=True)
        client.csrf = {
            "X-CSRFToken": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        }
        client.who = who
        return client

    async def post(self, client, path, item):
        r = await client.post(path, json={"item": item}, headers=client.csrf)
        return r.status_code, await r.get_json()

    async def sign(self, client, path, item):
        status, body = await self.post(client, path, item)
        if status != 200:
            return status, body
        return await self.post(
            client,
            "/api/review/commit",
            {
                "hash": body["hash"],
                "credential": self.keys[client.who].assert_(body["hash"]),
            },
        )

    def form(self, **kw):
        return {
            "investigation": CASE,
            "text": "The vendor and the consultant are one man.",
            "standard": "reasonable_grounds",
            "rests_on": [str(self.link.id)],
            "alternatives": [
                {"text": "The surveyor is the same man", "why": "born a year later"}
            ],
            "not_established": ["Who approved the payments"],
        } | kw

    async def write(self, client=None, **kw):
        client = client or await self.client()
        status, out = await self.sign(
            client, "/api/review/conclusions/options", self.form(**kw)
        )
        self.assertEqual(status, 200, out)
        return out

    async def listing(self, client=None):
        client = client or await self.client()
        r = await client.get(f"/api/cases/conclusions?investigation={CASE}")
        self.assertEqual(r.status_code, 200)
        return await r.get_json()

    # --- write path --------------------------------------------------------------

    async def test_conclusion_and_status_signed_in_one_gesture(self):
        out = await self.write()
        props = {s["prop"] for s in out["statements"]}
        self.assertEqual(props, {Prop.CONCLUSION, Prop.CASE_STATUS})
        async with ext.async_session_factory() as s:
            rows = (
                await s.execute(
                    select(Statement, StatementTier.tier, Payload.value)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .where(Statement.subject == f"case:{CASE}")
                    .where(Statement.prop != Prop.ACCESS)
                )
            ).all()
            self.assertEqual({t for _, t, _ in rows}, {Tier.ACCEPTED})
            self.assertTrue(all(st.sig["kind"] == "webauthn" for st, _, _ in rows))
            self.assertEqual(len({st.sig["challenge"] for st, _, _ in rows}), 1)
            [value] = [v for st, _, v in rows if st.prop == Prop.CONCLUSION]
            self.assertEqual(value["standard_label"], "Reasonable grounds to believe")
            self.assertEqual(value["rests_on"], [str(self.link.id)])
            self.assertEqual(
                set(value["people"]),
                {self.person["vendors"], self.person["consultants"]},
            )
            self.assertEqual(await verify_db(s), [])
        got = await self.listing()
        self.assertEqual(got["status"]["status"], "concluded")
        [c] = got["conclusions"]
        self.assertTrue(c["stands"])
        self.assertEqual(c["not_established"], ["Who approved the payments"])
        self.assertEqual(c["standard"]["label"], "Reasonable grounds to believe")
        self.assertTrue(c["standard"]["meaning"])
        self.assertEqual([x["key"] for x in got["scale"]][0], "reasonable_suspicion")
        # The read is on the custody record.
        async with ext.async_session_factory() as s:
            logged = await s.scalar(
                select(Payload.value)
                .join(Statement, Statement.commitment == Payload.commitment)
                .where(
                    Statement.subject == f"case:{CASE}", Statement.prop == Prop.ACCESS
                )
            )
        self.assertIn(c["id"], logged["statements"])

    async def test_refusals(self):
        client = await self.client()
        open_claim = str(self.claims[("surveyors", Prop.BIRTH_DATE)].id)
        for bad, words in (
            ({"rests_on": [open_claim]}, "not an accepted"),
            ({"rests_on": [str(self.open_link.id)]}, "not an accepted"),
            ({"standard": "beyond doubt"}, "confidence level"),
            ({"alternatives": []}, "alternative"),
            ({"text": "two\nsentences"}, "one sentence"),
        ):
            status, body = await self.post(
                client, "/api/review/conclusions/options", self.form(**bad)
            )
            self.assertEqual(status, 400, bad)
            self.assertIn(words, body["error"])
        status, _ = await self.post(
            client,
            "/api/review/conclusions/options",
            self.form(investigation="case-nobody"),
        )
        self.assertEqual(status, 404)

    async def test_accepted_claims_links_and_findings_are_citeable(self):
        client = await self.client()
        r = await client.get(f"/api/cases/conclusions/citeable?investigation={CASE}")
        got = await r.get_json()
        self.assertEqual(
            [c["id"] for c in got["claims"]],
            [str(self.claims[("vendors", Prop.NAME)].id)],
        )
        [link] = got["links"]
        self.assertEqual(link["id"], str(self.link.id))
        self.assertIn("Ari Example\u2069 (Vendors)", link["text"])
        self.assertTrue(link["text"].endswith("are the same person"))

    # --- status ------------------------------------------------------------------

    async def test_close_reopen_and_conclude_are_statements(self):
        client = await self.client()
        st, body = await self.post(
            client,
            "/api/review/conclusions/status/options",
            {"investigation": CASE, "status": "closed", "reason": ""},
        )
        self.assertEqual(st, 400, body)
        st, body = await self.post(
            client,
            "/api/review/conclusions/status/options",
            {"investigation": CASE, "status": "concluded", "reason": "x"},
        )
        self.assertEqual(st, 400)
        st, body = await self.post(
            client,
            "/api/review/conclusions/status/options",
            {"investigation": CASE, "status": "open", "reason": "x"},
        )
        self.assertIn("already open", body["error"])
        st, _ = await self.sign(
            client,
            "/api/review/conclusions/status/options",
            {"investigation": CASE, "status": "closed", "reason": "No lead left"},
        )
        self.assertEqual(st, 200)
        self.assertEqual((await self.listing())["status"]["reason"], "No lead left")
        await self.sign(
            client,
            "/api/review/conclusions/status/options",
            {"investigation": CASE, "status": "open", "reason": "New file arrived"},
        )
        await self.write(client)
        got = await self.listing()
        self.assertEqual(
            [h["status"] for h in got["history"]], ["closed", "open", "concluded"]
        )
        # Reopening a concluded case keeps the conclusion on record.
        await self.sign(
            client,
            "/api/review/conclusions/status/options",
            {"investigation": CASE, "status": "open", "reason": "Second look"},
        )
        got = await self.listing()
        self.assertEqual(got["status"]["status"], "open")
        self.assertEqual(len(got["conclusions"]), 1)
        # A second conclusion on an open case concludes it again; a revision
        # replaces the first.
        first = got["conclusions"][0]["id"]
        await self.write(client, text="One man used both names.", revises=first)
        got = await self.listing()
        self.assertEqual(got["status"]["status"], "concluded")
        self.assertEqual(
            [c["text"] for c in got["conclusions"]], ["One man used both names."]
        )
        self.assertEqual(got["earlier"], 1)

    # --- stale citations ---------------------------------------------------------

    async def test_conclusion_no_longer_stands_when_its_link_is_undone(self):
        await self.write()
        async with ext.async_session_factory() as s:
            seed = Seeder(s)
            seed.passkey, seed.credential_id, seed.cose = (
                self.seed.passkey,
                self.seed.credential_id,
                self.seed.cose,
            )
            await seed.review(
                self.human, self.link_review, None, "undo", Prop.RETRACTION
            )
            await s.commit()
        [c] = (await self.listing())["conclusions"]
        self.assertFalse(c["stands"])
        self.assertEqual(c["broken"][0]["state"], "unconfirmed")
        self.assertEqual(c["broken"][0]["words"], "no longer accepted")

    async def test_cited_states(self):
        claim = self.claims[("vendors", Prop.NAME)]
        async with ext.async_session_factory() as s:
            seed = Seeder(s)
            seed.passkey, seed.credential_id, seed.cose = (
                self.seed.passkey,
                self.seed.credential_id,
                self.seed.cose,
            )
            ids = [str(claim.id), str(self.link.id), str(uuid.uuid4())]
            self.assertEqual(
                await conclusions.cited(s, ids),
                dict(zip(ids, ["stands", "stands", "missing"], strict=True)),
            )
            await seed.review(self.human, self.link, "reject", "two people")
            await seed.review(self.human, claim, None, "wrong", Prop.RETRACTION)
            await s.flush()
            got = await conclusions.cited(s, ids[:2])
            self.assertEqual(got, {ids[0]: "retracted", ids[1]: "rejected"})
            await s.execute(
                delete(Payload).where(Payload.commitment == claim.commitment)
            )
            self.assertEqual((await conclusions.cited(s, ids[:1]))[ids[0]], "erased")
            await s.rollback()

    async def test_erasing_a_person_erases_the_conclusion_about_them(self):
        await self.write()
        async with ext.async_session_factory() as s:
            await erase(s, self.person["vendors"], self.author, "subject asked")
            await s.commit()
        [c] = (await self.listing())["conclusions"]
        self.assertEqual(c["state"], "erased")
        self.assertNotIn("text", c)
        self.assertFalse(c["stands"])

    # --- permissions -------------------------------------------------------------

    async def test_peer_review_withdrawal_and_who_may_do_them(self):
        author = await self.client()
        await self.write(author)
        [c] = (await self.listing(author))["conclusions"]
        outsider = await self.client("outsider")
        r = await outsider.get(f"/api/cases/conclusions?investigation={CASE}")
        self.assertEqual(r.status_code, 403)
        st, _ = await self.post(
            outsider, "/api/review/conclusions/options", self.form()
        )
        self.assertEqual(st, 403)
        st, body = await self.post(
            author,
            "/api/collaboration/requests",
            {"statement": c["id"], "reviewer": self.users["peer"], "reason": "Check"},
        )
        self.assertEqual(st, 201, body)
        st, body = await self.post(
            author, "/api/review/conclusions/peer/options", {"conclusion": c["id"]}
        )
        self.assertEqual(st, 403, body)
        other = await self.client("other")
        st, _ = await self.post(
            other, "/api/review/conclusions/peer/options", {"conclusion": c["id"]}
        )
        self.assertEqual(st, 403)
        peer = await self.client("peer")
        todo = await (await peer.get("/api/todo")).get_json()
        self.assertEqual([a["conclusion"] for a in todo["asked"]], [c["id"]])
        # Asked of them in this case, and in no other.
        for case, want in ((CASE, [c["id"]]), ("case-nobody", [])):
            todo = await (await peer.get(f"/api/todo?investigation={case}")).get_json()
            self.assertEqual([a["conclusion"] for a in todo["asked"]], want, case)
        st, _ = await self.sign(
            peer,
            "/api/review/conclusions/peer/options",
            {"conclusion": c["id"], "note": "Agree, the birth dates match"},
        )
        self.assertEqual(st, 200)
        [c] = (await self.listing(author))["conclusions"]
        self.assertEqual(c["peer"]["state"], "agreed")
        self.assertEqual(c["peer"]["by"], "peer")
        st, body = await self.post(
            other,
            "/api/review/conclusions/withdraw/options",
            {"conclusion": c["id"], "reason": "x"},
        )
        self.assertEqual(st, 403, body)
        st, _ = await self.sign(
            author,
            "/api/review/conclusions/withdraw/options",
            {"conclusion": c["id"], "reason": "The link was wrong"},
        )
        self.assertEqual(st, 200)
        got = await self.listing(author)
        self.assertEqual(got["conclusions"], [])
        self.assertEqual(got["status"]["status"], "open")
        self.assertIn("The link was wrong", got["status"]["reason"])
        r = await author.get(f"/api/cases/conclusions/{c['id']}")
        detail = await r.get_json()
        self.assertEqual(detail["conclusion"]["state"], "withdrawn")

    async def test_detail_names_what_it_rests_on(self):
        await self.write()
        [c] = (await self.listing())["conclusions"]
        client = await self.client()
        detail = await (
            await client.get(f"/api/cases/conclusions/{c['id']}")
        ).get_json()
        [item] = detail["items"]
        self.assertEqual((item["kind"], item["state"]), ("link", "stands"))
        self.assertEqual([v["id"] for v in detail["versions"]], [c["id"]])

    # --- the drafter -------------------------------------------------------------

    async def test_fixed_draft_proposes_and_argues_against_without_writing(self):
        async with ext.async_session_factory() as s:
            run = await conclude.queue(s, CASE, self.users["author"], read.FIXED)
            rid = run.id
            await s.commit()
        await process(ext.async_session_factory, rid)
        async with ext.async_session_factory() as s:
            run = await s.get(Run, rid)
            self.assertEqual(run.status, "done", run.error)
            out = read.read_output(await s.get(Blob, run.output_hash))
            written = list(
                await s.scalars(select(Statement.prop).where(Statement.run_id == rid))
            )
        self.assertEqual(written, [Prop.ACCESS])
        self.assertEqual(run.model_served, read.FIXED)
        self.assertIn("demo", run.inputs)
        [item] = out["items"]
        self.assertEqual(item["kind"], "one person")
        self.assertEqual(item["basis"], [str(self.link.id)])
        self.assertIn("Ari Example", item["conclusion"])
        # The open proposal's record gives another birth year: it does not fit.
        [against] = item["against"]
        self.assertIn(
            str(self.claims[("surveyors", Prop.BIRTH_DATE)].id), against["claims"]
        )
        client = await self.client()
        shown = await (
            await client.get(f"/api/agents/conclusions?investigation={CASE}")
        ).get_json()
        [draft] = shown["items"]
        self.assertTrue(shown["model"]["demo"])
        self.assertEqual(draft["against"][0]["claims"][0]["field"], "Birth date")
        # Nothing was concluded on the drafter's word.
        self.assertEqual((await self.listing())["conclusions"], [])

    def test_validate_drops_uncited_handles_and_foreign_citations(self):
        h = read.Handles()
        basis = {"id": "L1", "text": "x", "same": True}
        cands = [
            {
                "key": "k1",
                "kind": "one person",
                "basis": [basis],
                "claims": [],
                "against": [],
            }
        ]
        h("c", "L1")
        h("c", "other")
        good = {
            "candidate": "k1",
            "basis": ["c1"],
            "conclusion": "One man.",
            "against": [],
        }
        items, dropped = conclude.validate(
            {
                "items": [
                    good,
                    good | {"candidate": "k9"},
                    good | {"basis": ["c2"]},
                    good | {"basis": []},
                    good | {"conclusion": "See c1."},
                ]
            },
            cands,
            h,
            [],
        )
        self.assertEqual([i["basis"] for i in items], [["L1"]])
        self.assertEqual((dropped["unknown"], dropped["handle"]), (3, 1))

    # --- packages ----------------------------------------------------------------

    async def test_package_opens_with_the_case_conclusion(self):
        await self.write()
        async with ext.async_session_factory() as s:
            brief = Brief(
                title="Brief", audience="court", author_id=self.users["author"]
            )
            s.add(brief)
            await s.flush()
            s.add(
                Sentence(
                    brief_id=brief.id,
                    ord=1,
                    text="The vendor is named Ari Example.",
                    statement_ids=[self.claims[("vendors", Prop.NAME)].id],
                )
            )
            await s.commit()
        async with ext.async_session_factory() as s:
            brief = await s.get(Brief, brief.id)
            opening = await packages.case_conclusions(s, brief)
            [case] = opening
            self.assertEqual(case["status"]["status"], "concluded")
            rows = await packages.collect(s, brief, opening)
            props = {st.prop for st in rows["statements"]}
            self.assertTrue({Prop.CONCLUSION, Prop.CASE_STATUS, Prop.SAME_AS} <= props)
            seed = Seeder(s)
            seed.passkey, seed.credential_id, seed.cose = (
                self.seed.passkey,
                self.seed.credential_id,
                self.seed.cose,
            )
            await seed.review(self.human, self.link, "reject", "two people")
            await s.flush()
            opening = await packages.case_conclusions(s, brief)
            with self.assertRaisesRegex(Exception, "no longer stands"):
                await packages.collect(s, brief, opening)
            await s.rollback()

    async def test_seed_plants_a_signed_conclusion(self):
        async with ext.async_session_factory() as s:
            seed = Seeder(s)
            human = await seed.analyst("seeded", CASE)
            rows = await conclusions.seed(
                s,
                CASE,
                {
                    "text": "One man.",
                    "standard": "balance_of_probabilities",
                    "rests_on": ["links"],
                    "alternatives": [{"text": "Two men", "why": "same birth date"}],
                    "not_established": [],
                },
                human.id,
                seed.assertion,
            )
            await s.commit()
            self.assertEqual(
                [r.prop for r in rows], [Prop.CONCLUSION, Prop.CASE_STATUS]
            )
            got = await conclusions.listing(s, CASE)
        self.assertTrue(got["conclusions"][0]["stands"])
        self.assertEqual(got["conclusions"][0]["rests_on"], [str(self.link.id)])
