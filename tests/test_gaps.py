"""Gap rules without services, plus fixed-output runs on throwaway Postgres."""

import dataclasses
import re
import shutil
import subprocess
import unittest
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import orjson
from quart_security import hash_password
from sqlalchemy import select

import stk.app  # noqa: F401
import stk.extensions as ext
from stk import blobs
from stk.agent_login import create_agent_login_token
from stk.agents import gaps, read
from stk.agents.read import Claim, Handles
from stk.cases.service import ensure
from stk.ledger import write
from stk.ledger.contracts import Prop, Tier
from stk.ledger.models import Blob, Principal, Run, Statement
from stk.ledger.write import principal_for_user, record_acquisition, verify_db
from stk.monitor.seed import Seeder
from stk.places.models import Place
from stk.places.resolve import confirmation
from stk.runs.worker import process
from stk.settings import Config
from stk.user.models import Role, User
from tests.test_people import POSTGRES, _app

TODAY = date(2014, 1, 1)
CASE = "case-gap-test"


def claim(
    prop,
    lo=None,
    hi=None,
    person="person:a",
    source="ordinary",
    text="Branch 999",
    sensitive=False,
):
    return Claim(
        id=uuid.uuid4(),
        subject=person,
        person=person,
        prop=prop,
        text=text,
        lo=date.fromisoformat(lo) if lo else None,
        hi=date.fromisoformat(hi or lo) if lo else None,
        source=source,
        span_blob=None,
        span=None,
        tier=Tier.UNCONFIRMED,
        sensitive=sensitive,
    )


class GapRulesTest(unittest.TestCase):
    def test_strict_sixty_day_threshold(self):
        arrest = claim(Prop.ARRESTED_AT, "2013-01-01")
        for days, expected in ((59, 0), (60, 0), (61, 1)):
            sighting = dataclasses.replace(
                claim(Prop.SEEN_AT),
                lo=arrest.lo + timedelta(days=days),
                hi=arrest.lo + timedelta(days=days),
            )
            self.assertEqual(
                len(gaps.detect([sighting, arrest], today=TODAY)), expected
            )

    def test_open_end_after_last_seen(self):
        last = claim(Prop.LAST_SEEN, "2013-08-01")
        out = gaps.detect([last], today=TODAY)
        self.assertEqual(out[0]["window"], ["2013-08-01", None])
        self.assertEqual(out[0]["bounds"], [last])

    def test_release_closes_the_record_without_a_gap(self):
        arrest = claim(Prop.ARRESTED_AT, "2013-01-01")
        release = claim(Prop.EVENT, "2013-08-01", text="Released")
        self.assertEqual(
            gaps.detect([arrest, release], {release.id: "released"}, TODAY), []
        )

    def test_release_on_same_date_takes_precedence_over_detention(self):
        held = claim(Prop.DETAINED_AT, "2013-01-01")
        released = claim(Prop.EVENT, "2013-01-01")
        later = claim(Prop.SEEN_AT, "2013-08-01")
        self.assertEqual(
            gaps.detect([held, released, later], {released.id: "released"}, TODAY), []
        )

    def test_missing_event_starts_a_gap_and_later_sighting_ends_it(self):
        missing = claim(Prop.EVENT, "2013-01-01")
        seen = claim(Prop.SEEN_AT, "2013-08-01")
        out = gaps.detect([missing, seen], {missing.id: "missing"}, TODAY)
        self.assertEqual([g["window"] for g in out], [["2013-01-01", "2013-08-01"]])

    def test_year_only_uses_the_latest_possible_date(self):
        held = claim(Prop.DETAINED_AT, "2013-01-01", "2013-12-31")
        seen = claim(Prop.SEEN_AT, "2014-02-01")
        self.assertEqual(gaps.detect([held, seen], today=TODAY), [])
        self.assertEqual(read.when(held.lo, held.hi), "2013")

    def test_overlapping_ranges_do_not_invent_a_silence(self):
        year = claim(Prop.DETAINED_AT, "2013-01-01", "2013-12-31")
        short = claim(Prop.DETAINED_AT, "2013-02-01")
        seen = claim(Prop.SEEN_AT, "2014-01-02")
        self.assertEqual(gaps.detect([year, short, seen], today=TODAY), [])

    def test_gaps_are_ranked_by_length_and_people_stay_separate(self):
        a = claim(Prop.LAST_SEEN, "2013-08-01")
        b = claim(Prop.LAST_SEEN, "2013-01-01", person="person:b")
        self.assertEqual(
            [g["person"] for g in gaps.detect([a, b], today=TODAY)],
            ["person:b", "person:a"],
        )

    def test_fresh_uses_bounds_and_does_not_move_open_end_each_day(self):
        last = claim(Prop.LAST_SEEN, "2013-08-01")
        gap = gaps.detect([last], today=TODAY)[0]
        seen = {(gap["person"], *gap["window"])}
        self.assertFalse(gaps.fresh(gap, seen))
        self.assertFalse(
            gaps.fresh(gaps.detect([last], today=TODAY + timedelta(days=2))[0], seen)
        )
        later = claim(Prop.LAST_SEEN, "2013-09-01")
        self.assertTrue(gaps.fresh(gaps.detect([last, later], today=TODAY)[0], seen))


