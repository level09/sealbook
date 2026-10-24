"""Persons, search and erasure: acceptance tests 14 and 18 against a throwaway
Postgres database seeded with the synthetic demo case."""

import asyncio
import re
import tempfile
import unittest
import uuid
from datetime import date, datetime
from types import SimpleNamespace
from unittest import mock

import orjson
from quart_security import hash_password
from sqlalchemy import select
from sqlalchemy.engine import make_url

import stk.extensions as ext
from alembic import command
from stk.agent_login import create_agent_login_token
from stk.app import create_app
from stk.ledger.contracts import Prop, StatementIn
from stk.ledger.models import Blob, Payload, Statement
from stk.ledger.write import append, verify_db
from stk.mcp.tools import _search as mcp_search
from stk.migrations import (
    build_alembic_config,
    get_database_url,
    throwaway_database_url,
)
from stk.monitor.data import statement_rows
from stk.monitor.data import track as track_of
from stk.monitor.seed import seed
from stk.packages.models import Brief, Package, Sentence
from stk.people import data
from stk.settings import Config
from stk.user.models import Role, User
from tests.passkey import attester

POSTGRES = make_url(get_database_url()).get_backend_name() == "postgresql"
SPELLINGS = ("Yusuf Khaled al-Ali", "Youssef Khalid Al Ali", "يوسف خالد العلي")
SECRETS = ("يوسف", "Yusuf", "Yousef", "حرستا", "فاطمة", "حمص")


def _app(test):
    url = test.enterContext(throwaway_database_url(sync=True))
    config = build_alembic_config()
    config.config_file_name = None
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    test.enterContext(
        mock.patch.object(
            Config, "BLOB_DIR", test.enterContext(tempfile.TemporaryDirectory())
        )
    )
    db_url = (
        make_url(url)
        .set(drivername="postgresql+asyncpg")
        .render_as_string(hide_password=False)
    )

    class TestConfig(Config):
        SQLALCHEMY_DATABASE_URI = db_url
        TESTING = True
        SESSION_TYPE = None
        STK_ENV = "testing"
        STK_ENABLE_AGENT_LOGIN = True
        STK_AGENT_LOGIN_MAX_TTL_SECONDS = 60

    return create_app(TestConfig)


async def _case(app) -> dict:
    """The demo case plus a third mention of P1 (another spelling, a conflicting
    sighting), an accepted same_as joining it to P1, and a same_as wrongly joining
    the twin P2 that an accepted not_same blocks."""
    async with ext.async_session_factory() as s:
        case = await seed(s)
    async with ext.async_session_factory() as s, s.begin():
        analyst = (
            await s.execute(select(Role).where(Role.name == "analyst"))
        ).scalar_one()
        for email, roles in (
            ("analyst@example.com", [analyst]),
            ("nobody@example.com", []),
        ):
            user = User(
                email=email,
                name=email.split("@")[0],
                password=hash_password("TestPassword123!", app=app),
                active=True,
                confirmed_at=datetime.now(),
            )
            user.roles.extend(roles)
            s.add(user)
        human, passkey = await attester(s, "reviewer")
        p1, p2, p3 = case["P1"], case["P2"], f"person:{uuid.uuid4()}"

        async def say(subject, prop, value, valid=None):
            # A registered passkey's assertion: tier accepted.
            return await append(
                s,
                StatementIn(
                    subject=subject,
                    prop=prop,
                    value=value,
                    author_id=human.id,
                    valid_from=valid,
                    valid_to=valid,
                ),
                sign=passkey.sig,
            )

        await say(p3, Prop.NAME, {"text": "Yusuf Khalid al Ali", "lang": "en"})
        await say(p3, Prop.SEEN_AT, {"text": "حمص", "lang": "ar"}, date(2013, 7, 10))
        await say(p3, Prop.SAME_AS, {"person": data.pid(p1)})
        await say(p1, Prop.NOT_SAME, {"person": data.pid(p2)})
        await say(p2, Prop.SAME_AS, {"person": data.pid(p1)})
        case |= {"P3": p3, "human": human.id}
    return {k: data.pid(v) if k[0] == "P" else v for k, v in case.items()}


