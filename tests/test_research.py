"""Web research and capture against recorded synthetic responses: no network at all.
Brave Search and Cloudflare Browser Rendering answers come from
tests/fixtures/research (hand-made, shaped after the published schemas, never
captured traffic); every outbound request goes to one MockTransport that records it,
and yt-dlp is replaced by a function that copies a local clip."""

import io
import shutil
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import httpx
import orjson
from PIL import Image
from quart_security import hash_password
from sqlalchemy import select

import stk.extensions as ext
from stk import blobs, netguard
from stk.agent_login import create_agent_login_token
from stk.ingest import service as ingest
from stk.ledger.contracts import Prop, StatementIn
from stk.ledger.models import Acquisition, Blob, Payload, Run, Statement, StatementTier
from stk.ledger.write import (
    append,
    principal_for_user,
    record_acquisition,
    verify_db,
)
from stk.people.data import erase
from stk.places.models import Place, point
from stk.research import brave, cloudflare, service, store, video
from stk.research import queries as rq
from stk.research import views as research_views
from stk.runs.worker import work_once
from stk.settings import Config
from stk.user.models import Role, User
from tests import test_review
from tests.passkey import attester

FIX = Path(__file__).resolve().parent / "fixtures" / "research"
ROOT = Path(__file__).resolve().parent.parent / "acceptance"
PHOTO = ROOT / "data" / "photo_damaged_building.jpg"
CLIP = ROOT / "public" / "jdeideh_2015.webm"
PUBLIC_IP = ["93.184.215.14"]
NAME = "Lina Najjar"
SECRET = "محمد"


def fixture(name: str) -> dict:
    return orjson.loads((FIX / name).read_bytes())


def jpeg(path: Path, size=None, quality=90) -> bytes:
    im = Image.open(path).convert("RGB")
    if size:
        im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