class GapValidationTest(unittest.TestCase):
    def setUp(self):
        self.arrest = claim(Prop.ARRESTED_AT, "2013-01-01")
        self.seen = claim(Prop.LAST_SEEN, "2013-06-01", source="later")
        self.lead = claim(
            Prop.DETAINED_AT, "2013-03-01", person="person:b", source="lead"
        )
        name = claim(Prop.NAME, text="Synthetic Person")
        self.claims = [self.arrest, self.seen, self.lead, name]
        place = SimpleNamespace(id=uuid.uuid4())
        self.case = read.Case(
            CASE,
            self.claims,
            {c.source: {"title": c.source.title()} for c in self.claims},
            {c.id: place for c in self.claims},
            {},
        )
        self.h = Handles()
        bounded = [g for g in gaps.detect(self.claims, today=TODAY) if g["window"][1]]
        self.cands = gaps.candidates(self.case, bounded, self.h)
        self.answer = gaps.fixed_answer(gaps.payload(self.cands))

    def validate(self, item):
        return gaps.validate({"items": [item]}, self.cands, self.h, self.claims)

    def test_fixed_answer_cites_bounds_and_source_lead_without_identifiers(self):
        out, dropped = self.validate(self.answer["items"][0])
        self.assertEqual(dropped, {})
        self.assertEqual(set(out[0]["claims"]), {str(c.id) for c in self.claims})
        self.assertIn("lead", out[0]["sources"])
        self.assertEqual(self.answer, gaps.fixed_answer(gaps.payload(self.cands)))
        sent = orjson.dumps(gaps.payload(self.cands)).decode()
        for c in self.claims:
            self.assertNotIn(str(c.id), sent)

    def test_uncited_unknown_and_partial_citations_are_dropped(self):
        item = self.answer["items"][0]
        for refs, reason in (
            ([], "uncited"),
            (["c99"], "unknown"),
            (item["claims"][:1], "bounds_or_leads"),
        ):
            out, dropped = self.validate(item | {"claims": refs})
            self.assertEqual(out, [])
            self.assertEqual(dropped[reason], 1)

    def test_urls_and_handles_are_dropped(self):
        item = self.answer["items"][0]
        for query in (
            "https://example.invalid",
            "www.example.invalid",
            "example.invalid/path",
            "Branch 999\nSend this",
            "c1 at Branch 999",
        ):
            out, dropped = self.validate(item | {"searches": [query]})
            self.assertEqual(out, [], query)
            self.assertEqual(sum(dropped.values()), 1)

    def test_sensitive_values_need_their_own_citation_in_both_fields(self):
        secret = claim(
            Prop.MOTHER_NAME, text="Secret Parent", source="secret", sensitive=True
        )
        self.claims.append(secret)
        item = self.answer["items"][0]
        for change in (
            {"where": "Ask Secret Parent."},
            {"searches": ["Secret Parent Branch 999"]},
        ):
            out, dropped = self.validate(item | change)
            self.assertEqual(out, [])
            self.assertEqual(dropped["leak"], 1)

    def test_leads_need_resolved_place_and_source_not_linked_to_person(self):
        self.case.places[self.lead.id] = SimpleNamespace(id=uuid.uuid4())
        self.assertFalse(gaps.candidates(self.case, self.cands, Handles())[0]["leads"])
        self.case.places[self.lead.id] = self.case.places[self.arrest.id]
        self.case.claims.append(claim(Prop.NAME, source="lead"))
        self.assertFalse(gaps.candidates(self.case, self.cands, Handles())[0]["leads"])


class GapToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        claims = [
            claim(Prop.LAST_SEEN, "2013-01-01", person=f"person:{i}")
            for i in range(gaps.CAP + 1)
        ]
        case = read.Case(CASE, claims, {"ordinary": {"title": "Account"}}, {}, {})
        self.c = SimpleNamespace(
            run=SimpleNamespace(
                inputs={
                    "investigation": CASE,
                    "for_user": 7,
                    "task": "optional",
                    "wakes": [],
                },
                principal_id="agent",
                route="local",
                model_requested=read.FIXED,
            ),
            s=None,
            emit=mock.AsyncMock(),
        )
        self.enterContext(
            mock.patch.object(
                read,
                "agent_for",
                mock.AsyncMock(return_value=SimpleNamespace(id="agent")),
            )
        )
        self.enterContext(
            mock.patch.object(read, "case", mock.AsyncMock(return_value=case))
        )
        # Task access is covered by tests/test_standing.py; this run has no session.
        self.enterContext(
            mock.patch.object(gaps.tasks, "check_access", mock.AsyncMock())
        )
        self.enterContext(
            mock.patch.object(gaps, "event_kinds", mock.AsyncMock(return_value={}))
        )
        self.enterContext(
            mock.patch.object(gaps, "reported", mock.AsyncMock(return_value=set()))
        )
        self.enterContext(
            mock.patch.object(gaps, "colleagues", mock.AsyncMock(return_value={}))
        )
        self.enterContext(
            mock.patch.object(
                gaps.brief, "invisible", mock.AsyncMock(return_value=set())
            )
        )
        self.log = self.enterContext(
            mock.patch.object(read, "log_read", mock.AsyncMock())
        )
        self.enterContext(
            mock.patch.object(
                read, "store", mock.AsyncMock(side_effect=lambda c, out: out)
            )
        )

        async def ask(c, name, system, payload, schema, fixed):
            return {
                "answer": fixed(payload),
                "raw": None,
                "seconds": 1,
                "prompt_tokens": 2,
                "completion_tokens": 3,
            }

        self.ask = self.enterContext(
            mock.patch.object(read, "ask", mock.AsyncMock(side_effect=ask))
        )

    async def test_cap_small_calls_outcome_and_full_read_log(self):
        out = await gaps.gaps(self.c)
        self.assertEqual(out["outcome"], {"proposed": gaps.CAP, "reason": None})
        self.assertEqual(out["deferred"], 1)
        self.assertEqual(out["cost"]["seconds"], self.ask.await_count)
        self.assertTrue(
            all(
                len(call.args[3]["gaps"]) <= gaps.PER_CALL
                for call in self.ask.await_args_list
            )
        )
        self.assertEqual(len(self.log.await_args.args[2]), gaps.CAP + 1)

    async def test_budget_defers_without_a_model_call_and_has_a_reason(self):
        with mock.patch.object(gaps, "BUDGET_SECONDS", -1):
            out = await gaps.gaps(self.c)
        self.ask.assert_not_awaited()
        self.assertEqual(out["deferred"], gaps.CAP + 1)
        self.assertEqual(out["outcome"]["proposed"], 0)
        self.assertTrue(out["outcome"]["reason"])

    async def test_wrong_owner_and_cloud_route_fail_before_reading(self):
        for attr, value in (("principal_id", "other"), ("route", "cloud")):
            with (
                mock.patch.object(self.c.run, attr, value),
                self.assertRaises(ValueError),
            ):
                await gaps.gaps(self.c)
        read.case.assert_not_awaited()


