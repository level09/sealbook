"""Add evidence: case records and their migration, intake refusing unknown cases,
per-item type and sensitivity, document type suggestions, the browser fingerprint
check, duplicates pointing at the original, the waiting-for-a-model state and sheet
headers below title rows. Throwaway Postgres; models are not called."""

import hashlib
import io
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest import mock

from openpyxl import Workbook
from quart.datastructures import FileStorage
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

import stk.extensions as ext
from alembic import command
from stk.cases import service as cases
from stk.cases.service import ensure as ensure_case
from stk.ingest.batch import _failure
from stk.ingest.service import suggest_doc_type
from stk.ledger.models import Acquisition, Blob, Run
from stk.migrations import (
    build_alembic_config,
    get_database_url,
    throwaway_database_url,
)
from stk.settings import Config
from stk.sheets import mapping, parse
from stk.sheets.models import DatasetVersion
from tests import test_review

POSTGRES = make_url(get_database_url()).get_backend_name() == "postgresql"


def titled_sheet() -> bytes:
    """Two title rows, then the header, with an empty column in the middle."""
    book = Workbook()
    ws = book.active
    ws.title = "Lists"
    ws.append(["Detainees from Harasta"])
    ws.append(["Volunteer copy, 2013"])
    ws.append(["No", "Name", "Mother", None, "Place"])
    ws.append(["217", "Yousef Khaled Al-Ali", "Fatima", None, "Harasta"])
    ws.append(["218", "Mohammad Ahmad", "Mariam", None, "Homs"])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


class PureTest(unittest.TestCase):
    def test_model_timeouts_read_as_words(self):
        for error in (
            "RouteError: local unreachable: ReadTimeout('')",
            "ReadTimeout: ",
        ):
            run = Run(id=uuid.uuid4(), tool="ocr", error=error)
            self.assertEqual(
                _failure(run)["detail"],
                "The model on this server did not answer in time; try again later.",
            )

    def test_doc_type_suggestions(self):
        cases_ = {
            ("handwritten_note_14.png", "image/png"): "letter",
            ("transfer_order_17.pdf", "application/pdf"): "transfer_order",
            ("witness_call_0412.m4a", "audio/mp4"): "witness_statement",
            ("شهادة.ogg", "audio/ogg"): "witness_statement",
            ("IMG_2231.jpg", "image/jpeg"): "photo",
            ("clip.mp4", "video/mp4"): "video",
            ("scan.pdf", "application/pdf"): "unknown",
        }
        for (name, mime), want in cases_.items():
            self.assertEqual(suggest_doc_type(name, mime)["doc_type"], want, name)
        people = suggest_doc_type("list.xlsx", parse.XLSX, people=True)
        self.assertEqual(people["doc_type"], "detention_register")
        self.assertIn("columns", people["why"])

    def test_header_found_below_title_rows(self):
        (sheet,) = parse.read(titled_sheet(), parse.XLSX)
        self.assertEqual(sheet.header, 3)
        self.assertEqual(sheet.head, ("No", "Name", "Mother", "", "Place"))
        self.assertEqual(sheet.dropped, (4,))
        self.assertEqual(list(sheet.body), [4, 5])
        cols = mapping.columns(sheet)
        self.assertEqual([c["column"] for c in cols], [1, 2, 3, 5])
        self.assertEqual(cols[1]["samples"], ["Yousef Khaled Al-Ali", "Mohammad Ahmad"])
        fields = {
            "No": "key",
            "Name": "name",
            "Mother": "mother_name",
            "Place": "place",
        }
        mapping.check(sheet, "No", fields)  # the dropped column needs no field
        rows = mapping.diff(sheet, None, "No", fields)
        self.assertEqual(
            [(r["row"], r["status"]) for r in rows], [(4, "new"), (5, "new")]
        )
        # A plain sheet keeps its header on row 1; numbers alone are never a header.
        (plain,) = parse.read(b"id,name\n1,a\n", parse.CSV)
        self.assertEqual(plain.header, 1)
        (numbers,) = parse.read(b"1,2\nid,name\n3,b\n", parse.CSV)
        self.assertEqual(numbers.header, 2)

    def test_close_names(self):
        known = [cases.Case(slug=s, name=s) for s in ("case-demo-02", "Branch 999")]
        self.assertEqual(cases.close("case-demo-2", known).slug, "case-demo-02")
        self.assertEqual(cases.close("branch 999 2013", known).slug, "Branch 999")
        self.assertIsNone(cases.close("Harasta lists", known))
        self.assertIsNone(cases.close("abc", known))