class Web:
    """The whole outside world for these tests, keyed by host and path."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.login_pages: set[str] = set()
        other = io.BytesIO()
        Image.linear_gradient("L").convert("RGB").save(other, "JPEG")
        self.files = {
            ("imgs.search.brave.com", "/img-thumb-1"): (jpeg(PHOTO, 400), "image/jpeg"),
            ("imgs.search.brave.com", "/web-thumb-1"): (jpeg(PHOTO, 300), "image/jpeg"),
            ("imgs.search.brave.com", "/vid-thumb-1"): (other.getvalue(), "image/jpeg"),
            # The page serves a re-encoded copy of the photo already in the case.
            ("news.example.org", "/media/photo.jpg"): (
                jpeg(PHOTO, quality=70),
                "image/jpeg",
            ),
            ("news.example.org", "/media/clip.webm"): (
                CLIP.read_bytes(),
                "application/octet-stream",
            ),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers["host"]
        path = request.url.path
        if host == "api.search.brave.com":
            kind = path.split("/")[-2]
            return httpx.Response(200, json=fixture(f"brave_{kind}.json"))
        if host == "api.cloudflare.com":
            page = orjson.loads(request.content)["url"]
            name = "cf_login.json" if page in self.login_pages else "cf_snapshot.json"
            return httpx.Response(
                200, json=fixture(name), headers={"X-Browser-Ms-Used": "1834"}
            )
        if (host, path) in self.files:
            data, mime = self.files[(host, path)]
            return httpx.Response(200, content=data, headers={"content-type": mime})
        return httpx.Response(404)

    def sent(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.headers["host"] == host]


class PureTest(unittest.TestCase):
    def test_page_media_and_login_form(self):
        html = fixture("cf_snapshot.json")["result"]["content"]
        media, password = cloudflare.read(html, "https://news.example.org/2013/07/a")
        self.assertFalse(password)
        self.assertEqual(
            [(m["url"], m["kind"]) for m in media],
            [
                ("https://news.example.org/media/photo.jpg", "image"),
                ("https://news.example.org/media/clip.webm", "video"),
            ],
        )
        login = fixture("cf_login.json")["result"]["content"]
        self.assertTrue(cloudflare.read(login, "https://x.example.org/")[1])

    def test_clean_keeps_exactly_what_was_confirmed(self):
        q = service.clean([{"text": '  "Lina   Najjar"  ', "kinds": ["web", "web"]}])
        self.assertEqual(q[0]["text"], '"Lina Najjar"')
        self.assertEqual(q[0]["kinds"], ["web"])
        for bad in (
            [],
            [{"text": "", "kinds": ["web"]}],
            [{"text": "x", "kinds": ["maps"]}],
            [{"text": "x" * 301, "kinds": ["web"]}],
            [{"text": "x", "kinds": ["web"]}] * 9,
        ):
            with self.assertRaises(ValueError):
                service.clean(bad)


class BraveTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(Config, "BRAVE_API_KEY", "test-key"))
        self.web = Web()
        self.transport = httpx.MockTransport(self.web)

    async def test_three_kinds_parse_into_one_shape(self):
        web = await brave.search("web", '"Lina Najjar"', 20, self.transport)
        self.assertEqual(len(web), 1)  # the ftp result is dropped
        self.assertEqual(web[0].title, "Families ask about Lina Najjar")
        self.assertIn("checkpoint & never", web[0].description)
        self.assertEqual(
            (web[0].domain, web[0].date[:10]), ("news.example.org", "2013-07-21")
        )
        images = await brave.search("images", "x", 20, self.transport)
        self.assertEqual(
            images[0].media_url, "https://news.example.org/media/photo.jpg"
        )
        videos = await brave.search("videos", "x", 80, self.transport)
        self.assertEqual(videos[0].url, "https://video.example.net/watch/abc123")
        sent = self.web.sent("api.search.brave.com")
        self.assertEqual(sent[0].headers["X-Subscription-Token"], "test-key")
        self.assertEqual(sent[0].url.params["q"], '"Lina Najjar"')
        self.assertEqual(sent[2].url.params["count"], "50")

    async def test_quota_and_missing_key_are_plain_errors(self):
        quota = httpx.MockTransport(lambda r: httpx.Response(429))
        with self.assertRaisesRegex(brave.SearchError, "quota"):
            await brave.search("web", "x", 5, quota)
        with mock.patch.object(Config, "BRAVE_API_KEY", None):
            with self.assertRaisesRegex(brave.SearchError, "BRAVE_API_KEY"):
                await brave.search("web", "x", 5, self.transport)
        self.assertEqual(len(self.web.requests), 0)

    async def test_renderer_refusal_names_the_reason(self):
        failing = httpx.MockTransport(
            lambda r: httpx.Response(200, json=fixture("cf_error.json"))
        )
        with (
            mock.patch.object(Config, "CLOUDFLARE_ACCOUNT_ID", "acct"),
            mock.patch.object(Config, "CLOUDFLARE_API_TOKEN", "test-token"),
        ):
            with self.assertRaisesRegex(cloudflare.RenderError, "Rate limit exceeded"):
                await cloudflare.snapshot("https://news.example.org/", failing)
            snap = await cloudflare.snapshot(
                "https://news.example.org/", self.transport
            )
        self.assertEqual(snap.screenshot[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(snap.browser_ms, "1834")
        sent = self.web.sent("api.cloudflare.com")[0]
        self.assertEqual(sent.headers["Authorization"], "Bearer test-token")
        self.assertTrue(
            sent.url.path.endswith("/accounts/acct/browser-rendering/snapshot")
        )


@unittest.skipUnless(test_review.POSTGRES, "research needs Postgres")
class ResearchTest(unittest.IsolatedAsyncioTestCase):
    setUp_review = test_review.ReviewTest.setUp
    client = test_review.ReviewTest.client

    def setUp(self):
        self.setUp_review()
        for key, value in {
            "BRAVE_API_KEY": "test-key",
            "CLOUDFLARE_ACCOUNT_ID": "acct",
            "CLOUDFLARE_API_TOKEN": "test-token",
            "RESEARCH_SEARCH_INTERVAL": 0,
            "RUN_MODELS": {},
            "SPEECH_MODEL": "",
        }.items():
            self.enterContext(mock.patch.object(Config, key, value))
        self.enterContext(
            mock.patch.object(netguard, "resolve", return_value=PUBLIC_IP)
        )
        # Any request that misses the MockTransport would reach the network: refuse it.
        self.enterContext(
            mock.patch.object(
                httpx.AsyncHTTPTransport,
                "handle_async_request",
                side_effect=AssertionError("real network call"),
            )
        )
        self.web = Web()
        self.transport = httpx.MockTransport(self.web)
        real = service.search
        self.enterContext(
            mock.patch.object(
                research_views.service,
                "search",
                lambda *a, **k: real(*a, **k, transport=self.transport),
            )
        )
        store.clear()

    async def asyncSetUp(self):
        await test_review.ReviewTest.asyncSetUp(self)
        async with ext.async_session_factory() as s, s.begin():
            roles = {r.name: r for r in (await s.execute(select(Role))).scalars()}
            if "admin" not in roles:
                roles["admin"] = Role(name="admin")
                s.add(roles["admin"])
            for email, role in (
                ("admin@example.com", "admin"),
                ("analyst-2@example.com", "analyst"),
                ("nobody@example.com", None),
            ):
                user = User(
                    email=email,
                    name=email.split("@")[0],
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                if role:
                    user.roles.append(roles[role])
                s.add(user)
            self.human, self.key = await attester(s, "clerk")
            ordinary = (
                await blobs.put(
                    s, f"Witness list: {NAME}".encode(), "text/plain", Config.BLOB_DIR
                )
            ).sha256
            hidden = (
                await blobs.put(
                    s, b"Closed case: Samir Haddad", "text/plain", Config.BLOB_DIR
                )
            ).sha256
            for sha, case, sens in (
                (ordinary, "case-r", "ordinary"),
                (hidden, "case-closed", "ordinary"),
            ):
                await record_acquisition(
                    s,
                    blob_sha256=sha,
                    origin="device:desk",
                    collector_id=self.agent.id,
                    at=datetime.now(UTC),
                    context={"investigation": case},
                    sensitivity=sens,
                    doc_type="register",
                )
            self.person = f"person:{uuid.uuid4()}"
            text = test_review.TEXT
            for sha, value, start in (
                (ordinary, NAME, len("Witness list: ")),
                (self.sha, SECRET, text.index(SECRET)),  # sensitive source
                (hidden, "Samir Haddad", len("Closed case: ")),  # blocked case
            ):
                await append(
                    s,
                    StatementIn(
                        subject=self.person,
                        prop=Prop.NAME,
                        value={
                            "text": value,
                            "lang": "ar" if value == SECRET else "en",
                        },
                        author_id=self.human.id,
                        span_blob=sha,
                        span={"start": start, "end": start + len(value)},
                    ),
                    sign=self.key.sig,
                )
            admin = await s.scalar(
                select(User).where(User.email == "admin@example.com")
            )
            who = await principal_for_user(s, admin.id, "admin")
            await service.set_case(s, "case-closed", who.id, research=False)
            # The photo already held in the case, fingerprinted.
            await ingest.ingest_bytes(
                s,
                PHOTO.read_bytes(),
                blob_dir=Config.BLOB_DIR,
                mime="image/jpeg",
                origin="device:camera",
                collector_id=self.agent.id,
                context={"investigation": "case-r"},
                sensitivity="ordinary",
                doc_type="photo",
            )
        while await work_once(ext.async_session_factory, ["fingerprint"]):
            pass

    async def post(self, client, path, item):
        r = await client.post(path, json={"item": item}, headers=client.csrf)
        return r.status_code, await r.get_json()

    async def send(self, client, queries, scope=None, investigation="case-r"):
        return await self.post(
            client,
            "/api/research/search",
            {
                "scope": scope or self.person,
                "investigation": investigation,
                "queries": queries,
            },
        )

    async def test_proposals_use_only_values_that_may_leave(self):
        client = await self.client()
        status, body = await self.post(
            client, "/api/research/queries", {"scope": self.person}
        )
        self.assertEqual(status, 200, body)
        texts = [q["text"] for q in body["queries"]]
        self.assertEqual(texts, [f'"{NAME}"'])
        dumped = orjson.dumps(body).decode()
        self.assertNotIn(SECRET, dumped)  # sensitive source
        self.assertNotIn("Samir", dumped)  # case with research off
        self.assertEqual(body["queries"][0]["people"], [self.person])
        self.assertEqual(body["queries"][0]["why"][0]["value"], NAME)
        self.assertEqual(self.web.requests, [])  # proposing sends nothing

    async def test_place_proposals_are_gazetteer_names(self):
        async with ext.async_session_factory() as s, s.begin():
            gov = Place(
                name_ar="ريف دمشق",
                name_en="Rif Dimashq",
                kind="facility",
                geom=point(36.5, 33.5),
                geo_source="analyst",
                source="person",
            )
            s.add(gov)
            await s.flush()
            place = Place(
                name_ar="دوما",
                name_en="Douma",
                kind="facility",
                geom=point(36.4, 33.57),
                geo_source="analyst",
                source="person",
                governorate_id=gov.id,
            )
            s.add(place)
            await s.flush()
            found = await rq.propose(s, f"place:{place.id}", set())
        self.assertEqual(
            [q["text"] for q in found],
            ['"دوما"', '"دوما" ريف دمشق', '"Douma"', '"Douma" Rif Dimashq'],
        )

    async def test_send_logs_each_query_before_it_leaves_and_flags_held_copies(self):
        client = await self.client()
        q = [
            {
                "text": f'"{NAME}"',
                "kinds": ["web", "images", "videos"],
                "people": [self.person],
                "why": [],
            }
        ]
        status, body = await self.send(client, q)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.web.sent("api.search.brave.com")), 3)
        self.assertEqual([s["count"] for s in body["sent"]], [1, 1, 1])
        image = body["groups"]["images"][0]
        self.assertEqual(image["domain"], "news.example.org")
        self.assertEqual(image["why"]["query"], f'"{NAME}"')
        kinds = {c["kind"] for c in image["in_case"]}
        self.assertEqual(kinds, {"same_picture"})
        self.assertFalse(body["groups"]["videos"][0]["in_case"])
        async with ext.async_session_factory() as s:
            logs = (
                await s.execute(
                    select(Statement.subject, Payload.value, StatementTier.tier)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .where(
                        Statement.prop == Prop.ACCESS,
                        Payload.value["action"].astext == "research",
                    )
                )
            ).all()
        self.assertEqual(len(logs), 3)
        for subject, value, tier in logs:
            self.assertEqual((subject, tier), (self.person, "accepted"))
            self.assertEqual(value["query"], f'"{NAME}"')
            self.assertEqual(value["people"], [self.person])
        # The thumbnail comes from the server, never from the provider to the browser.
        r = await client.get(f"/api/research/thumb/{image['id']}")
        self.assertEqual((r.status_code, r.mimetype), (200, "image/jpeg"))
        other = await self.client("analyst-2@example.com")
        self.assertEqual(
            (await other.get(f"/api/research/thumb/{image['id']}")).status_code, 404
        )

    async def test_guardrails_refuse_before_anything_is_sent(self):
        client = await self.client()
        q = [{"text": "anything", "kinds": ["web"]}]
        status, body = await self.send(client, q, investigation="case-closed")
        self.assertEqual((status, body["reason"]), (409, "refused"))
        self.assertIn("switched off", body["message"])
        admin = await self.client("admin@example.com")
        status, _ = await self.post(
            admin, "/api/research/case", {"investigation": "case-r", "sensitive": True}
        )
        self.assertEqual(status, 200)
        status, body = await self.send(client, q)
        self.assertEqual(status, 409)
        self.assertIn("marked sensitive", body["message"])
        self.assertEqual(
            (
                await self.post(
                    client,
                    "/api/research/case",
                    {"investigation": "case-r", "sensitive": False},
                )
            )[0],
            403,
        )
        await self.post(
            admin, "/api/research/case", {"investigation": "case-r", "sensitive": False}
        )
        with mock.patch.object(Config, "BRAVE_API_KEY", None):
            status, body = await self.send(client, q)
            self.assertIn("BRAVE_API_KEY", body["message"])
            st = await (
                await client.get("/api/research/status?investigation=case-r")
            ).get_json()
            self.assertEqual(st["missing"]["search"], ["BRAVE_API_KEY"])
        self.assertEqual(
            (await self.send(client, q, investigation="no-such-case"))[0], 409
        )
        self.assertEqual(self.web.requests, [])
        with mock.patch.object(Config, "RESEARCH_QUERIES_PER_HOUR", 2):
            status, body = await self.send(
                client, [{"text": "x", "kinds": ["web", "images", "videos"]}]
            )
        self.assertEqual(
            [s.get("error") for s in body["sent"]],
            [None, None, "Hourly query limit reached"],
        )
        self.assertEqual(len(self.web.sent("api.search.brave.com")), 2)

    async def test_role_rules(self):
        nobody = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token("nobody@example.com", "/")
        await nobody.get(f"/_test/login?token={token}")
        self.assertEqual((await nobody.get("/api/research/status")).status_code, 403)
        anon = self.app.test_client()
        self.assertIn((await anon.get("/api/research/status")).status_code, (302, 401))
        client = await self.client()
        self.assertEqual(
            (await self.post(client, "/api/research/queries", {"scope": "person:x"}))[
                0
            ],
            400,
        )
        self.assertEqual(
            (
                await self.post(
                    client, "/api/research/queries", {"scope": f"person:{uuid.uuid4()}"}
                )
            )[0],
            404,
        )

    async def capture(
        self, client, result_kind, investigation="case-r", sensitivity="ordinary"
    ):
        status, body = await self.send(
            client,
            [{"text": f'"{NAME}"', "kinds": [result_kind], "people": [self.person]}],
            investigation=investigation,
        )
        self.assertEqual(status, 200, body)
        pick = body["groups"][result_kind][0]
        status, queued = await self.post(
            client,
            "/api/research/capture",
            {"ids": [pick["id"]], "sensitivity": sensitivity, "doc_type": "web_post"},
        )
        self.assertEqual(status, 200, queued)
        while await work_once(ext.async_session_factory, ["capture"], self.transport):
            pass
        r = await client.get(f"/api/research/batch/{queued['batch']}")
        return pick, queued, await r.get_json()

    async def test_capture_records_provenance_and_enters_intake(self):
        client = await self.client()
        pick, queued, progress = await self.capture(client, "images")
        self.assertEqual(progress["captures"][0]["status"], "done", progress)
        async with ext.async_session_factory() as s:
            run = await s.get(Run, uuid.UUID(queued["runs"][0]))
            manifest = orjson.loads(
                blobs.read(await s.get(Blob, run.raw_output_ref), Config.BLOB_DIR)
            )
            acqs = (
                (
                    await s.execute(
                        select(Acquisition).where(
                            Acquisition.context["batch"].astext == queued["batch"]
                        )
                    )
                )
                .scalars()
                .all()
            )
            access = await s.get(Statement, uuid.UUID(run.inputs["query_access"]))
            queued_tools = set(
                (
                    await s.execute(
                        select(Run.tool).where(
                            Run.inputs["acquisition"].astext.in_(
                                [str(a.id) for a in acqs]
                            )
                        )
                    )
                ).scalars()
            )
            self.assertEqual(await verify_db(s), [])
        self.assertEqual(run.output_hash, run.raw_output_ref)
        self.assertEqual(run.provider_served, "cloudflare")
        self.assertEqual(manifest["method"]["provider"]["name"], "brave")
        self.assertEqual(manifest["method"]["renderer"]["browser_ms"], "1834")
        self.assertIsNone(manifest["method"]["yt_dlp"])
        self.assertEqual(access.prop, Prop.ACCESS)
        roles = sorted(a.context["capture"]["role"] for a in acqs)
        self.assertEqual(roles, ["media", "media", "page", "screenshot"])
        for a in acqs:
            self.assertEqual(a.collector_id, (await self._analyst()).id)
            self.assertEqual(a.context["capture"]["run"], str(run.id))
            self.assertEqual(a.context["investigation"], "case-r")
            self.assertEqual(a.sensitivity, "ordinary")
        # The query is erasable only if no chained field carries it.
        chained = orjson.dumps([run.inputs, [a.context for a in acqs]]).decode()
        self.assertNotIn(
            NAME.split()[1], chained.replace("Families ask about Lina Najjar", "")
        )
        self.assertLessEqual(
            {"fingerprint", "thumbnail", "geotag", "index"}, queued_tools
        )
        self.assertEqual(
            sorted(a.origin for a in acqs),
            sorted(
                [f"url:{pick['url']}"] * 2
                + [
                    "url:https://news.example.org/media/clip.webm",
                    "url:https://news.example.org/media/photo.jpg",
                ]
            ),
        )
        # A re-encoded copy of a held photo is proposed as a match, never merged.
        while await work_once(ext.async_session_factory, ["fingerprint"]):
            pass
        async with ext.async_session_factory() as s:
            photo = next(a for a in acqs if a.origin.endswith("photo.jpg"))
            match = (
                (
                    await s.execute(
                        select(StatementTier.tier)
                        .join(Statement, Statement.id == StatementTier.statement_id)
                        .where(
                            Statement.subject == f"blob:{photo.blob_sha256}",
                            Statement.prop == Prop.MEDIA_MATCH,
                        )
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual(match, ["unconfirmed"])
        # The same link now shows as already in the case.
        _, body = await self.send(client, [{"text": "again", "kinds": ["web"]}])
        self.assertEqual(body["groups"]["web"][0]["in_case"][0]["kind"], "same_link")

    async def _analyst(self):
        async with ext.async_session_factory() as s:
            user = await s.scalar(
                select(User).where(User.email == "analyst@example.com")
            )
            return await principal_for_user(s, user.id, user.name)

    async def test_video_page_goes_to_yt_dlp(self):
        def fake(page, directory, max_bytes):
            path = Path(directory) / "abc123.webm"
            shutil.copy(CLIP, path)
            return path, {"id": "abc123", "extractor": "generic", "duration": 12}

        with mock.patch.object(video, "_download", fake):
            _, queued, progress = await self.capture(await self.client(), "videos")
        self.assertEqual(progress["captures"][0]["status"], "done", progress)
        async with ext.async_session_factory() as s:
            acqs = (
                (
                    await s.execute(
                        select(Acquisition).where(
                            Acquisition.context["batch"].astext == queued["batch"]
                        )
                    )
                )
                .scalars()
                .all()
            )
        clip = next(a for a in acqs if a.context["capture"]["role"] == "video")
        self.assertEqual(clip.origin, "url:https://video.example.net/watch/abc123")
        self.assertEqual(clip.context["capture"]["method"]["yt_dlp"], video.VERSION)
        self.assertEqual(clip.context["capture"]["video"]["id"], "abc123")

    async def test_login_wall_and_late_switch_fail_the_capture_plainly(self):
        self.web.login_pages.add("https://news.example.org/2013/07/families-ask")
        _, queued, progress = await self.capture(await self.client(), "web")
        self.assertEqual(progress["captures"][0]["status"], "failed")
        self.assertIn("login", progress["captures"][0]["error"])
        self.assertEqual(progress["intake"]["items"], [])

    async def test_a_case_switched_off_after_queueing_is_not_captured(self):
        client = await self.client()
        _, body = await self.send(client, [{"text": "x", "kinds": ["web"]}])
        _, queued = await self.post(
            client, "/api/research/capture", {"ids": [body["groups"]["web"][0]["id"]]}
        )
        admin = await self.client("admin@example.com")
        await self.post(
            admin, "/api/research/case", {"investigation": "case-r", "research": False}
        )
        self.assertTrue(
            await work_once(ext.async_session_factory, ["capture"], self.transport)
        )
        progress = await (
            await client.get(f"/api/research/batch/{queued['batch']}")
        ).get_json()
        self.assertEqual(progress["captures"][0]["status"], "failed")
        self.assertIn("switched off", progress["captures"][0]["error"])
        self.assertEqual(self.web.sent("api.cloudflare.com"), [])

    async def test_erasing_the_person_removes_the_queries(self):
        client = await self.client()
        await self.send(
            client, [{"text": f'"{NAME}"', "kinds": ["web"], "people": [self.person]}]
        )
        async with ext.async_session_factory() as s, s.begin():
            await erase(s, self.person, self.human.id, "request")
        async with ext.async_session_factory() as s:
            left = await s.scalar(
                select(Payload.value["query"].astext)
                .join(Statement, Statement.commitment == Payload.commitment)
                .where(
                    Statement.prop == Prop.ACCESS,
                    Payload.value["action"].astext == "research",
                )
            )
        self.assertIsNone(left)
