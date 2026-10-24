"""v0 acceptance (PLAN.md Part A): one scenario that walks the synthetic case in
acceptance/case.json end to end on a single throwaway Postgres database.

Humans and agents act through HTTP (ingest, review, MCP, people, watch, packages);
background work (worker runs, matcher, anchoring, URL recheck) is called directly.
No network: the model, the web page, the TSA and the OTS calendar are local fakes.
Steps share state and run in ORDER (see load_tests), not alphabetically.

This original scenario uploads the transfer order's text beside its PDF and does
not extract the witness clip. Located PDF search is covered separately in
tests/test_search_workflow.py and acceptance/check_workflow.py. Transcription is
off here (SPEECH_MODEL empty); tests/test_speech.py covers it.
"""

import asyncio
import base64
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest import mock

import httpx
import orjson
from quart.datastructures import FileStorage
from quart_security import hash_password
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

import stk.extensions as ext
from alembic import command
from stk import netguard
from stk.agent_login import create_agent_login_token
from stk.anchor import service as anchor
from stk.anchor.models import Batch
from stk.app import create_app
from stk.blobs import blob_path
from stk.ingest.service import recheck_url
from stk.ledger import chain, merkle
from stk.ledger.models import (
    Acquisition,
    Blob,
    Chain,
    Payload,
    Principal,
    Run,
    RunEvent,
    Statement,
    StatementTier,
)
from stk.ledger.write import row_dict, verify_db
from stk.migrations import (
    build_alembic_config,
    get_database_url,
    throwaway_database_url,
)
from stk.people.cli import erase_cmd
from stk.places import cli as places
from stk.runs.worker import locate, work_once
from stk.settings import Config
from stk.user.models import Role, User, roles_users
from tests.passkey import SoftPasskey
from tests.test_anchor import FakeTsa
from tests.test_mcp import client_metadata, pkce
from tests.test_packages import make_signing_cert

HERE = Path(__file__).parent
DATA = HERE / "data"
CASE = json.loads((HERE / "case.json").read_text())
DOCS = {d["key"]: d for d in CASE["documents"]}
TIMED = HERE / "timed_comparison.json"
POSTGRES = make_url(get_database_url()).get_backend_name() == "postgresql"

PUBLIC_IP = "93.184.215.14"
CALENDAR, TSA = "https://calendar.test", "https://tsa.test/tsr"
LOCAL = "http://local.test/v1"
MCP_CLIENT = "https://agent.example.org/client.json"
MCP_REDIRECT = "http://127.0.0.1:53682/callback"
ALL_SCOPES = "read propose_statements propose_identity"
BRANCH = "الفرع 999"
POST = (DATA / "missing_post.html").read_bytes()

# What a model reading the register image returns: header, column titles, two rows.
REGISTER_LINES = [
    [BRANCH, "سجل الموقوفين", "صفحة 14", "2013/5/3"],
    ["الرقم", "الاسم", "اسم الأب", "اسم الأم", "التولد"]
    + ["مكان الولادة", "مكان التوقيف", "تاريخ التوقيف"],
    ["217", "يوسف خالد العلي", "خالد", "فاطمة", "1991", "دوما", "حرستا", "2013/5/2"],
    ["218", "يوسف خالد العلي", "خالد", "سعاد", "1985", "حمص", "القابون", "2013/5/2"],
]
REGISTER_TEXT = "\n".join(" | ".join(cells) for cells in REGISTER_LINES)
# (mention, prop, line, cell, valid); the cell holds the quoted value.
REGISTER_FACTS = [
    ("P1", "name", 2, 1, None),
    ("P1", "father_name", 2, 2, None),
    ("P1", "mother_name", 2, 3, None),
    ("P1", "birth_date", 2, 4, None),
    ("P1", "arrested_at", 2, 6, "2013-05-02"),
    ("P1", "detention_branch", 0, 0, None),
    ("P2", "name", 3, 1, None),
    ("P2", "mother_name", 3, 3, None),
    ("P2", "birth_date", 3, 4, None),
    ("document", "document_date", 0, 3, "2013-05-03"),
]
# The transfer order's lines in reading order (acceptance/make_case.py).
TRANSFER_TEXT = "\n".join(
    [
        BRANCH,
        "قسم الديوان",
        "أمر نقل رقم 4471/ن",
        "التاريخ: 2013-09-17",
        "الموقوف: يوسف خالد علي، تولد 1991-03-14، والدته فاطمة",
        "الجهة الموقوف لديها: " + BRANCH,
        "ينقل الموقوف المذكور أعلاه إلى سجن ك المركزي بتاريخ 2013-09-17",
        "يسلم مع أماناته وملفه إلى إدارة السجن أصولا.",
    ]
)


def occurrence(text: str, offset: int, value: str) -> int:
    return text[:offset].count(value) + 1


def item(mention, prop, value, occ, valid=None, lang="ar"):
    return {
        "mention": mention,
        "prop": prop,
        "value": {"text": value, "lang": lang},
        "occurrence": occ,
        "evidence": ["printed_text"],
        "valid": valid,
    }


def register_items() -> list[dict]:
    starts = [0]
    for cells in REGISTER_LINES:
        starts.append(starts[-1] + len(" | ".join(cells)) + 1)
    out = []
    for mention, prop, line, cell, valid in REGISTER_FACTS:
        cells = REGISTER_LINES[line]
        offset = starts[line] + len(" | ".join(cells[:cell] + [""]))
        value = cells[cell]
        out.append(
            item(mention, prop, value, occurrence(REGISTER_TEXT, offset, value), valid)
        )
    # A value the page does not hold: no span, so the gate rejects it.
    return out + [item("P1", "place_of_birth", "Damascus", 1, lang="en")]


TRANSFER_ITEMS = [
    item("P1", "name", "يوسف خالد علي", 1),
    item("P1", "birth_date", "1991-03-14", 1),
    item("P1", "transferred_to", "سجن ك المركزي", 1, "2013-09-17"),
    item("P1", "detention_branch", BRANCH, 1),
    item("document", "document_date", "2013-09-17", 1, "2013-09-17"),
]
POST_ITEMS = [
    item("P1", "name", "Yousef Khaled Al-Ali", 2, lang="en"),  # 1 is the <title>
    item("P1", "last_seen", "دوما", 1, "2013-05-02"),
]