@unittest.skipUnless(POSTGRES, "cases need Postgres (set SQLALCHEMY_DATABASE_URI)")
class CaseMigrationTest(unittest.TestCase):
    def test_backfill_downgrade_and_upgrade_again(self):
        url = self.enterContext(throwaway_database_url(sync=True))
        config = build_alembic_config()
        config.config_file_name = None
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "20261027_research")
        engine = create_engine(url)
        self.addCleanup(engine.dispose)
        early = datetime(2024, 1, 2, tzinfo=UTC)
        who = uuid.uuid4()
        with engine.begin() as c:
            c.execute(
                text(
                    "insert into principal (id, kind, name, created_at) values (:i, 'human', 'clerk', now())"
                ),
                {"i": who},
            )
            c.execute(
                text(
                    "insert into blob (sha256, md5, size, mime, storage_ref, created_at) values (:s, :m, 1, 'text/plain', 'x', now())"
                ),
                {"s": "a" * 64, "m": "b" * 32},
            )
            for case, at in (
                ("case-a", early),
                ("case-a", early + timedelta(days=3)),
                ("case-b", early),
                (None, early),
            ):
                c.execute(
                    text(
                        "insert into acquisition (id, blob_sha256, origin, collector_id, at, context, sensitivity, doc_type)"
                        " values (:i, :s, 'device:x', :w, :at, cast(:ctx as jsonb), 'sensitive', 'unknown')"
                    ),
                    {
                        "i": uuid.uuid4(),
                        "s": "a" * 64,
                        "w": who,
                        "at": at,
                        "ctx": f'{{"investigation": "{case}"}}' if case else "{}",
                    },
                )
            c.execute(
                text(
                    "insert into case_setting (investigation, sensitive, research, updated_by, updated_at) values ('case-c', true, false, :w, now())"
                ),
                {"w": who},
            )

        def rows():
            with engine.connect() as c:
                return {
                    r.slug: r
                    for r in c.execute(
                        text(
                            "select slug, name, created_by, created_at from case_record"
                        )
                    )
                }

        command.upgrade(config, "head")
        found = rows()
        self.assertEqual(set(found), {"case-a", "case-b", "case-c"})
        self.assertEqual(found["case-a"].name, "case-a")
        self.assertIsNone(found["case-a"].created_by)  # nobody is known to have made it
        self.assertEqual(found["case-a"].created_at, early)
        command.downgrade(config, "20261027_research")
        with engine.connect() as c:
            self.assertIsNone(c.scalar(text("select to_regclass('case_record')")))
        command.upgrade(config, "head")
        self.assertEqual(set(rows()), {"case-a", "case-b", "case-c"})