class ForksTest(unittest.TestCase):
    """Disagreements compare resolved places, never display text."""

    def _row(self, prop, text, day="2016-11-17"):
        return {
            "id": str(uuid.uuid4()),
            "prop": prop,
            "display": text,
            "from": day,
            "to": day,
            "inherited": False,
        }

    def _forks(self, rows, places):
        steps = track_of(
            [
                r | {"place": r["display"], "place_id": str(places[r["id"]].id)}
                for r in rows
            ]
        )["steps"]
        facts = {}
        for r in rows:
            facts.setdefault(r["prop"], []).append(r)
        links = {r["id"]: {"file": "list", "href": "/evidence/abc"} for r in rows}
        places = {uuid.UUID(i): p for i, p in places.items()}
        return data._forks(facts, steps, places, links)

    def test_two_spellings_of_one_place_do_not_disagree(self):
        aleppo = SimpleNamespace(id=uuid.uuid4())
        rows = [self._row("seen_at", "Aleppo"), self._row("seen_at", "حلب")]
        self.assertEqual(self._forks(rows, {r["id"]: aleppo for r in rows}), [])

    def test_two_places_on_one_day_disagree_once_with_sources(self):
        a, b = self._row("seen_at", "Aleppo"), self._row("seen_at", "Douma")
        found = self._forks(
            [a, b],
            {
                a["id"]: SimpleNamespace(id=uuid.uuid4()),
                b["id"]: SimpleNamespace(id=uuid.uuid4()),
            },
        )
        [f] = found
        self.assertEqual({x["value"] for x in f["sides"]}, {"Aleppo", "Douma"})
        self.assertEqual({x["href"] for x in f["sides"]}, {"/evidence/abc"})

    def test_names_compare_normalised(self):
        rows = [
            self._row("occupation", "Pharmacist"),
            self._row("occupation", "pharmacist "),
        ]
        facts = {"occupation": rows}
        self.assertEqual(data._forks(facts, [], {}, {}), [])


