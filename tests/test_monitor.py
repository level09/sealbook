"""Evidence monitor against a throwaway Postgres database migrated to head."""

import asyncio
import re
import tempfile
import unittest
import uuid
from datetime import UTC, date, datetime
from io import BytesIO
from unittest import mock

import orjson
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PIL import Image
from quart_security import hash_password
from sqlalchemy import select
from sqlalchemy.engine import make_url

import stk.extensions as ext
from alembic import command
from stk import blobs
from stk.agent_login import create_agent_login_token
from stk.app import create_app
from stk.ledger.contracts import Event, Prop, StatementIn
from stk.ledger.models import Principal, Run
from stk.ledger.write import append, emit, record_acquisition
from stk.migrations import (
    build_alembic_config,
    get_database_url,
    throwaway_database_url,
)
from stk.monitor import filter as mfilter
from stk.monitor.data import map_layer, track, tracks
from stk.places import cli as places
from stk.places import resolve
from stk.settings import Config
from stk.user.models import Role, User
from tests.passkey import attester

POSTGRES = make_url(get_database_url()).get_backend_name() == "postgresql"


def png(color: str) -> bytes:
    """A real image, so span validation has page sizes to check against."""
    out = BytesIO()
    Image.new("RGB", (64, 64), color).save(out, "PNG")
    return out.getvalue()


class TrackTest(unittest.TestCase):
    def test_conflicting_positions_fork_and_silences_are_gaps(self):
        def pt(i, place, day, prop="seen_at", inherited=False):
            return {
                "id": i,
                "place": place,
                "place_id": None,
                "from": day,
                "prop": prop,
                "inherited": inherited,
            }

        t = track(
            [
                pt("a", "Harasta", "2013-05-02", "arrested_at"),
                pt("b", "Douma", "2013-05-02", "last_seen"),
                pt("c", "Branch", "2013-05-03", "detention_branch", inherited=True),
                pt("d", "Branch", "2013-09-17", "detention_branch"),
                pt("e", "Prison", "2013-09-17", "transferred_to"),
            ]
        )
        steps = t["steps"]
        self.assertEqual(
            [s["date"] for s in steps], ["2013-05-02", "2013-05-03", "2013-09-17"]
        )
        self.assertTrue(steps[0]["fork"])
        # A branch letterhead and a transfer destination on one day are a move, not a conflict.
        self.assertFalse(steps[2]["fork"])
        self.assertEqual(steps[2]["gap_days"], 137)
        self.assertEqual(t["gap_after"], "2013-09-17")

    def test_tracks_many_subjects(self):
        def p(i, subject, day):
            return {
                "id": i,
                "subject": subject,
                "place": "Branch",
                "place_id": None,
                "from": day,
                "prop": "seen_at",
                "inherited": False,
            }

        t = tracks(
            [
                p("a", "P1", "2013-07-01"),
                p("b", "P2", "2013-07-02"),
                p("c", "P1", "2013-09-17"),
            ]
        )
        self.assertEqual(set(t), {"P1", "P2"})
        self.assertEqual(t["P1"]["steps"][1]["gap_days"], 78)
        self.assertEqual(len(t["P2"]["steps"]), 1)

    def test_two_spellings_of_one_place_never_fork_on_map_or_story(self):
        from types import SimpleNamespace

        def row(text):
            return {
                "id": str(uuid.uuid4()),
                "prop": "seen_at",
                "masked": False,
                "display": text,
                "from": "2016-11-17",
                "when": "2016-11-17",
                "inherited": False,
                "tier": "accepted",
                "subject": "person:x",
                "subject_label": "x",
                "actor": {"agent": False},
            }

        aleppo = SimpleNamespace(id=uuid.uuid4(), lon=37.16, lat=36.2)
        douma = SimpleNamespace(id=uuid.uuid4(), lon=36.4, lat=33.57)
        en, ar, other = row("Aleppo"), row("حلب"), row("Douma")
        places = {uuid.UUID(en["id"]): aleppo, uuid.UUID(ar["id"]): aleppo}
        # The map's tracks and the Stories rows both come from track().
        [step] = map_layer([en, ar], places)["tracks"]["person:x"]["steps"]
        self.assertFalse(step["fork"])
        layer = map_layer([en, ar, other], places | {uuid.UUID(other["id"]): douma})
        [step] = layer["tracks"]["person:x"]["steps"]
        self.assertTrue(step["fork"])
        story_points = [p | {"subject": "x"} for p in layer["points"][:2]]
        [step] = tracks(story_points)["x"]["steps"]
        self.assertFalse(step["fork"])
        # Unresolved names compare by normalised text: case and spacing never fork.
        loose = [
            {"id": i, "place": t, "place_id": None, "from": "2016-11-17"}
            | {"prop": "seen_at", "inherited": False}
            for i, t in (("a", "Douma "), ("b", "douma"))
        ]
        self.assertFalse(track(loose)["steps"][0]["fork"])

    def test_unresolved_stay_visible_on_map(self):
        row = {
            "id": str(uuid.uuid4()),
            "prop": "seen_at",
            "masked": False,
            "display": "قرية مجهولة",
            "from": "2013-05-02",
            "tier": "accepted",
        }
        layer = map_layer([row], {})
        self.assertEqual(layer["points"], [])
        self.assertEqual(
            layer["unresolved"], [{"id": row["id"], "place": "قرية مجهولة", "count": 1}]
        )
        self.assertEqual(layer["attribution"], "GeoNames (CC BY 4.0)")

    def test_same_text_at_two_places_stays_two_places(self):
        from types import SimpleNamespace

        def row(day):
            return {
                "id": str(uuid.uuid4()),
                "prop": "seen_at",
                "masked": False,
                "display": "الجديدة",
                "from": day,
                "when": day,
                "inherited": False,
                "tier": "accepted",
                "subject": "person:x",
                "subject_label": "x",
                "actor": {"agent": False},
            }

        a, b = row("2013-05-02"), row("2013-05-03")
        near = SimpleNamespace(id=uuid.uuid4(), lon=36.3, lat=33.5)
        far = SimpleNamespace(id=uuid.uuid4(), lon=37.1, lat=36.2)
        layer = map_layer([a, b], {uuid.UUID(a["id"]): near, uuid.UUID(b["id"]): far})
        labels = {p["place"] for p in layer["points"]}
        self.assertEqual(len(labels), 2)
        self.assertEqual({layer["places"][label][0] for label in labels}, {36.3, 37.1})

    def test_unresolved_names_are_listed_once_with_a_count(self):
        rows = [
            {
                "id": str(uuid.uuid4()),
                "prop": "seen_at",
                "masked": False,
                "display": "قرية مجهولة",
                "from": None,
                "tier": "unconfirmed",
            }
            for _ in range(2)
        ]
        [item] = map_layer(rows, {})["unresolved"]
        self.assertEqual(item["count"], 2)

    def test_filter_parse_rejects_bad_input(self):
        self.assertEqual(mfilter.parse(None), mfilter.EMPTY)
        for raw in (
            '{"x": 1}',
            '{"time": ["2013-02-01", "2013-01-01"]}',
            "[",
            '{"time": ["a", "b"]}',
        ):
            with self.assertRaises(ValueError):
                mfilter.parse(raw)


