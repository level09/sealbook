"""Contradiction reports: citation rules without services, and fixed runs on Postgres."""

import asyncio
import re
import unittest
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest import mock

from jsonschema import Draft202012Validator
from quart_security import hash_password
from sqlalchemy import select

import stk.app  # noqa: F401 (registers the CLI before the seed module loads)
import stk.extensions as ext
from stk import blobs
from stk.agent_login import create_agent_login_token
from stk.agents import contradictions, read
from stk.cases.service import ensure
from stk.ledger.contracts import Prop, Tier
from stk.ledger.models import Blob, Payload, Principal, Run, Statement
from stk.ledger.write import principal_for_user, record_acquisition, verify_db
from stk.monitor.seed import Seeder
from stk.runs.worker import process
from stk.settings import Config
from stk.user.models import Role, User
from tests.test_agents import claim
from tests.test_people import POSTGRES, _app

CASE = "case-contradictions"


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.a = claim(Prop.BIRTH_DATE, "1990")
        self.b = claim(Prop.BIRTH_DATE, "1991", source="s2")
        self.secret = claim(
            Prop.MOTHER_NAME, "Mira Example", sensitive=True, source="s3"
        )
        self.claims = [self.a, self.b, self.secret]
        self.h = read.Handles()
        self.cands = [{"sides": [[self.a], [self.b]]}]
        for c in self.claims:
            self.h("c", c.id)

    def item(self, **kw):
        return {
            "claims": ["c1", "c2"],
            "why": "The sources give different birth years.",
            "question": "Which source can confirm the birth year?",
            "kind": "sources conflict",
        } | kw

    def validate(self, *items):
        return contradictions.validate(
            {"items": list(items)}, self.cands, self.h, self.claims
        )

    def test_one_side_and_unknown_handles_are_dropped(self):
        items, dropped = self.validate(
            self.item(), self.item(claims=["c1"]), self.item(claims=["c1", "c99"])
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(set(items[0]["claims"]), {str(self.a.id), str(self.b.id)})
        self.assertEqual((dropped["one_sided"], dropped["unknown"]), (1, 1))

    def test_citations_cannot_mix_candidates_or_add_unrelated_claims(self):
        items, dropped = self.validate(self.item(claims=["c1", "c2", "c3"]))
        self.assertEqual(items, [])
        self.assertEqual(dropped["one_sided"], 1)

    def test_two_claims_from_one_side_are_not_two_sides(self):
        self.cands = [{"sides": [[self.a, self.b], [self.secret]]}]
        items, dropped = self.validate(self.item())
        self.assertEqual(items, [])
        self.assertEqual(dropped["one_sided"], 1)

    def test_citations_cannot_join_two_different_candidates(self):
        self.cands = [
            {"sides": [[self.a], [self.b]]},
            {"sides": [[self.b], [self.secret]]},
        ]
        items, dropped = self.validate(self.item(claims=["c1", "c3"]))
        self.assertEqual(items, [])
        self.assertEqual(dropped["one_sided"], 1)

    def test_why_and_question_cannot_leak_or_print_handles(self):
        for field in ("why", "question"):
            with self.subTest(field=field):
                items, dropped = self.validate(
                    self.item(**{field: "Ask Mira Example."}),
                    self.item(**{field: "Check c1 against c2."}),
                )
                self.assertEqual(items, [])
                self.assertEqual((dropped["leak"], dropped["handle"]), (1, 1))

    def test_fresh_suppresses_the_same_claim_set_in_any_order(self):
        seen = {frozenset((str(self.a.id), str(self.b.id)))}
        self.assertFalse(contradictions.fresh([self.b.id, self.a.id], seen))
        self.assertTrue(contradictions.fresh([self.a.id, self.secret.id], seen))


class CandidateTest(unittest.TestCase):
    def case(self, claims, places=None):
        return read.Case(
            "synthetic-case",
            claims,
            {c.source: {"title": c.source, "sensitive": c.sensitive} for c in claims},
            places or {},
            {},
        )

    def test_undated_birth_years_disagree_but_precision_and_same_source_do_not(self):
        a = claim(Prop.BIRTH_DATE, "1990")
        b = claim(Prop.BIRTH_DATE, "1991", source="s2")
        precise = claim(Prop.BIRTH_DATE, "1990-02-03", source="s3")
        same = claim(Prop.BIRTH_DATE, "1989")
        cands = contradictions.candidates(self.case([a, b, precise, same]))
        pairs = {frozenset(c.id for side in x["sides"] for c in side) for x in cands}
        self.assertIn(frozenset((a.id, b.id)), pairs)
        self.assertNotIn(frozenset((a.id, precise.id)), pairs)
        self.assertNotIn(frozenset((a.id, same.id)), pairs)

    def test_accepted_claims_rank_first_and_agreeing_pairs_stay_out(self):
        a = claim(Prop.BIRTH_DATE, "1990")
        b = claim(Prop.BIRTH_DATE, "1991", source="s2")
        c = replace(claim(Prop.MOTHER_NAME, "Mira", source="s3"), tier=Tier.ACCEPTED)
        d = claim(Prop.MOTHER_NAME, "Lina", source="s4")
        e = claim(Prop.BIRTH_DATE, "1992", person="person:b", source="s5")
        f = claim(Prop.BIRTH_DATE, "1992", person="person:b", source="s6")
        cands = contradictions.candidates(self.case([a, b, c, d, e, f]))
        self.assertEqual(len(cands), 2)
        self.assertEqual({x.id for s in cands[0]["sides"] for x in s}, {c.id, d.id})

    def test_dated_values_only_disagree_over_overlapping_time(self):
        a = replace(
            claim(Prop.DETENTION_BRANCH, "Branch 901"),
            lo=date(2013, 1, 1),
            hi=date(2013, 1, 1),
        )
        b = replace(
            claim(Prop.DETENTION_BRANCH, "Branch 902", source="s2"),
            lo=date(2014, 1, 1),
            hi=date(2014, 1, 1),
        )
        self.assertEqual(contradictions.candidates(self.case([a, b])), [])

    def test_place_identity_wins_over_spelling(self):
        a = replace(
            claim(Prop.SEEN_AT, "Town"), lo=date(2013, 1, 1), hi=date(2013, 1, 1)
        )
        b = replace(claim(Prop.SEEN_AT, "Town", source="s2"), lo=a.lo, hi=a.hi)
        place = SimpleNamespace(
            id=uuid.uuid4(), lon=35, lat=34, name_en="Town", name_ar="", precision_m=100
        )
        other = SimpleNamespace(
            id=uuid.uuid4(), lon=36, lat=34, name_en="Town", name_ar="", precision_m=100
        )
        case = self.case([a, b], {a.id: place, b.id: other})
        self.assertEqual(len(contradictions.candidates(case)), 1)
        case.places[b.id] = place
        self.assertEqual(contradictions.candidates(case), [])

    def test_fixed_payload_has_only_handles_and_is_deterministic(self):
        a = claim(Prop.BIRTH_DATE, "1990")
        b = claim(Prop.BIRTH_DATE, "1991", source="s2")
        case = self.case([a, b])
        h = read.Handles()
        cands = contradictions.candidates(case)
        payload = contradictions.payload(cands, h, case.docs)
        self.assertNotIn(str(a.id), str(payload))
        self.assertNotIn(str(b.id), str(payload))
        answer = contradictions.fixed_answer(payload)
        Draft202012Validator(contradictions.SCHEMA).validate(answer)
        self.assertEqual(answer, contradictions.fixed_answer(payload))
        items, dropped = contradictions.validate(answer, cands, h, case.claims)
        self.assertEqual((len(items), sum(dropped.values())), (1, 0))

    def test_position_citations_must_include_an_actual_conflicting_pair(self):
        a = replace(
            claim(Prop.LAST_SEEN, "Town A"), lo=date(2013, 1, 1), hi=date(2013, 1, 1)
        )
        b = replace(a, id=uuid.uuid4(), source="s2")
        c = replace(
            a, id=uuid.uuid4(), source="s3", prop=Prop.ARRESTED_AT, text="Town B"
        )
        d = replace(a, id=uuid.uuid4(), source="s4", prop=Prop.SEEN_AT, text="Town C")

        def place(lon):
            return SimpleNamespace(
                id=uuid.uuid4(),
                lon=lon,
                lat=34,
                name_en="Town",
                name_ar="",
                precision_m=100,
            )

        p = place(35)
        case = self.case(
            [a, b, c, d], {a.id: p, b.id: p, c.id: place(36), d.id: place(37)}
        )
        cands = contradictions.candidates(case)
        h = read.Handles()
        for claim_ in case.claims:
            h("c", claim_.id)

        def item(refs):
            return {
                "claims": refs,
                "kind": "sources conflict",
                "why": "Different places.",
                "question": "Where was the person?",
            }

        items, dropped = contradictions.validate(
            {"items": [item(["c1", "c2"]), item(["c1", "c3"]), item(["c1", "c4"])]},
            cands,
            h,
            case.claims,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(set(items[0]["claims"]), {str(a.id), str(d.id)})
        self.assertEqual(dropped["one_sided"], 2)


class ReportTest(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, claims, seen=(), ask_delay=0, **limits):
        case = CandidateTest().case(claims)
        principal = uuid.uuid4()
        c = SimpleNamespace(
            s=None,
            emit=mock.AsyncMock(),
            run=SimpleNamespace(
                inputs={"investigation": CASE, "for_user": 1},
                principal_id=principal,
                route=Config.AGENT_ROUTE,
                model_requested=read.FIXED,
            ),
        )

        async def ask(c, name, system, payload, schema, fixed):
            await asyncio.sleep(ask_delay)
            return {
                "answer": fixed(payload),
                "raw": "synthetic-output",
                "seconds": 1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
            }

        async def store(c, out):
            return out

        with (
            mock.patch.object(
                read,
                "agent_for",
                mock.AsyncMock(return_value=SimpleNamespace(id=principal)),
            ),
            mock.patch.object(read, "case", mock.AsyncMock(return_value=case)),
            mock.patch.object(
                contradictions, "reported", mock.AsyncMock(return_value=set(seen))
            ),
            mock.patch.object(read, "log_read", mock.AsyncMock()),
            mock.patch.object(read, "ask", ask),
            mock.patch.object(read, "store", store),
            mock.patch.object(
                contradictions.brief, "invisible", mock.AsyncMock(return_value=set())
            ),
            mock.patch.multiple(contradictions, **limits)
            if limits
            else mock.patch.dict({}, {}),
        ):
            return await contradictions.contradictions(c)

    async def test_empty_and_already_reported_outcomes(self):
        empty = await self.invoke([])
        self.assertEqual(
            empty["outcome"],
            {
                "proposed": 0,
                "reason": "No two sources disagree in this case.",
            },
        )
        a = claim(Prop.BIRTH_DATE, "1990")
        b = claim(Prop.BIRTH_DATE, "1991", source="s2")
        repeated = await self.invoke([a, b], {frozenset((str(a.id), str(b.id)))})
        self.assertEqual(
            repeated["outcome"],
            {
                "proposed": 0,
                "reason": "Every disagreement here was already reported.",
            },
        )
        self.assertEqual(repeated["raw"], [])

    async def test_candidate_cap_and_time_budget_defer_work(self):
        claims = [
            claim(Prop.BIRTH_DATE, "1990"),
            claim(Prop.BIRTH_DATE, "1991", source="s2"),
            claim(Prop.BIRTH_DATE, "1992", person="person:b"),
            claim(Prop.BIRTH_DATE, "1993", person="person:b", source="s2"),
        ]
        capped = await self.invoke(claims, CAP=1)
        self.assertEqual(
            (capped["candidates"], capped["outcome"]["proposed"], capped["deferred"]),
            (2, 1, 1),
        )
        timed = await self.invoke(claims, BUDGET_SECONDS=-1)
        self.assertEqual(
            (timed["outcome"]["proposed"], timed["deferred"], timed["raw"]), (0, 2, [])
        )
        self.assertTrue(timed["outcome"]["reason"])

    async def test_model_call_cannot_outlive_remaining_budget(self):
        a = claim(Prop.BIRTH_DATE, "1990")
        b = claim(Prop.BIRTH_DATE, "1991", source="s2")
        out = await self.invoke([a, b], ask_delay=0.05, BUDGET_SECONDS=0.02)
        self.assertEqual(
            (out["outcome"]["proposed"], out["deferred"], out["raw"]), (0, 1, [])
        )
        self.assertEqual(out["dropped"]["timeout"], 1)


@unittest.skipUnless(
    POSTGRES, "contradictions need Postgres (set SQLALCHEMY_DATABASE_URI)"
)
class ContradictionRunTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        self.enterContext(mock.patch.object(Config, "AGENT_MODEL", read.FIXED))
        self.app = _app(self)

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def asyncSetUp(self):
        async with ext.async_session_factory() as s:
            role = await s.scalar(select(Role).where(Role.name == "analyst"))
            users = []
            for name in ("requester", "reader"):
                user = User(
                    email=f"{name}@example.com",
                    name=name,
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                user.roles.append(role)
                s.add(user)
                users.append(user)
            await s.flush()
            self.user, self.reader = users
            me = await principal_for_user(s, self.user.id, self.user.name)
            await ensure(s, CASE, me.id)
            seed = self.seed = Seeder(s)
            texts = {
                "first": "Ari Example born 1990. Bex Example born 1988.",
                "second": "Ari Example born 1991. Bex Example born 1988.",
                "third": "Bex Example born 1988.",
            }
            for key, body in texts.items():
                blob = await blobs.put(s, body.encode(), "text/plain", Config.BLOB_DIR)
                await record_acquisition(
                    s,
                    blob_sha256=blob.sha256,
                    origin=f"channel:synthetic/{key}",
                    collector_id=me.id,
                    at=datetime.now(UTC),
                    context={"investigation": CASE, "title": key.title()},
                    sensitivity="sensitive" if key == "second" else "ordinary",
                    doc_type="report",
                )
                seed.blobs[key], seed.texts[key] = blob.sha256, body
            extractor = await seed.principal("Synthetic extractor")
            self.human = await seed.analyst("reviewer", CASE)
            a, b = (f"person:{uuid.uuid4()}" for _ in range(2))
            self.pair = []
            for doc in texts:
                run = await seed.run(extractor, "extract", [doc])
                count = 0
                for person, name, year in (
                    (a, "Ari Example", "1990" if doc == "first" else "1991"),
                    (b, "Bex Example", "1988"),
                ):
                    if name not in texts[doc]:
                        continue
                    for prop, value in ((Prop.NAME, name), (Prop.BIRTH_DATE, year)):
                        st = await seed.say(
                            run, doc, person, prop, {"text": value, "lang": "en"}, value
                        )
                        count += 1
                        if person == a and prop == Prop.BIRTH_DATE:
                            self.pair.append(st)
                await seed.done(run, count)
            await seed.review(self.human, self.pair[0], "accept", "Checked the passage")
            await s.commit()

    async def invoke(self):
        async with ext.async_session_factory() as s:
            run = await contradictions.queue(s, CASE, self.user.id, read.FIXED)
            rid = run.id
            await s.commit()
        await process(ext.async_session_factory, rid)
        async with ext.async_session_factory() as s:
            run = await s.get(Run, rid)
            self.assertEqual(run.status, "done", run.error)
            return run, read.read_output(await s.get(Blob, run.output_hash))

    async def login(self, client, user=None):
        async with self.app.app_context():
            token = create_agent_login_token((user or self.user).email, "/desk")
        await client.get(f"/_test/login?token={token}")
        page = await (await client.get("/desk")).get_data(as_text=True)
        return {
            "X-CSRFToken": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        }

    async def test_fixed_run_reports_only_disagreeing_pair_and_never_writes_facts(self):
        run, out = await self.invoke()
        self.assertEqual(out["outcome"], {"proposed": 1, "reason": None})
        [item] = out["items"]
        self.assertEqual(set(item["claims"]), {str(st.id) for st in self.pair})
        self.assertTrue(item["question"].endswith("?"))
        self.assertEqual((run.route, run.model_served), ("local", read.FIXED))
        async with ext.async_session_factory() as s:
            agent = await s.get(Principal, run.principal_id)
            human = await principal_for_user(s, self.user.id, self.user.name)
            self.assertEqual(
                (agent.name, agent.on_behalf_of), (contradictions.AGENT, human.id)
            )
            statements = list(
                await s.scalars(select(Statement).where(Statement.run_id == run.id))
            )
            self.assertEqual([st.prop for st in statements], [Prop.ACCESS])
            access = await s.get(Payload, statements[0].commitment)
            self.assertTrue(
                {str(st.id) for st in self.pair} <= set(access.value["read"])
            )
            self.assertEqual(await verify_db(s), [])
        _, again = await self.invoke()
        self.assertEqual(again["items"], [])
        self.assertEqual(
            again["outcome"],
            {
                "proposed": 0,
                "reason": "Every disagreement here was already reported.",
            },
        )
        self.assertEqual(again["raw"], [])

    async def test_reader_must_reveal_sensitive_source_and_rejected_claim_disappears(
        self,
    ):
        await self.invoke()
        client = self.app.test_client()
        headers = await self.login(client, self.reader)
        response = await client.get(f"/api/agents/contradictions?investigation={CASE}")
        self.assertEqual(response.status_code, 200)
        body = await response.get_data(as_text=True)
        shown = await response.get_json()
        [item] = shown["items"]
        self.assertTrue(item["locked"])
        self.assertIsNone(item["why"])
        self.assertIsNone(item["question"])
        self.assertEqual(item["claims"], [])
        self.assertNotIn("1991", body)
        sha = self.seed.blobs["second"]
        response = await client.post(
            f"/api/evidence/{sha}/reveal", json={}, headers=headers
        )
        self.assertEqual(response.status_code, 200)
        shown = await (
            await client.get(f"/api/agents/contradictions?investigation={CASE}")
        ).get_json()
        self.assertFalse(shown["items"][0]["locked"])
        self.assertEqual(
            {c["value"] for c in shown["items"][0]["claims"]}, {"1990", "1991"}
        )
        async with ext.async_session_factory() as s:
            self.seed.s = s
            await self.seed.review(self.human, self.pair[1], "reject", "Misread")
            await s.commit()
        shown = await (
            await client.get(f"/api/agents/contradictions?investigation={CASE}")
        ).get_json()
        self.assertEqual(shown["items"], [])
        self.assertEqual(shown["outcome"]["proposed"], 0)

    async def test_manual_routes_refuse_off_busy_and_unowned_status(self):
        client = self.app.test_client()
        headers = await self.login(client)
        url = "/api/agents/contradictions"
        response = await client.post(url, json={"item": {}}, headers=headers)
        self.assertEqual(response.status_code, 400)
        with mock.patch.object(Config, "AGENT_MODEL", None):
            response = await client.post(
                url, json={"item": {"investigation": CASE}}, headers=headers
            )
        self.assertEqual(
            (response.status_code, (await response.get_json())["reason"]), (409, "off")
        )
        with mock.patch.dict(
            "os.environ", {"ROUTE_LOCAL_BASE_URL": "http://synthetic.invalid/v1"}
        ):
            response = await client.post(
                url, json={"item": {"investigation": CASE}}, headers=headers
            )
            self.assertEqual(response.status_code, 202)
            rid = (await response.get_json())["run"]
            busy = await client.post(
                url, json={"item": {"investigation": CASE}}, headers=headers
            )
        self.assertEqual(
            (busy.status_code, (await busy.get_json())["reason"]), (409, "busy")
        )
        self.assertEqual((await client.get(f"{url}/runs/{rid}")).status_code, 200)
        other = self.app.test_client()
        await self.login(other, self.reader)
        self.assertEqual((await other.get(f"{url}/runs/{rid}")).status_code, 404)


if __name__ == "__main__":
    unittest.main()
