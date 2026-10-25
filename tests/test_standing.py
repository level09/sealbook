"""Standing question rules offline, and worker lifecycle on throwaway Postgres."""

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import stk.extensions as ext
from sqlalchemy import func, select
from stk.user.models import User

import tests.test_agents as fixtures
from sealbook.agents import read, standing, tasks
from sealbook.agents.models import AgentTask, AgentWake
from sealbook.cases.service import ensure
from sealbook.collaboration.models import Notification
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import Acquisition, Blob, Principal, Run, Statement
from sealbook.runs.worker import TOOLS, process
from sealbook.settings import Config


class StandingRulesTest(unittest.TestCase):
    def setUp(self):
        self.h = read.Handles()
        self.claim = fixtures.claim(Prop.DETAINED_AT, "Branch 999")
        self.secret = fixtures.claim(
            Prop.MOTHER_NAME, "Invented Mother", sensitive=True, source="s2"
        )
        self.handle = self.h("c", self.claim.id)
        self.secret_handle = self.h("c", self.secret.id)

    def validate(self, matches, seen=None):
        return standing.validate(
            {"matches": matches},
            [self.claim],
            self.h,
            [self.claim, self.secret],
            seen if seen is not None else set(),
        )

    def test_backoff_caps_at_eight_intervals_and_skips_missed_ticks(self):
        self.assertEqual(
            [tasks.backoff(60, n).total_seconds() for n in range(6)],
            [60, 120, 240, 480, 480, 480],
        )
        now = datetime(2026, 10, 24, tzinfo=UTC)
        self.assertEqual(now + tasks.backoff(60, 2), now + timedelta(minutes=4))

    def test_drops_uncited_unknown_and_unoffered_handles(self):
        out, dropped = self.validate(
            [
                {"why": "No citation.", "claims": []},
                {"why": "Unknown claim.", "claims": ["c999"]},
                {
                    "why": "Known but not supplied in this call.",
                    "claims": [self.secret_handle],
                },
                {
                    "why": "The source places someone at Branch 999.",
                    "claims": [self.handle],
                },
            ]
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["claims"], [str(self.claim.id)])
        self.assertEqual((dropped["uncited"], dropped["unknown"]), (1, 2))

    def test_repeat_suppression_is_a_set_of_claims(self):
        seen = {standing.match_key([self.claim.id])}
        out, dropped = self.validate(
            [{"why": "Another wording.", "claims": [self.handle, self.handle]}], seen
        )
        self.assertEqual(out, [])
        self.assertEqual(dropped["repeat"], 1)
        self.assertEqual(
            standing.match_key(["a", "b"]), standing.match_key(["b", "a", "a"])
        )

    def test_sensitive_leaks_and_handles_in_prose_are_dropped(self):
        out, dropped = self.validate(
            [
                {"why": "Invented Mother was there.", "claims": [self.handle]},
                {"why": "See c1.", "claims": [self.handle]},
            ]
        )
        self.assertEqual(out, [])
        self.assertEqual((dropped["leak"], dropped["text"]), (1, 1))

    def test_fixed_answer_cites_only_matching_input(self):
        out = standing.fixed_answer(
            {
                "question": "Branch 999",
                "claims": [
                    {"h": "c1", "field": "detained at", "value": "Branch 999"},
                    {"h": "c2", "field": "detained at", "value": "Elsewhere"},
                ],
            }
        )
        self.assertEqual([m["claims"] for m in out["matches"]], [["c1"]])
        self.assertEqual(
            out["matches"][0]["why"],
            "Demo match: the value and the question both name 999.",
        )


