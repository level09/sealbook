"""Sources list against a throwaway Postgres database migrated to head, filled by the
same synthetic seeder that proves the page at 20,000 rows."""

import random
import re
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest import mock

import stk
import stk.extensions as ext
from alembic import command
from quart_security import hash_password
from sqlalchemy import func, insert, select
from sqlalchemy.engine import make_url
from stk.agent_login import create_agent_login_token
from stk.migrations import (
    build_alembic_config,
    throwaway_database_url,
)
from stk.user.models import Role, User

from sealbook.app import app as sealbook_app
from sealbook.app import create_app
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Principal,
    Statement,
    StatementTier,
)
from sealbook.settings import Config
from sealbook.sources import scale

POSTGRES = make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql"
A, B = "case-scale-test-a", "case-scale-test-b"


def hexs(n=64):
    return f"{random.getrandbits(n * 4):0{n}x}"


@unittest.skipUnless(POSTGRES, "sources need Postgres (set SQLALCHEMY_DATABASE_URI)")
class SourcesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(
            throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True)
        )
        config = build_alembic_config(sealbook_app, url, quiet=True)
        command.upgrade(config, "heads")
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

        self.app = create_app(TestConfig)
        self.enterContext(
            mock.patch.object(
                Config, "BLOB_DIR", self.enterContext(tempfile.TemporaryDirectory())
            )
        )

    async def asyncSetUp(self):
        async with ext.async_session_factory() as s, s.begin():
            analyst = (
                await s.execute(select(Role).where(Role.name == "analyst"))
            ).scalar_one()
            self.users = {}
            for email, roles in (
                ("analyst@example.com", [analyst]),
                ("bob@example.com", [analyst]),
                ("nobody@example.com", []),
            ):
                user = User(
                    email=email,
                    name=email.split("@")[0],
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                user.roles.extend(roles)
                s.add(user)
                await s.flush()
                self.users[email] = user.id
            self.truth = await scale.seed(s, A, 150, rng_seed=1)
            self.other = await scale.seed(s, B, 20, rng_seed=2)

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def client(self, email="analyst@example.com"):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(email, "/sources")
        self.assertEqual(
            (await client.get(f"/_test/login?token={token}")).status_code, 302
        )
        return client

    async def get(self, client, **params):
        r = await client.get("/api/sources", query_string=params)
        return r.status_code, await r.get_json()

    async def all_rows(self, client, **params):
        out, page = [], 1
        while True:
            status, body = await self.get(client, page=page, per_page=100, **params)
            self.assertEqual(status, 200, body)
            out += body["items"]
            if len(out) >= body["total"]:
                return out, body["total"]
            page += 1

    async def test_access_is_analyst_only(self):
        anon = self.app.test_client()
        self.assertNotEqual((await anon.get("/api/sources")).status_code, 200)
        nobody = await self.client("nobody@example.com")
        self.assertEqual((await nobody.get("/api/sources")).status_code, 403)
        self.assertEqual((await nobody.get("/sources")).status_code, 403)
        analyst = await self.client()
        page = await analyst.get(f"/sources?investigation={A}")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="sources-boot"', await page.get_data(as_text=True))

    async def test_paging_and_scoping(self):
        c = await self.client()
        status, body = await self.get(c, investigation=A, per_page=40, page=4)
        self.assertEqual((status, body["total"], len(body["items"])), (200, 150, 30))
        rows, total = await self.all_rows(c, investigation=A)
        self.assertEqual(total, 150)
        self.assertEqual({r["sha256"] for r in rows}, set(self.truth))
        _, everything = await self.all_rows(c)
        self.assertEqual(everything, 170)
        status, body = await self.get(c, investigation=A, page=99)
        self.assertEqual((status, body["items"], body["total"]), (200, [], 150))
        for bad in ({"per_page": 101}, {"page": 0}, {"sort": "hash"}, {"order": "up"}):
            self.assertEqual((await self.get(c, **bad))[0], 400, bad)
        for bad in ({"type": "doc"}, {"from": "soon"}, {"assignee": "x"}, {"by": "1"}):
            self.assertEqual((await self.get(c, **bad))[0], 400, bad)

    async def test_counts_match_the_seeded_truth(self):
        c = await self.client()
        rows, _ = await self.all_rows(c, investigation=A)
        for r in rows:
            t = self.truth[r["sha256"]]
            self.assertEqual(
                (r["claims"], r["pending"], r["findings"], r["c2pa"], r["sensitive"]),
                (t["claims"], t["pending"], t["findings"], t["c2pa"], t["sensitive"]),
            )
            # No reveal was made in this session: a sensitive source stays locked.
            self.assertEqual(r["revealed"], not r["sensitive"])
            status = (
                "failed"
                if t["failed"]
                else "needs review"
                if t["pending"]
                else "indexed"
            )
            self.assertEqual(r["status"], status)
            want = t.get("assignee")
            self.assertEqual(r["assignee"] and r["assignee"]["id"], want)

    async def test_sort_on_every_column(self):
        c = await self.client()
        keys = {
            "claims": lambda r: r["claims"],
            "findings": lambda r: r["findings"],
            "size": lambda r: r["size"],
            "title": lambda r: r["title"],
            "acquired_at": lambda r: r["acquired_at"],
            "c2pa": lambda r: r["c2pa"],
            "status": lambda r: r["status"],
            "type": lambda r: r["type"],
            "origin": lambda r: r["origin"],
            "by": lambda r: r["by"]["name"],
            "assignee": lambda r: r["assignee"]["name"] if r["assignee"] else "~",
        }
        for sort, key in keys.items():
            for order in ("asc", "desc"):
                rows, total = await self.all_rows(
                    c, investigation=A, sort=sort, order=order
                )
                self.assertEqual(total, 150, sort)
                got = [key(r) for r in rows]
                if sort == "assignee" and order == "desc":
                    # Nulls sort last either way.
                    named = [g for g in got if g != "~"]
                    self.assertEqual(named, sorted(named, reverse=True))
                    self.assertTrue(all(g == "~" for g in got[len(named) :]))
                    continue
                if sort in ("title", "origin", "by", "assignee", "c2pa", "status"):
                    # Postgres collation, compare case-folded.
                    got = [g.lower() if isinstance(g, str) else g for g in got]
                self.assertEqual(
                    got, sorted(got, reverse=order == "desc"), f"{sort} {order}"
                )

    async def test_filters(self):
        c = await self.client()
        truth = self.truth

        async def shas(**params):
            rows, total = await self.all_rows(c, investigation=A, **params)
            self.assertEqual(total, len(rows))
            return {r["sha256"] for r in rows}

        self.assertEqual(
            await shas(sensitive=1), {s for s, t in truth.items() if t["sensitive"]}
        )
        self.assertEqual(
            await shas(sensitive=0), {s for s, t in truth.items() if not t["sensitive"]}
        )
        self.assertEqual(
            await shas(findings=1), {s for s, t in truth.items() if t["findings"]}
        )
        self.assertEqual(
            await shas(c2pa="valid,absent"),
            {s for s, t in truth.items() if t["c2pa"] in ("valid", "absent")},
        )
        self.assertEqual(
            await shas(assignee="unassigned"),
            {s for s, t in truth.items() if "assignee" not in t},
        )
        me = self.users["analyst@example.com"]
        self.assertEqual(
            await shas(assignee="me"),
            {s for s, t in truth.items() if t.get("assignee") == me},
        )
        images = await shas(type="image")
        rows, _ = await self.all_rows(c, investigation=A)
        self.assertEqual(images, {r["sha256"] for r in rows if r["type"] == "image"})
        self.assertTrue(images)
        one = rows[0]
        self.assertIn(one["sha256"], await shas(q=one["title"].upper()))
        self.assertEqual(await shas(q=one["sha256"][:10]), {one["sha256"]})
        self.assertEqual(await shas(q="100%_"), set())
        by = await shas(by=one["by"]["id"])
        self.assertEqual(by, {r["sha256"] for r in rows if r["by"] == one["by"]})
        day = one["acquired_at"][:10]
        self.assertEqual(
            await shas(**{"from": day, "to": day}),
            {r["sha256"] for r in rows if r["acquired_at"][:10] == day},
        )
        # Combined: count filters and base filters together.
        self.assertEqual(
            await shas(sensitive=1, findings=1),
            {s for s, t in truth.items() if t["sensitive"] and t["findings"]},
        )

    async def test_select_all_matching_returns_every_sha(self):
        c = await self.client()
        r = await c.get(
            "/api/sources/ids", query_string={"investigation": A, "findings": 1}
        )
        body = await r.get_json()
        want = {s for s, t in self.truth.items() if t["findings"]}
        self.assertEqual((set(body["ids"]), body["total"]), (want, len(want)))

    async def test_sensitivity_and_title_follow_the_monitor_rule(self):
        """Any sensitive acquisition makes a source sensitive; the title comes from
        the earliest one; claims on a derived text blob count for its source."""
        now = datetime.now(UTC)
        sha, text = hexs(), hexs()
        async with ext.async_session_factory() as s, s.begin():
            who = (await s.execute(select(Principal).limit(1))).scalar_one()
            for b, mime in ((sha, "image/png"), (text, "text/plain")):
                s.add(Blob(sha256=b, md5=hexs(32), size=10, mime=mime, storage_ref="x"))
            await s.flush()
            for at, file, sens in (
                (now - timedelta(days=2), "first.png", "ordinary"),
                (now, "second.png", "sensitive"),
            ):
                s.add(
                    Acquisition(
                        id=uuid.uuid4(),
                        blob_sha256=sha,
                        origin=f"device:{file}",
                        collector_id=who.id,
                        at=at,
                        context={"file": file, "investigation": A},
                        sensitivity=sens,
                        doc_type="photo",
                    )
                )
            commitment = hexs()
            s.add(
                Payload(
                    commitment=commitment,
                    salt=b"0" * 32,
                    value={"blob": sha},
                    value_key=hexs(),
                    idem_key=hexs(),
                )
            )
            rows = [
                {
                    "subject": f"blob:{text}",
                    "prop": Prop.DERIVED_FROM,
                    "commitment": commitment,
                },
                {
                    "subject": f"person:{uuid.uuid4()}",
                    "prop": Prop.NAME,
                    "span_blob": text,
                },
            ]
            for row in rows:
                sid = uuid.uuid4()
                await s.execute(
                    insert(Statement).values(
                        id=sid,
                        author_id=who.id,
                        recorded_at=now,
                        hash=hexs(),
                        evidence=[],
                        **{"commitment": hexs(), **row},
                    )
                )
                s.add(
                    StatementTier(statement_id=sid, tier=Tier.UNCONFIRMED, reason="t")
                )
        c = await self.client()
        _, body = await self.get(c, investigation=A, q="first.png")
        [row] = body["items"]
        self.assertEqual((row["sha256"], row["sensitive"]), (sha, True))
        self.assertEqual(
            row["acquired_at"][:10], (now - timedelta(days=2)).date().isoformat()
        )
        self.assertEqual(
            (row["claims"], row["pending"], row["status"]), (1, 1, "needs review")
        )
        _, body = await self.get(c, investigation=A, sensitive=0, q="first.png")
        self.assertEqual(body["total"], 0)

    async def test_request_review_of_open_proposals(self):
        from sealbook.collaboration.models import Notification

        sha, t = next((k, v) for k, v in self.truth.items() if v["pending"] >= 2)
        async with ext.async_session_factory() as s, s.begin():
            pending = list(
                await s.scalars(
                    select(Statement)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .where(
                        Statement.span_blob == sha,
                        StatementTier.tier == Tier.UNCONFIRMED,
                    )
                )
            )
            # The seeder writes no payloads; give this source's proposals values.
            for st in pending:
                s.add(
                    Payload(
                        commitment=st.commitment,
                        salt=b"0" * 32,
                        value={"text": "synthetic", "lang": "en"},
                        value_key=hexs(),
                        idem_key=hexs(),
                    )
                )
        c = await self.client()
        page = await (await c.get("/sources")).get_data(as_text=True)
        csrf = {
            "X-CSRFToken": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        }

        async def post(**item):
            r = await c.post(
                "/api/sources/review",
                headers=csrf,
                json={"item": {"investigation": A, "shas": [sha], **item}},
            )
            return r.status_code, await r.get_json()

        self.assertEqual(
            await post(dry_run=True), (200, {"proposals": t["pending"], "sources": 1})
        )
        bob = self.users["bob@example.com"]
        me = self.users["analyst@example.com"]
        self.assertEqual((await post(reviewer=me, reason="check"))[0], 400)
        self.assertEqual((await post(reviewer=bob, reason=" "))[0], 400)
        status, body = await post(reviewer=bob, reason="check the names")
        self.assertEqual((status, body["requested"]), (201, t["pending"]))
        async with ext.async_session_factory() as s:
            asked = await s.scalar(
                select(func.count()).where(Statement.prop == Prop.REVIEW_REQUEST)
            )
            notes = list(await s.scalars(select(Notification)))
        self.assertEqual(asked, t["pending"])
        self.assertEqual([(n.user_id, n.kind) for n in notes], [(bob, "assigned")])
        self.assertEqual((await post(dry_run=True))[1], {"proposals": 0, "sources": 1})
        other = next(iter(self.other))
        r = await c.post(
            "/api/sources/review",
            headers=csrf,
            json={"item": {"investigation": A, "shas": [other], "dry_run": True}},
        )
        self.assertEqual(r.status_code, 403)
        r = await c.post(
            "/api/sources/review",
            headers=csrf,
            json={"item": {"investigation": A, "shas": ["x"], "dry_run": True}},
        )
        self.assertEqual(r.status_code, 400)


class SeederGuardTest(unittest.TestCase):
    def test_refuses_real_databases_and_cases(self):
        self.assertFalse(scale.throwaway("postgresql+asyncpg:///sealbook"))
        self.assertTrue(scale.throwaway("postgresql+asyncpg:///sealbook_scale"))
        self.assertTrue(scale.throwaway("postgresql+asyncpg:///stk_tmp_abc"))
        import asyncio

        with self.assertRaises(ValueError):
            asyncio.run(scale.seed(None, "case-demo-02", 1))


if __name__ == "__main__":
    unittest.main()