def completion(content: str) -> dict:
    return {
        "model": "local/qwen-test",
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


class Model:
    """The local model route: OCR of the register image, extraction of known texts."""

    def __init__(self):
        self.extractions = {}
        self.calls = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused")
        messages = orjson.loads(request.content)["messages"]
        if isinstance(messages[0]["content"], list):
            # Ingest queues OCR for every image; only the register page has text.
            image = messages[0]["content"][1]["image_url"]["url"].partition(",")[2]
            register = base64.b64encode((DATA / DOCS["register"]["file"]).read_bytes())
            text = REGISTER_TEXT if image == register.decode() else ""
            return httpx.Response(200, json=completion(text))
        # PDF pages are read for claims too; a text the scenario did not script yields none.
        items = self.extractions.get(messages[1]["content"], [])
        return httpx.Response(
            200, json=completion(orjson.dumps({"statements": items}).decode())
        )


class Web:
    """The public web: the missing-person post, edited after capture, plus the
    OTS calendar and the TSA."""

    def __init__(self):
        self.post = POST
        self.tsa = FakeTsa()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == CALENDAR + "/digest":
            return httpx.Response(200, content=b"\xf0\x10" + bytes(16) + b"\x08\x00")
        if str(request.url) == TSA:
            return httpx.Response(200, content=self.tsa.respond(request.content))
        assert request.url.host == PUBLIC_IP, request.url
        return httpx.Response(
            200, content=self.post, headers={"content-type": "text/html; charset=utf-8"}
        )


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(V0Acceptance(name) for name in ORDER)


@unittest.skipUnless(
    POSTGRES, "acceptance needs Postgres (set SQLALCHEMY_DATABASE_URI)"
)
class V0Acceptance(unittest.IsolatedAsyncioTestCase):
    s: dict = {}  # scenario state shared by the steps

    @classmethod
    def setUpClass(cls):
        url = cls.enterClassContext(throwaway_database_url(sync=True))
        config = build_alembic_config()
        config.config_file_name = None
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        tmp = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        cls.tmp, cls.partner = tmp, tmp / "partner"
        cls.partner.mkdir()
        cert, key = make_signing_cert(tmp)

        class AcceptanceConfig(Config):
            SQLALCHEMY_DATABASE_URI = (
                make_url(url)
                .set(drivername="postgresql+asyncpg")
                .render_as_string(hide_password=False)
            )
            TESTING = True
            SESSION_TYPE = None
            STK_ENV = "testing"
            STK_ENABLE_AGENT_LOGIN = True
            STK_AGENT_LOGIN_MAX_TTL_SECONDS = 60
            STK_PUBLIC_URL = None
            SECURITY_WAN_RP_ID = None
            SECURITY_WAN_EXPECTED_ORIGIN = None
            SESSION_COOKIE_SECURE = False
            SECURITY_COOKIE_SECURE = False
            QUART_RATE_LIMITER_ENABLED = False
            BLOB_DIR = str(tmp / "blobs")
            C2PA_SIGN_CERT = cert
            C2PA_SIGN_KEY = key

        for name, value in {
            "BLOB_DIR": AcceptanceConfig.BLOB_DIR,
            "RUN_MODELS": {"local": "local/extract"},
            "EMBEDDING_MODEL": None,
            # This scenario predates transcription; tests/test_speech.py covers it.
            "SPEECH_MODEL": "",
            "OTS_CALENDARS": [CALENDAR],
            "TSA_URL": TSA,
            "PARTNER_ROOT_DIRS": [str(cls.partner)],
        }.items():
            cls.enterClassContext(mock.patch.object(Config, name, value))
        cls.enterClassContext(
            mock.patch.dict("os.environ", {"ROUTE_LOCAL_BASE_URL": LOCAL})
        )
        cls.enterClassContext(
            mock.patch(
                "stk.mcp.oauth.fetch_client_metadata", side_effect=client_metadata
            )
        )
        cls.app = create_app(AcceptanceConfig)
        cls.model, cls.web = Model(), Web()
        pin = hashlib.sha256(cls.web.tsa.cert.dump()).hexdigest()
        cls.enterClassContext(mock.patch.object(Config, "TSA_CERT_SHA256", [pin]))
        cls.keys = {name: SoftPasskey() for name in ("amal", "basel")}
        cls.clients = {}

    async def asyncSetUp(self):
        if self.clients:
            return
        async with ext.async_session_factory() as s, s.begin():
            analyst = (
                await s.execute(select(Role).where(Role.name == "analyst"))
            ).scalar_one()
            for name in ("amal", "basel", "carol", "nobody"):
                user = User(
                    email=f"{name}@example.com",
                    name=name,
                    password=hash_password("TestPassword123!", app=self.app),
                    active=True,
                    confirmed_at=datetime.now(),
                )
                if name != "nobody":
                    user.roles.append(analyst)
                s.add(user)
                await s.flush()
                if name in self.keys:
                    s.add(self.keys[name].register(user))
            extractor = Principal(kind="agent", name="extractor")
            s.add(extractor)
            await s.flush()
            self.s["extractor"] = extractor.id
        for name in ("amal", "basel", "carol", "nobody"):
            self.clients[name] = await self.login(name)

    async def asyncTearDown(self):
        await ext.engine.dispose()

    # --- helpers -------------------------------------------------------------------

    async def login(self, name):
        client = self.app.test_client()
        async with self.app.app_context():
            token = create_agent_login_token(f"{name}@example.com", "/people")
        self.assertEqual(
            (await client.get(f"/_test/login?token={token}")).status_code, 302
        )
        page = await (await client.get("/ingest/")).get_data(as_text=True)
        client.csrf = {
            "X-CSRFToken": re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        }
        return client

    async def get(self, path, who="amal", status=200, **kw):
        r = await self.clients[who].get(path, **kw)
        self.assertEqual(r.status_code, status, await r.get_data(as_text=True))
        return await r.get_json()

    async def post(self, path, item=None, who="amal", status=200, **kw):
        client = self.clients[who]
        if item is not None:
            kw["json"] = {"item": item}
        r = await client.post(path, headers=client.csrf, **kw)
        self.assertEqual(r.status_code, status, await r.get_data(as_text=True))
        return await r.get_json()

    async def upload(self, key, data=None, mime=None, name=None):
        doc = DOCS[key]
        kind, _, origin = doc["origin"].partition(":")
        data = data or (DATA / doc["file"]).read_bytes()
        mime = (
            mime
            or {
                ".png": "image/png",
                ".pdf": "application/pdf",
                ".ogg": "audio/ogg",
                ".jpg": "image/jpeg",
            }[Path(doc["file"]).suffix]
        )
        storage = FileStorage(io.BytesIO(data), name or doc["file"], content_type=mime)
        out = await self.post(
            "/ingest/file",
            files={"file": storage},
            form={
                "origin_kind": kind,
                "origin": origin,
                "doc_type": doc["doc_type"],
                "sensitivity": doc["sensitivity"],
            },
        )
        [result] = out["items"]
        return result

    async def review(self, sid, verdict="accept", who="amal", reason="checked"):
        """Start a review and sign it with the reviewer's passkey."""
        item = {"statement": str(sid), "verdict": verdict, "reason": reason}
        started = await self.post("/api/review/options", item, who=who)
        return await self.post(
            "/api/review/commit",
            {
                "hash": started["hash"],
                "credential": self.keys[who].assert_(started["hash"]),
            },
            who=who,
        )

    async def work(self, transport=None):
        while await work_once(
            ext.async_session_factory,
            transport=transport or httpx.MockTransport(self.model),
        ):
            pass

    async def queue_run(
        self, acquisition, sha, tool="extract", route="local", **inputs
    ):
        async with ext.async_session_factory() as s, s.begin():
            acq = await s.get(Acquisition, uuid.UUID(acquisition))
            run = Run(
                principal_id=self.s["extractor"],
                tool=tool,
                route=route,
                model_requested=f"local/{tool}",
                inputs={"blobs": [sha], "acquisition": str(acq.id), **inputs},
            )
            s.add(run)
        return run.id

    async def statements(self, sha):
        """(mention key, prop) -> (statement id, subject, tier, span) for one text,
        mapped back through where each quote sits."""
        items = self.model.extractions[self.text(sha)]
        async with ext.async_session_factory() as s:
            rows = (
                await s.execute(
                    select(Statement, StatementTier.tier)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .where(Statement.span_blob == sha)
                )
            ).all()
        out = {}
        for i in items:
            span = locate(self.text(sha), i["value"]["text"], i["occurrence"])
            for st, tier in rows:
                if st.prop == i["prop"] and st.span == span:
                    out[(i["mention"], i["prop"])] = (st.id, st.subject, tier, span)
        return out

    def text(self, sha):
        return blob_path(Config.BLOB_DIR, sha).read_text()

    async def panels(self, window=None):
        f = orjson.dumps({"investigation": None, "time": window}).decode()
        return await self.get(f"/api/monitor/panels?filter={f}")

    async def queue(self):
        return [c["id"] for c in (await self.panels())["queue"]]

    @contextmanager
    def internet(self):
        """Outbound fetches reach the fake web whatever host they name."""
        real = httpx.AsyncClient
        with (
            mock.patch.object(netguard, "resolve", return_value=[PUBLIC_IP]),
            mock.patch.object(
                netguard.httpx,
                "AsyncClient",
                lambda **kw: real(**kw | {"transport": httpx.MockTransport(self.web)}),
            ),
        ):
            yield

    async def mcp_connect(self, who="amal", scope=ALL_SCOPES):
        """OAuth authorization code with PKCE: the human consents in the browser,
        the agent redeems the code."""
        browser, (verifier, challenge) = self.clients[who], pkce()
        r = await browser.get(
            "/oauth/authorize",
            query_string={
                "response_type": "code",
                "client_id": MCP_CLIENT,
                "redirect_uri": MCP_REDIRECT,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": scope,
                "state": "s1",
                "resource": "http://localhost/mcp",
            },
        )
        self.assertEqual(r.status_code, 200, await r.get_data(as_text=True))
        async with browser.session_transaction() as cookie:
            nonce, csrf = cookie["mcp_authz"]["nonce"], cookie["_csrf_token"]
        r = await browser.post(
            "/oauth/authorize",
            form={"nonce": nonce, "csrf_token": csrf, "decision": "approve"},
        )
        code = re.search(r"code=([^&]+)", r.headers["Location"])[1]
        r = await self.app.test_client().post(
            "/oauth/token",
            form={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": MCP_CLIENT,
                "redirect_uri": MCP_REDIRECT,
                "resource": "http://localhost/mcp",
            },
        )
        self.assertEqual(r.status_code, 200)
        return (await r.get_json())["access_token"]

    async def mcp(self, token, name, arguments, status=200):
        r = await self.app.test_client().post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments,
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    },
                },
            },
            headers={
                "Authorization": f"Bearer {token}",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": name,
            },
        )
        self.assertEqual(r.status_code, status, await r.get_data(as_text=True))
        if status != 200:
            return r
        result = (await r.get_json())["result"]
        if result.get("isError"):
            return {"error": result["content"][0]["text"]}
        return result["structuredContent"]

    async def head(self, token, subject):
        history = await self.mcp(token, "read", {"target": "history", "ref": subject})
        return history["head"]

    def mcp_item(self, subject, prop, value, bbox, head):
        return {
            "subject": subject,
            "prop": prop,
            "value": {"text": value, "lang": "ar"},
            "span": {
                "blob": self.s["register"]["sha256"],
                "span": {"page": 1, "bbox": bbox},
            },
            "evidence": ["handwritten_entry"],
            "based_on": head,
        }

    async def person(self, ref, who="amal"):
        return await self.get(f"/api/people/{ref.removeprefix('person:')}", who)

    async def p1(self):
        """P1's person id: the smallest mention id of the cluster, so it moves as
        mentions join. Opening the view logs an access."""
        return (await self.person(self.s["register_st"][("P1", "name")][1]))["person"]

    async def search(self, query):
        return (await self.get("/api/people/search", query_string={"q": query}))[
            "items"
        ]

    async def verify(self):
        async with ext.async_session_factory() as s:
            return await verify_db(s)

    # --- the scenario --------------------------------------------------------------

    def test_00_timed_comparison(self):
        """The timed comparison from the Usage section is run and recorded."""
        if not TIMED.exists():
            self.skipTest(
                "a person runs it: time the case in the current tool and in Sealbook and "
                f"record it in {TIMED.name}"
            )
        run = json.loads(TIMED.read_text())
        # PLAN.md: several times faster with better sourcing, or the thesis fails.
        self.assertGreaterEqual(run["baseline_seconds"] / run["sealbook_seconds"], 3)
        self.assertTrue(run["sealbook_better_sourcing"])

    async def test_01_ingest(self):
        """Ingest a PDF scan, a C2PA photo and a URL: blobs, acquisitions, hashes, C2PA verify result."""
        for key in ("register", "transfer", "witness", "photo"):
            data = (DATA / DOCS[key]["file"]).read_bytes()
            out = self.s[key] = await self.upload(key)
            self.assertEqual(out["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(out["md5"], hashlib.md5(data).hexdigest())
            values = {st["prop"]: st for st in out["statements"]}
            self.assertEqual(values["sha256"]["value"], out["sha256"])
            self.assertTrue(all(st["tier"] == "accepted" for st in out["statements"]))
        photo = {st["prop"]: st["value"] for st in self.s["photo"]["statements"]}
        self.assertEqual(photo["c2pa_verify"]["status"], "valid")
        register = {st["prop"]: st["value"] for st in self.s["register"]["statements"]}
        self.assertEqual(register["c2pa_verify"]["status"], "absent")

        url = DOCS["post"]["origin"].removeprefix("url:")
        with self.internet():
            post = self.s["post"] = await self.post("/ingest/url", {"url": url})
        self.assertEqual(post["sha256"], hashlib.sha256(self.web.post).hexdigest())
        async with ext.async_session_factory() as s:
            acqs = {
                a.blob_sha256: a
                for a in (await s.execute(select(Acquisition))).scalars()
            }
            self.assertEqual(await s.scalar(select(func.count()).select_from(Blob)), 5)
        for key in DOCS:
            self.assertEqual(acqs[self.s[key]["sha256"]].origin, DOCS[key]["origin"])
        self.assertEqual(acqs[post["sha256"]].context["status"], 200)

        # The page is edited after capture; the background recheck records it.
        self.web.post = (DATA / "missing_post_v2.html").read_bytes()
        with self.internet():
            async with ext.async_session_factory() as s, s.begin():
                out = await recheck_url(
                    s,
                    uuid.UUID(post["acquisition_id"]),
                    transport=httpx.MockTransport(self.web),
                )
        [liveness] = out["statements"]
        self.assertEqual(
            (liveness["value"]["status"], liveness["tier"]), ("edited", "accepted")
        )
        self.assertEqual(await self.verify(), [])

    async def test_02_extraction_spans(self):
        """An extraction run produces statements with spans; a value without a span is rejected."""
        self.model.extractions[REGISTER_TEXT] = register_items()
        self.model.extractions[POST.decode()] = POST_ITEMS
        # Ingest queued OCR for the images and extraction for the post: no forms.
        self.assertEqual(self.s["register"]["processing"]["queued"], "ocr")
        self.assertEqual(self.s["post"]["processing"]["queued"], "extract")
        await self.work()
        async with ext.async_session_factory() as s:
            runs = (await s.execute(select(Run.tool, Run.status, Run.error))).all()
            self.assertTrue(all(r.status == "done" for r in runs), runs)
            derived = (
                await s.execute(
                    select(Statement.subject)
                    .join(Run, Run.id == Statement.run_id)
                    .where(
                        Statement.prop == "derived_from",
                        Run.tool == "ocr",
                        Run.inputs["blobs"][0].astext == self.s["register"]["sha256"],
                    )
                )
            ).scalar_one()
            drops = (
                (
                    await s.execute(
                        select(RunEvent.drop_reason).where(RunEvent.stage == "dropped")
                    )
                )
                .scalars()
                .all()
            )
        self.s["register_text"] = derived.removeprefix("blob:")
        self.assertEqual(self.text(self.s["register_text"]), REGISTER_TEXT)
        self.assertEqual(drops, ["quote_not_in_text"])

        for sha, key in (
            (self.s["register_text"], "register"),
            (self.s["post"]["sha256"], "post"),
        ):
            found = self.s[f"{key}_st"] = await self.statements(sha)
            for (mention, prop), (_, _, tier, span) in found.items():
                if prop == "place_of_birth":
                    self.assertEqual(tier, "rejected")
                    self.assertIsNone(span)
                    continue
                self.assertEqual(tier, "unconfirmed", (mention, prop))
                quoted = self.text(sha)[span["start"] : span["end"]]
                expect = {
                    e["prop"]: e["value"]
                    for e in DOCS[key]["expect"]
                    if e.get("person") == mention
                }
                if prop in expect:
                    self.assertEqual(quoted, expect[prop], (mention, prop))
        reg = self.s["register_st"]
        self.assertEqual(len(reg), len(register_items()))
        self.assertNotEqual(reg[("P1", "name")][1], reg[("P2", "name")][1])
        self.assertEqual(await self.verify(), [])

    async def test_10_sensitive_routes(self):
        """With sensitivity set, a sensitive document cannot reach a `cloud` route, and a stopped local model fails the run instead of rerouting."""
        reg = self.s["register"]
        async with ext.async_session_factory() as s:
            acq = await s.get(Acquisition, uuid.UUID(reg["acquisition_id"]))
        self.assertEqual(acq.sensitivity, "sensitive")  # the intake default
        self.model.calls.clear()
        cloud = await self.queue_run(
            reg["acquisition_id"],
            reg["sha256"],
            tool="ocr",
            route="cloud",
            extract_model="m",
        )
        await self.work()
        self.assertEqual(self.model.calls, [])
        self.model.down = True
        try:
            local = await self.queue_run(
                reg["acquisition_id"], reg["sha256"], tool="ocr", extract_model="m"
            )
            await self.work()
        finally:
            self.model.down = False
        self.assertEqual([r.url.host for r in self.model.calls], ["local.test"])
        async with ext.async_session_factory() as s:
            cloud, local = await s.get(Run, cloud), await s.get(Run, local)
            queued = await s.scalar(
                select(func.count()).select_from(Run).where(Run.status == "queued")
            )
        self.assertEqual(cloud.status, "failed")
        self.assertIn("route cloud not allowed", cloud.error)
        self.assertEqual(local.status, "failed")
        self.assertIn("local unreachable", local.error)
        self.assertEqual(queued, 0)

    async def test_03_review_with_passkey(self):
        """A gated statement appears in the review queue; a human accepts it with a passkey."""
        name = str(self.s["register_st"][("P1", "name")][0])
        self.assertIn(name, await self.queue())
        # Another person's passkey cannot sign amal's review.
        started = await self.post(
            "/api/review/options", {"statement": name, "verdict": "accept"}
        )
        await self.post(
            "/api/review/commit",
            {
                "hash": started["hash"],
                "credential": self.keys["basel"].assert_(started["hash"]),
            },
            status=409,
        )
        done = await self.review(name)
        self.assertEqual(done["tier"], "accepted")
        self.assertNotIn(name, await self.queue())
        page = await self.get(f"/api/monitor/statement/{name}")
        [review] = [a for a in page["about"] if a["prop"] == "review"]
        self.assertEqual(review["actor"]["kind"], "human")
        self.assertTrue(review["actor"]["label"].startswith("amal"))

    async def test_04_mcp_proposal_attribution(self):
        """An external agent connects over MCP, searches, proposes; the proposal shows attributed to the agent on behalf of its user."""
        token = self.s["token"] = await self.mcp_connect()
        hits = (await self.mcp(token, "read", {"target": "search", "query": "Yousef"}))[
            "hits"
        ]
        post_p1 = self.s["post_st"][("P1", "last_seen")]
        self.assertIn(post_p1[1], [h["subject"] for h in hits])
        # The post and the register entry name him the same day in neighbouring
        # towns; the agent proposes they are one person, citing both.
        reg = self.s["register_st"]
        register_p1 = reg[("P1", "name")][1]
        made = await self.mcp(
            token,
            "propose_identity",
            {
                "relation": "same_as",
                "person": register_p1,
                "other": post_p1[1],
                "basis": [
                    f"statement:{self.s['post_st'][('P1', 'name')][0]}",
                    f"statement:{post_p1[0]}",
                    f"statement:{reg[('P1', 'name')][0]}",
                    f"statement:{reg[('P1', 'arrested_at')][0]}",
                ],
                "evidence": ["corroborating_field"],
                "based_on": await self.head(token, register_p1),
            },
        )
        self.assertEqual(made["tier"], "unconfirmed")
        sid = made["statement"].removeprefix("statement:")
        [card] = [c for c in (await self.panels())["queue"] if c["id"] == sid]
        self.assertEqual(card["actor"]["kind"], "agent")
        self.assertTrue(card["actor"]["for"].startswith("amal"))
        self.assertTrue(card["actor"]["label"].startswith("Test Agent for "))
        self.assertEqual((await self.review(sid))["tier"], "accepted")

    async def test_20_confirm_places(self):
        """Position claims stay unplaced until a person confirms a place, once per name."""
        async with ext.async_session_factory() as s, s.begin():
            await places.import_file(s, places.FIXTURE)
        layer = (await self.panels())["map"]
        self.assertEqual(layer["points"], [])
        self.assertTrue(layer["unresolved"])
        tried = set()
        while todo := [u for u in layer["unresolved"] if u["id"] not in tried]:
            claim = todo[0]
            tried.add(claim["id"])
            found = (await self.get("/api/places", query_string={"q": claim["place"]}))[
                "items"
            ]
            if not found:  # not in the gazetteer: a person records the site
                site = await self.post(
                    "/api/places",
                    json={"name_ar": claim["place"], "lon": 36.33, "lat": 33.54},
                    status=201,
                )
                found = [site]
            # A governorate sharing a town's name is not the town.
            pick = next(p for p in found if p["kind"] != "governorate")
            started = await self.post(
                "/api/places/confirm", {"statement": claim["id"], "place": pick["id"]}
            )
            await self.post(
                "/api/review/commit",
                {
                    "hash": started["hash"],
                    "credential": self.keys["amal"].assert_(started["hash"]),
                },
            )
            layer = (await self.panels())["map"]
        self.assertTrue(layer["points"])
        self.assertEqual(layer["unresolved"], [])
        self.assertEqual(await self.verify(), [])

    async def test_16_use_driven_queue(self):
        """A statement placed on a person's track moves ahead of untouched ones in the queue."""
        # The transfer arrived last, so its fact sits behind an older card nobody
        # has used (the OCR derivation) until the person view puts it on P1's track.
        last_seen = str(self.s["transfer_st"][("P1", "transferred_to")][0])
        cards = (await self.panels())["queue"]
        untouched = next(c["id"] for c in cards if c["prop"] == "derived_from")
        before = [c["id"] for c in cards]
        self.assertLess(before.index(untouched), before.index(last_seen))
        mentions = [
            self.s[k][("P1", "name")][1]
            for k in ("register_st", "post_st", "transfer_st")
        ]
        view = await self.person(mentions[0])
        self.assertEqual(
            sorted(view["members"]), sorted(m.removeprefix("person:") for m in mentions)
        )
        on_track = {i for step in view["track"]["steps"] for i in step["ids"]}
        self.assertIn(last_seen, on_track)
        cards = (await self.panels())["queue"]
        ids = [c["id"] for c in cards]
        self.assertLess(ids.index(last_seen), ids.index(untouched))
        used = [c["used"] for c in cards]
        self.assertEqual(used, sorted(used, reverse=True))

    async def test_08_two_click_trace(self):
        """Every statement in the UI traces to its bytes, run and signer in at most two clicks."""
        for card in (await self.panels())["queue"]:
            page = await self.get(f"/api/monitor/statement/{card['id']}")  # click 1
            self.assertTrue(page["signature"]["verifies"], card["id"])
            run = await self.get(f"/api/monitor/run/{page['run_id']}")  # click 2
            self.assertEqual(run["id"], page["run_id"])
            signer = await self.get(f"/api/monitor/principal/{page['signer']['id']}")
            self.assertEqual(signer["id"], page["signer"]["id"])
            if page["doc"]:
                blob = await self.get(f"/api/monitor/blob/{page['doc']['sha256']}")
                self.assertEqual(blob["sha256"], page["doc"]["sha256"])
        # An accepted fact: its bytes are the OCR text, derived from the scan.
        name = self.s["register_st"][("P1", "name")][0]
        page = await self.get(f"/api/monitor/statement/{name}")
        self.assertEqual(page["doc"]["sha256"], self.s["register_text"])
        run = await self.get(f"/api/monitor/run/{page['run_id']}")
        self.assertEqual(
            (run["tool"], run["model_served"]), ("extract", "local/qwen-test")
        )

    async def test_12_proposal_rules(self):
        """An agent retries the same proposal: one statement exists. It re-proposes a value a human rejected: refused with a reason. A subject changes during review: the card shows stale."""
        token, p2 = self.s["token"], self.s["register_st"][("P2", "name")][1]
        father = self.mcp_item(
            p2, "father_name", "خالد", [972, 330, 1074, 410], await self.head(token, p2)
        )
        first = (await self.mcp(token, "propose_statements", {"items": [father]}))[
            "results"
        ][0]
        retry = (await self.mcp(token, "propose_statements", {"items": [father]}))[
            "results"
        ][0]
        self.assertEqual(retry["statement"], first["statement"])
        async with ext.async_session_factory() as s:
            count = await s.scalar(
                select(func.count()).where(
                    Statement.subject == p2, Statement.prop == "father_name"
                )
            )
        self.assertEqual(count, 1)
        self.assertEqual(
            (
                await self.review(
                    first["statement"].removeprefix("statement:"),
                    "reject",
                    reason="misread",
                )
            )["tier"],
            "rejected",
        )
        again = (await self.mcp(token, "propose_statements", {"items": [father]}))[
            "results"
        ][0]
        self.assertEqual(
            again["refused"], "value was rejected by a human for this subject"
        )

        # A misread of the twin's place of arrest waits for review ...
        place = self.mcp_item(
            p2, "arrested_at", "حرستا", [351, 330, 464, 420], await self.head(token, p2)
        )
        pending = (await self.mcp(token, "propose_statements", {"items": [place]}))[
            "results"
        ][0]
        pending = pending["statement"].removeprefix("statement:")
        started = await self.post(
            "/api/review/options", {"statement": pending, "verdict": "accept"}
        )
        # ... and while its passkey prompt is open, the right reading is accepted.
        arrest = self.mcp_item(
            p2,
            "arrested_at",
            "القابون",
            [351, 330, 464, 420],
            await self.head(token, p2),
        )
        other = (await self.mcp(token, "propose_statements", {"items": [arrest]}))[
            "results"
        ][0]
        await self.review(other["statement"].removeprefix("statement:"))
        refused = await self.post(
            "/api/review/commit",
            {
                "hash": started["hash"],
                "credential": self.keys["amal"].assert_(started["hash"]),
            },
            status=409,
        )
        self.assertIn("stale", refused["error"])
        [card] = [c for c in (await self.panels())["queue"] if c["id"] == pending]
        stale = {i["name"]: i["ok"] for i in card["invariants"]}
        self.assertFalse(stale["subject not stale"])

    async def test_13_mcp_never_exceeds_human(self):
        """An MCP session never sees or does more than the human behind it."""
        reader = await self.mcp_connect(scope="read")
        p2 = self.s["register_st"][("P2", "name")][1]
        item = self.mcp_item(p2, "mother_name", "سعاد", [843, 341, 949, 417], None)
        r = await self.mcp(reader, "propose_statements", {"items": [item]}, status=403)
        self.assertIn('error="insufficient_scope"', r.headers["WWW-Authenticate"])
        me = await self.mcp(reader, "read", {"target": "whoami"})
        self.assertEqual(me["scopes"], ["read"])

        carol = await self.mcp_connect("carol")
        self.assertIn(
            "hits", await self.mcp(carol, "read", {"target": "search", "query": "دوما"})
        )
        async with ext.async_session_factory() as s, s.begin():
            user = (
                await s.execute(select(User).where(User.email == "carol@example.com"))
            ).scalar_one()
            await s.execute(delete(roles_users).where(roles_users.c.user_id == user.id))
        # The human lost the role, so the agent's live token stops at once.
        await self.mcp(carol, "read", {"target": "search", "query": "دوما"}, status=403)
        r = await self.clients["nobody"].get(
            "/oauth/authorize",
            query_string={
                "response_type": "code",
                "client_id": MCP_CLIENT,
                "redirect_uri": MCP_REDIRECT,
                "code_challenge": pkce()[1],
                "code_challenge_method": "S256",
                "scope": "read",
                "resource": "http://localhost/mcp",
            },
        )
        self.assertEqual(r.status_code, 403)

    async def test_15_watchlist_match(self):
        """A new document matching a watched person appears in the watcher's feed as a proposal."""
        watched = self.s["register_st"][("P1", "name")][1]
        await self.post("/api/watch", {"person": watched}, status=201)
        self.assertEqual((await self.get("/api/watch", "basel"))["items"], [])

        self.model.extractions[TRANSFER_TEXT] = TRANSFER_ITEMS
        text = self.s["transfer_text"] = await self.upload(
            "transfer", TRANSFER_TEXT.encode(), "text/plain", "transfer_order.txt"
        )
        await self.work()
        self.s["transfer_st"] = await self.statements(text["sha256"])
        mention = self.s["transfer_st"][("P1", "name")][1]

        [feed] = (await self.get("/api/watch"))["items"]
        self.assertEqual(feed["person"], watched)
        [proposal] = feed["proposals"]
        self.assertEqual(
            (proposal["mention"], proposal["tier"]), (mention, "unconfirmed")
        )
        # Sensitive names arrive masked; revealing one is a logged access.
        names = proposal["evidence"]["name"]
        self.assertTrue(all(n["masked"] for n in names))
        revealed = [
            (await self.post("/api/monitor/reveal", {"statement": n["statement"]}))[
                "display"
            ]
            for n in names
        ]
        # The new mention's name against any spelling already known for P1.
        self.assertEqual(revealed[0], "يوسف خالد علي")
        self.assertIn(revealed[1], CASE["persons"]["P1"]["spellings"])
        self.assertEqual(
            [f["prop"] for f in proposal["evidence"]["fields"]],
            ["birth_date", "detention_branch"],
        )
        self.assertEqual((await self.get("/api/watch", "basel"))["items"], [])
        # The watcher confirms it; the transfer joins the person.
        self.assertEqual((await self.review(proposal["id"]))["tier"], "accepted")

    async def test_11_time_brush_refilters(self):
        """A time brush on the timeline refilters map, feed, document list and review queue."""
        whole = await self.panels()
        may = await self.panels(["2013-05-01", "2013-05-31"])
        september = await self.panels(["2013-09-01", "2013-09-30"])
        later = await self.panels(["2014-01-01", "2014-12-31"])
        texts = {
            "may": {self.s["register"]["sha256"], self.s["post"]["sha256"]},
            "september": {self.s["transfer_text"]["sha256"]},
        }
        # The register's facts sit on its OCR text, which stands for the scan.
        scan = {self.s["register_text"]: self.s["register"]["sha256"]}
        for label, window, panel in (
            ("may", ("2013-05-01", "2013-05-31"), may),
            ("september", ("2013-09-01", "2013-09-30"), september),
        ):
            docs = {d["sha256"] for d in panel["documents"]}
            self.assertEqual(docs, texts[label], panel["documents"])
            self.assertTrue(
                panel["queue"] and panel["feed"] and panel["map"]["points"], label
            )
            for row in panel["queue"] + panel["feed"]:
                sha = row["doc"]["sha256"]
                self.assertIn(scan.get(sha, sha), docs, label)
            for p in panel["map"]["points"]:
                self.assertTrue(window[0] <= p["from"] <= window[1], (label, p))
            for name in ("queue", "feed", "documents"):
                self.assertLess(len(panel[name]), len(whole[name]), (label, name))
        for name in ("queue", "feed", "documents"):
            self.assertEqual(later[name], [], name)
        self.assertEqual(later["map"]["points"], [])
        self.assertEqual(later["timeline"], whole["timeline"])  # the brush can move

    async def test_14_person_from_three_spellings(self):
        """A name searched in three spellings opens one person view with track, forks and gaps."""
        p1 = await self.p1()
        twin = self.s["register_st"][("P2", "name")][1].removeprefix("person:")
        for query in CASE["person_view_for_P1"]["search"]:
            hits = {h["person"]: h for h in await self.search(query)}
            self.assertIn(p1, hits, query)
            self.assertEqual(hits[p1]["members"], 3, query)
        view = await self.person(p1)
        self.assertEqual(len(view["members"]), 3)
        self.assertNotIn(twin, view["members"])
        steps = {s["date"]: s for s in view["track"]["steps"]}
        # Last seen in Douma, arrested in Harasta: one plausible day, no fork.
        self.assertFalse(steps["2013-05-02"]["fork"])
        self.assertEqual(sorted(steps["2013-05-02"]["places"]), ["حرستا", "دوما"])
        self.assertIn("سجن ك المركزي", steps["2013-09-17"]["places"])
        self.assertEqual(view["track"]["gap_after"], "2013-09-17")
        self.assertNotIn("position", {f["kind"] for f in view["forks"]})
        self.assertTrue(any(g["to"] == "2013-09-17" for g in view["gaps"]))
        [dup] = view["duplicates"]
        self.assertEqual(dup["person"], twin)
        self.assertIn("mother_name", dup["differ"])

    async def test_06_direct_edit_caught(self):
        """Edit one statement row directly in Postgres; verification catches it."""
        sid = self.s["register_st"][("P1", "name")][0]
        edit = text("update statement set prop = 'father_name' where id = :i")
        async with ext.async_session_factory() as s:
            with self.assertRaises(DBAPIError):
                async with s.begin():
                    await s.execute(edit, {"i": sid})
        async with ext.async_session_factory() as s:
            async with s.begin():
                # A superuser bypasses the trigger; the edit is undone after the check.
                await s.execute(text("set local session_replication_role = replica"))
                await s.execute(edit, {"i": sid})
                problems = await verify_db(s)
                await s.rollback()
        self.assertTrue(
            any(str(sid) in p and "modified" in p for p in problems), problems
        )
        self.assertEqual(await self.verify(), [])

    async def test_17_acquisition_and_run_edits_caught(self):
        """Editing an acquisition's origin or a completed run's model served is caught by verification."""
        acq = self.s["register"]["acquisition_id"]
        async with ext.async_session_factory() as s:
            run = (
                await s.execute(
                    select(Run.id).where(Run.tool == "extract", Run.status == "done")
                )
            ).scalar()
        for sql, ref in (
            ("update acquisition set origin = 'device:elsewhere' where id = :i", acq),
            ("update run set model_served = 'vendor/other' where id = :i", run),
        ):
            async with ext.async_session_factory() as s:
                async with s.begin():
                    await s.execute(
                        text("set local session_replication_role = replica")
                    )
                    await s.execute(text(sql), {"i": ref})
                    problems = await verify_db(s)
                    await s.rollback()
            self.assertTrue(any(str(ref) in p for p in problems), (sql, problems))
        self.assertEqual(await self.verify(), [])

    async def anchor(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.web)) as client:
            batch, errors = await anchor.run_once(ext.async_session_factory, client)
        self.assertEqual(errors, [])
        return batch

    async def test_07_anchored_rewrite_caught(self):
        """Rewrite anchored history as an insider with DB and key access; verification against the OpenTimestamps, TSA and partner-held roots catches it. Rows after the last anchor show as provisional, and a package cannot be finalized until its rows are anchored."""
        batch = await self.anchor()
        async with ext.async_session_factory() as s:
            self.assertIsNone(await anchor.provisional(s))
            self.assertEqual((await anchor.verify_anchors(s))[0], [])
        self.assertEqual(len(list(self.partner.glob("*.root"))), 1)

        # An acquisition carries no signature, so only the anchors can catch a
        # consistent rewrite of it (a signed statement already fails verify_db).
        target = self.s["register"]["acquisition_id"]
        async with ext.async_session_factory() as s:
            async with s.begin():
                # Triggers off; rewrite the origin, rehash it, relink the chain and
                # recompute the batch root, as an insider with database access would.
                await s.execute(text("set local session_replication_role = replica"))
                acq = await s.get(Acquisition, uuid.UUID(target))
                acq.origin = "device:planted"
                new_hash = chain.row_hash("acquisition", row_dict(acq, "acquisition"))
                await s.flush()
                seqs = list(
                    (await s.execute(select(Chain.seq).order_by(Chain.seq))).scalars()
                )
                entry = (
                    await s.execute(select(Chain).where(Chain.ref == target))
                ).scalar_one()
                before = await s.get(Chain, entry.seq - 1)
                prev = before.hash if before else chain.GENESIS
                for seq in seqs[entry.seq - 1 :]:
                    e = await s.get(Chain, seq)
                    if e.ref == target:
                        e.row_hash = new_hash
                    e.prev_hash = prev
                    e.hash = prev = chain.link(prev, seq, e.kind, e.row_hash)
                    await s.flush()
                b = await s.get(Batch, batch.id)
                b.root = merkle.root([(await s.get(Chain, q)).hash for q in seqs])
                await s.flush()
                self.assertEqual(await verify_db(s), [])
                problems, _ = await anchor.verify_anchors(s)
                await s.rollback()
        for kind in ("ots", "tsa", "partner"):
            self.assertTrue(any(kind in p for p in problems), (kind, problems))

        await self.p1()  # a view is a new row, after the anchor
        async with ext.async_session_factory() as s:
            self.assertEqual(await anchor.provisional(s), batch.last_seq + 1)

    async def test_19_brief_and_two_attestations(self):
        """A brief sentence without statement ids cannot be saved; export needs two distinct attestations over the manifest hash."""
        brief = await self.post(
            "/api/packages/briefs",
            {"title": "What we hold on Yousef", "audience": "court"},
        )
        sentences = f"/api/packages/briefs/{brief['id']}/sentences"
        name = str(self.s["register_st"][("P1", "name")][0])
        transfer = str(self.s["transfer_st"][("P1", "transferred_to")][0])
        for ids in ([], [transfer]):  # uncited, and citing a draft
            await self.post(
                sentences, {"text": "He was moved.", "statement_ids": ids}, status=400
            )
        # The transfer is accepted now, after the last anchor.
        await self.review(transfer)
        await self.post(
            sentences, {"text": "The register names him.", "statement_ids": [name]}
        )
        await self.post(
            sentences,
            {
                "text": "He was transferred to Central Prison K.",
                "statement_ids": [transfer],
            },
        )
        build = f"/api/packages/briefs/{brief['id']}/build"
        refused = await self.post(build, {}, status=400)
        self.assertIn("anchored", refused["message"])
        await self.anchor()
        built = await self.post(build, {})
        pid, sha = built["id"], built["manifest_sha256"]

        async def attest(who, status=200):
            await self.post(f"/api/packages/{pid}/attest/options", {}, who=who)
            return await self.post(
                f"/api/packages/{pid}/attest",
                self.keys[who].assert_(sha),
                who=who,
                status=status,
            )

        await self.get(f"/packages/{pid}/download", status=400)
        await attest("amal")
        self.assertIn("second person", (await attest("amal", 400))["message"])
        await self.post(f"/api/packages/{pid}/finalize", {}, status=400)
        await attest("basel")
        await self.post(f"/api/packages/{pid}/finalize", {}, who="basel")
        r = await self.clients["basel"].get(f"/packages/{pid}/download")
        self.assertEqual(r.status_code, 200)
        self.s["package"] = (sha, await r.get_data())

    async def test_05_offline_package_verify(self):
        """Export a package and verify it offline with `c2patool` plus the inclusion proof, with no database access."""
        sha, body = self.s["package"]
        await ext.engine.dispose()
        out = self.s["unzipped"] = self.tmp / "package"
        zipfile.ZipFile(io.BytesIO(body)).extractall(out)
        self.assertEqual(
            hashlib.sha256((out / "manifest.json").read_bytes()).hexdigest(), sha
        )
        # A separate process that is never given the database.
        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(self.tmp),
        }
        cli = subprocess.run(
            [sys.executable, "-m", "stk.ledger.verify_package", str(out)],
            capture_output=True,
            text=True,
            env=env
            | {
                "SQLALCHEMY_DATABASE_URI": "postgresql+asyncpg://nobody@127.0.0.1:1/none"
            },
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        manifest = orjson.loads((out / "manifest.json").read_bytes())
        self.assertTrue(manifest["proofs"])
        for p in manifest["proofs"]:
            entry = next(e for e in manifest["chain"] if e["seq"] == p["seq"])
            size = next(
                b["size"] for b in manifest["batches"] if b["root"] == p["batch_root"]
            )
            self.assertTrue(
                merkle.verify_inclusion(
                    entry["hash"], p["index"], size, p["path"], p["batch_root"]
                )
            )
        report = orjson.loads(
            subprocess.run(
                ["c2patool", str(out / "package.svg")], capture_output=True, check=True
            ).stdout
        )
        self.assertNotEqual(report["validation_state"], "Invalid")
        claim = next(
            a
            for a in report["manifests"][report["active_manifest"]]["assertions"]
            if a["label"] == "org.sealbook.package"
        )
        for name in ("brief.json", "methodology.md"):
            self.assertEqual(
                claim["data"]["documents"][name],
                hashlib.sha256((out / name).read_bytes()).hexdigest(),
            )

    async def test_09_methodology_report(self):
        """The exported package includes a methodology report generated from its runs."""
        method = (self.s["unzipped"] / "methodology.md").read_text()
        self.assertRegex(method, r"amal\S*: attested 2 statements")
        for needle in (
            "Tool: extract",
            "Model requested: local/extract",
            "Model served: local/qwen-test",
            "Route: local",
        ):
            self.assertIn(needle, method)

    async def test_18_erasure(self):
        """After erasing a person's data, the chain still verifies and no search, view or payload returns their values."""
        p1, token = await self.p1(), self.s["token"]
        twin = self.s["register_st"][("P2", "name")][1].removeprefix("person:")
        members = (await self.person(p1))["members"]
        await ext.engine.dispose()
        result = await asyncio.to_thread(
            self.app.test_cli_runner().invoke,
            erase_cmd,
            [f"person:{p1}", "--reason", "family request", "--by", "amal@example.com"],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(await self.verify(), [])

        for query in CASE["person_view_for_P1"]["search"]:
            found = {h["person"] for h in await self.search(query)}
            self.assertFalse(found & set(members), query)
        self.assertIn(twin, {h["person"] for h in await self.search("يوسف خالد العلي")})
        secrets = ("حرستا", "دوما", "فاطمة", "Yousef", "يوسف خالد علي", "سجن ك المركزي")
        for ref in members:
            view = await self.person(ref)
            del view["places"]  # the gazetteer, not anything about him
            view = orjson.dumps(view).decode()
            for secret in secrets:
                self.assertNotIn(secret, view, ref)
        for query in ("حرستا", "Yousef", "يوسف خالد علي"):
            hits = (
                await self.mcp(token, "read", {"target": "search", "query": query})
            )["hits"]
            self.assertFalse(
                [h for h in hits if h["subject"].removeprefix("person:") in members],
                query,
            )
        sid = self.s["register_st"][("P1", "name")][0]
        page = await self.get(f"/api/monitor/statement/{sid}")
        self.assertTrue(page["erased"])
        async with ext.async_session_factory() as s:
            values = (
                (
                    await s.execute(
                        select(Statement.prop)
                        .join(Payload, Payload.commitment == Statement.commitment)
                        .where(
                            Statement.subject.in_([f"person:{m}" for m in members]),
                            Statement.prop != "access",  # views after the erasure
                        )
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual(values, ["erasure"])


ORDER = [
    "test_00_timed_comparison",
    "test_01_ingest",
    "test_02_extraction_spans",
    "test_10_sensitive_routes",
    "test_03_review_with_passkey",
    "test_04_mcp_proposal_attribution",
    "test_08_two_click_trace",
    "test_12_proposal_rules",
    "test_13_mcp_never_exceeds_human",
    "test_15_watchlist_match",
    "test_20_confirm_places",
    "test_16_use_driven_queue",
    "test_11_time_brush_refilters",
    "test_14_person_from_three_spellings",
    "test_06_direct_edit_caught",
    "test_17_acquisition_and_run_edits_caught",
    "test_07_anchored_rewrite_caught",
    "test_19_brief_and_two_attestations",
    "test_05_offline_package_verify",
    "test_09_methodology_report",
    "test_18_erasure",  # last: it destroys values
]


if __name__ == "__main__":
    unittest.main()
