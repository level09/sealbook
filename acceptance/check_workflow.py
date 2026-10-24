"""Populated search/review browser check using synthetic, disposable fixtures.

Run: uv run python -m acceptance.check_workflow
"""

import asyncio
import tempfile
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import numpy as np
import uvicorn
from PIL import Image
from playwright.async_api import async_playwright, expect
from sqlalchemy import select

import stk.extensions as ext
from stk import blobs
from stk.agent_login import create_agent_login_token
from stk.discovery import service
from stk.ledger.models import Acquisition
from stk.ledger.write import record_acquisition
from stk.runs.worker import work_once
from stk.settings import Config
from tests.test_review import ReviewTest

case = ReviewTest(methodName="test_correction_is_pending_and_reviewer_receives_it")
case.setUp()


async def main():
    await case.asyncSetUp()
    server = None
    runner = None
    try:
        proposal = await case.propose(f"person:{uuid.uuid4()}", "محمد")
        await case.colleague()
        async with ext.async_session_factory() as db, db.begin():
            acq = await db.scalar(select(Acquisition))
            await service.queue(db, acq, "text/plain")
        await work_once(ext.async_session_factory, tools=["index"])
        image = Image.fromarray(
            np.random.default_rng(7).integers(0, 256, (256, 256, 3), dtype=np.uint8)
        )
        for fmt, mime in [("PNG", "image/png"), ("JPEG", "image/jpeg")]:
            buffer = BytesIO()
            image.save(buffer, format=fmt)
            async with ext.async_session_factory() as db, db.begin():
                blob = await blobs.put(db, buffer.getvalue(), mime, Config.BLOB_DIR)
                acq = await record_acquisition(
                    db,
                    blob_sha256=blob.sha256,
                    origin=f"device:{fmt}",
                    collector_id=case.agent.id,
                    at=datetime.now(UTC),
                    context={"filename": f"example.{fmt.lower()}"},
                    sensitivity="sensitive",
                    doc_type="photo",
                )
                await service.queue(db, acq, mime)
            await work_once(ext.async_session_factory, tools=["fingerprint"])
        fixture = Path(__file__).parent / "data/transfer_order.pdf"
        async with ext.async_session_factory() as db, db.begin():
            blob = await blobs.put(
                db, fixture.read_bytes(), "application/pdf", Config.BLOB_DIR
            )
            acq = await record_acquisition(
                db,
                blob_sha256=blob.sha256,
                origin="device:pdf",
                collector_id=case.agent.id,
                at=datetime.now(UTC),
                context={"filename": "transfer_order.pdf"},
                sensitivity="sensitive",
                doc_type="order",
            )
            await service.queue(db, acq, blob.mime)
        await work_once(ext.async_session_factory, tools=["pdf"])
        while await work_once(ext.async_session_factory, tools=["index"]):
            pass
        origin = "http://127.0.0.1:5017"
        case.app.config["STK_PUBLIC_URL"] = origin
        server = uvicorn.Server(
            uvicorn.Config(case.app, host="127.0.0.1", port=5017, log_level="error")
        )
        runner = asyncio.create_task(server.serve())
        async with asyncio.timeout(15):
            while not server.started:
                await asyncio.sleep(0.05)
        async with case.app.app_context():
            token = create_agent_login_token("analyst@example.com", "/review")
        async with async_playwright() as p:
            tmp = case.enterContext(tempfile.TemporaryDirectory())
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(8000)
            errors = []
            failures = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.on(
                "response",
                lambda response: (
                    failures.append(f"{response.status} {response.url}")
                    if response.status >= 400
                    else None
                ),
            )
            await page.goto(
                f"{origin}/_test/login?token={token}", wait_until="networkidle"
            )
            await page.goto(
                f"{origin}/review?statement={proposal.id}", wait_until="networkidle"
            )
            await page.get_by_role("button", name="Ask", exact=False).first.click()
            await (
                page.locator(".v-select")
                .filter(has_text="Colleague")
                .locator(".v-field")
                .click()
            )
            await (
                page.locator(".v-overlay-container")
                .get_by_role("option", name="Colleague")
                .click()
            )
            await page.get_by_label("What should they check?").fill(
                "Check the source entry together."
            )
            await page.get_by_role("button", name="Send for review", exact=True).click()
            await expect(
                page.get_by_role("heading", name="Thread", exact=True)
            ).to_be_visible()
            await expect(page.get_by_role("button", name="Accept")).to_have_count(0)
            await page.get_by_label("Reply", exact=True).fill(
                "The source has three entries."
            )
            await page.get_by_role("button", name="Reply", exact=True).click()
            await expect(
                page.get_by_text("The source has three entries.", exact=True)
            ).to_be_visible()
            await page.goto(f"{origin}/evidence", wait_until="networkidle")
            await page.get_by_role("link", name="Ask for review", exact=True).click()
            await page.get_by_role("button", name="Ask", exact=False).first.click()
            await (
                page.locator(".v-select")
                .filter(has_text="Colleague")
                .locator(".v-field")
                .click()
            )
            await (
                page.locator(".v-overlay-container")
                .get_by_role("option", name="Colleague")
                .click()
            )
            await page.get_by_label("What should they check?").fill(
                "Compare the two images."
            )
            await page.get_by_role("button", name="Send for review", exact=True).click()
            await expect(
                page.get_by_role("heading", name="Thread", exact=True)
            ).to_be_visible()
            buttons = page.get_by_role("button", name="Show sensitive item (access is")
            await expect(buttons).to_have_count(2)
            await buttons.first.click()
            await buttons.last.click()
            await expect(page.locator("section img")).to_have_count(2)
            await page.wait_for_function(
                "[...document.querySelectorAll('section img')].every(im => im.complete && im.naturalWidth > 0)"
            )
            await page.goto(f"{origin}/evidence", wait_until="networkidle")
            await page.get_by_label("Name, phrase or investigative question").fill(
                "الاسم"
            )
            await page.get_by_role("button", name="Search evidence", exact=True).click()
            source = page.locator('a[href*="/evidence/segments/"]')
            await expect(source).to_have_count(1)
            await source.click()
            reveal = page.get_by_role("button", name="Show sensitive item (access is")
            await expect(reveal).to_be_visible()
            await expect(page.locator("mark.hit")).to_have_count(0)
            await reveal.click()
            await expect(page.locator("mark.hit").first).to_have_text("الاسم")
            await page.goto(f"{origin}/evidence", wait_until="networkidle")
            await (
                page.locator(".v-expansion-panel-title")
                .filter(has_text="Indexing coverage")
                .click()
            )
            await expect(
                page.get_by_text("transfer_order.pdf", exact=True)
            ).to_be_visible()
            await page.get_by_label("Name, phrase or investigative question").fill(
                "999"
            )
            await page.get_by_role("button", name="Search evidence", exact=True).click()
            await expect(page.locator('a[href*="/evidence/segments/"]')).to_have_count(
                1
            )
            await page.locator('a[href*="/evidence/segments/"]').click()
            await page.get_by_role(
                "button", name="Show sensitive item (access is"
            ).click()
            await expect(page.get_by_label("Page", exact=True)).to_have_value("1")
            await expect(page.locator("mark.hit").first).to_have_text("999")
            await page.wait_for_function(
                "[...document.querySelectorAll('.mv canvas')].some(c => c.width > 0 && c.height > 0)"
            )
            # Worker telemetry is an admin page now; an analyst reads the case map.
            await page.goto(f"{origin}/case-map", wait_until="networkidle")
            await expect(
                page.get_by_role("heading", name="Case map", exact=True)
            ).to_be_visible()
            # Add evidence: a case is made on purpose, a file is staged with the
            # browser's fingerprint, added, and the same bytes again point at it.
            case.app.config["BLOB_DIR"] = Config.BLOB_DIR
            note = Path(tmp) / "witness_note.txt"
            note.write_text("Synthetic witness note for the workflow check.")
            await page.goto(f"{origin}/ingest/", wait_until="networkidle")
            await page.locator(".casebtn").click()
            await page.get_by_text("New case", exact=True).click()
            await page.get_by_label("Name", exact=True).fill("Workflow check case")
            await page.get_by_role("button", name="Create case").click()
            await expect(page.locator(".casebtn")).to_contain_text(
                "Workflow check case"
            )
            for round_ in ("first", "again"):
                await page.locator(".ways input[type=file][multiple]").set_input_files(
                    str(note)
                )
                await expect(page.locator(".it .mono").first).not_to_have_text(
                    "taking fingerprint"
                )
                await page.get_by_role(
                    "button", name="Add 1 item to Workflow check case"
                ).click()
                await expect(page.locator(".it.run")).to_contain_text(
                    "fingerprint matched"
                )
                if round_ == "again":
                    await expect(page.locator(".status.dup")).to_contain_text(
                        "Same bytes as"
                    )
                    break
                await page.goto(f"{origin}/ingest/", wait_until="networkidle")
            assert not errors, errors
            assert not failures, failures
            print(
                "Browser checks passed: assign, sender permissions, comment, two logged media previews, Arabic text jump, PDF page jump, coverage, add evidence to a new case with a duplicate; no browser errors or failed requests."
            )
            await browser.close()
    finally:
        if server:
            server.should_exit = True
        if runner:
            await runner
        await case.asyncTearDown()


try:
    asyncio.run(main())
finally:
    case.doCleanups()