@unittest.skipUnless(POSTGRES, "monitor needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class MonitorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        url = self.enterContext(throwaway_database_url(sync=True))
        config = build_alembic_config()
        config.config_file_name = None
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
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

        self.app = create_app(TestConfig)
        blob_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(Config, "BLOB_DIR", blob_dir))

    async def asyncSetUp(self):
        async with ext.async_session_factory() as s, s.begin():
            # The analyst role is seeded by the mcp oauth migration.
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
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                user.roles.extend(roles)
                s.add(user)
            self.agent = Principal(kind="agent", name="extractor")
            s.add(self.agent)
            await s.flush()
            self.key = Ed25519PrivateKey.generate()
            self.blobs, self.runs = {}, {}
            for case in ("case-a", "case-b"):
                color = "white" if case == "case-a" else "black"
                sha = (
                    await blobs.put(s, png(color), "image/png", Config.BLOB_DIR)
                ).sha256
                await record_acquisition(
                    s,
                    blob_sha256=sha,
                    origin=f"device:{case}",
                    collector_id=self.agent.id,
                    at=datetime.now(UTC),
                    context={"investigation": case},
                    sensitivity="sensitive",
                    doc_type="register",
                )
                run = Run(
                    principal_id=self.agent.id,
                    tool="extract",
                    inputs={"blobs": [sha]},
                    pubkey=self.key.public_key().public_bytes(
                        Encoding.Raw, PublicFormat.Raw
                    ),
                )
                s.add(run)
                await s.flush()
                self.blobs[case], self.runs[case] = sha, run.id
        self.seq = 0

    async def asyncTearDown(self):
        await ext.engine.dispose()

    async def login(self, client, email="analyst@example.com"):
        async with self.app.app_context():
            token = create_agent_login_token(email, "/monitor")
        response = await client.get(f"/_test/login?token={token}")
        self.assertEqual(response.status_code, 302)

    async def emit(self, case) -> int:
        self.seq += 1
        async with ext.async_session_factory() as s, s.begin():
            row = await emit(
                s, Event(run_id=self.runs[case], seq=self.seq, stage="page_read")
            )
            return row.id

    def stream_path(self, case):
        return (
            "/monitor/stream?filter=" + orjson.dumps({"investigation": case}).decode()
        )

    async def read_events(self, conn, n):
        """The first n run_event ids pushed on an open SSE connection."""
        ids, buffer = [], ""
        while len(ids) < n:
            buffer += (await asyncio.wait_for(conn.receive(), 5)).decode()
            *messages, buffer = buffer.split("\n\n")
            for message in messages:
                fields = dict(
                    line.split(": ", 1) for line in message.splitlines() if ": " in line
                )
                if "id" in fields:
                    self.assertEqual(
                        int(fields["id"]), orjson.loads(fields["data"])["id"]
                    )
                    ids.append(int(fields["id"]))
        return ids

    async def test_page_redirects_when_logged_out(self):
        async with self.app.test_client() as client:
            response = await client.get("/monitor")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    async def test_page_renders_for_analyst_only(self):
        async with self.app.test_client() as client:
            await self.login(client)
            response = await client.get("/monitor")
            self.assertEqual(response.status_code, 200)
            body = await response.get_data(as_text=True)
            self.assertIn('id="monitor-boot"', body)
            self.assertIn("case-a", body)
        async with self.app.test_client() as client:
            await self.login(client, "nobody@example.com")
            self.assertEqual((await client.get("/monitor")).status_code, 403)

    async def test_stream_pushes_matching_events_only(self):
        async with self.app.test_client() as client:
            await self.login(client)
            async with client.request(
                self.stream_path("case-a"), headers={"Accept": "text/event-stream"}
            ) as conn:
                await conn.send_complete()
                self.assertIn(b"retry:", await asyncio.wait_for(conn.receive(), 5))
                await self.emit("case-b")
                wanted = await self.emit("case-a")
                self.assertEqual(await self.read_events(conn, 1), [wanted])
                await conn.disconnect()

    async def test_stream_replays_from_last_event_id(self):
        first, *missed = [await self.emit("case-a") for _ in range(3)]
        await self.emit("case-b")
        async with self.app.test_client() as client:
            await self.login(client)
            async with client.request(
                self.stream_path("case-a"),
                headers={"Accept": "text/event-stream", "Last-Event-ID": str(first)},
            ) as conn:
                await conn.send_complete()
                self.assertEqual(await self.read_events(conn, 2), missed)
                live = await self.emit("case-a")
                self.assertEqual(await self.read_events(conn, 1), [live])
                await conn.disconnect()

    async def test_statement_detail_links_bytes_run_and_signer(self):
        async with ext.async_session_factory() as s, s.begin():
            st = await append(
                s,
                StatementIn(
                    subject=f"person:{uuid.uuid4()}",
                    prop=Prop.NAME,
                    value={"text": "محمد", "lang": "ar"},
                    author_id=self.agent.id,
                    run_id=self.runs["case-a"],
                    span_blob=self.blobs["case-a"],
                    span={"page": 1, "bbox": [0, 0, 10, 10]},
                ),
                sign=lambda h: {
                    "kind": "ed25519",
                    "sig": self.key.sign(h.encode()).hex(),
                },
            )
            agent_id = (
                await s.execute(
                    select(Principal.id).where(Principal.name == "extractor")
                )
            ).scalar()
        async with self.app.test_client() as client:
            await self.login(client)
            page = await (
                await client.get(f"/api/monitor/statement/{st.id}")
            ).get_json()
            # Personal data from a sensitive source never leaves the server unrevealed.
            self.assertTrue(page["masked"])
            self.assertIsNone(page["display"])
            self.assertEqual(page["doc"]["sha256"], self.blobs["case-a"])
            self.assertEqual(page["run_id"], str(self.runs["case-a"]))
            self.assertEqual(page["signer"]["id"], str(agent_id))
            self.assertTrue(page["signature"]["verifies"])
            for link in (
                f"/api/monitor/blob/{page['doc']['sha256']}",
                f"/api/monitor/run/{page['run_id']}",
                f"/api/monitor/principal/{page['signer']['id']}",
            ):
                self.assertEqual((await client.get(link)).status_code, 200, link)
            revealed = await client.post(
                "/api/monitor/reveal",
                json={"item": {"statement": str(st.id)}},
                headers={"X-CSRFToken": await self.csrf(client)},
            )
            self.assertEqual((await revealed.get_json())["display"], "محمد")
            again = await (
                await client.get(f"/api/monitor/statement/{st.id}")
            ).get_json()
            self.assertEqual([a["prop"] for a in again["about"]], ["access"])

    async def test_time_window_refilters_panels(self):
        async with ext.async_session_factory() as s, s.begin():
            await places.import_file(s, places.FIXTURE)
            [prison] = await resolve.candidates(s, "سجن ك المركزي")
            human, passkey = await attester(s, "reviewer")
            for day in ("2013-05-02", "2013-09-17"):
                claim = await append(
                    s,
                    StatementIn(
                        subject=f"person:{uuid.uuid4()}",
                        prop=Prop.TRANSFERRED_TO,
                        value={"text": "سجن ك المركزي", "lang": "ar"},
                        author_id=self.agent.id,
                        run_id=self.runs["case-a"],
                        span_blob=self.blobs["case-a"],
                        span={"page": 1, "bbox": [0, 0, 10, 10]},
                        valid_from=date.fromisoformat(day),
                    ),
                    sign=lambda h: {
                        "kind": "ed25519",
                        "sig": self.key.sign(h.encode()).hex(),
                    },
                )
                await append(
                    s,
                    StatementIn(
                        subject=f"statement:{claim.id}",
                        prop=Prop.LOCATED_AT,
                        value={"place": f"place:{prison.id}", "name": "x"},
                        author_id=human.id,
                    ),
                    sign=passkey.sig,
                )
        await self.emit("case-a")
        await self.emit("case-b")

        async def panels(f):
            q = orjson.dumps(f).decode()
            return await (
                await client.get(f"/api/monitor/panels?filter={q}")
            ).get_json()

        async with self.app.test_client() as client:
            await self.login(client)
            whole = await panels({"investigation": "case-a"})
            window = await panels(
                {"investigation": "case-a", "time": ["2013-09-01", "2013-09-30"]}
            )
            empty = await panels(
                {"investigation": "case-a", "time": ["2014-01-01", "2014-12-31"]}
            )
        self.assertEqual(len(whole["queue"]), 2)
        self.assertEqual([c["from"] for c in window["queue"]], ["2013-09-17"])
        self.assertEqual([p["from"] for p in window["map"]["points"]], ["2013-09-17"])
        self.assertEqual(len(window["feed"]), 1)
        self.assertEqual(
            [d["sha256"] for d in window["documents"]], [self.blobs["case-a"]]
        )
        # The timeline keeps the whole case so the brush can move.
        self.assertEqual(len(window["timeline"]), 2)
        for panel in ("feed", "documents", "queue"):
            self.assertEqual(empty[panel], [], panel)

    async def test_ocr_text_stands_for_its_scan(self):
        """Facts read from an OCR text match filters through the scan it came from:
        the text has no acquisition of its own."""
        scan = self.blobs["case-a"]
        sign = {
            "sign": lambda h: {
                "kind": "ed25519",
                "sig": self.key.sign(h.encode()).hex(),
            }
        }
        async with ext.async_session_factory() as s, s.begin():
            text = (
                await blobs.put(s, "حرستا".encode(), "text/plain", Config.BLOB_DIR)
            ).sha256
            for st in (
                StatementIn(
                    subject=f"blob:{text}",
                    prop=Prop.DERIVED_FROM,
                    value={"blob": scan},
                    author_id=self.agent.id,
                    run_id=self.runs["case-a"],
                ),
                StatementIn(
                    subject=f"person:{uuid.uuid4()}",
                    prop=Prop.ARRESTED_AT,
                    value={"text": "حرستا", "lang": "ar"},
                    author_id=self.agent.id,
                    run_id=self.runs["case-a"],
                    span_blob=text,
                    span={"start": 0, "end": 5},
                    valid_from=date(2013, 5, 2),
                ),
            ):
                await append(s, st, **sign)
        async with self.app.test_client() as client:
            await self.login(client)
            f = orjson.dumps(
                {"investigation": "case-a", "time": ["2013-05-01", "2013-05-31"]}
            )
            panels = await (
                await client.get(f"/api/monitor/panels?filter={f.decode()}")
            ).get_json()
        self.assertEqual([d["sha256"] for d in panels["documents"]], [scan])
        self.assertEqual({c["doc"]["sha256"] for c in panels["queue"]}, {text})

    async def csrf(self, client):
        page = await (await client.get("/monitor")).get_data(as_text=True)
        return re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)


if __name__ == "__main__":
    unittest.main()