class StandingPassTest(unittest.IsolatedAsyncioTestCase):
    async def run_tool(self, question, clock=None):
        claim = fixtures.claim(Prop.DETAINED_AT, "Branch 999")
        task = SimpleNamespace(
            id=uuid.uuid4(), investigation="synthetic", spec={"question": question}
        )
        run = SimpleNamespace(
            id=uuid.uuid4(), model_requested=read.FIXED, inputs={"task": str(task.id)}
        )
        db = SimpleNamespace(
            get=mock.AsyncMock(return_value=task),
            scalars=mock.AsyncMock(return_value=[]),
        )
        ctx = SimpleNamespace(s=db, run=run)
        case = read.Case(
            "synthetic", [claim], {"s1": {"title": "Synthetic source"}}, {}, {}
        )

        async def ask(c, name, system, payload, schema, fixed):
            self.assertNotIn(str(claim.id), str(payload))
            self.assertNotIn(str(task.id), str(payload))
            return {
                "answer": fixed(payload),
                "valid": True,
                "raw": "raw",
                "seconds": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }

        with (
            mock.patch.object(tasks, "check_access", new_callable=mock.AsyncMock),
            mock.patch.object(
                read, "case", new_callable=mock.AsyncMock, return_value=case
            ),
            mock.patch.object(
                standing, "runs", new_callable=mock.AsyncMock, return_value=[]
            ),
            mock.patch.object(
                read, "ask", new_callable=mock.AsyncMock, side_effect=ask
            ) as asked,
            mock.patch.object(
                read, "store", new_callable=mock.AsyncMock, return_value="sealed"
            ) as stored,
            mock.patch.object(read, "log_read", new_callable=mock.AsyncMock) as logged,
            mock.patch.object(tasks, "wake", new_callable=mock.AsyncMock) as wake,
        ):
            if clock:
                with mock.patch.object(standing.time, "monotonic", side_effect=clock):
                    result = await standing.standing(ctx)
            else:
                result = await standing.standing(ctx)
            self.assertEqual(result, "sealed")
            return stored.call_args.args[1], asked, logged, wake, claim

    async def test_screen_survivors_are_not_reported_as_matches(self):
        out, asked, logged, _, claim = await self.run_tool("elsewhere")
        self.assertEqual(
            (out["screened"], out["maybe"], len(out["matches"])), (1, 1, 0)
        )
        self.assertEqual(out["outcome"]["proposed"], 0)
        self.assertEqual(asked.await_count, 2)
        self.assertEqual(logged.call_args.args[2], [claim.id])

    async def test_two_pass_fixed_match_has_citation_and_outcome(self):
        out, _, _, _, claim = await self.run_tool("Branch 999")
        self.assertEqual(out["outcome"], {"proposed": 1, "reason": None})
        self.assertEqual(out["matches"][0]["claims"], [str(claim.id)])

    async def test_budget_between_passes_defers_survivors_with_ids_only(self):
        out, asked, _, wake, claim = await self.run_tool(
            "Branch 999", [0, 0, standing.BUDGET_SECONDS + 1]
        )
        self.assertEqual((out["deferred"], out["outcome"]["proposed"]), (1, 0))
        self.assertEqual(asked.await_count, 1)
        self.assertEqual(wake.call_args.args[-1], {"claims": [str(claim.id)]})


