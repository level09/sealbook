"""Web research and capture against recorded synthetic responses: no network at all.
Brave Search answers come from tests/fixtures/research (hand-made, shaped after the
published schemas, never captured traffic); every outbound request goes to one
MockTransport that records it, and yt-dlp is replaced by a function that copies a
local clip. The capture tests that need a database replace the browser with a fake
that returns the recorded page; BrowserTest runs the real local Chromium against
static pages served from this process, with no internet."""

import http.server
import io
import shutil
import threading
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import httpx
import orjson
import stk.extensions as ext
from PIL import Image
from quart_security import hash_password
from sqlalchemy import select
from stk.agent_login import create_agent_login_token
from stk.user.models import Role, User

from sealbook import blobs, netguard
from sealbook.cases.service import ensure as ensure_case
from sealbook.ingest import service as ingest
from sealbook.ledger.contracts import Prop, StatementIn
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Run,
    Statement,
    StatementTier,
)
from sealbook.ledger.write import (
    append,
    principal_for_user,
    record_acquisition,
    verify_db,
)
from sealbook.people.data import erase
from sealbook.places.models import Place, point
from sealbook.research import brave, browser, service, store, video, youtube
from sealbook.research import queries as rq
from sealbook.research import views as research_views
from sealbook.runs.worker import work_once
from sealbook.settings import Config
from tests import test_review
from tests.passkey import attester

FIX = Path(__file__).resolve().parent / "fixtures" / "research"
ROOT = Path(__file__).resolve().parent.parent / "acceptance"
PHOTO = ROOT / "data" / "photo_damaged_building.jpg"
CLIP = ROOT / "public" / "jdeideh_2015.webm"
ip_public = browser.public
PUBLIC_IP = ["93.184.215.14"]
NAME = "Lina Najjar"
SECRET = "محمد"
INFOS = orjson.loads((FIX / "youtube_info.json").read_bytes())


