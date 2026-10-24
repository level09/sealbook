"""Generate the synthetic Arabic/English retrieval gold set into acceptance/retrieval/.

Everything here is invented, except the two photos from acceptance/public/ that the image queries use. Run: uv run python acceptance/make_retrieval.py
"""

import json
import random
import wave
from pathlib import Path

import numpy as np
from make_case import scan_realism, speak
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
OUT = ROOT / "retrieval"
FONT = (ROOT / "fonts" / "ArefRuqaa-Regular.ttf").resolve().as_uri()
SEED = 999
RATE = 16000

# One sentence per line, so a line is the unit a text query points at.
TEXTS = {
    "ar_market": [
        "افتتح السوق الشعبي في مدينة الزهراء أبوابه صباح الثلاثاء.",
        "بلغ سعر كيلو الطماطم ٣٥٠ ليرة.",
        "ازدحم الباعة قرب الجسر القديم.",
    ],
    "ar_water": [
        "انقطعت المياه عن حي النهضة لمدة ثلاثة أيام.",
        "أرسلت البلدية صهريجين فقط.",
        "طالب السكان بإصلاح الأنبوب الرئيسي.",
    ],
    "ar_school": [
        "يَبْدَأُ العَامُ الدِّرَاسِيُّ فِي الأُسْبُوعِ الأَوَّلِ مِنْ أَيْلُولَ.",
        "تَسَلَّمَ الطُّلَّابُ كُتُبَهُمْ مِنَ المَدْرَسَةِ.",
    ],
    "ar_clinic": [
        "افتتحت عيادة متنقلة في قرية الوادي الأخضر.",
        "تعالج العيادة ٤٠ مريضا يوميا.",
        "يعمل فيها طبيبان وممرضة.",
    ],
    "ar_snow": [
        "أغلق الطريق السريع بين المدينتين بسبب الثلوج.",
        "وصل ارتفاع الثلج إلى نصف متر.",
        "استؤنفت الحركة مساء الخميس.",
    ],
    "ar_post": [
        "وصل البريد إلى المركز الثقافي متأخرا ٢٠ دقيقة.",
        "وزع الموظف الرسائل على ١٢ صندوقا.",
    ],
    "sy_bakery": [
        "شو في بالفرن اليوم؟",
        "الخبز مو متوفر والطابور طويل كتير.",
        "قالولنا ارجعوا بكرا الصبح.",
    ],
    "sy_bus": [
        "الباص تأخر ساعة ونص وما حدا عرف ليش.",
        "السواق قال إن المازوت خلص.",
        "مشينا لحد الدوار ع إجرينا.",
    ],
    "sy_kibbeh": [
        "ستي عم تحضّر كبة بالصينية.",
        "لازم نجيب لحمة وبرغل من الدكان.",
        "بدها ياها جاهزة قبل العصر.",
    ],
    "sy_football": [
        "مبارح لعبوا ع الملعب الترابي وغلب فريق حارتنا ٣ - ١.",
        "كل الولاد فرحانين كتير.",
        "بكرا في إعادة.",
    ],
    "sy_power": [
        "الكهربا بتروح ست ساعات بالنهار.",
        "هلق عم نشحن التلفون عند الجيران.",
        "شو رأيك نشتري بطارية؟",
    ],
    "en_library": [
        "The city library reopened on Monday after a roof repair.",
        "It now lends 3,000 books and offers a quiet reading room.",
    ],
    "en_ferry": [
        "The harbor ferry leaves at 6:40 and returns before noon.",
        "Passengers should buy tickets at the small kiosk by the pier.",
    ],
    "en_weather": [
        "A weather station on the hill recorded 12 mm of rain overnight.",
        "Wind speeds stayed low and the fog lifted by nine.",
    ],
}

SCAN_LINES = [
    "جرى توزيع الخبز على سكان الحي يوم الأحد",
    "عدد الأسر المسجلة مئتان واثنتا عشرة أسرة",
    "تم تسليم مفاتيح المخزن إلى أبي سليم",
]