@unittest.skipUnless(fixtures.POSTGRES, "standing questions need Postgres")
class StandingRunTest(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.AgentRunTest.setUp
    asyncTearDown = fixtures.AgentRunTest.asyncTearDown
    login = fixtures.AgentRunTest.login

    async def asyncSetUp(self):
        await fixtures.AgentRunTest.asyncSetUp(self)
        self.enterContext(mock.patch.object(Config, "AGENT_MODEL", read.FIXED))
        self.enterContext(
            mock.patch.dict(
                "os.environ", {"ROUTE_LOCAL_BASE_URL": "http://local.test/v1"}
            )
        )
        async with ext.async_session_factory() as db:
            await ensure(db, fixtures.CASE)
            self.task = AgentTask(
                user_id=self.user.id,
                investigation=fixtures.CASE,
                kind="standing",
                spec={"question": "Branch 999"},
                every_seconds=60,
                next_at=datetime.now(UTC),
            )
            db.add(self.task)
            await db.commit()

    async def queue(self, wakes=()):
        async with ext.async_session_factory() as db:
            task = await db.get(AgentTask, self.task.id)
            run = await tasks.queue(db, task, list(wakes), read.FIXED)
            await db.commit()
            return run.id

    async def output(self, rid):
        async with ext.async_session_factory() as db:
            run = await db.get(Run, rid)
            self.assertEqual(run.status, "done", run.error)
            return read.read_output(await db.get(Blob, run.output_hash))

    async def test_wake_dedup_coalescing_active_run_and_agents_off(self):
        async with ext.async_session_factory() as db:
            for key in ("one", "one", "two"):
                await tasks.wake(db, self.task.id, key, "manual", {})
            await db.commit()
        with mock.patch("sealbook.agents.views.missing", return_value=["AGENT_MODEL"]):
            self.assertEqual(await tasks.tick(ext.async_session_factory), 0)
        self.assertEqual(await tasks.tick(ext.async_session_factory), 1)
        self.assertEqual(await tasks.tick(ext.async_session_factory), 0)
        async with ext.async_session_factory() as db:
            wakes = list(await db.scalars(select(AgentWake)))
            self.assertEqual(len(wakes), 2)
            self.assertEqual(len({w.run_id for w in wakes}), 1)
            run = await db.get(Run, wakes[0].run_id)
            self.assertEqual(set(run.inputs["wakes"]), {str(w.id) for w in wakes})
            self.assertEqual(run.inputs["for_user"], self.user.id)
            self.assertEqual(run.route, "local")

    async def test_success_backoff_resets_failures_and_settlement_is_idempotent(self):
        async with ext.async_session_factory() as db:
            task = await db.get(AgentTask, self.task.id)
            task.failures, task.empty_streak = 2, 1
            task.spec = {"question": "unmatchedword"}
            await db.commit()
        rid = await self.queue()
        before = datetime.now(UTC)
        await process(ext.async_session_factory, rid)
        await tasks.settle(ext.async_session_factory, rid)
        async with ext.async_session_factory() as db:
            task = await db.get(AgentTask, self.task.id)
            self.assertEqual((task.failures, task.empty_streak), (0, 2))
            self.assertGreaterEqual(task.next_at, before + timedelta(seconds=240))
            self.assertEqual(
                task.next_at - task.last_success_at, timedelta(seconds=240)
            )
        out = await self.output(rid)
        self.assertEqual(out["outcome"]["proposed"], 0)
        self.assertTrue(out["outcome"]["reason"])

    async def test_api_validation_owner_controls_and_manual_check(self):
        client = self.app.test_client()
        headers = await self.login(client)
        response = await client.post(
            "/api/agents/tasks",
            json={
                "item": {
                    "investigation": fixtures.CASE,
                    "kind": "standing",
                    "question": "no",
                }
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 400)
        response = await client.post(
            "/api/agents/tasks",
            json={
                "item": {
                    "investigation": fixtures.CASE,
                    "kind": "standing",
                    "question": "Branch 999",
                }
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 201)
        tid = uuid.UUID((await response.get_json())["id"])
        async with ext.async_session_factory() as db:
            self.assertEqual((await db.get(AgentTask, tid)).every_seconds, 86400)
            other = User(
                email="other-synthetic@example.com",
                name="Other analyst",
                active=True,
                password=self.user.password,
            )
            db.add(other)
            await db.flush()
            foreign = AgentTask(
                user_id=other.id,
                investigation=fixtures.CASE,
                kind="standing",
                spec={"question": "Branch 999"},
            )
            db.add(foreign)
            await db.commit()
            foreign_id = str(foreign.id)
        for path, method, item in (
            (f"/api/agents/tasks/{foreign_id}/run", client.post, {}),
            (f"/api/agents/tasks/{foreign_id}", client.patch, {"enabled": False}),
        ):
            self.assertEqual(
                (await method(path, json={"item": item}, headers=headers)).status_code,
                404,
            )
        path = f"/api/agents/tasks/{tid}"
        self.assertEqual(
            (
                await client.post(path + "/run", json={"item": {}}, headers=headers)
            ).status_code,
            202,
        )
        await tasks.tick(ext.async_session_factory)
        self.assertEqual(
            (
                await client.post(path + "/run", json={"item": {}}, headers=headers)
            ).status_code,
            409,
        )
        self.assertEqual(
            (
                await client.patch(
                    path, json={"item": {"enabled": False}}, headers=headers
                )
            ).status_code,
            200,
        )
        async with ext.async_session_factory() as db:
            task = await db.get(AgentTask, tid)
            task.paused_reason, task.failures = "The check could not finish.", 3
            await db.commit()
        self.assertEqual(
            (
                await client.patch(
                    path, json={"item": {"enabled": True}}, headers=headers
                )
            ).status_code,
            200,
        )
        async with ext.async_session_factory() as db:
            task = await db.get(AgentTask, tid)
            self.assertEqual(
                (task.enabled, task.failures, task.paused_reason), (True, 0, None)
            )

    async def test_failure_releases_wakes_waits_and_pauses_after_three(self):
        async with ext.async_session_factory() as db:
            await tasks.wake(db, self.task.id, "new", "manual", {})
            await db.commit()
        for failure in range(1, 4):
            if failure > 1:
                async with ext.async_session_factory() as db:
                    task = await db.get(AgentTask, self.task.id)
                    task.next_at = datetime.now(UTC) - timedelta(seconds=1)
                    await db.commit()
            self.assertEqual(await tasks.tick(ext.async_session_factory), 1)
            async with ext.async_session_factory() as db:
                task = await db.get(AgentTask, self.task.id)
                run = await db.get(Run, task.last_run_id)
                await write_run_failure(db, run)
                rid = run.id
            await tasks.settle(ext.async_session_factory, rid)
            await tasks.settle(ext.async_session_factory, rid)
            async with ext.async_session_factory() as db:
                task = await db.get(AgentTask, self.task.id)
                self.assertEqual(task.failures, failure)
                self.assertIsNone((await db.scalar(select(AgentWake))).run_id)
                self.assertGreater(
                    task.next_at, datetime.now(UTC) + timedelta(minutes=4)
                )
                self.assertEqual(bool(task.paused_reason), failure == 3)
            self.assertEqual(await tasks.tick(ext.async_session_factory), 0)
        async with ext.async_session_factory() as db:
            self.assertEqual(
                await db.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.kind == "agent_paused")
                ),
                1,
            )

    async def test_access_loss_fails_and_pauses_before_reading(self):
        rid = await self.queue()
        async with ext.async_session_factory() as db:
            user = await db.get(User, self.user.id)
            user.active = False
            await db.commit()
        await process(ext.async_session_factory, rid)
        async with ext.async_session_factory() as db:
            self.assertEqual((await db.get(Run, rid)).status, "failed")
            self.assertIn(
                "no longer has access",
                (await db.get(AgentTask, self.task.id)).paused_reason,
            )
            self.assertFalse(
                await db.scalar(select(Statement.id).where(Statement.run_id == rid))
            )

    async def test_sealed_evidence_wakes_only_its_case_and_enabled_tasks(self):
        async with ext.async_session_factory() as db:
            other = AgentTask(
                user_id=self.user.id,
                investigation="another-case",
                kind="standing",
                spec={"question": "Branch 999"},
            )
            stopped = AgentTask(
                user_id=self.user.id,
                investigation=fixtures.CASE,
                kind="standing",
                spec={"question": "Branch 999"},
                enabled=False,
            )
            db.add_all([other, stopped])
            await db.flush()
            st = await db.get(Statement, self.said["roster_y"].id)
            source_run = await db.get(Run, st.run_id)
            acquisition = await db.scalar(
                select(Acquisition).where(Acquisition.blob_sha256 == st.span_blob)
            )
            run = Run(
                principal_id=source_run.principal_id,
                tool="extract",
                route=Config.AGENT_ROUTE,
                model_requested=read.FIXED,
                inputs={"acquisition": str(acquisition.id)},
            )
            db.add(run)
            await db.commit()
            rid = run.id

        async def extract(c):
            await write.append(
                c.s,
                StatementIn(
                    subject=st.subject,
                    prop=Prop.NAME,
                    value={"text": "Yusuf Khalid Alali", "lang": "en"},
                    span_blob=st.span_blob,
                    span=st.span,
                    author_id=c.run.principal_id,
                    run_id=c.run.id,
                ),
                sign=c.sign,
            )
            return None

        with mock.patch.dict(TOOLS, {"extract": extract}):
            await process(ext.async_session_factory, rid)
        async with ext.async_session_factory() as db:
            run = await db.get(Run, rid)
            self.assertEqual(run.status, "done")
            await tasks.evidence(db, run)
            await tasks.evidence(db, run)
            await db.commit()
            wakes = list(await db.scalars(select(AgentWake)))
            self.assertEqual([w.task_id for w in wakes], [self.task.id])
            self.assertEqual(wakes[0].payload, {"run": str(run.id)})

    async def test_tick_recovers_sealed_run_before_queueing_again(self):
        rid = await self.queue()
        with mock.patch.object(tasks, "settle", new_callable=mock.AsyncMock):
            await process(ext.async_session_factory, rid)
        async with ext.async_session_factory() as db:
            self.assertIsNone((await db.get(AgentTask, self.task.id)).last_success_at)
        self.assertEqual(await tasks.tick(ext.async_session_factory), 0)
        async with ext.async_session_factory() as db:
            self.assertIsNotNone(
                (await db.get(AgentTask, self.task.id)).last_success_at
            )

    async def test_fixed_end_to_end_citations_outcome_notifications_and_repeat(self):
        rid = await self.queue()
        await process(ext.async_session_factory, rid)
        out = await self.output(rid)
        self.assertGreater(out["outcome"]["proposed"], 0)
        self.assertIsNone(out["outcome"]["reason"])
        async with ext.async_session_factory() as db:
            for match in out["matches"]:
                self.assertTrue(match["claims"])
                for cid in match["claims"]:
                    self.assertIsNotNone(await db.get(Statement, uuid.UUID(cid)))
            self.assertEqual(
                await db.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.kind == "standing_match")
                ),
                1,
            )
            self.assertEqual(
                list(
                    await db.scalars(
                        select(Statement.prop).where(Statement.run_id == rid)
                    )
                ),
                [Prop.ACCESS],
            )
        second = await self.queue()
        await process(ext.async_session_factory, second)
        self.assertEqual((await self.output(second))["matches"], [])

    async def test_evidence_wakes_limit_new_claims_manual_checks_whole_case(self):
        async with ext.async_session_factory() as db:
            st = await db.get(Statement, self.said["roster_y"].id)
            await tasks.wake(
                db, self.task.id, "only-name", "evidence", {"run": str(st.run_id)}
            )
            task = await db.get(AgentTask, self.task.id)
            wakes = list(await db.scalars(select(AgentWake)))
            run = await tasks.queue(db, task, wakes, read.FIXED)
            await db.commit()
            rid = run.id
        await process(ext.async_session_factory, rid)
        out = await self.output(rid)
        self.assertEqual(out["screened"], 1)
        self.assertEqual(out["matches"], [])
        async with ext.async_session_factory() as db:
            await tasks.wake(db, self.task.id, "manual", "manual", {})
            wakes = list(
                await db.scalars(select(AgentWake).where(AgentWake.run_id.is_(None)))
            )
            run = await tasks.queue(
                db, await db.get(AgentTask, self.task.id), wakes, read.FIXED
            )
            await db.commit()
            rid = run.id
        await process(ext.async_session_factory, rid)
        self.assertGreater((await self.output(rid))["outcome"]["proposed"], 0)

    async def test_masking_without_reveal_and_last_good_survives_failure(self):
        async with ext.async_session_factory() as db:
            task = await db.get(AgentTask, self.task.id)
            task.spec = {"question": "Fatimah Zahra"}
            await db.commit()
        rid = await self.queue()
        await process(ext.async_session_factory, rid)
        self.assertTrue((await self.output(rid))["matches"])
        async with ext.async_session_factory() as db:
            with mock.patch("sealbook.agents.standing.granted", return_value=False):
                shown = await standing.show(db, self.user, self.task.id)
            self.assertTrue(shown["matches"][0]["locked"])
            self.assertNotIn("Fatimah", str(shown["matches"]))
        failed = await self.queue()
        async with ext.async_session_factory() as db:
            await write_run_failure(db, await db.get(Run, failed))
        await tasks.settle(ext.async_session_factory, failed)
        async with ext.async_session_factory() as db:
            with (
                mock.patch("sealbook.agents.standing.granted", return_value=True),
                mock.patch(
                    "sealbook.agents.brief.log_view", new_callable=mock.AsyncMock
                ),
            ):
                shown = await standing.show(db, self.user, self.task.id)
            self.assertIn("Fatimah", str(shown["matches"]))
            self.assertEqual(shown["history"][0]["state"], "failed")

    async def test_one_task_per_person_case_and_agent_runs_and_settles(self):
        client = self.app.test_client()
        headers = await self.login(client)
        for kind, agent in (
            ("contradictions", "Contradiction hunter"),
            ("gaps", "Gap finder"),
        ):
            item = {"investigation": fixtures.CASE, "kind": kind}
            first = await client.post(
                "/api/agents/tasks", json={"item": item}, headers=headers
            )
            again = await client.post(
                "/api/agents/tasks", json={"item": item}, headers=headers
            )
            self.assertEqual((first.status_code, again.status_code), (201, 200))
            tid = (await first.get_json())["id"]
            self.assertEqual((await again.get_json())["id"], tid)
            listed = await client.get(
                "/api/agents/tasks",
                query_string={"investigation": fixtures.CASE, "kind": kind},
                headers=headers,
            )
            self.assertEqual(
                [t["id"] for t in (await listed.get_json())["items"]], [tid]
            )
            async with ext.async_session_factory() as db:
                task = await db.get(AgentTask, uuid.UUID(tid))
                run = await tasks.queue(db, task, [], read.FIXED)
                await db.commit()
                rid = run.id
                principal = await db.get(Principal, run.principal_id)
                self.assertEqual((run.tool, principal.name), (kind, agent))
            await process(ext.async_session_factory, rid)
            self.assertIn("outcome", await self.output(rid))
            await tasks.settle(ext.async_session_factory, rid)
            async with ext.async_session_factory() as db:
                task = await db.get(AgentTask, uuid.UUID(tid))
                self.assertIsNotNone(task.last_success_at)
                self.assertEqual(task.failures, 0)


async def write_run_failure(db, run):
    from sealbook.ledger.queue import fail

    await fail(db, run, "RuntimeError: The local check could not finish.")
    await db.commit()
