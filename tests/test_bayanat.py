"""Bayanat import: a synthetic export zip becomes one text item per bulletin and an
item per media file, with the analysts' title and labels proposed as each item's
first pass, accepted in one gesture."""

import hashlib
import io
import unittest
import zipfile
from datetime import UTC, datetime

import orjson
import stk.extensions as ext
from PIL import Image
from sqlalchemy import select

from sealbook import blobs
from sealbook.bayanat import export
from sealbook.collaboration import stage
from sealbook.ledger.blobmeta import span_check
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Acquisition, Blob, Run, Statement, StatementTier
from sealbook.ledger.write import record_acquisition, verify_db
from sealbook.settings import Config
from tests import test_review

CASE = "case-r"
WORKED = {
    "class": "bulletin",
    "id": 101,
    "title": "Shelling of the market",
    "title_ar": "قصف السوق",
    "sjac_title": "Shelling hits Tel Ward market",
    "sjac_title_ar": "قصف يصيب سوق تل الورد",
    "source_link": "https://example.org/post/101",
    "publish_date": "2016-05-02T10:00",
    "status": "Finalized",
    "assigned_to": {"id": 3, "name": "Analyst One"},
    "first_peer_reviewer": {"id": 4, "name": "Reviewer Two"},
    "locations": [{"id": 1, "title": "Tel Ward", "full_string": "Tel Ward, Idlib"}],
    "events": [
        {
            "title": "Market shelled",
            "eventtype": {"title": "Shelling"},
            "location": {"full_string": "Tel Ward, Idlib"},
            "from_date": "2016-05-01",
            "to_date": None,
        }
    ],
    "labels": [{"title": "Killing", "path": "Type > Human"}],
    "verLabels": [
        {"title": "Damaged", "path": "Property/Buildings"},
        {"title": "Damaged", "path": "Property/Buildings"},
    ],
    "description": "<p>Smoke over the <b>market</b>.</p><p>Two shops&nbsp;burned.</p>",
    "medias": [{"filename": "market.png"}, {"filename": "absent.mp4"}],
}
MACHINE = {
    "class": "bulletin",
    "id": 102,
    "title": "A post",
    "status": "Machine Created",
}


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (120, 30, 30)).save(out, "PNG")
    return out.getvalue()


def archive(bulletins) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("export.json", orjson.dumps({"bulletins": bulletins}))
        z.writestr("market.png", png())
    return out.getvalue()


class RenderTest(unittest.TestCase):
    def test_spans_quote_the_decisions(self):
        r = export.render(WORKED)
        cut = lambda key: r.text[r.spans[key]["start"] : r.spans[key]["end"]]  # noqa: E731
        self.assertEqual(cut(("title", None)), "Shelling hits Tel Ward market")
        self.assertEqual(cut(("label", "Type, Human, Killing")), "Type, Human, Killing")
        self.assertEqual(
            cut(("observed", "Property/Buildings, Damaged")),
            "Property/Buildings, Damaged",
        )
        self.assertIn("Event: Shelling Market shelled at Tel Ward, Idlib", r.text)
        self.assertTrue(r.text.endswith("Smoke over the market.\nTwo shops burned.\n"))
        self.assertEqual(export.render(WORKED).text, r.text)

    def test_what_is_not_an_export(self):
        with self.assertRaisesRegex(ValueError, "not JSON"):
            export.read(b"{nope")
        with self.assertRaisesRegex(ValueError, "actor and incident"):
            export.read(orjson.dumps({"actors": []}))
        self.assertEqual(len(export.read(archive([WORKED])).bulletins), 1)


class ImportTest(unittest.IsolatedAsyncioTestCase):
    setUp = test_review.ReviewTest.setUp
    asyncSetUp = test_review.ReviewTest.asyncSetUp
    asyncTearDown = test_review.ReviewTest.asyncTearDown
    client = test_review.ReviewTest.client

    async def add_export(self, data):
        async with ext.async_session_factory() as s, s.begin():
            blob = await blobs.put(s, data, "application/zip", Config.BLOB_DIR)
            acq = await record_acquisition(
                s,
                blob_sha256=blob.sha256,
                origin="device:test",
                collector_id=self.agent.id,
                at=datetime.now(UTC),
                context={"investigation": CASE},
                sensitivity="ordinary",
                doc_type="unknown",
            )
        return acq.id

    async def test_import_preview_confirm_and_accept(self):
        acq = await self.add_export(archive([WORKED, MACHINE]))
        client = await self.client()
        r = await client.get(f"/api/bayanat/{acq}/preview")
        p = await r.get_json()
        self.assertEqual(r.status_code, 200, p)
        self.assertEqual(
            (p["bulletins"], p["worked"], p["media"], p["media_in_file"]), (2, 1, 2, 1)
        )
        self.assertEqual(p["labels_unknown"], [])

        r = await client.post(
            f"/api/bayanat/{acq}/confirm", json={"item": {}}, headers=client.csrf
        )
        out = await r.get_json()
        self.assertEqual(r.status_code, 200, out)
        self.assertEqual(
            out["counts"],
            {"bulletins": 2, "new": 2, "proposals": 3, "media": 1, "media_missing": 1},
        )
        again = await client.post(
            f"/api/bayanat/{acq}/confirm", json={"item": {}}, headers=client.csrf
        )
        self.assertEqual(again.status_code, 409)

        async with ext.async_session_factory() as s:
            sha = hashlib.sha256(export.render(WORKED).text.encode()).hexdigest()
            rows = (
                await s.execute(
                    select(Statement, StatementTier.tier)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .where(Statement.subject == f"blob:{sha}")
                    .where(Statement.prop.in_((Prop.TITLE, Prop.LABEL)))
                )
            ).all()
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(t == Tier.UNCONFIRMED for _, t in rows))
            blob = await s.get(Blob, sha)
            for st, _ in rows:
                self.assertEqual(span_check(blob, st.span), (True, ""))
            acqs = {
                a.blob_sha256: a.context
                for a in await s.scalars(
                    select(Acquisition).where(
                        Acquisition.context["bayanat"].isnot(None)
                    )
                )
            }
            self.assertEqual(acqs[sha]["bayanat"]["reviewer"], "Reviewer Two")
            media = [c for c in acqs.values() if c["bayanat"].get("media")]
            self.assertEqual(media[0]["bayanat"]["item"], sha)
            self.assertEqual(
                (await stage.stages(s, CASE, [sha]))[sha]["stage"], "Machine created"
            )

        body = await (
            await client.post(
                "/api/review/firstpass/imported/options",
                json={"item": {"run": out["run"]}},
                headers=client.csrf,
            )
        ).get_json()
        self.assertEqual(len(body["rows"]), 3, body)
        r = await client.post(
            "/api/review/commit",
            json={
                "item": {
                    "hash": body["hash"],
                    "credential": self.passkey.assert_(body["hash"]),
                }
            },
            headers=client.csrf,
        )
        self.assertEqual(r.status_code, 200, await r.get_json())
        async with ext.async_session_factory() as s:
            self.assertEqual(
                (await stage.stages(s, CASE, [sha]))[sha]["stage"], "Finalized"
            )
            run = await s.get(Run, out["run"])
            self.assertEqual(run.status, "done")
            self.assertEqual(await verify_db(s), [])
