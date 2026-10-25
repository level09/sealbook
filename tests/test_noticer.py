"""The Noticer: candidates from each source of resemblance, validation, cross-case
access, fresh suppression, the click to propose a link, and the Agents page."""

import os
import re
import unittest
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest import mock

import stk.app  # noqa: F401 (registers the CLI before the seed module loads)
import stk.extensions as ext
from jsonschema import Draft202012Validator
from quart_security import hash_password
from sqlalchemy import func, select
from stk.agent_login import create_agent_login_token
from stk.user.models import Role, User

from sealbook import blobs
from sealbook.agents import noticer, read, tasks
from sealbook.agents.models import AgentTask
from sealbook.cases.service import ensure
from sealbook.discovery.models import EmbeddingSpace, Segment, SegmentEmbedding
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Blob, Principal, Run, Statement, StatementTier
from sealbook.ledger.write import append, principal_for_user, record_acquisition
from sealbook.monitor.seed import Seeder
from sealbook.runs.worker import TOOLS, process
from sealbook.settings import Config
from tests.test_agents import claim
from tests.test_people import POSTGRES, _app

CASE = "case-noticer"
OTHER = "case-noticer-other"
HIDDEN = "case-noticer-hidden"
COPIED = (
    "The men were taken from the bakery at dawn and driven north in two white "
    "vans without plates, and nobody saw them again."
)


def doc(title, case=CASE, sensitive=False):
    return {"title": title, "doc_type": "report", "date": None, "sensitive": sensitive}


def seen(claims=(), docs=None, texts=None, near=None, copies=None, others=None):
    case = read.Case(CASE, list(claims), {}, {}, {})
    docs = docs or {}
    homes = dict.fromkeys(docs, CASE)
    for slug, other in (others or {}).items():
        for c in other.claims:
            homes[c.source] = slug
    return noticer.Seen(
        CASE,
        case,
        others or {},
        docs,
        texts or {},
        homes,
        near or {},
        copies or {},
        set(),
    )


class SharedWordingTest(unittest.TestCase):
    def test_longest_run_survives_folding_and_short_runs_do_not_count(self):
        a = "Before that. " + COPIED + " After."
        b = "In another account: " + COPIED.upper() + " Then he left."
        words, here, there = noticer.shared(a, b)
        self.assertEqual(words, len(COPIED.split()))
        self.assertEqual(here, COPIED.rstrip("."))
        self.assertEqual(there, COPIED.upper().rstrip("."))
        self.assertIsNone(noticer.shared("one two three four five six seven", COPIED))
        self.assertIsNone(
            noticer.shared(
                "taken from the bakery at dawn and driven",
                "taken from the bakery at night",
            )
        )

    def test_arabic_letter_forms_fold_together(self):
        a = "قالت إن الأمن أخذهم من الفرن عند الفجر إلى جهة مجهولة في سيارتين"
        b = "قال ان الامن اخذهم من الفرن عند الفجر الى جهة مجهولة في سيارتين بيضاوين"
        self.assertGreaterEqual(noticer.shared(a, b)[0], noticer.SHINGLE)