SPEECH = [  # colloquial Damascene Arabic, like a real voice note
    "صباح الخير، هي رسالة صوتية قصيرة عن رحلة الأسبوع الجاي عالساحل.",
    "رح ننطلق من كراج الباصات الساعة سبعة الصبح.",
    "جيبوا معكن جاكيت خفيف لأنو الجو برد جنب البحر.",
    "رح نوقف بضيعة صغيرة لنشتري زيتون وجبنة بلدية.",
    "بعد الغدا رح نزور القلعة القديمة وبعدين نتمشى عالبحر.",
    "الكل رح يرجع عالمدينة قبل نص الليل.",
]
GAP_MS = 900
EXCERPT_FROM, EXCERPT_TO = 2, 4  # sentence indexes, inclusive
EXCERPT_PAD_MS = 1500

# ---- queries ----------------------------------------------------------------
# (type, text, kind, key, line)  kind: text -> key is a TEXTS name, line a line index;
# image/scan/speech/tone -> described in build_queries
TEXT_QUERIES = [
    ("msa", "ما سعر الطماطم في السوق؟", "ar_market", 1),
    ("msa", "انقطاع المياه عن الحي", "ar_water", 0),
    ("msa", "عيادة متنقلة في القرية", "ar_clinic", 0),
    ("msa", "إغلاق الطريق السريع بسبب الثلج", "ar_snow", 0),
    ("msa", "بداية العام الدراسي في أيلول", "ar_school", 0),
    ("msa", "كم مريضا تعالج العيادة كل يوم", "ar_clinic", 1),
    ("msa", "تأخر وصول البريد إلى المركز الثقافي", "ar_post", 0),
    ("dialect", "ليش تأخر الباص", "sy_bus", 0),
    ("dialect", "ما في خبز بالفرن والطابور طويل", "sy_bakery", 1),
    ("dialect", "ستي عم تعمل كبة", "sy_kibbeh", 0),
    ("dialect", "نتيجة مباراة حارتنا بالملعب", "sy_football", 0),
    ("dialect", "الكهربا مقطوعة وعم نشحن الموبايل", "sy_power", 1),
    ("dialect", "السواق قال المازوت خلص", "sy_bus", 1),
    ("cross", "bread shortage and a long queue at the bakery", "sy_bakery", 1),
    ("cross", "water outage in the neighbourhood for three days", "ar_water", 0),
    ("cross", "mobile clinic in a village", "ar_clinic", 0),
    ("cross", "grandmother making kibbeh", "sy_kibbeh", 0),
    ("cross", "shu fi bil forn el yom", "sy_bakery", 0),
    ("cross", "مكتبة المدينة تفتح أبوابها بعد إصلاح السقف", "en_library", 0),
    ("cross", "ferry schedule at the harbor", "en_ferry", 0),
    ("cross", "كمية المطر التي سجلتها محطة الطقس", "en_weather", 0),
]
NEGATIVES = [
    "جدول مباريات كأس العالم للأندية",
    "recipe for a chocolate cake with hazelnuts",
    "التعرفة الجمركية على استيراد السيارات",
    "deadline for quarterly tax filing",
]


