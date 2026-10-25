"""Render the five paper documents of the demo case (case-demo-02) and their ground
truth into acceptance/demo/, and record the app's OCR of the two scans once.

Everything here is invented: the people, the branch, the documentation groups.

  uv run python -m acceptance.make_demo_docs          # render, reuse recorded OCR
  uv run python -m acceptance.make_demo_docs --ocr    # also record OCR (needs OPENROUTER_API_KEY)

OCR is recorded, never written by hand: the app's own prompt goes to MODEL_CLOUD on
the cloud route (both scans are ordinary, which the policy allows), and ocr.json
keeps the answer as received. A recording whose image or prompt no longer matches
fails the run until it is recorded again.
"""

import asyncio
import base64
import hashlib
import json
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from acceptance.make_case import BRANCH, FONT, scan_realism

OUT = Path(__file__).parent / "demo"
SEED = 1402
PAGE = {"width": 1240, "height": 1754}  # A4 at 150 dpi
SYNTHETIC = "synthetic document for the Sealbook demo, all names are fictional"
BOX_JS = "e => { const b = e.getBoundingClientRect(); return [b.left - 6, b.top - 6, b.right + 6, b.bottom + 6]; }"

# ---- detainee list (typed form, scanned) -----------------------------------------

LIST_DATE = "2014-02-12"
# (person key, name, father, mother, born, arrest place, arrest date)
LIST_ROWS = [
    ("K1", "محمد أحمد الخطيب", "أحمد", "ليلى", "1979", "حمص", "2014-01-28"),
    ("K2", "محمد أحمد الخطيب", "أحمد", "هدى", "1994", "حلب", "2014-02-03"),
    ("L1", "لينا عمر النجار", "عمر", "-", "1988", "حي الجديدة", "2014-02-11"),
    ("Z1", "عمر حسن الزين", "حسن", "-", "1990", "دوما", "2014-02-09"),
]
LIST_COLS = ["name", "father_name", "mother_name", "birth_date", "arrested_at"]
# The claims the seed makes on the list: rows 1 and 2 by mother, 3 and 4 by father.
LIST_FACTS = [
    (key, prop)
    for key, *_ in LIST_ROWS
    for prop in (
        "name",
        "birth_date",
        "arrested_at",
        "father_name" if key in ("L1", "Z1") else "mother_name",
    )
]


def list_html():
    cells = []
    for i, (key, *vals) in enumerate(LIST_ROWS):
        tds = "".join(
            f'<td><span id="{key}-{LIST_COLS[j]}">{v}</span></td>'
            if j < len(LIST_COLS)
            else f"<td>{v}</td>"
            for j, v in enumerate(vals)
        )
        cells.append(f"<tr><td>{i + 1}</td>{tds}</tr>")
    return f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><style>
