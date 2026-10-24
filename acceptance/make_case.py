"""Generate the synthetic v0 case files and their ground truth into acceptance/data/.

Everything here is invented. Run: uv run python acceptance/make_case.py
"""

import base64
import json
import math
import os
import random
import subprocess
import tempfile
import wave
from pathlib import Path

import httpx
from PIL import Image, ImageChops, ImageFilter
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

ROOT = Path(__file__).parent
OUT = ROOT / "data"
PUBLIC = ROOT / "public"
PHOTO = "photo_damaged_building.jpg"
FONT = (ROOT / "fonts" / "ArefRuqaa-Regular.ttf").resolve().as_uri()
SEED = 999
BRANCH = "الفرع 999"

# ---- register page --------------------------------------------------------

REG_COLS = [
    "الرقم",
    "الاسم",
    "اسم الأب",
    "اسم الأم",
    "التولد",
    "مكان الولادة",
    "مكان التوقيف",
    "تاريخ التوقيف",
]
REG_ROWS = [
    (
        "P1",
        [
            "217",
            "يوسف خالد العلي",
            "خالد",
            "فاطمة",
            "1991",
            "دوما",
            "حرستا",
            "2013/5/2",
        ],
    ),
    (
        "P2",
        [
            "218",
            "يوسف خالد العلي",
            "خالد",
            "سعاد",
            "1985",
            "حمص",
            "القابون",
            "2013/5/2",
        ],
    ),
]
REG_FACTS = [  # (person, prop, row index or None for header, column index)
    ("P1", "name", 0, 1),
    ("P1", "father_name", 0, 2),
    ("P1", "mother_name", 0, 3),
    ("P1", "birth_date", 0, 4),
    ("P1", "arrested_at", 0, 6),
    ("P1", "detention_branch", None, None),
    ("P2", "name", 1, 1),
    ("P2", "mother_name", 1, 3),
    ("P2", "birth_date", 1, 4),
]


def register_html():
    rng = random.Random(SEED)

    def cell(r, c, text):
        # small per-cell jitter so the hand does not sit on a perfect baseline
        dx, dy, rot = rng.uniform(-6, 6), rng.uniform(-5, 3), rng.uniform(-2, 2)
        style = f"transform:translate({dx:.1f}px,{dy:.1f}px) rotate({rot:.1f}deg)"
        return f'<td><span id="c{r}_{c}" style="{style}">{text}</span></td>'

    rows = "".join(
        "<tr>" + "".join(cell(r, c, v) for c, v in enumerate(vals)) + "</tr>"
        for r, (_, vals) in enumerate(REG_ROWS)
    )
    blank = "<tr>" + "<td></td>" * len(REG_COLS) + "</tr>"
    return f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><style>