@unittest.skipUnless(POSTGRES, "people need Postgres (set SQLALCHEMY_DATABASE_URI)")
class PersonTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = _app(self)

    async def asyncSetUp(self):
        self.case = await _case(self.app)
        self.root = min(self.case["P1"], self.case["P3"])

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def test_three_spellings_open_one_person_and_the_twin_stays_apart(self):
        p1, p2, p3 = self.case["P1"], self.case["P2"], self.case["P3"]
        async with ext.async_session_factory() as s:
            for query in SPELLINGS:
                hits = {h["person"]: h for h in await data.search(s, query)}
                self.assertEqual(hits[self.root]["members"], 2, query)
                self.assertIn(p2, hits, query)
            self.assertEqual(await data.search(s, "Samer"), [])

            view = await data.person(s, f"person:{p3}")
            self.assertEqual(view["person"], self.root)
            self.assertEqual(view["members"], sorted([p1, p3]))
            self.assertEqual((await data.person(s, p1))["person"], self.root)
            self.assertEqual((await data.person(s, p2))["members"], [p2])

            steps = view["track"]["steps"]
            self.assertGreaterEqual(len(steps), 4)
            # Each disagreement once: P3's unmapped Homs sighting against P1's
            # branch as one seen_at pair. Last seen in Douma and arrested in
            # Harasta on one day is a sequence, not a disagreement.
            pairs = [frozenset(x["id"] for x in f["sides"]) for f in view["forks"]]
            self.assertEqual(len(pairs), len(set(pairs)))
            self.assertNotIn("position", {f["kind"] for f in view["forks"]})
            [homs] = [f for f in view["forks"] if f["from"] == "2013-07-10"]
            self.assertEqual(homs["prop"], "seen_at")
            self.assertIn("حمص", {x["value"] for x in homs["sides"]})
            self.assertTrue(view["gaps"])
            self.assertTrue(all(g["days"] > data.GAP_DAYS for g in view["gaps"]))

            # The wrong same_as is blocked by the not_same and surfaced, not applied.
            [conflict] = view["conflicts"]
            self.assertEqual(sorted(conflict["join"]), sorted([p1, p2]))
            [dup] = view["duplicates"]
            self.assertEqual(dup["person"], p2)
            self.assertTrue(dup["not_same"])
            self.assertIn("mother_name", dup["differ"])
            self.assertIn("birth_date", dup["differ"])
            # One line per candidate: each matching name once, however many of
            # this person's spellings it matched. The twin's names are all masked.
            [masked] = dup["names"]
            self.assertEqual((masked["name"], masked["count"]), (None, 1), dup)

            factual = await data.person(s, p1, "factual")
            tiers = {r["tier"] for rows in factual["facts"].values() for r in rows}
            self.assertLessEqual(tiers, {"accepted", "corroborated"})
            self.assertIn(
                "unconfirmed",
                {r["tier"] for rows in view["facts"].values() for r in rows},
            )
            with self.assertRaises(ValueError):
                await data.person(s, p1, "everything")
            self.assertIsNone(await data.person(s, uuid.uuid4()))

    async def test_roster_and_search_follow_the_investigation(self):
        async with ext.async_session_factory() as s:
            everyone = await data.roster(s)
            here = await data.roster(s, self.case["investigation"])
            other = await data.roster(s, "case-nowhere")
            found = await data.search(s, "Yusuf", "case-nowhere")
        self.assertEqual(set(here), set(everyone))
        self.assertEqual((other, found), ({}, []))

    async def test_the_roster_counts_what_the_person_page_lists(self):
        async with ext.async_session_factory() as s:
            everyone = await data.roster(s)
            for person, row in everyone.items():
                page = await data.person(s, uuid.UUID(person))
                facts = [r for rs in page["facts"].values() for r in rs]
                docs = {r["doc"]["sha256"] for r in facts if r["doc"]}
                self.assertEqual(
                    (row["facts"], row["documents"]), (len(facts), len(docs)), person
                )

    async def test_same_named_people_are_told_apart_by_a_fact_before_the_id(self):
        root, twin, ghost = self.root, self.case["P2"], str(uuid.uuid4())
        rows = [
            {"person": root, "name": "X", "from": "2013-01-01", "to": None},
            {"person": twin, "name": "X", "from": "2013-01-01", "to": None},
            {"person": ghost, "name": "X", "from": "2014-05-01", "to": "2014-06-01"},
            {"person": str(uuid.uuid4()), "name": "Only one", "from": None, "to": None},
        ]
        async with ext.async_session_factory() as s:
            told = await data.hints(s, rows)
        # Every list shows the dates, so the third already reads differently and
        # needs no hint. The twins share a case and their first source, so only the
        # id is left. A name nobody shares needs no hint.
        self.assertEqual(told, {root: f"record {root[:8]}", twin: f"record {twin[:8]}"})
        rows[1]["from"] = "2013-02-01"
        async with ext.async_session_factory() as s:
            self.assertEqual(await data.hints(s, rows), {})
        # Filtered to their own case, the case tells nothing; a case is named only
        # when it is another one.
        rows[1]["from"] = "2013-01-01"
        async with ext.async_session_factory() as s:
            told = await data.hints(s, rows, self.case["investigation"])
        self.assertNotIn(self.case["investigation"], " ".join(told.values()))

    async def test_a_label_is_the_best_name_the_reader_may_see(self):
        async with ext.async_session_factory() as s:
            ids = [self.root, self.case["P2"]]
            labels = await data.names(s, ids)
            rows = await statement_rows(
                s,
                Statement.subject.in_([f"person:{p}" for p in ids]),
                Statement.prop == Prop.NAME,
            )
        masked = {r["subject"] for r in rows if r["masked"]}
        # The root has masked spellings too, but a visible one wins; the twin has
        # only masked names, so it has no label and pages say "Unnamed person".
        self.assertEqual(masked, {f"person:{p}" for p in ids})
        self.assertEqual(labels, {self.root: "Yousef Khaled Al-Ali"})

    async def login(self, client, email):
        async with self.app.app_context():
            token = create_agent_login_token(email, "/people")
        self.assertEqual(
            (await client.get(f"/_test/login?token={token}")).status_code, 302
        )

    async def test_pages_require_the_role_and_a_view_is_an_access_statement(self):
        client = self.app.test_client()
        self.assertEqual((await client.get("/people")).status_code, 302)
        await self.login(client, "nobody@example.com")
        self.assertEqual((await client.get("/people")).status_code, 403)

        client = self.app.test_client()
        await self.login(client, "analyst@example.com")
        for path in ("/people", f"/people/{self.case['P3']}"):
            self.assertEqual((await client.get(path)).status_code, 200, path)
        r = await client.get("/api/people/search", query_string={"q": SPELLINGS[1]})
        self.assertIn(self.root, [h["person"] for h in (await r.get_json())["items"]])
        self.assertEqual((await client.get("/api/people/search")).status_code, 400)
        self.assertEqual(
            (await client.get(f"/api/people/{uuid.uuid4()}")).status_code, 404
        )

        r = await client.get(f"/api/people/{self.case['P3']}")
        self.assertEqual(r.status_code, 200)
        shown = [
            i for step in (await r.get_json())["track"]["steps"] for i in step["ids"]
        ]
        async with ext.async_session_factory() as s:
            [access] = (
                await s.execute(
                    select(Payload.value)
                    .join(Statement, Statement.commitment == Payload.commitment)
                    .where(
                        Statement.subject == f"person:{self.root}",
                        Statement.prop == Prop.ACCESS,
                    )
                )
            ).scalars()
        self.assertEqual(access["action"], "view")
        self.assertEqual(access["statements"], shown)

        everyone = (await (await client.get("/api/people")).get_json())["items"]
        mine = next(p for p in everyone if p["person"] == self.root)
        self.assertGreater(mine["facts"], 0)
        self.assertNotIn("text", str(mine))  # counts and dates, never values

        view = await (await client.get(f"/api/people/{self.case['P3']}")).get_json()
        masked = {
            r["id"] for rows in view["facts"].values() for r in rows if r["masked"]
        }
        self.assertTrue(masked)
        page = await (await client.get("/people")).get_data(as_text=True)
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        r = await client.post(
            f"/api/people/{self.case['P3']}/reveal",
            json={},
            headers={"X-CSRFToken": csrf},
        )
        values = (await r.get_json())["values"]
        self.assertEqual(set(values), masked)
        async with ext.async_session_factory() as s:
            reveals = [
                v
                for v in (
                    await s.execute(
                        select(Payload.value)
                        .join(Statement, Statement.commitment == Payload.commitment)
                        .where(Statement.prop == Prop.ACCESS)
                    )
                ).scalars()
                if v["action"] == "reveal"
            ]
        self.assertEqual([sorted(v["statements"]) for v in reveals], [sorted(masked)])