def write_texts():
    spans = {}
    for name, lines in TEXTS.items():
        text = "\n".join(lines) + "\n"
        (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
        spans[name] = text
    return spans


def line_span(texts, name, line):
    text = texts[name]
    s = text.index(TEXTS[name][line])
    e = s + len(TEXTS[name][line])
    assert text[s:e] == TEXTS[name][line]
    return {
        "file": f"{name}.txt",
        "unit": "chars",
        "start": s,
        "end": e,
        "expected": text[s:e],
    }


# ---- scanned page -------------------------------------------------------------


def make_scan(browser):
    rows = "".join(
        f'<p class="l"><span id="l{i}">{t}</span></p>' for i, t in enumerate(SCAN_LINES)
    )
    html = f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><style>
@font-face {{ font-family: Hand; src: url({FONT}); }}
body {{ margin:0; width:1200px; background:#efe6d2; font-family:Hand; color:#1d2a52; }}
.page {{ padding:70px 80px 90px; }}
.l {{ font-size:46px; margin:0 0 60px; text-align:right; }}
span {{ display:inline-block; padding:4px 0.2em 4px 0.9em; }}
</style><body><div class="page">{rows}</div></body></html>"""
    page = browser.new_page(viewport={"width": 1200, "height": 600})
    raw = OUT / "scan.raw.html"
    raw.write_text(html, encoding="utf-8")
    page.goto(raw.resolve().as_uri())
    page.evaluate("document.fonts.load('46px Hand')")
    raw.unlink()
    js = "e => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; }"
    boxes = {i: page.locator(f"#l{i}").evaluate(js) for i in range(len(SCAN_LINES))}
    png = OUT / "scan.raw.png"
    page.screenshot(path=png, full_page=True)
    page.close()
    img, moved, angle = scan_realism(Image.open(png), boxes, random.Random(SEED))
    png.unlink()
    img.save(OUT / "scan_page.png", optimize=True)
    return moved, img.size


# ---- audio --------------------------------------------------------------------


def write_wav(path, data):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data)


def make_speech():
    frames, spans, t = [], [], 0
    gap = b"\0" * (2 * RATE * GAP_MS // 1000)
    for i, s in enumerate(SPEECH):
        data = speak(f"retrieval_{i}", s, RATE)
        ms = len(data) * 1000 // (2 * RATE)
        spans.append((t, t + ms))
        frames += [data, gap]
        t += ms + GAP_MS
    pcm = b"".join(frames)
    write_wav(OUT / "speech.wav", pcm)
    a = max(0, spans[EXCERPT_FROM][0] - EXCERPT_PAD_MS)
    b = min(t, spans[EXCERPT_TO][1] + EXCERPT_PAD_MS)
    write_wav(OUT / "speech_excerpt.wav", pcm[a * 32 : b * 32])
    return spans, a, b, t - GAP_MS


def make_tone():
    n = RATE * 5
    rng = np.random.default_rng(SEED)
    x = np.sin(np.arange(n) * (2 * np.pi * 660 / RATE)) * 9000 + rng.normal(0, 400, n)
    write_wav(OUT / "tone.wav", x.astype("<i2").tobytes())
    return n * 1000 // RATE


# ---- queries ------------------------------------------------------------------


def main():
    OUT.mkdir(exist_ok=True)
    texts = write_texts()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        boxes, scan_size = make_scan(browser)
        browser.close()
    spans, ex_a, ex_b, speech_ms = make_speech()
    tone_ms = make_tone()

    queries = []

    def add(kind, text, relevant):
        queries.append(
            {
                "id": f"q{len(queries) + 1:02d}",
                "type": kind,
                "text": text,
                "relevant": relevant,
            }
        )

    for kind, q, name, line in TEXT_QUERIES:
        add(kind, q, [line_span(texts, name, line)])

    def whole(f, expected):
        return {"file": f, "unit": "whole", "expected": expected}

    # The two photos live in acceptance/public/ with their Commons credits.
    add(
        "text_to_image",
        "حاجز على الطريق وكتل إسمنتية بألوان العلم وصورة ممزقة",
        [whole("aleppo_checkpoint.png", "abandoned checkpoint, concrete barriers")],
    )
    add(
        "text_to_image",
        "a roadside checkpoint with concrete barriers painted in flag colours and a torn portrait",
        [whole("aleppo_checkpoint.png", "abandoned checkpoint, concrete barriers")],
    )
    add(
        "text_to_image",
        "سوق مسقوف مدمر وأنقاض في الممر وأقواس منهارة",
        [whole("aleppo_souk.jpg", "ruined covered market lane")],
    )
    add(
        "text_to_image",
        "a ruined covered market lane with rubble and collapsed arches",
        [whole("aleppo_souk.jpg", "ruined covered market lane")],
    )
    for i in (1, 2):
        add(
            "text_to_scan",
            SCAN_LINES[i],
            [
                {
                    "file": "scan_page.png",
                    "unit": "bbox",
                    "bbox": boxes[i],
                    "expected": SCAN_LINES[i],
                }
            ],
        )

    def audio_rel(i):
        s, e = spans[i]
        rel = [
            {
                "file": "speech.wav",
                "unit": "ms",
                "start": s,
                "end": e,
                "expected": SPEECH[i],
            }
        ]
        if ex_a <= s and e <= ex_b:
            rel.append(
                {
                    "file": "speech_excerpt.wav",
                    "unit": "ms",
                    "start": s - ex_a,
                    "end": e - ex_a,
                    "expected": SPEECH[i],
                    "excerpt_offset_ms": ex_a,
                }
            )
        return rel

    add("text_to_audio", "موعد انطلاق الحافلة من المحطة صباحا", audio_rel(1))
    add("text_to_audio", "ماذا نحضر معنا لأن الجو بارد قرب البحر", audio_rel(2))
    add("text_to_audio", "شراء الزيتون والجبنة من قرية صغيرة", audio_rel(3))
    add(
        "text_to_audio",
        "visiting the old castle after lunch and walking on the beach",
        audio_rel(4),
    )
    add(
        "text_to_audio",
        "a steady electronic beep tone",
        [
            {
                "file": "tone.wav",
                "unit": "ms",
                "start": 0,
                "end": tone_ms,
                "expected": "non-speech tone",
            }
        ],
    )
    for q in NEGATIVES:
        add("negative", q, [])

    (OUT / "queries.json").write_text(
        json.dumps(
            {
                "corpus": {
                    "text": [f"{n}.txt" for n in TEXTS],
                    "image": [
                        "aleppo_checkpoint.png",
                        "aleppo_souk.jpg",
                        "scan_page.png",
                    ],
                    "audio": ["speech.wav", "speech_excerpt.wav", "tone.wav"],
                },
                "speech": {
                    "duration_ms": speech_ms,
                    "sentences": [
                        {"text": s, "start_ms": a, "end_ms": b}
                        for s, (a, b) in zip(SPEECH, spans, strict=True)
                    ],
                    "excerpt": {
                        "source": "speech.wav",
                        "start_ms": ex_a,
                        "end_ms": ex_b,
                    },
                },
                "scan": {"size": list(scan_size), "line_bboxes": boxes},
                "queries": queries,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    check()


def check():
    """Re-read the written files and prove every stated location holds."""
    data = json.loads((OUT / "queries.json").read_text(encoding="utf-8"))
    for q in data["queries"]:
        for r in q["relevant"]:
            if r["unit"] == "chars":
                t = (OUT / r["file"]).read_text(encoding="utf-8")
                assert t[r["start"] : r["end"]] == r["expected"], q["id"]
            elif r["unit"] == "ms":
                with wave.open(str(OUT / r["file"])) as w:
                    assert (w.getnchannels(), w.getframerate()) == (1, RATE)
                    assert (
                        0 <= r["start"] < r["end"] <= w.getnframes() * 1000 // RATE + 1
                    )
            elif r["unit"] == "bbox":
                w, h = data["scan"]["size"]
                x0, y0, x1, y1 = r["bbox"]
                assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, q["id"]
    ex = data["speech"]["excerpt"]
    for q in data["queries"]:
        for r in q["relevant"]:
            if "excerpt_offset_ms" in r:
                assert r["excerpt_offset_ms"] == ex["start_ms"]
    # longer than one 30 s eval window, so the audio windowing is exercised
    assert 31000 <= data["speech"]["duration_ms"] <= 45000, data["speech"][
        "duration_ms"
    ]
    total = sum(f.stat().st_size for f in OUT.iterdir())
    assert total < 5_000_000, total


if __name__ == "__main__":
    main()