@font-face {{ font-family: Hand; src: url({FONT}); }}
body {{ margin:0; width:1600px; background:#efe6d2; font-family:Hand; color:#1d2a52; }}
.page {{ padding:60px 70px 80px; }}
header {{ display:flex; justify-content:space-between; font-size:44px; margin-bottom:30px; }}
table {{ width:100%; border-collapse:collapse; }}
th, td {{ border:2px solid #7d8fb0; height:92px; font-size:40px; text-align:center; }}
th {{ font-size:30px; height:64px; color:#3a3a3a; font-family:serif; }}
/* Ruqaa ink overhangs its advance box at the end of a word; pad so the DOM box holds the ink */
span {{ display:inline-block; padding:4px 0.2em 4px 0.9em; }}
</style><body><div class="page">
<header><span id="hdr">{BRANCH}</span><span>سجل الموقوفين</span><span>صفحة 14 &nbsp; 2013/5/3</span></header>
<table><tr>{"".join(f"<th>{c}</th>" for c in REG_COLS)}</tr>{rows}{blank * 6}</table>
</div></body></html>"""


def scan_realism(img, boxes, rng):
    """Paper grain, uneven ink and a slight skew; returns the image and boxes moved with it."""
    gray = img.convert("L")
    w, h = gray.size
    # uneven ink: a coarse random field upscaled so strokes fade in patches
    field = Image.new("L", (w // 80 + 1, h // 80 + 1))
    field.putdata([rng.randint(0, 70) for _ in range(field.width * field.height)])
    field = field.resize((w, h), Image.BICUBIC)
    gray = ImageChops.add(
        gray, ImageChops.multiply(ImageChops.invert(gray), field), scale=1
    )
    gray = gray.filter(ImageFilter.GaussianBlur(0.6))
    random.seed(SEED)  # effect_noise draws from the C rand state, pin what we can
    noise = Image.effect_noise((w, h), 14)
    gray = Image.blend(
        gray, ImageChops.multiply(gray, noise.point(lambda v: 160 + v // 3)), 0.35
    )

    angle = rng.uniform(0.8, 1.4)
    gray = gray.rotate(angle, resample=Image.BICUBIC, fillcolor=225)
    a = math.radians(angle)
    cx, cy = w / 2, h / 2

    def move(x, y):  # PIL rotates counter clockwise on screen
        dx, dy = x - cx, y - cy
        return cx + dx * math.cos(a) + dy * math.sin(a), cy - dx * math.sin(
            a
        ) + dy * math.cos(a)

    moved = {}
    for k, (x0, y0, x1, y1) in boxes.items():
        pts = [move(x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        moved[k] = [
            math.floor(min(xs)),
            math.floor(min(ys)),
            math.ceil(max(xs)),
            math.ceil(max(ys)),
        ]
    tint = Image.merge(
        "RGB",
        (
            gray.point(lambda v: v),
            gray.point(lambda v: v * 0.97),
            gray.point(lambda v: v * 0.9),
        ),
    )
    return tint, moved, angle


def make_register(browser):
    page = browser.new_page(viewport={"width": 1600, "height": 800})
    raw = OUT / "register_page.raw.html"
    raw.write_text(register_html(), encoding="utf-8")
    page.goto(raw.resolve().as_uri())
    page.evaluate(
        "document.fonts.load('40px Hand')"
    )  # measure only after the hand font is in
    raw.unlink()
    ids = ["hdr"] + [f"c{r}_{c}" for _, _, r, c in REG_FACTS if r is not None]
    js = "e => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; }"
    boxes = {i: page.locator(f"#{i}").evaluate(js) for i in ids}
    raw = OUT / "register_page.raw.png"
    page.screenshot(path=raw, full_page=True)
    page.close()
    img, moved, angle = scan_realism(Image.open(raw), boxes, random.Random(SEED))
    raw.unlink()
    img.save(OUT / "register_page.png", optimize=True)
    facts = []
    for person, prop, r, c in REG_FACTS:
        key = "hdr" if r is None else f"c{r}_{c}"
        value = BRANCH if r is None else REG_ROWS[r][1][c]
        facts.append(
            {
                "person": person,
                "prop": prop,
                "value": value,
                "page": 1,
                "bbox": moved[key],
            }
        )
    return {"rotation_deg_ccw": round(angle, 3), "size": list(img.size), "facts": facts}


# ---- transfer order -------------------------------------------------------

ORDER_LINES = [
    "أمر نقل رقم 4471/ن",
    "التاريخ: 2013-09-17",
    "الموقوف: يوسف خالد علي، تولد 1991-03-14، والدته فاطمة",
    "الجهة الموقوف لديها: " + BRANCH,
    "ينقل الموقوف المذكور أعلاه إلى سجن ك المركزي بتاريخ 2013-09-17",
    "يسلم مع أماناته وملفه إلى إدارة السجن أصولا.",
]
ORDER_FACTS = [  # (person, prop, value, line number, 0 means letterhead)
    ("P1", "name", "يوسف خالد علي", 3),
    ("P1", "birth_date", "1991-03-14", 3),
    ("P1", "transferred_to", "سجن ك المركزي", 5),
    ("P1", "detention_branch", BRANCH, 0),
]


def make_order(browser):
    lines = "".join(f'<p class="l">{t}</p>' for t in ORDER_LINES)
    html = f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><style>
body {{ font-family:"Geeza Pro","Arial",sans-serif; margin:0; }}
.head {{ border-bottom:3px double #000; padding-bottom:12px; text-align:center; }}
.head p {{ margin:4px; font-size:22px; font-weight:bold; }}
.l {{ font-size:18px; margin:18px 0; }}
.stamp {{ margin-top:60px; width:170px; height:170px; border:4px solid #a01d2a; border-radius:50%;
  color:#a01d2a; display:flex; flex-direction:column; justify-content:center; align-items:center;
  transform:rotate(-12deg); font-size:16px; }}
</style><body>
<div class="head"><p id="lh">{BRANCH}</p><p>قسم الديوان</p></div>
{lines}
<div class="stamp"><span>ديوان</span><span>وارد 2013/9/17</span></div>
</body></html>"""
    page = browser.new_page()
    page.set_content(html)
    page.pdf(
        path=OUT / "transfer_order.pdf",
        format="A4",
        margin={"top": "2cm", "bottom": "2cm", "left": "2cm", "right": "2cm"},
    )
    page.close()

    text = PdfReader(OUT / "transfer_order.pdf").pages[0].extract_text()
    text_lines = text.split("\n")
    starts = [sum(len(x) + 1 for x in text_lines[:i]) for i in range(len(text_lines))]
    facts = []
    for person, prop, value, n in ORDER_FACTS:
        i = (
            0 if n == 0 else n + 1
        )  # extracted lines: letterhead, department, then body lines
        # pypdf keeps Arabic words in logical order but emits each digit run of a
        # date or a mixed Arabic and digit phrase in visual order: 1991-03-14 comes
        # out as 14-03-1991 and the branch as "999 الفرع"
        forms = (value, *(sep.join(reversed(value.split(sep))) for sep in "- "))
        found = next(v for v in forms if v in text_lines[i])
        col = text_lines[i].index(found)
        facts.append(
            {
                "person": person,
                "prop": prop,
                "value": value,
                "extracted_as": found,
                "page": 1,
                "line": n or "letterhead",
                "char_start": starts[i] + col,
                "char_end": starts[i] + col + len(found),
            }
        )
    for f in facts:
        assert text[f["char_start"] : f["char_end"]] == f["extracted_as"], f
    return {
        "extractor": "pypdf PdfReader(...).pages[0].extract_text(), page 1 only, default mode",
        "facts": facts,
    }


# ---- witness audio --------------------------------------------------------

# Colloquial Damascene Arabic, as a witness would speak.
SAY = [
    "اسمي سامر، أنا من دوما.",
    "اعتقلوني بربيع الألفين وتلتعش، وضلّيت محجوز كم شهر.",
    "بشهر تموز من سنة الألفين وتلتعش، شفت يوسف خالد العلي بالفرع تسعمية وتسعة وتسعين.",
    "قلّي إنّو اعتقلوه على حاجز بحرستا، وإنّو أهلو ما بيعرفوا وينو.",
    "أنا مستعد إشهد باللي شفتو قدّام أي جهة.",
]
SAY_FACT = 2
GAP_MS = 600
RATE = 24000
# OpenAI gpt-audio (via OpenRouter) renders each sentence once; the cache keeps the
# case byte-stable. A native speaker chose the "ash" voice reading Shami text.
VOICE_DIR = Path(__file__).parent / "voice"
VOICE = "ash"
ACTOR = (
    "You are a voice actor from Damascus. Read the user's text aloud exactly as"
    " written, word for word. It is colloquial Syrian (Shami) Arabic, not Modern"
    " Standard Arabic: pronounce it the way a Damascene speaks naturally, calm tone."
    " Do not add, translate, or comment."
)


def speak(name, text, rate=RATE):
    """Natural Arabic speech for text, cached as voice/<name>.flac, as mono 16-bit PCM."""
    cached = VOICE_DIR / f"{name}.flac"
    if not cached.exists():
        pcm, said = bytearray(), []
        with httpx.stream(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
            json={
                "model": "openai/gpt-audio",
                "stream": True,
                "modalities": ["text", "audio"],
                "audio": {"voice": VOICE, "format": "pcm16"},
                "messages": [
                    {"role": "system", "content": ACTOR},
                    {"role": "user", "content": text},
                ],
            },
            timeout=300,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                for choice in json.loads(line[6:]).get("choices", []):
                    audio = choice.get("delta", {}).get("audio") or {}
                    pcm += base64.b64decode(audio.get("data", ""))
                    said.append(audio.get("transcript", ""))
        # The model must read the script, not paraphrase it.
        assert "".join(said).split() == text.split(), "".join(said)
        VOICE_DIR.mkdir(exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE)]
            + ["-ac", "1", "-i", "pipe:0", cached],
            input=bytes(pcm),
            check=True,
        )
    return subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", cached, "-f", "s16le"]
        + ["-ar", str(rate), "-ac", "1", "pipe:1"],
        capture_output=True,
        check=True,
    ).stdout


def make_witness():
    with tempfile.TemporaryDirectory() as tmp:
        frames, spans, t = [], [], 0
        for i, s in enumerate(SAY):
            data = speak(f"witness_{i}", s)
            ms = len(data) * 1000 // (2 * RATE)
            spans.append((t, t + ms))
            frames += [data, b"\0" * (2 * RATE * GAP_MS // 1000)]
            t += ms + GAP_MS
        joined = Path(tmp) / "all.wav"
        with wave.open(str(joined), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(frames))
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                joined,
                "-c:a",
                "libopus",
                "-b:a",
                "24k",
                "-fflags",
                "+bitexact",
                "-flags:a",
                "+bitexact",
                OUT / "witness_clip.ogg",
            ],
            check=True,
        )
    start, end = spans[SAY_FACT]
    return {
        "duration_ms": t - GAP_MS,
        "sentences": [
            {"text": s, "start_ms": a, "end_ms": b}
            for s, (a, b) in zip(SAY, spans, strict=True)
        ],
        "facts": [
            {
                "person": "P1",
                "prop": "seen_at",
                "value": BRANCH,
                "valid": "2013-07",
                "spoken_as": "بالفرع تسعمية وتسعة وتسعين",
                "start_ms": start,
                "end_ms": end,
            }
        ],
    }


# ---- C2PA photo -----------------------------------------------------------


def make_photo():
    """A real Commons photograph (public/homs_bab_dreeb_1.jpg, credits in
    public/ATTRIBUTION.md), downscaled and given a test C2PA manifest. The
    manifest is ours; the original carries none."""
    img = Image.open(PUBLIC / "homs_bab_dreeb_1.jpg").convert("RGB").resize((1024, 768))
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.jpg"
        img.save(src, quality=85)
        manifest = Path(tmp) / "m.json"
        manifest.write_text(
            json.dumps(
                {
                    "claim_generator": "sealbook-acceptance/0.1",
                    "title": PHOTO,
                    "assertions": [
                        {
                            "label": "c2pa.actions",
                            "data": {
                                "actions": [
                                    {
                                        "action": "c2pa.created",
                                        "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture",
                                    }
                                ]
                            },
                        }
                    ],
                }
            )
        )
        subprocess.run(
            [
                "c2patool",
                src,
                "-m",
                manifest,
                "-o",
                OUT / PHOTO,
                "-f",
            ],
            check=True,
            capture_output=True,
        )


# ---- missing person post --------------------------------------------------

POST_H1 = "مفقود: Yousef Khaled Al-Ali"
POST_P1 = "شوهد يوسف آخر مرة في دوما يوم 2013-05-02 عند خروجه من البيت صباحا."
POST_P2 = [
    "عمره 22 عاما، طويل القامة، يرتدي نظارة طبية.",
    "عمره 22 عاما، طويل القامة، وكان يرتدي سترة زرقاء.",
]
POST_P3 = "من لديه أي معلومة يرجى التواصل مع العائلة عبر هذه الصفحة."


def post_html(v):
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>{POST_H1}</title>
<style>body{{font-family:sans-serif;max-width:640px;margin:2em auto;line-height:1.6}}</style></head>
<body><article><h1>{POST_H1}</h1><p>{POST_P1}</p><p>{POST_P2[v]}</p><p>{POST_P3}</p></article></body></html>
"""


def make_post(browser):
    out = {
        "extractor": "Playwright document.body.innerText of the file loaded in Chromium",
        "files": {},
    }
    page = browser.new_page()
    for v, name in enumerate(("missing_post.html", "missing_post_v2.html")):
        (OUT / name).write_text(post_html(v), encoding="utf-8")
        page.goto((OUT / name).resolve().as_uri())
        text = page.evaluate("document.body.innerText")
        facts = []
        for person, prop, value, anchor, valid in (
            ("P1", "name", "Yousef Khaled Al-Ali", POST_H1, None),
            ("P1", "last_seen", "دوما", POST_P1, "2013-05-02"),
        ):
            s = text.index(anchor) + anchor.index(value)
            facts.append(
                {
                    "person": person,
                    "prop": prop,
                    "value": value,
                    "valid": valid,
                    "char_start": s,
                    "char_end": s + len(value),
                }
            )
        out["files"][name] = {"visible_text_len": len(text), "facts": facts}
    page.close()
    out["changed_sentence"] = {"v1": POST_P2[0], "v2": POST_P2[1]}
    return out


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        expected = {
            "about": "Ground truth placed by acceptance/make_case.py. All content is synthetic.",
            "register_page.png": make_register(browser),
            "transfer_order.pdf": make_order(browser),
            "missing_post": make_post(browser),
        }
        browser.close()
    expected["witness_clip.ogg"] = make_witness()
    make_photo()
    expected[PHOTO] = {
        "facts": [
            {
                "subject": "blob",
                "prop": "c2pa_verify",
                "value": "valid",
                "check": "c2patool " + PHOTO + "",
            }
        ]
    }
    (OUT / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