@font-face {{ font-family: Hand; src: url({FONT}); }}
body{{margin:0;background:#fff;font-family:"Geeza Pro",serif;color:#1d1d1d}}
.page{{width:1240px;height:1754px;padding:90px 90px;box-sizing:border-box;position:relative}}
.top{{display:flex;justify-content:space-between;align-items:flex-start;font-size:26px;line-height:1.6}}
.top .r b{{display:block;font-size:30px}}
h1{{text-align:center;font-size:40px;margin:70px 0 10px;text-decoration:underline;text-underline-offset:12px}}
.sub{{text-align:center;font-size:24px;margin-bottom:50px}}
table{{width:100%;border-collapse:collapse;font-size:25px}}
th,td{{border:1.5px solid #222;padding:18px 10px;text-align:center;white-space:nowrap}}
th{{background:#eee;font-size:23px}}
.foot{{margin-top:90px;display:flex;justify-content:space-between;font-size:24px}}
.stamp{{position:absolute;left:180px;bottom:330px;width:210px;height:210px;border:5px solid #2a3f8f;border-radius:50%;
 color:#2a3f8f;display:flex;flex-direction:column;justify-content:center;align-items:center;transform:rotate(-14deg);font-size:22px;opacity:.8}}
.stamp i{{font-style:normal;font-size:30px;border-top:2px solid #2a3f8f;border-bottom:2px solid #2a3f8f;margin:6px 0;padding:2px 10px}}
.sig{{font-family:Hand,cursive;font-size:44px;color:#183a8a}}
.syn{{position:absolute;bottom:40px;left:0;right:0;text-align:center;font-size:16px;color:#888;font-family:Arial}}
bdi{{direction:ltr}}
</style><body><div class="page">
<div class="top"><div class="r"><b id="branch">{BRANCH}</b>قسم حلب<br>الديوان</div><div>الرقم: 212/ك<br>التاريخ: <bdi id="date">{LIST_DATE}</bdi></div></div>
<h1>كشف بأسماء الموقوفين</h1><div class="sub">الموقوفون لدى الفرع خلال الفترة من <bdi>2014-01-25</bdi> ولغاية <bdi>{LIST_DATE}</bdi></div>
<table><tr><th>م</th><th>الاسم الثلاثي</th><th>اسم الأب</th><th>اسم الأم</th><th>المواليد</th><th>مكان التوقيف</th><th>تاريخ التوقيف</th></tr>
{"".join(cells)}</table>
<div class="foot"><div>رئيس القسم<br><span class="sig">ع. س.</span></div><div>المنظم<br><span class="sig">م. ح.</span></div></div>
<div class="stamp"><span>{BRANCH}</span><i>الديوان</i><bdi>{LIST_DATE}</bdi></div>
<div class="syn">{SYNTHETIC}</div>
</div></body></html>"""


def list_values():
    """The claimed values, by span id."""
    cells = {
        f"{key}-{c}": v
        for key, *vals in LIST_ROWS
        for c, v in zip(LIST_COLS, vals, strict=False)
    }
    return {"branch": BRANCH, "date": LIST_DATE} | {
        f"{key}-{prop}": cells[f"{key}-{prop}"] for key, prop in LIST_FACTS
    }


# ---- dormitory roster (printed form, filled by hand, scanned) --------------------

ROSTER_NAMES = [("R1", "يوسف خالد العلي"), ("B1", "باسل نادر"), ("F1", "فادي كرم")]


def roster_html():
    rng = random.Random(SEED)

    def hand(id_, text):
        dx, dy, rot = rng.uniform(-8, 8), rng.uniform(-4, 3), rng.uniform(-2, 2)
        style = f"transform:translate({dx:.1f}px,{dy:.1f}px) rotate({rot:.1f}deg)"
        return f'<span class="hand" id="{id_}" style="{style}">{text}</span>'

    rows = "".join(
        f"<tr><td>{i + 1}</td><td>{hand(f'{key}-name', name)}</td><td></td></tr>"
        for i, (key, name) in enumerate(ROSTER_NAMES)
    )
    blank = "".join(f"<tr><td>{i}</td><td></td><td></td></tr>" for i in range(4, 11))
    return f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><style>
@font-face {{ font-family: Hand; src: url({FONT}); }}
body{{margin:0;background:#fff;font-family:"Geeza Pro",serif;color:#222}}
.page{{width:1240px;height:1754px;padding:100px 110px;box-sizing:border-box;position:relative}}
.top{{display:flex;justify-content:space-between;font-size:26px;line-height:1.7}}
.top b{{font-size:30px}}
h1{{text-align:center;font-size:36px;margin:60px 0 40px}}
.month{{font-size:26px;margin-bottom:24px}}
table{{width:100%;border-collapse:collapse}}
th,td{{border:1.5px solid #333;height:88px;font-size:24px;text-align:center}}
th{{height:60px;background:#eee}}
td:first-child{{width:90px}} td:last-child{{width:330px}}
/* Ruqaa ink overhangs its advance box at the end of a word; pad so the box holds it */
.hand{{display:inline-block;font-family:Hand;font-size:42px;color:#1d2a6e;padding:4px .2em 4px .9em;white-space:nowrap}}
.fill{{font-family:Hand;font-size:40px;color:#1d2a6e}}
.sign{{margin-top:70px;font-size:24px}}
.syn{{position:absolute;bottom:40px;left:0;right:0;text-align:center;font-size:16px;color:#888;font-family:Arial}}
</style><body><div class="page">
<div class="top"><div><b id="branch">{BRANCH}</b><br>السجن الداخلي</div><div>نموذج رقم 7</div></div>
<h1>قائمة نزلاء المهجع 4</h1>
<div class="month">الشهر: <span class="fill">تموز 2013</span></div>
<table><tr><th>الرقم</th><th>الاسم</th><th>ملاحظات</th></tr>{rows}{blank}</table>
<div class="sign">مسؤول المهجع: <span class="fill">أ. ك.</span></div>
<div class="syn">{SYNTHETIC}</div>
</div></body></html>"""


def roster_values():
    return {"branch": BRANCH} | {f"{k}-name": v for k, v in ROSTER_NAMES}


# ---- text-layer PDFs from fictional documentation groups -------------------------

PDF_CSS = """body{font-family:"Times New Roman",serif;font-size:12.5pt;line-height:1.55;color:#111;margin:0}
.lh{border-bottom:1.5px solid #333;padding-bottom:10px;margin-bottom:26px;display:flex;justify-content:space-between;align-items:flex-end}
.lh b{font-size:15pt;letter-spacing:.02em}.lh small{font-size:9.5pt;color:#444;text-align:right}
h1{font-size:15pt;margin:0 0 4px}.meta{font-size:10pt;color:#444;margin-bottom:22px}
p{margin:0 0 12px;text-align:justify}.ref{font-size:9.5pt;color:#444;margin-top:30px;border-top:1px solid #999;padding-top:8px}
/* a name broken across lines breaks it in the extracted text too */
.n{white-space:nowrap}"""


def n(text):
    return f'<span class="n">{text}</span>'


NOTE = f"""<!doctype html><html lang="en"><meta charset="utf-8"><style>{PDF_CSS}</style><body>
<div class="lh"><b>Lantern Documentation Group</b><small>a fictional documentation group<br>Documentation notes, 2014</small></div>
<h1>Documentation note 14/2014</h1>
<div class="meta">Compiled 20 February 2014 &middot; Sources: family interviews by phone &middot; Status: open</div>
<p>{n("Lina Omar Al-Najjar")}, born 1988, a pharmacist from Aleppo, was arrested at a checkpoint in the Jdeideh quarter on 11 February 2014. Her family reports that she was held at {n("Branch 999")}.</p>
<p>{n("Mohammed Ahmad Al-Khatib")}, born 1994, was detained in Aleppo in early February 2014.</p>
<p>Both families asked that the note be kept on file until news of either person is confirmed.</p>
<div class="ref">This document is synthetic. All names are fictional and were written for the Sealbook demo case.</div>
</body></html>"""
# (key, value, the text the value follows)
NOTE_FACTS = [
    ("L2-name", "Lina Omar Al-Najjar", ""),
    ("L2-birth_date", "1988", "Lina Omar Al-Najjar"),
    ("K2E-name", "Mohammed Ahmad Al-Khatib", ""),
    ("K2E-birth_date", "1994", "Mohammed Ahmad Al-Khatib"),
]

RELEASE = f"""<!doctype html><html lang="en"><meta charset="utf-8"><style>{PDF_CSS}</style><body>
<div class="lh"><b>Northern Testimony Archive</b><small>a fictional documentation group<br>Statement series S-2013</small></div>
<h1>Statement of a released detainee</h1>
<div class="meta">Ref. S-2013/041 &middot; Taken 28 September 2013 &middot; Published 4 October 2013 &middot; Language of interview: Arabic</div>
<p>The witness, {n("Salim Haddad")}, says he was held at {n("Branch 999")} throughout July 2013, in dormitory 4. He describes a room of about forty men and a daily count at dawn.</p>
<p>He names {n("Yousef Khaled Al-Ali")}, {n("Bassel Nader")} and {n("Fadi Karam")} as cellmates who were with him for most of that month. He did not see any of them after his release.</p>
<p>He was released on 2 August 2013 without charge and returned to his family the same evening.</p>
<p>The interview was recorded with consent. Identifying details of the witness's family have been withheld.</p>
<div class="ref">This document is synthetic. All names are fictional and were written for the Sealbook demo case.</div>
</body></html>"""
RELEASE_FACTS = [
    ("S1-name", "Salim Haddad", ""),
    ("S1-detained_at", "Branch 999", ""),
    ("R1-name", "Yousef Khaled Al-Ali", ""),
    ("B1-name", "Bassel Nader", ""),
    ("F1-name", "Fadi Karam", ""),
    ("S1-event", "released", "He was "),
]

TESTIMONY_LINES = [
    ("00:00", "الشاهدة: اسمي ريم."),
    # The branch opens her sentence: pypdf writes a digit run inside an Arabic line
    # out of order, and only a line that starts with it keeps "999" beside "الفرع".
    (
        "00:05",
        "الفرع 999، هونيك كنت موقوفة بشهر آذار 2014، ولينا النجار كانت معنا بالمهجع.",
    ),
    ("00:19", "بعدين نقلوها، سمعنا إنو على سجن ك المركزي."),
]
TESTIMONY = f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><style>
body{{font-family:"Geeza Pro",serif;font-size:13pt;line-height:1.9;color:#111;margin:0}}
.lh{{border-bottom:1.5px solid #333;padding-bottom:10px;margin-bottom:24px}}
.lh b{{font-size:15pt}}.lh small{{display:block;font-size:9.5pt;color:#444}}
h1{{font-size:15pt;margin:0 0 4px}}.meta{{font-size:10pt;color:#444;margin-bottom:22px}}
.l{{margin:0 0 10px}}.t{{font-family:Menlo,monospace;font-size:10.5pt;color:#555;margin-left:10px}}
.ref{{font-size:9.5pt;color:#444;margin-top:30px;border-top:1px solid #999;padding-top:8px;font-family:Arial;direction:ltr;text-align:left}}
.n{{white-space:nowrap}}
</style><body>
<div class="lh"><b>أرشيف الشهادات الشمالي</b><small>مجموعة توثيق متخيلة</small></div>
<h1>تفريغ شهادة صوتية، جهاز التسجيل 05</h1>
<div class="meta">ملف سري، لا ينشر ولا يرسل إلى خدمات خارجية</div>
{"".join(f'<p class="l"><span class="t">[{t}]</span>{text}</p>' for t, text in TESTIMONY_LINES)}
<div class="ref">This transcript is synthetic. All names are fictional and were written for the Sealbook demo case.</div>
</body></html>"""
TESTIMONY_FACTS = [
    ("branch", BRANCH, ""),
    ("L1-transferred_to", "سجن ك المركزي", ""),
    ("RM-name", "ريم", ""),
]


# ---- rendering ---------------------------------------------------------------------


def load(page, html, name):
    # from a file, so the page may load the hand font from acceptance/fonts
    raw = OUT / f"{name}.raw.html"
    raw.write_text(html, encoding="utf-8")
    page.goto(raw.resolve().as_uri())
    page.evaluate("document.fonts.load('40px Hand').then(() => document.fonts.ready)")
    raw.unlink()


def scan(browser, html, name, ids, seed):
    """Render a page, measure the boxes of `ids`, and age it like a flatbed scan."""
    page = browser.new_page(viewport=PAGE)
    load(page, html, name)
    boxes = {i: page.locator(f"#{i}").evaluate(BOX_JS) for i in ids}
    raw = OUT / f"{name}.raw.png"
    page.screenshot(path=raw)
    page.close()
    img, moved, angle = scan_realism(Image.open(raw), boxes, random.Random(seed))
    raw.unlink()
    w, h = img.size
    moved = {
        k: [max(0, x0), max(0, y0), min(w, x1), min(h, y1)]
        for k, (x0, y0, x1, y1) in moved.items()
    }
    img.save(OUT / f"{name}.jpg", quality=82)
    return {"size": [w, h], "rotation_deg_ccw": round(angle, 3), "boxes": moved}


def pdf(browser, html, name, facts):
    """Print a text-layer PDF and locate each fact in pypdf's page text, the reader
    the app runs on PDFs."""
    page = browser.new_page()
    load(page, html, name)
    path = OUT / f"{name}.pdf"
    page.pdf(
        path=path,
        format="A4",
        margin={"top": "2.2cm", "bottom": "2cm", "left": "2.4cm", "right": "2.4cm"},
    )
    page.close()
    text = PdfReader(path).pages[0].extract_text()
    out = []
    for key, value, after in facts:
        start = text.index(after) + len(after) if after else 0
        # pypdf writes a digit run inside Arabic in visual order ("999 الفرع"), and
        # may double the space between them.
        forms = [
            value,
            " ".join(reversed(value.split(" "))),
            "  ".join(reversed(value.split(" "))),
        ]
        found = min(
            ((text.find(f, start), f) for f in forms if text.find(f, start) >= 0),
            default=None,
        )
        if found is None:
            raise AssertionError(f"{name}: {value!r} not in the PDF text:\n{text}")
        at, form = found
        out.append(
            {
                "key": key,
                "value": value,
                "extracted_as": form,
                "span": {"start": at, "end": at + len(form)},
            }
        )
    return {
        "extractor": "pypdf PdfReader(...).pages[0].extract_text(), as sealbook.discovery.indexing.pdf_pages",
        "text_len": len(text),
        "facts": out,
    }


# ---- OCR, recorded once --------------------------------------------------------------


def ocr_request(image: bytes):
    """The request the worker's ocr tool sends, for the cloud route's model."""
    from sealbook.runs.worker import OCR_PROMPT

    url = f"data:image/jpeg;base64,{base64.b64encode(image).decode()}"
    return OCR_PROMPT, {
        "model": os.environ["MODEL_CLOUD"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ],
        "temperature": 0,
    }


def prompt_hash():
    from sealbook.ledger import chain
    from sealbook.runs.worker import OCR_PROMPT

    return chain.sha256(chain.canon([OCR_PROMPT]))


def record_ocr(name: str, image: bytes) -> dict:
    from sealbook.ledger.contracts import Route
    from sealbook.runs import routes

    _, body = ocr_request(image)
    served = asyncio.run(routes.chat(Route.CLOUD, body))
    return {
        "image": f"{name}.jpg",
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "route": str(Route.CLOUD),
        "endpoint": served.endpoint,
        "model_requested": body["model"],
        "model_served": served.model,
        "provider_served": served.provider,
        "prompt_hash": prompt_hash(),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "recorded_by": "acceptance/make_demo_docs.py --ocr",
        "text": routes.content(served),
        "raw": served.raw.decode(),
    }


def ocr_spans(text: str, values: dict, rows: list[list[str]]) -> dict:
    """Where OCR wrote each value exactly. A table value is searched only on its own
    row's line, a header value only on its own line (the stamp repeats the date and
    the branch); a value OCR did not write character for character gets no span and
    falls back to its box."""
    lines = text.split("\n")
    starts = [sum(len(x) + 1 for x in lines[:i]) for i in range(len(lines))]
    spans = {}
    used = set()
    for keys in rows:
        # the row's line: the first unused line holding the most of its values
        best = max(
            (i for i in range(len(lines)) if i not in used),
            key=lambda i: sum(values[k] in lines[i] for k in keys),
        )
        if not any(values[k] in lines[best] for k in keys):
            continue
        used.add(best)
        for k in keys:
            col = lines[best].find(values[k])
            if col >= 0:
                spans[k] = {
                    "start": starts[best] + col,
                    "end": starts[best] + col + len(values[k]),
                }
    # header values on their own line: the letterhead first, the date after its label
    for k, label in (("branch", ""), ("date", "التاريخ")):
        line = next((i for i, x in enumerate(lines) if label in x), None)
        if k in values and line is not None:
            col = lines[line].find(values[k], lines[line].index(label) + len(label))
            if col >= 0:
                spans[k] = {
                    "start": starts[line] + col,
                    "end": starts[line] + col + len(values[k]),
                }
    return spans


def main():
    OUT.mkdir(exist_ok=True)
    record = "--ocr" in sys.argv
    ocr_path = OUT / "ocr.json"
    recorded = json.loads(ocr_path.read_text()) if ocr_path.exists() else {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        expected = {
            "about": "Ground truth placed by acceptance/make_demo_docs.py. All content is synthetic.",
            "detainee_list.jpg": scan(
                browser, list_html(), "detainee_list", list(list_values()), SEED
            ),
            "dormitory_roster.jpg": scan(
                browser,
                roster_html(),
                "dormitory_roster",
                list(roster_values()),
                SEED + 1,
            ),
            "documentation_note.pdf": pdf(
                browser, NOTE, "documentation_note", NOTE_FACTS
            ),
            "released_detainee_statement.pdf": pdf(
                browser, RELEASE, "released_detainee_statement", RELEASE_FACTS
            ),
            "testimony_reem.pdf": pdf(
                browser, TESTIMONY, "testimony_reem", TESTIMONY_FACTS
            ),
        }
        browser.close()

    for name, values, rows in (
        (
            "detainee_list",
            list_values(),
            [
                [k for k in list_values() if k.startswith(f"{key}-")]
                for key, *_ in LIST_ROWS
            ],
        ),
        (
            "dormitory_roster",
            roster_values(),
            [[f"{key}-name"] for key, _ in ROSTER_NAMES],
        ),
    ):
        image = (OUT / f"{name}.jpg").read_bytes()
        sha = hashlib.sha256(image).hexdigest()
        rec = recorded.get(name)
        if (
            rec is None
            or rec["image_sha256"] != sha
            or rec["prompt_hash"] != prompt_hash()
        ):
            if not record:
                raise SystemExit(
                    f"{name}: OCR recording missing or stale; rerun with --ocr"
                )
            rec = recorded[name] = record_ocr(name, image)
        truth = expected[f"{name}.jpg"]
        spans = ocr_spans(rec["text"], values, rows)
        truth["facts"] = [
            {
                "key": k,
                "value": v,
                "page": 1,
                "bbox": truth["boxes"][k],
                "ocr": spans.get(k),
            }
            for k, v in values.items()
        ]
        del truth["boxes"]
        truth["ocr_missed"] = [k for k, v in values.items() if k not in spans]

    if record:
        ocr_path.write_text(json.dumps(recorded, ensure_ascii=False, indent=2) + "\n")
    (OUT / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