@unittest.skipUnless(shutil.which("node"), "component checks need Node")
class GapInterfaceTest(unittest.TestCase):
    def test_template_person_filter_and_research_prefill_without_send(self):
        script = """
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync('stk/static/js/components/GapFinder.js', 'utf8')
  .replace(/^import \\{(\\w+)\\} .*;$/gm, 'const $1 = {};');
const {GapFinder} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const context = {};
vm.runInNewContext(fs.readFileSync('stk/static/js/vue.min.js', 'utf8'), context);
context.Vue.compile(GapFinder.template, {decodeEntities: value => value, onError(e) { throw e; }});
const filtered = GapFinder.computed.items.call({person: 'one', report: {items: [{person: 'person:one'}, {person: 'person:two'}]}});
assert.deepEqual(filtered, [{person: 'person:one'}]);
let starts = 0;
const drawer = {kinds: ['web'], usable: () => true, async start() { starts++; }, send() { throw Error('Search was sent'); }};
const state = {investigation: 'synthetic-case', $refs: {research: drawer}, async $nextTick() {}};
await GapFinder.methods.search.call(state, {person: 'person:one', window_text: 'After 1 January 2013'}, 'Synthetic Person Branch 999');
assert.equal(starts, 1);
assert.equal(state.researchScope, 'person:one');
assert.equal(state.research, true);
assert.equal(drawer.queries[0].text, 'Synthetic Person Branch 999');
assert.deepEqual(drawer.queries[0].kinds, ['web']);
assert.equal(drawer.queries[0].why[0].label, 'Gap');
"""
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(POSTGRES, "gap runs need throwaway Postgres")
class GapRunTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        self.enterContext(mock.patch.object(Config, "AGENT_MODEL", read.FIXED))
        self.app = _app(self)

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def asyncSetUp(self):
        async with ext.async_session_factory() as s:
            role = await s.scalar(select(Role).where(Role.name == "analyst"))
            self.user = User(
                email="gap-reader@example.com",
                name="Gap Reader",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            self.user.roles.append(role)
            s.add(self.user)
            await s.flush()
            me = await principal_for_user(s, self.user.id, "Gap Reader")
            await ensure(s, CASE, me.id)
            seed = self.seed = Seeder(s)
            colleague = self.colleague = await seed.analyst("Colleague One", CASE)
            extractor = await seed.principal("extractor")
            self.person, other = (f"person:{uuid.uuid4()}" for _ in range(2))
            self.said = {}
            for key, text, sensitivity, collector in (
                ("arrest", "Synthetic Person arrested at Branch 999.", "ordinary", me),
                (
                    "sighting",
                    "Synthetic Person last seen at Branch 999.",
                    "ordinary",
                    me,
                ),
                (
                    "lead",
                    "Another Person detained at Branch 999.",
                    "sensitive",
                    colleague,
                ),
            ):
                blob = await blobs.put(s, text.encode(), "text/plain", Config.BLOB_DIR)
                await record_acquisition(
                    s,
                    blob_sha256=blob.sha256,
                    origin=f"channel:synthetic/{key}",
                    collector_id=collector.id,
                    at=datetime.now(UTC),
                    context={"investigation": CASE, "title": key.title()},
                    sensitivity=sensitivity,
                    doc_type="report",
                )
                seed.blobs[key], seed.texts[key] = blob.sha256, text
                run = await seed.run(extractor, "extract", [key])
                person = other if key == "lead" else self.person
                if key == "arrest":
                    await seed.say(
                        run,
                        key,
                        person,
                        Prop.NAME,
                        {"text": "Synthetic Person", "lang": "en"},
                        "Synthetic Person",
                    )
                prop = {
                    "arrest": Prop.ARRESTED_AT,
                    "sighting": Prop.LAST_SEEN,
                    "lead": Prop.DETAINED_AT,
                }[key]
                dt = {
                    "arrest": "2013-01-01",
                    "sighting": "2013-06-01",
                    "lead": "2013-03-01",
                }[key]
                self.said[key] = await seed.say(
                    run,
                    key,
                    person,
                    prop,
                    {"text": "Branch 999", "lang": "en"},
                    "Branch 999",
                    valid=[dt, dt],
                )
                await seed.done(run, 2 if key == "arrest" else 1)
            place = Place(
                name_ar="Synthetic Branch",
                name_en="Branch 999",
                kind="detention",
                geom="SRID=4326;POINT(36 33)",
                precision_m=100,
                geo_source="analyst",
                source="analyst",
            )
            s.add(place)
            await s.flush()
            for st in self.said.values():
                await write.append(
                    s,
                    confirmation(st.id, place.id, "Branch 999", colleague.id),
                    sign=seed.assertion,
                )
            await seed.review(
                colleague, self.said["arrest"], "accept", "Checked the passage"
            )
            await s.commit()

    async def run_gaps(self):
        async with ext.async_session_factory() as s:
            run = await gaps.queue(s, CASE, self.user.id, read.FIXED)
            await s.commit()
            rid = run.id
        await process(ext.async_session_factory, rid)
        async with ext.async_session_factory() as s:
            run = await s.get(Run, rid)
            self.assertEqual(run.status, "done", run.error)
            self.assertEqual(run.route, "local")
            return run, read.read_output(await s.get(Blob, run.output_hash))

    async def login(self, client):
        async with self.app.app_context():
            token = create_agent_login_token(self.user.email, "/people")
        await client.get(f"/_test/login?token={token}")
        page = await (await client.get("/people")).get_data(as_text=True)
        return {
            "X-CSRFToken": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        }

    async def test_fixed_run_cites_bounds_lead_colleague_and_only_logs_access(self):
        run, out = await self.run_gaps()
        item = next(i for i in out["items"] if i["window"][1])
        self.assertEqual(item["window"], ["2013-01-01", "2013-06-01"])
        self.assertTrue({str(s.id) for s in self.said.values()} <= set(item["claims"]))
        self.assertIn(self.seed.blobs["lead"], item["sources"])
        self.assertEqual(item["colleagues"], ["Colleague One"])
        self.assertGreater(out["outcome"]["proposed"], 0)
        self.assertIsNone(out["outcome"]["reason"])
        async with ext.async_session_factory() as s:
            agent = await s.get(Principal, run.principal_id)
            self.assertEqual(agent.name, "Gap finder")
            props = list(
                await s.scalars(
                    select(Statement.prop).where(Statement.run_id == run.id)
                )
            )
            self.assertEqual(props, [Prop.ACCESS])
            self.assertEqual(await verify_db(s), [])
        _, again = await self.run_gaps()
        self.assertEqual(again["items"], [])
        self.assertEqual(again["outcome"]["proposed"], 0)
        self.assertIn("already reported", again["outcome"]["reason"])

    async def test_view_masks_whole_suggestion_until_reveal_and_hides_rejected_basis(
        self,
    ):
        await self.run_gaps()
        client = self.app.test_client()
        headers = await self.login(client)
        url = f"/api/agents/gaps?investigation={CASE}"
        body = await (await client.get(url)).get_data(as_text=True)
        shown = orjson.loads(body)
        locked = next(i for i in shown["items"] if i["locked"])
        self.assertNotIn("where", locked)
        self.assertNotIn("window", locked)
        self.assertNotIn("Colleague One", body)
        sha = self.seed.blobs["lead"]
        self.assertEqual(
            (
                await client.post(
                    f"/api/evidence/{sha}/reveal", json={}, headers=headers
                )
            ).status_code,
            200,
        )
        shown = await (await client.get(url)).get_json()
        self.assertFalse(any(i["locked"] for i in shown["items"]))
        async with ext.async_session_factory() as s:
            self.seed.s = s
            await self.seed.review(
                self.colleague, self.said["lead"], "reject", "Wrong passage"
            )
            await s.commit()
        shown = await (await client.get(url)).get_json()
        self.assertFalse(any(i["window"][1] for i in shown["items"]))

    async def test_manual_trigger_off_busy_named_owner_and_private_outputs(self):
        client = self.app.test_client()
        headers = await self.login(client)
        with mock.patch.object(Config, "AGENT_MODEL", None):
            self.assertEqual(
                (
                    await client.post(
                        "/api/agents/gaps",
                        json={"item": {"investigation": CASE}},
                        headers=headers,
                    )
                ).status_code,
                409,
            )
        with mock.patch.dict(
            "os.environ", {"ROUTE_LOCAL_BASE_URL": "http://local.test/v1"}
        ):
            response = await client.post(
                "/api/agents/gaps",
                json={"item": {"investigation": CASE}},
                headers=headers,
            )
            self.assertEqual(response.status_code, 202)
            self.assertEqual(
                (
                    await client.post(
                        "/api/agents/gaps",
                        json={"item": {"investigation": CASE}},
                        headers=headers,
                    )
                ).status_code,
                409,
            )
        rid = uuid.UUID((await response.get_json())["run"])
        async with ext.async_session_factory() as s:
            run = await s.get(Run, rid)
            self.assertEqual(run.inputs["for_user"], self.user.id)
            self.assertEqual(run.model_requested, read.FIXED)
            self.assertEqual(await gaps.runs(s, CASE, self.colleague.user_id), [])