def fixture_text(name: str) -> str:
    return (FIX / name).read_text()


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
        self.by_query: dict[str, str] = {}  # query text -> fixture file
        other = io.BytesIO()
        Image.linear_gradient("L").convert("RGB").save(other, "JPEG")
        self.files = {
            ("imgs.search.brave.com", "/img-thumb-1"): (jpeg(PHOTO, 400), "image/jpeg"),
            ("imgs.search.brave.com", "/web-thumb-1"): (jpeg(PHOTO, 300), "image/jpeg"),
            ("imgs.search.brave.com", "/vid-thumb-1"): (other.getvalue(), "image/jpeg"),
            # A re-encoded copy of the held photo as one video's thumbnail.
            ("i.ytimg.com", "/vi/earlyCopy01/hqdefault.jpg"): (
                jpeg(PHOTO, 480),
                "image/jpeg",
            ),
            ("i.ytimg.com", "/vi/lnNjjr2016a/hqdefault.jpg"): (
                other.getvalue(),
                "image/jpeg",
            ),
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
            name = self.by_query.get(request.url.params["q"], f"brave_{kind}.json")
            return httpx.Response(200, json=fixture(name))
        if (host, path) in self.files:
            data, mime = self.files[(host, path)]
            return httpx.Response(200, content=data, headers={"content-type": mime})
        return httpx.Response(404)

    def sent(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.headers["host"] == host]


class PureTest(unittest.TestCase):
    def test_page_media_and_login_form(self):
        html = (FIX / "page.html").read_text()
        media, password = browser.read(html, "https://news.example.org/2013/07/a")
        self.assertFalse(password)
        self.assertEqual(
            [(m["url"], m["kind"]) for m in media],
            [
                ("https://news.example.org/media/photo.jpg", "image"),
                ("https://news.example.org/media/clip.webm", "video"),
            ],
        )
        login = (FIX / "login.html").read_text()
        self.assertTrue(browser.read(login, "https://x.example.org/")[1])

    def test_link_key_ignores_noise_and_keeps_meaning(self):
        key = service.link_key
        base = key("https://news.example.org/a/b")
        for same in (
            "http://www.news.example.org/a/b/",
            "https://NEWS.example.org/a/b?utm_source=x&utm_medium=y&fbclid=1&gclid=2#top",
        ):
            self.assertEqual(key(same), base)
        self.assertNotEqual(key("https://news.example.org/a/b?id=1"), base)
        self.assertNotEqual(
            key("https://news.example.org/a/b?id=1"),
            key("https://news.example.org/a/b?id=2"),
        )
        self.assertEqual(
            key("https://x.org/p?b=2&a=1&utm_x=3"), key("https://x.org/p?a=1&b=2")
        )

    def test_window_needs_both_ends_in_order(self):
        self.assertIsNone(service.clean_window(None, ""))
        self.assertEqual(
            service.clean_window("2013-07-01", "2013-07-31"),
            ("2013-07-01", "2013-07-31"),
        )
        for bad in (
            ("2013-07-01", None),
            ("July", "2013-07-31"),
            ("2013-08-01", "2013-07-31"),
        ):
            with self.assertRaises(ValueError):
                service.clean_window(*bad)

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


class YouTubePureTest(unittest.TestCase):
    def test_flat_entries_become_videos_and_channels_are_dropped(self):
        rows = orjson.loads((FIX / "youtube_search.json").read_bytes())["entries"]
        found = [v for r in rows if (v := youtube._video(r))]
        self.assertEqual(
            [v.video_id for v in found], ["lnNjjr2016a", "rcpVideo001", "earlyCopy01"]
        )
        v = found[0]
        self.assertEqual(v.url, "https://www.youtube.com/watch?v=lnNjjr2016a")
        self.assertEqual((v.date, v.approximate), ("2016-03-01", True))
        self.assertEqual(
            (v.channel, v.duration, v.views), ("Witness Channel", 125, 1520)
        )
        self.assertEqual(
            v.thumbnail, "https://i.ytimg.com/vi/lnNjjr2016a/hqdefault.jpg"
        )
        self.assertEqual(
            youtube.upload_day({"upload_date": "20130722"}), ("2013-07-22", False)
        )

    def test_tracks_prefer_manual_arabic_english_and_never_a_translation(self):
        en = youtube.tracks(INFOS["lnNjjr2016a"])
        self.assertEqual([(t["lang"], t["auto"]) for t in en], [("en", False)])
        self.assertTrue(en[0]["url"].endswith("fmt=vtt"))
        ar = youtube.tracks(INFOS["earlyCopy01"])
        self.assertEqual([(t["lang"], t["auto"]) for t in ar], [("ar", True)])
        self.assertNotIn("tlang", ar[0]["url"])
        self.assertEqual(youtube.tracks(INFOS["rcpVideo001"]), [])
        both = {
            "subtitles": {
                "de": [{"ext": "vtt", "url": "d"}],
                "en-GB": [{"ext": "vtt", "url": "e"}],
                "ar": [{"ext": "vtt", "url": "a"}],
            }
        }
        self.assertEqual([t["lang"] for t in youtube.tracks(both)], ["ar", "en-GB"])
        other = {"subtitles": {"de": [{"ext": "vtt", "url": "d"}]}}
        self.assertEqual([t["lang"] for t in youtube.tracks(other)], ["de"])

    def test_rolling_captions_are_read_once_with_their_second(self):
        cues = youtube.cues(fixture_text("youtube_earlyCopy01.ar.vtt"))
        self.assertEqual(
            cues,
            [(1, "هنا الحاجز"), (3, "وأخذوا لينا النجّار"), (6, "في الصباح")],
        )
        en = youtube.cues(fixture_text("youtube_lnNjjr2016a.en.vtt"))
        self.assertEqual(
            en[1], (65, "Her name is Lina Najjar, and she never came home.")
        )

    def test_spellings_are_heard_across_scripts_and_cues(self):
        named = [
            {
                "text": "x",
                "why": [
                    {"label": "Name", "value": "لينا النجار"},
                    {"label": "Name", "value": NAME},
                ],
            }
        ]
        spellings = service.spoken(named)
        cues = youtube.cues(fixture_text("youtube_earlyCopy01.ar.vtt"))
        said = service.heard(cues, spellings, "earlyCopy01")
        self.assertEqual(
            [(x["at"], x["url"]) for x in said],
            [(3, "https://www.youtube.com/watch?v=earlyCopy01&t=3s")],
        )
        split = [(10, "and then Lina"), (12, "Najjar was taken"), (20, "nothing")]
        said = service.heard(split, spellings, "abcdefghijk")
        self.assertEqual(
            [(x["at"], x["text"]) for x in said],
            [(10, "and then Lina Najjar was taken")],
        )
        self.assertEqual(
            service.heard([(1, "Lina only")], spellings, "abcdefghijk"), []
        )
        # A typed query listens for its own words, numbers left out.
        typed = service.spoken([{"text": '"Douma" 2018', "why": []}])
        self.assertEqual(typed, [{"douma"}])

    def test_bot_check_and_sign_in_read_plainly(self):
        bot = youtube.plain(
            Exception(
                "ERROR: [youtube] abc: Sign in to confirm you\u2019re not a bot. Use --cookies"
            )
        )
        self.assertIsInstance(bot, youtube.BotCheck)
        self.assertIn("bot check", str(bot))
        login = youtube.plain(
            Exception(
                "ERROR: [youtube] abc: Private video. Sign in if you've been granted access"
            )
        )
        self.assertIsInstance(login, youtube.LoginRequired)
        other = youtube.plain(Exception("ERROR: [youtube] abc: Video unavailable"))
        self.assertEqual(str(other), "YouTube did not answer: Video unavailable")


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


PAGES = {
    "/article": (
        200,
        {},
        (FIX / "page.html")
        .read_text()
        .replace(
            "</article>",
            '<p id="late"></p><script>document.getElementById("late").textContent='
            '"added by script";window.open("/popup");</script>'
            '<a id="dl" href="/file.bin">file</a></article>',
        ),
    ),
    "/login": (200, {}, (FIX / "login.html").read_text()),
    "/bot": (
        403,
        {"cf-mitigated": "challenge"},
        "<html><head><title>Just a moment...</title></head><body>wait</body></html>",
    ),
    "/gone": (404, {}, "<html><body>nothing here</body></html>"),
    "/hop": (302, {"Location": "/article"}, ""),
    "/to-private": (302, {"Location": "http://internal.test:81/secret"}, ""),
    "/file.bin": (
        200,
        {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": "attachment",
        },
        "bytes",
    ),
    "/popup": (200, {}, "<html><body>popup</body></html>"),
}


class Static(http.server.ThreadingHTTPServer):
    """Serves PAGES and a cookie counter on 127.0.0.1; `hits` is every path asked."""

    def __init__(self):
        self.hits: list[str] = []
        super().__init__(("127.0.0.1", 0), self.Handler)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.server.hits.append(self.path)
            if self.path == "/cookie":
                seen = self.headers.get("Cookie") or "none"
                self.send_response(200)
                self.send_header("Set-Cookie", "visit=1")
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f"<html><body>cookie {seen}</body></html>".encode())
                return
            status, headers, body = PAGES.get(self.path, (404, {}, ""))
            self.send_response(status)
            headers = {"Content-Type": "text/html; charset=utf-8"} | headers
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body.encode())


