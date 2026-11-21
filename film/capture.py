"""Record the film's screens again from the seeded case studies.

Run against a local development server with agent login on (see film/README.md):

    uv run python film/capture.py

Writes PNG crops of real app screens, the cases' own pictures and clips, the site
fonts and a verified test package screen into film/composition/assets/captures/,
which is not tracked. Viewport and scale are fixed so every run frames the same.
Nothing here signs, accepts or rejects: the passkey prompt is opened and cancelled.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "film/composition/assets/captures"
CASES = ROOT / "acceptance/cases"
BASE = os.environ.get("SEALBOOK_URL", "http://127.0.0.1:5021")
EMAIL = os.environ.get("FILM_USER", "admin@example.com")
# The keyboard hint bar is fixed to the window and cuts through tall crops.
QUIET = ".sb-hints { display: none !important; }"
VIEW = {"width": 1600, "height": 1000}
SCALE = 2

FILES = {
    "four-names/files/vendor_form_kassar.jpg": "scan_vendor.jpg",
    "four-names/files/consultancy_agreement_qassar.jpg": "scan_consultancy.jpg",
    "four-names/files/guarantee_deed_khassar.jpg": "scan_guarantee.jpg",
    "four-names/files/ship_agent_licence.jpg": "scan_licence.jpg",
    "four-names/files/passport_copy.jpg": "scan_passport.jpg",
    "never-there/files/IMG_4471.jpg": "never_photo.jpg",
    "unwritten-name/files/gate_book_14_march.jpg": "gate_book.jpg",
    "old-footage/files/original.mp4": "footage_original.mp4",
    "old-footage/files/copy_viral.mp4": "footage_viral.mp4",
}
FONTS = [
    "IBMPlexSans-normal-400-latin.woff2",
    "IBMPlexSans-normal-500-latin.woff2",
    "IBMPlexSans-normal-600-latin.woff2",
    "IBMPlexSansArabic-normal-400-arabic.woff2",
    "IBMPlexMono-normal-400-latin.woff2",
    "Newsreader-normal-400-latin.woff2",
    "Newsreader-normal-500-latin.woff2",
    "Newsreader-italic-400-latin.woff2",
]


def copy_files():
    for src, name in FILES.items():
        shutil.copyfile(CASES / src, OUT / name)
    fonts = OUT / "fonts"
    fonts.mkdir(exist_ok=True)
    for name in FONTS:
        shutil.copyfile(ROOT / "site/fonts" / name, fonts / name)


async def proposals(app):
    """The linker's same-person proposals in the four-names case, by id."""
    import stk.extensions as ext

    async with app.app_context(), ext.async_session_factory() as db:
        rows = await db.execute(
            text(
                "select s.id from statement s join run r on r.id = s.run_id "
                "where s.prop = 'same_as' and r.tool = 'link' "
                "and r.inputs->>'investigation' = 'case-four-names'"
            )
        )
        return [str(r[0]) for r in rows]


def test_package(target: Path) -> Path:
    """A valid package from the offline verifier's own test fixture."""
    from tests.passkey import SoftPasskey
    from tests.test_site_verify import pack
    from tests.test_verify_package import PackageTests

    case = PackageTests("test_valid_package_and_canonical_round_trip")
    case.setUp()
    result = case.run_verifier()
    case.attest(SoftPasskey(), SoftPasskey())
    assert result.returncode == 0, result.stdout
    pack(case.directory, target)
    case.doCleanups()
    return target


def card(page, heading):
    return page.locator(".ppl-card").filter(
        has=page.get_by_role("heading", name=heading, exact=True)
    )


async def shoot(page, name, clip=None, locator=None, pad=0):
    path = str(OUT / f"{name}.png")
    if locator is not None:
        box = await locator.bounding_box()
        clip = {
            "x": max(box["x"] - pad, 0),
            "y": max(box["y"] - pad, 0),
            "width": box["width"] + 2 * pad,
            "height": box["height"] + 2 * pad,
        }
    await page.screenshot(path=path, clip=clip, full_page=clip is not None)
    print("wrote", name)


async def session(browser, app, case, landing="/todo"):
    from stk.agent_login import create_agent_login_token

    ctx = await browser.new_context(
        viewport=VIEW, device_scale_factor=SCALE, reduced_motion="reduce"
    )
    await ctx.add_init_script(
        f"try{{localStorage.setItem('sb.case','{case}')}}catch(e){{}}"
    )
    page = await ctx.new_page()
    page.set_default_timeout(15000)
    async with app.app_context():
        token = create_agent_login_token(EMAIL, landing)
    await page.goto(f"{BASE}/_test/login?token={token}", wait_until="networkidle")
    return ctx, page