@unittest.skipUnless(POSTGRES, "intake needs Postgres (set SQLALCHEMY_DATABASE_URI)")
class IntakeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        test_review.ReviewTest.setUp(self)
        # Upload writes where the app says, sheets read Config: one place in tests.
        self.app.config["BLOB_DIR"] = Config.BLOB_DIR

    asyncTearDown = test_review.ReviewTest.asyncTearDown
    client = test_review.ReviewTest.client

    async def asyncSetUp(self):
        await test_review.ReviewTest.asyncSetUp(self)
        async with ext.async_session_factory() as s, s.begin():
            await ensure_case(s, "case-r")

    async def upload(self, client, data, name="memo.txt", mime="text/plain", **form):
        fields = {
            "investigation": "case-r",
            "origin": "partner-b",
            "batch": "b1",
        } | form
        r = await client.post(
            "/ingest/file",
            files={"file": FileStorage(io.BytesIO(data), name, content_type=mime)},
            form=fields,
            headers=client.csrf | {"Accept": "application/json"},
        )
        return r.status_code, await r.get_json()

    async def count(self, model):
        async with ext.async_session_factory() as s:
            return await s.scalar(select(func.count()).select_from(model))

    async def status(self, client, batch="b1"):
        r = await client.get(f"/ingest/batch/{batch}")
        return {i["name"]: i for i in (await r.get_json())["items"]}

    async def test_cases_list_create_and_warn_on_close_names(self):
        client = await self.client()

        async def create(**item):
            r = await client.post(
                "/api/cases", json={"item": item}, headers=client.csrf
            )
            return r.status_code, await r.get_json()

        self.assertEqual((await create(name="Case-R"))[0], 409)
        status, body = await create(name="case-rr", description="Detentions in 2013")
        self.assertEqual(
            (status, body["reason"], body["match"]["slug"]), (409, "close", "case-r")
        )
        status, body = await create(
            name="case-rr", description="Detentions in 2013", confirm=True
        )
        self.assertEqual(status, 201, body)
        self.assertEqual(body["item"]["slug"], "case-rr")
        self.assertEqual((await create(name=" "))[0], 400)
        listed = {
            c["slug"]: c
            for c in (await (await client.get("/api/cases")).get_json())["items"]
        }
        self.assertEqual(listed["case-r"]["sources"], 1)
        self.assertEqual(listed["case-r"]["origins"], ["device:scanner"])
        self.assertEqual(listed["case-rr"]["sources"], 0)
        self.assertEqual(listed["case-rr"]["description"], "Detentions in 2013")
        switcher = await (await client.get("/api/monitor/investigations")).get_json()
        self.assertEqual(switcher["items"], ["case-r", "case-rr"])

    async def test_intake_refuses_an_unknown_case_and_bad_fields(self):
        client = await self.client()
        before = await self.count(Acquisition)
        for form, want in (
            ({"investigation": "case-new"}, "no case named case-new"),
            ({"investigation": ""}, "Choose the case"),
            ({"doc_type": "scanned_order"}, "Unknown document type"),
            ({"sensitivity": "secret"}, "sensitive or ordinary"),
            ({"sha256": "not-hex"}, "64 hex"),
        ):
            status, body = await self.upload(client, b"new bytes", **form)
            self.assertEqual(status, 400, form)
            self.assertIn(want, body["message"])
        r = await client.post(
            "/ingest/url",
            json={"item": {"url": "https://example.org/", "investigation": "case-new"}},
            headers=client.csrf,
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(await self.count(Acquisition), before)
        async with ext.async_session_factory() as s:
            self.assertEqual(
                await s.scalar(select(func.count()).select_from(cases.Case)), 1
            )

    async def test_browser_fingerprint_must_match(self):
        client = await self.client()
        data = b"synthetic transfer order 17"
        blobs_before = await self.count(Blob)
        status, body = await self.upload(client, data, sha256="0" * 64)
        self.assertEqual(status, 422, body)
        self.assertIn("changed on the way", str(body))
        self.assertEqual(await self.count(Blob), blobs_before)
        status, body = await self.upload(
            client,
            data,
            sha256=hashlib.sha256(data).hexdigest(),
            doc_type="transfer_order",
            sensitivity="ordinary",
        )
        self.assertEqual(status, 200, body)
        async with ext.async_session_factory() as s:
            acq = await s.get(
                Acquisition, uuid.UUID(body["items"][0]["acquisition_id"])
            )
        self.assertEqual(acq.context["browser_sha256"], "matched")
        self.assertEqual(
            (acq.doc_type, acq.sensitivity, acq.origin),
            ("transfer_order", "ordinary", "device:partner-b"),
        )
        self.assertEqual(acq.context["investigation"], "case-r")

    async def test_duplicate_points_at_the_original_and_is_logged_on_it(self):
        client = await self.client()
        status, body = await self.upload(
            client, test_review.TEXT.encode(), name="again.txt"
        )
        self.assertEqual(status, 200, body)
        self.assertFalse(body["items"][0]["new_blob"])
        item = (await self.status(client))["again.txt"]
        self.assertTrue(item["duplicate"])
        self.assertEqual(item["original"]["origin"], "device:scanner")
        self.assertEqual(item["original"]["investigation"], "case-r")
        self.assertEqual(item["original"]["title"], "Register")
        async with ext.async_session_factory() as s:
            custody = await s.scalar(
                select(func.count())
                .select_from(Acquisition)
                .where(Acquisition.blob_sha256 == item["sha256"])
            )
        self.assertEqual(custody, 2)

    async def test_waiting_for_a_model_says_why(self):
        client = await self.client()
        with mock.patch.object(Config, "RUN_MODELS", {}):
            await self.upload(client, b"sensitive memo", name="held.txt")
            await self.upload(
                client, b"ordinary memo", name="open.txt", sensitivity="ordinary"
            )
            found = await self.status(client)
        self.assertEqual(found["held.txt"]["stages"]["read"], "waiting")
        self.assertIn(
            "sensitive items are read only by a model on this server",
            found["held.txt"]["waiting"],
        )
        self.assertIn("no model allowed for this item", found["open.txt"]["waiting"])
        # Nothing queues it later on its own, so a model set up since then is said.
        with mock.patch.object(Config, "RUN_MODELS", {"local": "m/l"}):
            later = (await self.status(client))["held.txt"]["waiting"]
        self.assertIn("A model is set up now", later)
        with mock.patch.object(Config, "RUN_MODELS", {"local": "m/l"}):
            await self.upload(client, b"first in line", name="one.txt", batch="b2")
            await self.upload(client, b"second in line", name="two.txt", batch="b2")
        found = await self.status(client, "b2")
        self.assertIsNone(found["one.txt"]["waiting"])
        self.assertIn("busy with 1 other item.", found["two.txt"]["waiting"])
        self.assertTrue(found["two.txt"]["pending"])

    async def test_suggest_and_sheet_peek(self):
        client = await self.client()
        r = await client.post(
            "/ingest/suggest",
            json={
                "items": [{"name": "transfer_order_17.pdf", "mime": "application/pdf"}]
            },
            headers=client.csrf,
        )
        self.assertEqual((await r.get_json())["items"][0]["doc_type"], "transfer_order")

        async def peek(**patch):
            with mock.patch.object(mapping, "suggest", **patch):
                r = await client.post(
                    "/ingest/sheet/peek",
                    files={
                        "file": FileStorage(
                            io.BytesIO(titled_sheet()), "lists.xlsx", content_type=""
                        )
                    },
                    form={"investigation": "case-r"},
                    headers=client.csrf,
                )
            return await r.get_json()

        down = await peek(side_effect=ValueError("EMBEDDING_MODEL is not configured"))
        self.assertEqual(
            (down["table"]["header_row"], down["titles"], down["table"]["dropped"]),
            (3, 2, [4]),
        )
        self.assertEqual(down["table"]["first_row"], 4)
        self.assertEqual(down["table"]["rows"][0][1], "Yousef Khaled Al-Ali")
        self.assertIn("EMBEDDING_MODEL", down["suggest_error"])
        self.assertEqual(down["suggestion"]["doc_type"], "unknown")
        self.assertFalse(down["people"])

        async def named(cols, remembered):
            return [
                c
                | {
                    "remembered": None,
                    "suggestions": [
                        {
                            "field": "name" if c["header"] == "Name" else "ignore",
                            "score": 0.5,
                        }
                    ],
                }
                for c in cols
            ]

        up = await peek(side_effect=named)
        self.assertTrue(up["people"])
        self.assertEqual(up["suggestion"]["doc_type"], "detention_register")
        async with ext.async_session_factory() as s:
            self.assertEqual(
                await s.scalar(
                    select(func.count())
                    .select_from(Blob)
                    .where(Blob.mime == parse.XLSX)
                ),
                0,
            )

    async def test_sheet_below_title_rows_reads_file_rows(self):
        client = await self.client()
        with mock.patch.object(Config, "RUN_MODELS", {"local": "m/l"}):
            status, body = await self.upload(
                client,
                titled_sheet(),
                name="lists.xlsx",
                mime=parse.XLSX,
                sensitivity="ordinary",
            )
            self.assertEqual(status, 200, body)
            acq = body["items"][0]["acquisition_id"]
            item = {
                "dataset_name": "Harasta lists",
                "key_column": "No",
                "mapping": {
                    "No": "key",
                    "Name": "name",
                    "Mother": "mother_name",
                    "Place": "place",
                },
            }
            r = await client.post(
                f"/api/sheets/{acq}/confirm", json={"item": item}, headers=client.csrf
            )
            out = await r.get_json()
        self.assertEqual(r.status_code, 200, out)
        async with ext.async_session_factory() as s:
            run = await s.get(Run, uuid.UUID(out["run"]))
            version = await s.scalar(select(DatasetVersion))
        self.assertEqual(run.inputs["rows"], [4, 5])
        self.assertEqual(version.diff["new"], 2)
        found = (await self.status(client))["lists.xlsx"]
        self.assertTrue(found["sheet_confirmed"])


if __name__ == "__main__":
    unittest.main()