@unittest.skipUnless(browser.installed(), "run `uv run playwright install chromium`")
class BrowserTest(unittest.IsolatedAsyncioTestCase):
    """The real local Chromium against static pages. pub.test is mapped to the local
    server and resolves to a public address as far as the guard can tell;
    internal.test resolves to a private one."""

    def setUp(self):
        self.server = Static()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

        async def resolve(host, port):
            return ["10.0.0.5"] if host == "internal.test" else PUBLIC_IP

        self.enterContext(mock.patch.object(netguard, "resolve", resolve))
        self.enterContext(
            mock.patch.object(
                browser,
                "EXTRA_ARGS",
                [
                    "--host-resolver-rules=MAP pub.test 127.0.0.1, MAP internal.test 127.0.0.1"
                ],
            )
        )
        # The server really is on loopback; only test_connected_address_is_checked
        # runs the true check.
        self.enterContext(mock.patch.object(browser, "public", lambda ip: True))

    def url(self, path):
        return f"http://pub.test:{self.port}{path}"

    async def test_snapshot_returns_rendered_page_and_log(self):
        snap = await browser.snapshot(self.url("/hop"))
        self.assertEqual(snap.final_url, self.url("/article"))
        self.assertEqual(snap.status, 200)
        self.assertIn("added by script", snap.html)  # rendered, not source
        self.assertEqual(snap.screenshot[:8], b"\x89PNG\r\n\x1a\n")
        self.assertTrue(snap.browser.startswith("chromium "))
        log = orjson.loads(snap.netlog)
        docs = [r for r in log["requests"] if r["type"] == "document"]
        self.assertEqual(
            [(r["url"], r["status"]) for r in docs],
            [(self.url("/hop"), 302), (self.url("/article"), 200)],
        )
        self.assertEqual(docs[1]["redirected_from"], self.url("/hop"))
        media, password = browser.read(snap.html, snap.final_url)
        self.assertFalse(password)
        self.assertIn("/media/photo.jpg", media[0]["url"])

    async def test_popups_and_downloads_are_blocked(self):
        await browser.snapshot(self.url("/article"))
        self.assertNotIn("/popup", self.server.hits)
        with self.assertRaisesRegex(browser.RenderError, "starts a download"):
            await browser.snapshot(self.url("/file.bin"))

    async def test_no_cookies_between_captures(self):
        first = await browser.snapshot(self.url("/cookie"))
        second = await browser.snapshot(self.url("/cookie"))
        self.assertIn("cookie none", first.html)
        self.assertIn("cookie none", second.html)

    async def test_redirect_to_a_private_host_is_refused_before_it_connects(self):
        with self.assertRaises(netguard.Blocked):
            await browser.snapshot(self.url("/to-private"))
        self.assertEqual(self.server.hits, ["/to-private"])
        with self.assertRaises(netguard.Blocked):
            await browser.snapshot(f"http://internal.test:{self.port}/article")
        self.assertEqual(self.server.hits, ["/to-private"])

    async def test_connected_address_is_checked(self):
        with mock.patch.object(browser, "public", ip_public):
            with self.assertRaisesRegex(netguard.Blocked, "non-public"):
                await browser.snapshot(self.url("/article"))

    async def test_bot_check_and_error_pages_are_refused_plainly(self):
        with self.assertRaisesRegex(browser.RenderError, "bot check"):
            await browser.snapshot(self.url("/bot"))
        with self.assertRaisesRegex(browser.RenderError, "status 404"):
            await browser.snapshot(self.url("/gone"))

    async def test_login_form_is_seen_in_the_captured_page(self):
        snap = await browser.snapshot(self.url("/login"))
        self.assertTrue(browser.read(snap.html, snap.final_url)[1])