class CandidateTest(unittest.TestCase):
    def test_each_source_of_resemblance_becomes_a_ranked_signal(self):
        names = [
            claim(Prop.NAME, "Yousef Khaled Al-Ali", person="person:a", source="s1"),
            claim(Prop.NAME, "يوسف خالد العلي", person="person:b", source="s2"),
            claim(Prop.NAME, "Sami", person="person:c", source="s3"),
            claim(Prop.NAME, "Sami Haddad", person="person:d", source="s2"),
        ]
        born = [
            claim(Prop.BIRTH_DATE, "1991", person="person:a", source="s1"),
            claim(Prop.BIRTH_DATE, "1991", person="person:b", source="s2"),
        ]
        place = SimpleNamespace(id=uuid.uuid4(), name_en="Douma", name_ar="دوما")
        here_seen = replace(
            claim(Prop.SEEN_AT, "Douma", person="person:a", source="s1"),
            lo=date(2013, 5, 2),
            hi=date(2013, 5, 2),
        )
        there_name = claim(
            Prop.NAME, "Youssef Khalid Al Ali", person="person:x", source="o1"
        )
        there_seen = replace(
            claim(Prop.SEEN_AT, "دوما", person="person:x", source="o2"),
            lo=date(2013, 5, 1),
            hi=date(2013, 5, 31),
        )
        other = read.Case(
            OTHER, [there_name, there_seen], {}, {there_seen.id: place}, {}
        )
        docs = {s: doc(s) for s in ("s1", "s2", "s3", "s4", "s5", "s6", "o1", "o2")}
        g = seen(
            [*names, *born, here_seen],
            docs,
            texts={"s1": "Intro. " + COPIED, "s4": COPIED + " The end."},
            near={
                "s1": [
                    {
                        "row": ("text", {}, "s5", "text", {}, 0.1),
                        "mine": "a",
                        "theirs": "b",
                    },
                    {
                        "row": ("text", {}, "s6", "text", {}, 0.5),
                        "mine": "a",
                        "theirs": "c",
                    },
                ]
            },
            copies={"s1": [{"other": "s6", "kind": "Re-upload", "strength": "strong"}]},
            others={OTHER: other},
        )
        g.case.places[here_seen.id] = place
        cands = noticer.candidates(g, {"s1"})
        signals = {c["b"]: {s["kind"] for s in c["signals"]} for c in cands}
        self.assertEqual(signals["s5"], {"close"})  # (a) by meaning
        self.assertEqual(signals["s6"], {"picture"})  # (b) weak meaning dropped
        self.assertEqual(signals["s2"], {"name"})  # (c) the lone first name is not
        self.assertEqual(signals["o1"], {"person"})  # (d) person elsewhere
        self.assertEqual(signals["o2"], {"place"})  # (d) place elsewhere
        self.assertEqual(signals["s4"], {"wording"})  # (e)
        self.assertNotIn("s3", signals)
        s2 = next(c for c in cands if c["b"] == "s2")
        self.assertEqual(
            {c.prop for c in s2["claims"]["s1"]}, {Prop.NAME, Prop.BIRTH_DATE}
        )
        self.assertEqual(next(c for c in cands if c["b"] == "o1")["b_case"], OTHER)
        self.assertEqual(cands[0]["b"], "s6")  # the fingerprint ranks first
        self.assertIn(
            "overlapping",
            next(c for c in cands if c["b"] == "o2")["signals"][0]["text"],
        )

    def test_a_moderate_lead_alone_is_not_asked_and_known_people_are_skipped(self):
        a = claim(Prop.NAME, "Lina Omar", person="person:a", source="s1")
        b = claim(Prop.NAME, "لينا عمر", person="person:b", source="s2")
        g = seen(
            [a, b],
            {s: doc(s) for s in ("s1", "s2", "s3")},
            near={
                "s1": [
                    {
                        "row": ("text", {}, "s3", "text", {}, 0.3),
                        "mine": None,
                        "theirs": None,
                    }
                ]
            },
        )
        g.known = {("person", frozenset(("person:a", "person:b")))}
        self.assertEqual(noticer.candidates(g, {"s1"}), [])
        g.case.links = [("person:a", "person:b")]
        [cand] = noticer.candidates(g, {"s1"})
        self.assertEqual([s["kind"] for s in cand["signals"]], ["proposal"])


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.a = claim(Prop.NAME, "Ari Example", person="person:a", source="s1")
        self.born = claim(Prop.BIRTH_DATE, "1990", person="person:a", source="s1")
        self.b = claim(Prop.NAME, "Ari Exampel", person="person:b", source="s2")
        self.born_b = claim(Prop.BIRTH_DATE, "1990", person="person:b", source="s2")
        self.secret = claim(
            Prop.MOTHER_NAME, "Mira Secretname", sensitive=True, source="s9"
        )
        self.claims = [self.a, self.born, self.b, self.born_b, self.secret]
        self.g = seen(
            self.claims, {"s1": doc("First"), "s2": doc("Second"), "s9": doc("Third")}
        )
        self.cand = {
            "a": "s1",
            "b": "s2",
            "b_case": CASE,
            "key": "s1:s2:name",
            "signals": [{"kind": "name", "text": "Names that may be one person"}],
            "claims": {"s1": [self.a, self.born], "s2": [self.b, self.born_b]},
            "passages": {"s1": [], "s2": []},
        }
        self.h = read.Handles()
        self.p = noticer.payload([self.cand], self.h, self.g)

    def item(self, **kw):
        return {
            "h": "n1",
            "kind": "same person",
            "why": "First and Second give one name and one birth year.",
            "next": "Compare the two records.",
            "cite": ["c1", "c2", "c3", "c4"],
        } | kw

    def check(self, *items):
        return noticer.validate(
            {"items": list(items)}, [self.cand], self.h, self.claims
        )

    def test_payload_carries_handles_only_and_the_fixed_answer_passes(self):
        text = str(self.p)
        self.assertNotIn(str(self.a.id), text)
        Draft202012Validator(noticer.SCHEMA).validate(noticer.fixed_answer(self.p))
        items, screened, dropped = noticer.validate(
            noticer.fixed_answer(self.p), [self.cand], self.h, self.claims
        )
        self.assertEqual((len(items), screened, sum(dropped.values())), (1, [], 0))

    def test_both_sides_known_handles_plain_text_and_no_leaks(self):
        cases = {
            "one_sided": self.item(cite=["c1", "c2"]),
            "unknown": self.item(h="n9"),
            "text": self.item(why="c1 matches c3."),
            "leak": self.item(why="Ask Mira Secretname."),
            "empty": self.item(next=" "),
        }
        for reason, bad in cases.items():
            with self.subTest(reason=reason):
                items, _, dropped = self.check(bad)
                self.assertEqual((items, dropped[reason]), ([], 1))
        items, _, dropped = self.check(self.item(cite=["c1", "c9"]))
        self.assertEqual((items, dropped["unknown"]), ([], 1))
        dashed = self.item(why="One record — two names.")
        self.assertEqual(self.check(dashed)[2]["text"], 1)

    def test_not_related_is_screened_and_links_take_the_linkers_shapes(self):
        items, screened, _ = self.check(self.item(kind="not related"))
        self.assertEqual((items, screened), ([], ["s1:s2:name"]))
        [item], _, _ = self.check(self.item())
        self.assertEqual(
            item["link"], {"shape": "same_person", "a": "person:a", "b": "person:b"}
        )
        [names], _, _ = self.check(self.item(cite=["s1", "c1", "c3"]))
        self.assertIsNone(names["link"])  # names alone never make one person
        [copied], _, _ = self.check(self.item(kind="copied wording", cite=["s1", "s2"]))
        self.assertEqual(copied["link"], {"shape": "document"})
        [place], _, _ = self.check(self.item(kind="related place or time"))
        self.assertIsNone(place["link"])