async def go(page, path, wait=1500):
    await page.goto(BASE + path, wait_until="networkidle")
    await page.add_style_tag(content=QUIET)
    await page.wait_for_timeout(wait)


async def four_names(browser, app):
    ctx, page = await session(browser, app, "case-four-names")

    await go(page, "/sources")
    await shoot(
        page, "fn_sources", clip={"x": 196, "y": 56, "width": 1390, "height": 905}
    )

    await go(page, "/review?box=open", wait=2500)
    await shoot(page, "fn_scan", clip={"x": 640, "y": 120, "width": 560, "height": 860})

    for name, query in (
        ("fn_search_latin", "Adrian Kassar"),
        ("fn_search_arabic", "قصار"),
    ):
        await go(page, "/people?q=" + quote(query))
        await shoot(page, name, clip={"x": 196, "y": 250, "width": 1396, "height": 380})

    true_link = surveyor = None
    for sid in await proposals(app):
        await go(page, f"/review?statement={sid}", wait=1200)
        body = await page.locator("main").inner_text()
        if "Adrian Qassar" in body and "Marine surveyors register" in body:
            surveyor = sid
        elif "Ship agent licence" in body and "Adrian Kasar" in body:
            true_link = sid
    assert true_link and surveyor, "linker proposals not found; is the case seeded?"

    await go(page, f"/review?statement={true_link}", wait=2000)
    await shoot(page, "fn_link", clip={"x": 640, "y": 120, "width": 945, "height": 460})
    await page.get_by_role("button", name="Accept").first.click()
    dialog = page.locator(".v-overlay--active .v-card").filter(
        has_text="Confirm with your passkey"
    )
    await dialog.wait_for()
    await page.wait_for_timeout(800)
    await shoot(page, "fn_passkey", locator=dialog, pad=0)
    await page.get_by_role("button", name="Cancel").click()

    await go(page, f"/review?statement={surveyor}", wait=2000)
    await shoot(
        page, "fn_surveyor", clip={"x": 640, "y": 120, "width": 945, "height": 460}
    )

    await go(page, "/people?q=" + quote("Adrian Kassar"))
    await page.get_by_role("link", name="Adrian Khassar").first.click()
    await page.wait_for_load_state("networkidle")
    await page.add_style_tag(content=QUIET)
    await page.wait_for_timeout(1500)
    await shoot(page, "fn_dupes", locator=card(page, "Candidate duplicates"))
    await ctx.close()


async def person(browser, app, case, name):
    ctx, page = await session(browser, app, case)
    await go(page, "/people?q=" + quote(name))
    await page.get_by_role("link", name=name).first.click()
    await page.wait_for_load_state("networkidle")
    await page.add_style_tag(content=QUIET)
    await page.wait_for_timeout(1800)
    return ctx, page


async def other_cases(browser, app):
    ctx, page = await person(browser, app, "case-never-there", "Mara Quill")
    fork = page.locator(".ppl-fork", has_text="Two places on one day").first
    await shoot(page, "nt_disagree", locator=fork.locator("xpath=.."), pad=12)
    await ctx.close()

    ctx, page = await person(browser, app, "case-unwritten-name", "Aron Vesk")
    silence = page.get_by_text("No evidence for 73 days").first
    box = await silence.bounding_box()
    await shoot(
        page,
        "un_silence",
        clip={"x": box["x"] - 70, "y": box["y"] - 128, "width": 520, "height": 220},
    )
    await ctx.close()


async def verifier(browser):
    with tempfile.TemporaryDirectory() as tmp:
        package = test_package(Path(tmp) / "package.zip")
        ctx = await browser.new_context(viewport=VIEW, device_scale_factor=SCALE)
        page = await ctx.new_page()
        await page.goto((ROOT / "site/verify.html").as_uri(), wait_until="load")
        await page.set_input_files("#package", str(package))
        await page.locator("#result").wait_for(state="visible")
        await page.wait_for_timeout(1500)
        await shoot(page, "seal_verify", locator=page.locator("main"), pad=0)
        await ctx.close()


async def main():
    from sealbook.app import app

    OUT.mkdir(parents=True, exist_ok=True)
    copy_files()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await four_names(browser, app)
        await other_cases(browser, app)
        await verifier(browser)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