@unittest.skipUnless(test_review.POSTGRES, "research needs Postgres")
class ResearchTest(unittest.IsolatedAsyncioTestCase):
    setUp_review = test_review.ReviewTest.setUp
    client = test_review.ReviewTest.client

    def setUp(self):
        self.setUp_review()
        for key, value in {
            "BRAVE_API_KEY": "test-key",
            "RESEARCH_SEARCH_INTERVAL": 0,
            "RUN_MODELS": {},
            "SPEECH_MODEL": "",
        }.items():
            self.enterContext(mock.patch.object(Config, key, value))
        self.enterContext(
            mock.patch.object(netguard, "resolve", return_value=PUBLIC_IP)
        )
        self.enterContext(mock.patch.object(browser, "installed", return_value=True))
        self.login_pages: set[str] = set()
        self.rendered: list[str] = []

        async def render(page):
            self.rendered.append(page)
            name = "login.html" if page in self.login_pages else "page.html"
            return browser.Snapshot(
                final_url=page,
                status=200,
                html=(FIX / name).read_text(),
                screenshot=(FIX / "page.png").read_bytes(),
                netlog=orjson.dumps({"final_url": page, "requests": []}),
                browser="chromium 0.0-test",
            )

        self.enterContext(mock.patch.object(browser, "snapshot", render))
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
        # YouTube: yt-dlp's network calls answer from the recorded fixtures.
        self.tube = {"search": [], "info": [], "fetch": []}
        self.tube_by_query = {f'"{NAME}"': "youtube_search.json"}

        def search(query, count):
            self.tube["search"].append((query, count))
            name = self.tube_by_query.get(query, "youtube_search_ar.json")
            return orjson.loads((FIX / name).read_bytes())

        def info(video_id):
            self.tube["info"].append(video_id)
            return INFOS[video_id]

        def fetch(url):
            self.tube["fetch"].append(url)
            vid, lang = url.split("v=")[1].split("&")[0], url.split("lang=")[1][:2]
            return (FIX / f"youtube_{vid}.{lang}.vtt").read_bytes()

        for name, fake in (("_search", search), ("_info", info), ("_fetch", fetch)):
            self.enterContext(mock.patch.object(youtube, name, fake))
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
            # Text read from the closed case's file: a derived blob, no acquisition.
            derived = (
                await blobs.put(
                    s, b"Read from the file: Rami Haddad", "text/plain", Config.BLOB_DIR
                )
            ).sha256
            for case in ("case-r", "case-closed"):
                await ensure_case(s, case)
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
                (derived, "Rami Haddad", len("Read from the file: ")),  # derived
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
            await append(
                s,
                StatementIn(
                    subject=f"blob:{derived}",
                    prop=Prop.DERIVED_FROM,
                    value={"blob": hidden},
                    author_id=self.human.id,
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
        self.assertNotIn("Rami", dumped)  # text read from that case's file
        self.assertEqual(body["queries"][0]["people"], [self.person])
        self.assertEqual(body["queries"][0]["why"][0]["value"], NAME)
        self.assertEqual(self.web.requests, [])  # proposing sends nothing

    def named(self, text, name=NAME):
        return {
            "text": text,
            "kinds": ["web"],
            "people": [self.person],
            "why": [{"label": "Name", "value": name}],
        }

    async def test_copies_merge_and_ungrounded_results_sink_but_stay(self):
        self.web.by_query['"Lina Najjar" 2013'] = "brave_web_mixed.json"
        self.web.by_query['"لينا النجار"'] = "brave_web_mixed.json"
        client = await self.client()
        q = [
            self.named(f'"{NAME}"'),
            self.named(f'"{NAME}" 2013'),
            self.named('"لينا النجار"', "لينا النجار"),
        ]
        status, body = await self.send(client, q)
        self.assertEqual(status, 200, body)
        web = body["groups"]["web"]
        # the fixture page and its feed copy (www, slash, utm, fragment) are one
        self.assertEqual(
            [r["url"] for r in web],
            [
                "https://news.example.org/2013/07/families-ask",
                "https://ar.example.org/lina",
                "https://other.example.org/recipes",
            ],
        )
        self.assertEqual(
            web[0]["queries"], [f'"{NAME}"', f'"{NAME}" 2013', '"لينا النجار"']
        )
        # grounded by an English or an Arabic spelling; the recipe page is kept
        self.assertEqual([r["grounded"] for r in web], [True, True, False])
        self.assertEqual(web[2]["subject"], NAME)
        self.assertIsNone(body["closest"])
        held = [c for u in store._store.values() for _, c in u.values()]
        merged = next(c for c in held if c["url"] == web[0]["url"])
        self.assertEqual(len(merged["accesses"]), 3)

    async def test_nothing_grounded_names_the_closest_weak_result(self):
        self.web.by_query[f'"{NAME}"'] = "brave_web_ungrounded.json"
        client = await self.client()
        status, body = await self.send(client, [self.named(f'"{NAME}"')])
        self.assertEqual(status, 200, body)
        web = body["groups"]["web"]
        self.assertEqual([r["grounded"] for r in web], [False, False])  # not hidden
        self.assertEqual(body["closest"], web[0]["id"])
        self.assertEqual(body["subject"], NAME)

    async def test_typed_queries_are_not_judged(self):
        client = await self.client()
        q = [{"text": "anything", "kinds": ["web"], "people": [], "why": []}]
        status, body = await self.send(client, q)
        self.assertEqual(status, 200, body)
        self.assertIsNone(body["groups"]["web"][0]["grounded"])
        self.assertIsNone(body["closest"])

    async def test_date_window_goes_to_web_only_and_is_logged(self):
        client = await self.client()
        q = [{**self.named(f'"{NAME}"'), "kinds": ["web", "images"]}]
        item = {"scope": self.person, "investigation": "case-r", "queries": q}
        status, body = await self.post(
            client,
            "/api/research/search",
            item | {"from": "2013-07-01", "to": "2013-07-31"},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["window"], ["2013-07-01", "2013-07-31"])
        web, images = self.web.sent("api.search.brave.com")
        self.assertEqual(web.url.params["freshness"], "2013-07-01to2013-07-31")
        self.assertNotIn("freshness", images.url.params)
        async with ext.async_session_factory() as s:
            logged = (
                await s.execute(
                    select(Payload.value).where(
                        Payload.value["action"].astext == "research",
                        Payload.value["kind"].astext == "web",
                    )
                )
            ).scalar_one()
        self.assertEqual(logged["window"], ["2013-07-01", "2013-07-31"])
        before = len(self.web.requests)
        status, _ = await self.post(
            client, "/api/research/search", item | {"from": "2013-07-01"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(len(self.web.requests), before)

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

    async def test_case_scope_from_add_evidence(self):
        """Add evidence searches for a whole case: proposals from the case's shareable
        values, the query logged on the case, captures into the page's own batch."""
        client = await self.client()
        status, body = await self.post(
            client, "/api/research/queries", {"scope": "case:case-r"}
        )
        self.assertEqual(status, 200, body)
        self.assertIn(f'"{NAME}"', [q["text"] for q in body["queries"]])
        self.assertNotIn(SECRET, orjson.dumps(body).decode())
        status, _ = await self.post(
            client, "/api/research/queries", {"scope": "case:no-such-case"}
        )
        self.assertEqual(status, 404)
        status, body = await self.send(
            client,
            [{"text": f'"{NAME}"', "kinds": ["images"], "people": [self.person]}],
            scope="case:case-r",
        )
        self.assertEqual(status, 200, body)
        async with ext.async_session_factory() as s:
            logged = await s.scalar(
                select(Statement)
                .where(Statement.prop == Prop.ACCESS)
                .order_by(Statement.recorded_at.desc())
                .limit(1)
            )
        self.assertEqual(logged.subject, "case:case-r")
        pick, batch = body["groups"]["images"][0], str(uuid.uuid4())
        item = {"ids": [pick["id"]], "sensitivity": "ordinary", "batch": batch}
        status, _ = await self.post(
            client, "/api/research/capture", item | {"doc_type": "scanned_order"}
        )
        self.assertEqual(status, 400)
        status, queued = await self.post(
            client, "/api/research/capture", item | {"doc_type": "photo"}
        )
        self.assertEqual((status, queued["batch"]), (200, batch))
        while await work_once(ext.async_session_factory, ["capture"], self.transport):
            pass
        progress = await (await client.get(f"/ingest/batch/{batch}")).get_json()
        (capture,) = progress["captures"]
        self.assertEqual((capture["status"], capture["doc_type"]), ("done", "photo"))
        roles = {i["role"] for i in progress["items"] if i["capture"] == capture["run"]}
        self.assertIn("page", roles)

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
        self.assertEqual(run.provider_served, "local")
        self.assertEqual(manifest["method"]["provider"]["name"], "brave")
        self.assertEqual(
            manifest["method"]["renderer"],
            {
                "name": "playwright-chromium",
                "browser": "chromium 0.0-test",
                "status": 200,
            },
        )
        self.assertIsNone(manifest["method"]["yt_dlp"])
        self.assertEqual(access.prop, Prop.ACCESS)
        roles = sorted(a.context["capture"]["role"] for a in acqs)
        self.assertEqual(roles, ["media", "media", "network_log", "page", "screenshot"])
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
                [f"url:{pick['url']}"] * 3
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
        self.login_pages.add("https://news.example.org/2013/07/families-ask")
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
        self.assertEqual(self.rendered, [])

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

    def tubed(self, text, name=NAME):
        return self.named(text, name) | {"kinds": ["youtube"]}

    async def test_youtube_search_merges_reads_subtitles_and_marks_held_copies(self):
        client = await self.client()
        q = [self.tubed(f'"{NAME}"'), self.tubed('"لينا النجار"', "لينا النجار")]
        with mock.patch.object(Config, "BRAVE_API_KEY", None):  # YouTube needs no key
            status, body = await self.send(client, q)
        self.assertEqual(status, 200, body)
        self.assertEqual(
            self.tube["search"], [(f'"{NAME}"', 20), ('"لينا النجار"', 20)]
        )
        self.assertEqual(self.web.sent("api.search.brave.com"), [])
        rows = body["groups"]["youtube"]
        # one row per video id; the channel entry is gone; relevance order, the
        # unrelated video last
        self.assertEqual(
            [r["video_id"] for r in rows], ["lnNjjr2016a", "earlyCopy01", "rcpVideo001"]
        )
        top, early, soup = rows
        self.assertEqual(top["queries"], [f'"{NAME}"', '"لينا النجار"'])
        self.assertEqual(
            (top["channel"], top["duration"], top["views"]),
            ("Witness Channel", 125, 1520),
        )
        # subtitles: manual English for one, the spoken Arabic captions for the other
        self.assertEqual(top["subtitles"]["status"], "said")
        self.assertEqual(top["subtitles"]["lines"][0]["at"], 65)
        self.assertEqual(
            early["subtitles"]["lines"][0]["url"],
            "https://www.youtube.com/watch?v=earlyCopy01&t=3s",
        )
        self.assertTrue(early["subtitles"]["lines"][0]["auto"])
        # grounded by what is said, though the title does not name her
        self.assertEqual([r["grounded"] for r in rows], [True, True, False])
        self.assertEqual(soup["subtitles"]["status"], "absent")
        # the upload date replaces the "years ago" estimate once read
        self.assertEqual((early["date"], early["approximate"]), ("2013-07-22", False))
        self.assertTrue(all("tlang" not in u for u in self.tube["fetch"]))
        # the thumbnail is fetched by the server and matches the held photo: a lead
        self.assertEqual([c["kind"] for c in early["in_case"]], ["same_picture"])
        r = await client.get(f"/api/research/thumb/{early['id']}")
        self.assertEqual(r.status_code, 200)
        async with ext.async_session_factory() as s:
            logs = (
                (
                    await s.execute(
                        select(Payload.value).where(
                            Payload.value["action"].astext == "research"
                        )
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual(
            sorted((v["provider"], v["kind"], v["query"]) for v in logs),
            sorted([("youtube", "youtube", x["text"]) for x in q]),
        )
        with mock.patch.object(Config, "BRAVE_API_KEY", None):
            status, body = await self.send(client, [self.named(f'"{NAME}"')])
        self.assertEqual(status, 409)
        self.assertIn("BRAVE_API_KEY", body["message"])

    async def test_subtitle_search_is_bounded_in_videos_and_time(self):
        client = await self.client()
        with mock.patch.object(Config, "RESEARCH_YOUTUBE_SUBTITLES", 1):
            _, body = await self.send(client, [self.tubed(f'"{NAME}"')])
        self.assertEqual(self.tube["info"], ["lnNjjr2016a"])
        self.assertEqual(
            [r["subtitles"]["status"] for r in body["groups"]["youtube"]],
            ["said", "not_checked", "not_checked"],
        )
        slow = threading.Event()

        def stuck(video_id):
            slow.wait(2)
            return INFOS[video_id]

        with (
            mock.patch.object(youtube, "_info", stuck),
            mock.patch.object(Config, "RESEARCH_YOUTUBE_SUBTITLE_SECONDS", 0.2),
        ):
            _, body = await self.send(client, [self.tubed(f'"{NAME}"')])
        slow.set()
        late = body["groups"]["youtube"][0]["subtitles"]
        self.assertEqual(late["status"], "failed")
        self.assertIn("ran out of time", late["message"])

    async def test_youtube_bot_check_reads_plainly(self):
        def refused(query, count):
            raise youtube.DownloadError(
                "ERROR: [youtube:search] x: Sign in to confirm you\u2019re not a bot"
            )

        client = await self.client()
        with mock.patch.object(youtube, "_search", refused):
            status, body = await self.send(client, [self.tubed(f'"{NAME}"')])
        self.assertEqual(status, 200, body)
        self.assertIn("bot check", body["sent"][0]["error"])
        self.assertEqual(body["groups"]["youtube"], [])

    def fake_download(self, video_id, directory, max_bytes, seconds):
        folder = Path(directory)
        media = folder / f"{video_id}.webm"
        shutil.copy(CLIP, media)
        thumb = folder / f"{video_id}.jpg"
        thumb.write_bytes(jpeg(PHOTO, 480))
        sub = folder / f"{video_id}.en.vtt"
        sub.write_text(fixture_text("youtube_lnNjjr2016a.en.vtt"))
        info = INFOS[video_id] | {
            "duration": 125.4,
            "webpage_url": youtube.WATCH.format(video_id),
        }
        return {
            "media": media,
            "thumbnail": thumb,
            "subtitles": [{"path": sub, "lang": "en", "auto": False}],
            "skipped": [],
            "info": youtube._small(info, youtube.INFO_KEYS)
            | {
                "original_url": youtube.WATCH.format(video_id),
                "yt_dlp": youtube.VERSION,
            },
            "context": youtube._small(info, youtube.CONTEXT_KEYS),
        }

    async def capture_tube(self, client):
        _, body = await self.send(client, [self.tubed(f'"{NAME}"')])
        pick = body["groups"]["youtube"][0]
        status, queued = await self.post(
            client,
            "/api/research/capture",
            {"ids": [pick["id"]], "sensitivity": "ordinary"},
        )
        self.assertEqual(status, 200, queued)
        while await work_once(ext.async_session_factory, ["capture"], self.transport):
            pass
        progress = await (
            await client.get(f"/api/research/batch/{queued['batch']}")
        ).get_json()
        return pick, queued, progress

    async def test_youtube_capture_takes_media_info_subtitles_and_thumbnail(self):
        client = await self.client()
        with (
            mock.patch.object(youtube, "_download", self.fake_download),
            mock.patch.object(browser, "installed", return_value=False),  # not needed
        ):
            pick, queued, progress = await self.capture_tube(client)
        self.assertEqual(progress["captures"][0]["status"], "done", progress)
        self.assertEqual(self.rendered, [])  # no browser visit for a YouTube video
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
            run = await s.get(Run, uuid.UUID(queued["runs"][0]))
            info_acq = next(a for a in acqs if a.context["capture"]["role"] == "info")
            info = orjson.loads(
                blobs.read(await s.get(Blob, info_acq.blob_sha256), Config.BLOB_DIR)
            )
            self.assertEqual(await verify_db(s), [])
        by_role = {a.context["capture"]["role"]: a for a in acqs}
        self.assertEqual(sorted(by_role), ["info", "subtitles", "thumbnail", "video"])
        clip = by_role["video"]
        self.assertEqual(clip.origin, "url:https://www.youtube.com/watch?v=lnNjjr2016a")
        self.assertEqual(
            clip.context["capture"]["video"]["channel_id"], "UCwitness000000000000000"
        )
        self.assertEqual(clip.context["capture"]["video"]["duration"], 125)
        for role in ("info", "subtitles", "thumbnail"):
            self.assertEqual(
                by_role[role].context["capture"]["of_video"], clip.blob_sha256
            )
            self.assertEqual(by_role[role].collector_id, clip.collector_id)
        self.assertEqual(
            (info["channel_id"], info["upload_date"], info["original_url"], info["id"]),
            ("UCwitness000000000000000", "20160301", pick["url"], "lnNjjr2016a"),
        )
        method = clip.context["capture"]["method"]
        self.assertEqual(
            (method["renderer"], method["yt_dlp"]), (None, youtube.VERSION)
        )
        self.assertEqual(run.inputs["provider"]["name"], "youtube")
        # captured: the same video now reads as already in this case
        _, body = await self.send(client, [self.tubed(f'"{NAME}"')])
        top = body["groups"]["youtube"][0]
        self.assertEqual([c["kind"] for c in top["in_case"]], ["same_link"])

    async def test_a_video_captured_from_another_link_is_marked_by_its_id(self):
        async with ext.async_session_factory() as s, s.begin():
            sha = (await blobs.put(s, b"clip", "video/mp4", Config.BLOB_DIR)).sha256
            await record_acquisition(
                s,
                blob_sha256=sha,
                origin="url:https://video.example.net/watch/soup",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={
                    "investigation": "case-r",
                    "capture": {"role": "video", "video": {"id": "rcpVideo001"}},
                },
                sensitivity="ordinary",
                doc_type="web_post",
            )
        _, body = await self.send(await self.client(), [self.tubed(f'"{NAME}"')])
        soup = next(
            r for r in body["groups"]["youtube"] if r["video_id"] == "rcpVideo001"
        )
        self.assertEqual(
            [(c["kind"], c["sha256"]) for c in soup["in_case"]], [("same_video", sha)]
        )

    async def test_youtube_bot_check_fails_the_capture_with_retry(self):
        def refused(*a):
            raise youtube.DownloadError(
                "ERROR: [youtube] lnNjjr2016a: Sign in to confirm you\u2019re not a bot"
            )

        client = await self.client()
        with mock.patch.object(youtube, "_download", refused):
            _, queued, progress = await self.capture_tube(client)
        failed = progress["captures"][0]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("bot check", failed["error"])
        self.assertEqual(progress["intake"]["items"], [])
        r = await client.post(
            f"/ingest/run/{failed['run']}/retry", json={"item": {}}, headers=client.csrf
        )
        self.assertEqual(r.status_code, 200)
        with mock.patch.object(youtube, "_download", self.fake_download):
            while await work_once(
                ext.async_session_factory, ["capture"], self.transport
            ):
                pass
        progress = await (
            await client.get(f"/api/research/batch/{queued['batch']}")
        ).get_json()
        self.assertEqual(progress["captures"][0]["status"], "done", progress)