@unittest.skipUnless(POSTGRES, "erasure needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class ErasureTest(unittest.TestCase):
    def setUp(self):
        self.app = _app(self)

    def run_db(self, coro):
        async def go():
            try:
                return await coro
            finally:
                await ext.engine.dispose()

        return asyncio.run(go())

    def test_erased_person_is_gone_and_the_chain_still_verifies(self):
        case = self.run_db(_case(self.app))
        p1, p2, p3 = case["P1"], case["P2"], case["P3"]
        runner = self.app.test_cli_runner()
        from stk.people.cli import erase_cmd

        drafts = self.run_db(self.drafts(case))
        missing = runner.invoke(
            erase_cmd, [f"person:{p1}", "--reason", "x", "--by", "ghost@example.com"]
        )
        self.assertNotEqual(missing.exit_code, 0)
        result = runner.invoke(
            erase_cmd,
            [
                f"person:{p3}",
                "--reason",
                "family request",
                "--by",
                "analyst@example.com",
            ],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("values deleted", result.output)

        async def check():
            async with ext.async_session_factory() as s:
                self.assertEqual(await verify_db(s), [])
                for query in SPELLINGS:
                    found = {h["person"] for h in await data.search(s, query)}
                    self.assertFalse(found & {p1, p3}, query)
                    self.assertIn(p2, found, "the twin is not erased")
                for ref in (p1, p3):
                    view = await data.person(s, ref)
                    self.assertEqual(view["members"], [ref])
                    self.assertEqual(view["track"]["steps"], [])
                    dumped = orjson.dumps(view["facts"]).decode()
                    for secret in SECRETS:
                        self.assertNotIn(secret, dumped)
                self.assertTrue((await data.person(s, min(p1, p3)))["erasures"])
                values = (
                    await s.execute(
                        select(Statement.prop, Payload.value)
                        .join(Payload, Payload.commitment == Statement.commitment)
                        .where(Statement.subject.in_([f"person:{p1}", f"person:{p3}"]))
                    )
                ).all()
                self.assertEqual([prop for prop, _ in values], [Prop.ERASURE])
                self.assertEqual(values[0][1]["reason"], "family request")
                rows = await statement_rows(s, Statement.subject == f"person:{p1}")
                self.assertTrue(rows)
                self.assertTrue(
                    all(r["erased"] or r["prop"] == "erasure" for r in rows)
                )
                for query in ("حرستا", "Yousef", "يوسف خالد علي"):
                    hits = (await mcp_search(s, query, 50))["hits"]
                    self.assertFalse(
                        [
                            h
                            for h in hits
                            if h["subject"] in (f"person:{p1}", f"person:{p3}")
                        ],
                        query,
                    )

        self.run_db(check())

        async def copies():
            async with ext.async_session_factory() as s:
                left = (await s.execute(select(Sentence.text))).scalars().all()
                self.assertEqual(left, ["About the twin."])
                for pid, revoked in drafts.items():
                    package = await s.get(Package, pid)
                    self.assertEqual(package.revoked_at is not None, revoked)

        self.run_db(copies())

    async def drafts(self, case) -> dict:
        """A brief citing P1 and the twin P2, and a package disclosing each."""
        async with ext.async_session_factory() as s, s.begin():
            first = {}
            for p in ("P1", "P2"):
                first[p] = (
                    await s.execute(
                        select(Statement.id)
                        .where(Statement.subject == f"person:{case[p]}")
                        .limit(1)
                    )
                ).scalar_one()
            author = (
                await s.execute(select(User).where(User.email == "analyst@example.com"))
            ).scalar_one()
            brief = Brief(title="t", audience="court", author_id=author.id)
            s.add(brief)
            await s.flush()
            s.add(
                Sentence(
                    brief_id=brief.id,
                    ord=1,
                    text="About P1.",
                    statement_ids=[first["P1"]],
                )
            )
            s.add(
                Sentence(
                    brief_id=brief.id,
                    ord=2,
                    text="About the twin.",
                    statement_ids=[first["P2"]],
                )
            )
            made = {}
            for p, digit in (("P1", "a"), ("P2", "b")):
                sha = digit * 64
                s.add(Blob(sha256=sha, md5="0" * 32, size=0, mime="x", storage_ref=sha))
                await s.flush()
                package = Package(
                    brief_id=brief.id,
                    audience="court",
                    manifest_sha256=sha,
                    zip_sha256=sha,
                    statements=1,
                    statement_ids=[first[p]],
                    created_by=author.id,
                )
                s.add(package)
                await s.flush()
                made[package.id] = p == "P1"
            return made