class RegistrationTest(unittest.TestCase):
    def test_tool_kind_and_words_are_registered(self):
        from sealbook.monitor.data import TOOL_WORDS

        self.assertIs(TOOLS["notice"], noticer.notice)
        self.assertEqual(tasks.KINDS["noticer"][:3], ("notice", "Noticer", True))
        self.assertIn("notice", TOOL_WORDS)
        self.assertIn("notice_link", TOOL_WORDS)


@unittest.skipUnless(
    POSTGRES, "the noticer needs Postgres (set SQLALCHEMY_DATABASE_URI)"
)
class NoticerRunTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        self.enterContext(mock.patch.object(Config, "AGENT_MODEL", read.FIXED))
        self.enterContext(mock.patch.dict(os.environ, {"ROUTE_LOCAL_BASE_URL": ""}))
        self.app = _app(self)

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def asyncSetUp(self):
        async with ext.async_session_factory() as s:
            role = await s.scalar(select(Role).where(Role.name == "analyst"))
            self.user = User(
                email="noticer@example.com",
                name="noticer",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            self.user.roles.append(role)
            s.add(self.user)
            await s.flush()
            me = await principal_for_user(s, self.user.id, self.user.name)
            await ensure(s, CASE, me.id)
            await ensure(s, OTHER, me.id)  # HIDDEN has acquisitions but no case record
            seed = Seeder(s)
            texts = {
                "first": f"Statement of Yousef Khaled Al-Ali, born 1990. {COPIED}",
                "second": f"Account of Youssef Khalid Al Ali, born 1990. {COPIED}",
                "arabic": "إفادة: يوسف خالد العلي، مواليد 1990، شوهد قرب الفرن.",
                "photo": "photo bytes one",
                "photo_copy": "photo bytes two",
                "elsewhere": "Register entry: Yousef Khaled Al-Ali, born 1990.",
                "hidden": "Hidden entry: Yousef Khaled Al-Ali, born 1990.",
            }
            home = {"elsewhere": OTHER, "hidden": HIDDEN}
            for key, body in texts.items():
                blob = await blobs.put(s, body.encode(), "text/plain", Config.BLOB_DIR)
                await record_acquisition(
                    s,
                    blob_sha256=blob.sha256,
                    origin=f"channel:synthetic/{key}",
                    collector_id=me.id,
                    at=datetime.now(UTC),
                    context={
                        "investigation": home.get(key, CASE),
                        "title": key.title(),
                    },
                    sensitivity="ordinary",
                    doc_type="report",
                )
                seed.blobs[key], seed.texts[key] = blob.sha256, body
            self.sha = seed.blobs
            extractor = await seed.principal("Synthetic extractor")
            people = {}
            for key, name, year in (
                ("first", "Yousef Khaled Al-Ali", "1990"),
                ("second", "Youssef Khalid Al Ali", "1990"),
                ("arabic", "يوسف خالد العلي", "1990"),
                ("elsewhere", "Yousef Khaled Al-Ali", "1990"),
                ("hidden", "Yousef Khaled Al-Ali", "1990"),
            ):
                run = await seed.run(extractor, "extract", [key])
                person = people[key] = f"person:{uuid.uuid4()}"
                await seed.say(run, key, person, Prop.NAME, {"text": name}, name)
                await seed.say(run, key, person, Prop.BIRTH_DATE, {"text": year}, year)
                await seed.done(run, 2)
            self.people = people
            # (a) the same vector for two passages; (b) a fingerprint match.
            space = EmbeddingSpace(
                key="test",
                model_requested="test",
                model_served="test",
                dimensions=768,
                recipe={},
                reference={},
            )
            indexer = await seed.run(extractor, "embed", ["first"])
            s.add(space)
            await s.flush()
            for i, key in enumerate(("arabic", "photo", "first", "second")):
                seg = Segment(
                    source_sha256=self.sha[key],
                    kind="text",
                    ordinal=0,
                    text_sha256=self.sha[key],
                    start=0,
                    end=len(texts[key]),
                    location={},
                    run_id=indexer.id,
                )
                s.add(seg)
                await s.flush()
                s.add(
                    SegmentEmbedding(
                        segment_id=seg.id,
                        space_id=space.id,
                        run_id=indexer.id,
                        # Two passages share a direction; the others point apart.
                        vector=[float(j == max(i - 1, 0)) for j in range(768)],
                    )
                )
            await append(
                s,
                StatementIn(
                    subject=f"blob:{self.sha['photo']}",
                    prop=Prop.MEDIA_MATCH,
                    value={
                        "blob": self.sha["photo_copy"],
                        "kind": "media_similarity",
                        "picture": {
                            "sample_pairs": [[0, 0]],
                            "query_coverage": [1, 1],
                            "max_distance": 10,
                            "query_ms": [0, 0],
                            "candidate_ms": [0, 0],
                        },
                        "sound": None,
                        "fingerprint": "synthetic",
                    },
                    author_id=extractor.id,
                    run_id=indexer.id,
                ),
                sign=lambda h: {
                    "kind": "ed25519",
                    "sig": indexer.key.sign(h.encode()).hex(),
                },
            )
            await seed.done(indexer, 0)
            await s.commit()

    async def invoke(self):
        async with ext.async_session_factory() as s:
            run = await noticer.queue(s, CASE, self.user.id, read.FIXED)
            rid = run.id
            await s.commit()
        await process(ext.async_session_factory, rid)
        async with ext.async_session_factory() as s:
            run = await s.get(Run, rid)
            self.assertEqual(run.status, "done", run.error)
            return run, read.read_output(await s.get(Blob, run.output_hash))

    async def login(self, client):
        async with self.app.app_context():
            token = create_agent_login_token(self.user.email, "/agents/")
        await client.get(f"/_test/login?token={token}")
        page = await (await client.get("/agents/")).get_data(as_text=True)
        return {
            "X-CSRFToken": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        }

    async def test_candidates_from_every_source_and_never_a_hidden_case(self):
        async with ext.async_session_factory() as s:
            focus = set(await noticer.sources(s, CASE))
            g = await noticer.gather(s, CASE, self.user.id, focus)
            cands = noticer.candidates(g, focus)
        self.assertEqual(list(g.others), [OTHER])
        found = {}
        for c in cands:
            for sig in c["signals"]:
                found.setdefault(sig["kind"], set()).add(frozenset((c["a"], c["b"])))
        sha = self.sha
        self.assertIn(frozenset((sha["arabic"], sha["photo"])), found["close"])  # (a)
        self.assertIn(
            frozenset((sha["photo"], sha["photo_copy"])), found["picture"]
        )  # (b)
        self.assertIn(frozenset((sha["first"], sha["arabic"])), found["name"])  # (c)
        self.assertIn(
            frozenset((sha["first"], sha["elsewhere"])), found["person"]
        )  # (d)
        self.assertIn(frozenset((sha["first"], sha["second"])), found["wording"])  # (e)
        self.assertNotIn(sha["hidden"], {x for c in cands for x in (c["a"], c["b"])})

        run, out = await self.invoke()
        self.assertTrue(out["items"])
        self.assertFalse(
            {sha["hidden"]} & {x for i in out["items"] for x in (i["here"], i["there"])}
        )
        async with ext.async_session_factory() as s:
            relations = await s.scalar(
                select(func.count()).where(
                    Statement.prop.in_((Prop.RELATION, Prop.SAME_AS)),
                    Statement.run_id == run.id,
                )
            )
        self.assertEqual(relations, 0)  # nothing is proposed without a click

        again_run, again = await self.invoke()
        self.assertEqual(again["items"], [])
        self.assertEqual(
            again["outcome"]["reason"],
            "Everything that resembles this evidence was already reported.",
        )

    async def test_a_case_the_person_loses_disappears_from_the_report(self):
        await self.invoke()
        client = self.app.test_client()
        await self.login(client)
        shown = await (
            await client.get("/api/agents/notice", query_string={"investigation": CASE})
        ).get_json()
        self.assertTrue(
            any((i.get("there") or {}).get("case") == OTHER for i in shown["items"])
        )
        with mock.patch.object(
            tasks,
            "can_read",
            mock.AsyncMock(side_effect=lambda db, u, slug: slug != OTHER),
        ):
            shown = await (
                await client.get(
                    "/api/agents/notice", query_string={"investigation": CASE}
                )
            ).get_json()
        self.assertNotIn(OTHER, str(shown))

    async def test_click_proposes_one_link_signed_by_the_noticer(self):
        await self.invoke()
        client = self.app.test_client()
        headers = await self.login(client)
        shown = await (
            await client.get(
                "/api/agents/notice",
                query_string={"investigation": CASE, "sha": self.sha["second"]},
            )
        ).get_json()
        item = next(i for i in shown["items"] if i["kind"] == "Copied wording")
        self.assertEqual(item["link"], "can")
        body = {"item": {"run": item["run"], "key": item["key"]}}
        made = await client.post("/api/agents/notice/link", json=body, headers=headers)
        self.assertEqual(made.status_code, 201)
        statement = uuid.UUID((await made.get_json())["statement"])
        async with ext.async_session_factory() as s:
            st = await s.get(Statement, statement)
            author = await s.get(Principal, st.author_id)
            tier = await s.scalar(
                select(StatementTier.tier).where(StatementTier.statement_id == st.id)
            )
            proposal = await s.get(Run, st.run_id)
        self.assertEqual(
            (st.prop, author.name, tier), (Prop.RELATION, "Noticer", "unconfirmed")
        )
        self.assertEqual((proposal.tool, proposal.status), ("notice_link", "done"))
        again = await client.post("/api/agents/notice/link", json=body, headers=headers)
        self.assertEqual(again.status_code, 409)
        shown = await (
            await client.get("/api/agents/notice", query_string={"investigation": CASE})
        ).get_json()
        self.assertEqual(
            next(i for i in shown["items"] if i.get("key") == item["key"])["link"],
            "proposed",
        )

    async def test_agents_page_states(self):
        client = self.app.test_client()
        headers = await self.login(client)

        async def cards():
            data = await (
                await client.get(
                    "/api/agents/overview", query_string={"investigation": CASE}
                )
            ).get_json()
            return data, {a["key"]: a for a in data["agents"]}

        data, by = await cards()
        self.assertIn("ROUTE_LOCAL_BASE_URL", data["off"])
        self.assertEqual({a["state"] for a in data["agents"]}, {"off"})
        with mock.patch.dict(
            os.environ, {"ROUTE_LOCAL_BASE_URL": "http://127.0.0.1:9"}
        ):
            data, by = await cards()
            self.assertIsNone(data["off"])
            self.assertEqual(
                (by["brief"]["state"], by["noticer"]["state"]), ("watching", "ready")
            )
            await self.invoke()
            item = {"investigation": CASE, "kind": "noticer"}
            made = await client.post(
                "/api/agents/tasks", json={"item": item}, headers=headers
            )
            self.assertEqual(made.status_code, 201)
            data, by = await cards()
            self.assertEqual(by["noticer"]["state"], "watching")
            self.assertRegex(
                by["noticer"]["last"]["text"],
                r"^Found \d+ resemblances\. Demo output\.$",
            )
            self.assertEqual(data["recent"][0]["agent"], "Noticer")
            async with ext.async_session_factory() as s, s.begin():
                task = await s.get(AgentTask, uuid.UUID((await made.get_json())["id"]))
                task.paused_reason = "Checking stopped after three failures."
            data, by = await cards()
            self.assertEqual(
                (by["noticer"]["state"], by["noticer"]["note"]),
                ("paused", "Checking stopped after three failures."),
            )
        page = await (await client.get("/agents/")).get_data(as_text=True)
        self.assertIn("/mcp", page)
        self.assertIn("Outside assistants", page)

    async def test_a_sensitive_side_locks_the_item_until_revealed(self):
        await self.invoke()
        async with ext.async_session_factory() as s, s.begin():
            # One sensitive acquisition makes the source sensitive.
            await record_acquisition(
                s,
                blob_sha256=self.sha["second"],
                origin="channel:synthetic/second-again",
                collector_id=(await principal_for_user(s, self.user.id, "noticer")).id,
                at=datetime.now(UTC),
                context={"investigation": CASE, "title": "Second"},
                sensitivity="sensitive",
                doc_type="report",
            )
        client = self.app.test_client()
        await self.login(client)
        query = {"investigation": CASE, "sha": self.sha["second"]}
        shown = await (
            await client.get("/api/agents/notice", query_string=query)
        ).get_json()
        self.assertTrue(shown["items"])
        self.assertTrue(all(i.get("locked") for i in shown["items"]))
        self.assertNotIn("Youssef", str(shown))
        with mock.patch.object(noticer, "granted", return_value=True):
            shown = await (
                await client.get("/api/agents/notice", query_string=query)
            ).get_json()
        self.assertFalse(any(i.get("locked") for i in shown["items"]))
