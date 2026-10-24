"""Regenerate site/data/case.json and site/media/ from the synthetic case files.

Every value is read from acceptance/, stk/monitor/seed.py, docs/ or the recorded
EmbeddingGemma 2 run, or measured here with the app's own discovery code.
Run: uv run python site/build_data.py
"""

import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pypdfium2
from PIL import Image, ImageDraw, ImageOps
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import stk.cli  # noqa: E402, F401  (seed.py imports stk.cli, which imports seed)
from stk.discovery import audio, compute, embeddings  # noqa: E402
from stk.discovery import media as frames  # noqa: E402
from stk.discovery.compute import _image as pdq  # noqa: E402
from stk.discovery.models import DIMENSIONS  # noqa: E402
from stk.monitor import seed  # noqa: E402
from stk.settings import Config  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import build_live  # noqa: E402

DATA, RET = ROOT / "acceptance/data", ROOT / "acceptance/retrieval"
PUBLIC = ROOT / "acceptance/public"
SITE = ROOT / "site"
MEDIA = SITE / "media"
RUN = Path.home() / "retrieval-ollama-embeddinggemma2.json"
OLLAMA, MODEL = "http://127.0.0.1:11434", "embeddinggemma-2"
MAX_BYTES = 400_000

expected = json.loads((DATA / "expected.json").read_text())
case = json.loads((ROOT / "acceptance/case.json").read_text())
docs = {d["file"]: d for d in case["documents"]}


def rel(p: Path) -> str:
    return str(p.relative_to(SITE))


def save_image(im: Image.Image, name: str, fmt="PNG", width=None) -> str:
    """Write under the size budget, shrinking width until it fits."""
    width = width or im.width
    while True:
        out = (
            im
            if im.width <= width
            else im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        )
        buf = io.BytesIO()
        if fmt == "PNG":
            out.save(buf, "PNG", optimize=True)
        else:
            out.convert("RGB").save(buf, "JPEG", quality=82, optimize=True)
        if buf.tell() <= MAX_BYTES:
            (MEDIA / name).write_bytes(buf.getvalue())
            return f"media/{name}"
        width = int(width * 0.85)


def decode(path: Path, rate=audio.SAMPLE_RATE) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "s16le",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768


def waveform(path: Path, name: str, width=1200, height=160) -> str:
    samples = decode(path)
    cols = np.array_split(np.abs(samples), width)
    peaks = np.array([c.max() if len(c) else 0 for c in cols])
    peaks = peaks / (peaks.max() or 1)
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    mid = height / 2
    for x, p in enumerate(peaks):
        h = max(1, p * (mid - 2))
        draw.line([(x, mid - h), (x, mid + h)], fill=(110, 110, 110, 255))
    return save_image(im, name)


def digests(raw: bytes) -> dict:
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "md5": hashlib.md5(raw).hexdigest(),  # noqa: S324
    }


def c2pa(path: Path) -> dict:
    r = subprocess.run(["c2patool", str(path)], capture_output=True, text=True)
    if r.returncode:
        err = (r.stderr or r.stdout).strip().removeprefix("Error: ")
        if err == "Unsupported file type":
            return {
                "state": "not_applicable",
                "note": "c2patool does not read this file type.",
            }
        if err == "No claim found":
            return {"state": "absent", "note": "The file carries no C2PA manifest."}
        raise RuntimeError(f"c2patool {path.name}: {err}")
    report = json.loads(r.stdout)
    signer = report["manifests"][report["active_manifest"]]["signature_info"]
    codes = {s["code"] for s in report.get("validation_status", [])}
    if codes - {"signingCredential.untrusted"}:
        raise RuntimeError(f"c2patool {path.name}: unexpected failures {codes}")
    if codes:
        return {
            "state": "untrusted",
            "note": f"The manifest signature is valid, but the signer "
            f"({signer['issuer']}) is not on a trust list.",
        }
    return {
        "state": "valid",
        "note": f"The manifest validates, signed by {signer['issuer']}.",
    }


def acquired(file: str) -> dict:
    key = next(k for k, v in seed.DOCS.items() if v[0] == file)
    origin = seed.DOCS[key][2]
    # seed.py: channel imports are collected by the importer principal, the rest by analyst-1.
    return {
        "by": "importer" if origin.startswith("channel:") else "analyst-1",
        "origin": origin,
    }


def claim(f: dict, where: dict, file: str, **extra) -> dict:
    out = {
        "prop": f["prop"],
        "person": f["person"],
        "value": f["value"],
        "where": where,
    }
    # expected.json omits some valid dates that acceptance/case.json carries.
    spec = [
        e
        for e in docs[file]["expect"]
        if (e.get("person"), e["prop"], e["value"])
        == (f["person"], f["prop"], f["value"])
    ]
    valid = f.get("valid") or (spec[0].get("valid") if spec else None)
    if valid:
        out["valid"] = valid
    return out | extra | {"tier": "unconfirmed"}


def base(fid: str, name: str, kind: str) -> dict:
    path = DATA / name
    return {
        "id": fid,
        "name": name,
        "kind": kind,
        **digests(path.read_bytes()),
        "acquired": acquired(name),
        "c2pa": c2pa(path),
        "doc_type": docs[name]["doc_type"],
    }


def build_files() -> list[dict]:
    files = []

    reg = expected["register_page.png"]
    with Image.open(DATA / "register_page.png") as im:
        media = save_image(im, "register_page.png")
    f = base("register", "register_page.png", "image")
    f |= {
        "media": media,
        "size": reg["size"],
        "text": {
            "lang": "ar",
            "dir": "rtl",
            "source": "transcript of the register supplied with the case",
            "content": seed.REGISTER,
        },
        "claims": [
            claim(x, {"page": x["page"], "bbox": x["bbox"]}, "register_page.png")
            for x in reg["facts"]
        ],
    }
    files.append(f)

    pdf = DATA / "transfer_order.pdf"
    text = PdfReader(pdf).pages[0].extract_text()
    doc = pypdfium2.PdfDocument(pdf)
    page = doc[0].render(scale=1.5).to_pil()
    doc.close()
    tr = expected["transfer_order.pdf"]
    f = base("transfer", "transfer_order.pdf", "pdf")
    f |= {
        "media": save_image(page, "transfer_order.png"),
        "text": {
            "lang": "ar",
            "dir": "rtl",
            "source": tr["extractor"],
            "content": text,
        },
        "claims": [],
    }
    for x in tr["facts"]:
        a, b = x["char_start"], x["char_end"]
        assert text[a:b] == x["extracted_as"], (text[a:b], x)
        f["claims"].append(
            claim(
                x,
                {"page": 1, "chars": [a, b]},
                "transfer_order.pdf",
                extracted_as=x["extracted_as"],
            )
        )
    # seed.py's case-assistant proposal; its date falls after the transfer.
    f["claims"].append(
        {
            "prop": "detained_at",
            "person": "P1",
            "value": "الفرع 999",
            "valid": "2013-10-01",
            "where": {"page": 1},
            "proposed_by": "case-assistant (on behalf of analyst-1)",
            "tier": "unconfirmed",
        }
    )
    files.append(f)

    w = expected["witness_clip.ogg"]
    (MEDIA / "witness_clip.ogg").write_bytes((DATA / "witness_clip.ogg").read_bytes())
    f = base("witness", "witness_clip.ogg", "audio")
    f |= {
        "media": waveform(DATA / "witness_clip.ogg", "witness_clip_wave.png"),
        "audio": "media/witness_clip.ogg",
        "duration_ms": w["duration_ms"],
        "text": {
            "lang": "ar",
            "dir": "rtl",
            "source": "transcript supplied with the file",
            "segments": [
                {"text": s["text"], "ms": [s["start_ms"], s["end_ms"]]}
                for s in w["sentences"]
            ],
            "content": " ".join(s["text"] for s in w["sentences"]),
        },
        "transcript_note": "the script the synthetic clip was spoken from; the speech chapter shows what local speech to text heard",
        "claims": [
            claim(
                x,
                {"ms": [x["start_ms"], x["end_ms"]]},
                "witness_clip.ogg",
                spoken_as=x["spoken_as"],
            )
            for x in w["facts"]
        ],
    }
    files.append(f)

    with Image.open(DATA / "photo_damaged_building.jpg") as im:
        media = save_image(im, "photo_damaged_building.jpg", "JPEG")
    f = base("photo", "photo_damaged_building.jpg", "image")
    f |= {
        "media": media,
        "claims": [],
        "credit": credit("File:Destruction in Homs (8).jpg"),
    }
    files.append(f)

    post = expected["missing_post"]["files"]["missing_post.html"]
    content = inner_text(DATA / "missing_post.html")
    assert len(content) == post["visible_text_len"], len(content)
    (MEDIA / "missing_post.html").write_bytes((DATA / "missing_post.html").read_bytes())
    f = base("post", "missing_post.html", "html")
    f |= {
        "media": "media/missing_post.html",
        "text": {
            "lang": "ar",
            "dir": "rtl",
            "source": expected["missing_post"]["extractor"],
            "content": content,
        },
        "changed_on_recheck": expected["missing_post"]["changed_sentence"],
        "claims": [],
    }
    for x in post["facts"]:
        a, b = x["char_start"], x["char_end"]
        assert content[a:b] == x["value"], (content[a:b], x)
        f["claims"].append(claim(x, {"chars": [a, b]}, "missing_post.html"))
    files.append(f)
    return files


def inner_text(path: Path) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(path.as_uri())
        text = page.evaluate("document.body.innerText")
        browser.close()
    return text


def build_people() -> list[dict]:
    out = []
    for pid, twin in (("P1", "P2"), ("P2", "P1")):
        p = case["persons"][pid]
        out.append(
            {
                "id": pid,
                "label": p["spellings"][0],
                "spellings": p["spellings"],
                "twin_of": twin,
                "father_name": p["father_name"],
                "mother_name": p["mother_name"],
                "birth_date": p["birth_date"],
                "place_of_birth": p["place_of_birth"],
                "note": p["role"],
            }
        )
    return out


def build_duplicates() -> tuple[list[dict], list[dict]]:
    src = PUBLIC / PHOTOS["street"][0]
    with Image.open(src) as im:
        original = ImageOps.exif_transpose(im).convert("RGB")
    # Derived copies start from the 1024 px web master so they stay under the size
    # budget; the reference fingerprint is of the unaltered download.
    master = original.resize(
        (1024, round(original.height * 1024 / original.width)), Image.LANCZOS
    )
    w, h = master.size
    variants = {
        "street_crop": (
            "cropped 8% per side and re-encoded at JPEG quality 70",
            lambda: master.crop(
                (w * 8 // 100, h * 8 // 100, w * 92 // 100, h * 92 // 100)
            ),
            70,
        ),
        "street_crop3": (
            "cropped 3% per side and re-encoded at JPEG quality 70",
            lambda: master.crop(
                (w * 3 // 100, h * 3 // 100, w * 97 // 100, h * 97 // 100)
            ),
            70,
        ),
        "street_q90": ("re-encoded at JPEG quality 90", lambda: master, 90),
        "street_q50": ("re-encoded at JPEG quality 50", lambda: master, 50),
        "street_q25": ("re-encoded at JPEG quality 25", lambda: master, 25),
        "street_half": (
            "resized to half width, JPEG quality 75",
            lambda: master.resize((w // 2, h // 2), Image.LANCZOS),
            75,
        ),
    }
    ref = pdq(src)
    derived, dups = [], []
    for vid, (how, make, q) in variants.items():
        out = MEDIA / f"{vid}.jpg"
        make().save(out, "JPEG", quality=q)
        fp = pdq(out)
        dist = (int(ref["hash"], 16) ^ int(fp["hash"], 16)).bit_count()
        derived.append(
            {
                "id": vid,
                "from": "street",
                "how": how,
                "media": rel(out),
                "derived": True,
                **digests(out.read_bytes()),
            }
        )
        dups.append(
            {
                "a": "street",
                "b": vid,
                "method": "pdq",
                "distance": dist,
                "of": 256,
                "offset_ms": None,
                "quality": [ref["quality"], fp["quality"]],
                "verdict": "proposed",
                "derived": True,
            }
        )

    clip = DATA / "witness_clip.ogg"
    # The sentence that names P1 and the branch.
    s = expected["witness_clip.ogg"]["sentences"][2]
    start, end = s["start_ms"], s["end_ms"]
    out = MEDIA / "witness_excerpt.ogg"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start / 1000}",
            "-to",
            f"{end / 1000}",
            "-i",
            str(clip),
            "-c:a",
            "libopus",
            "-b:a",
            "24k",
            "-bitexact",
            "-map_metadata",
            "-1",
            str(out),
        ],
        check=True,
    )
    match = audio.align(audio.landmarks(decode(out)), audio.landmarks(decode(clip)))
    derived.append(
        {
            "id": "witness_excerpt",
            "from": "witness",
            "how": f"cut {start}-{end} ms and re-encoded as Opus at 24 kbit/s",
            "media": rel(out),
            "derived": True,
            "wave": waveform(out, "witness_excerpt_wave.png"),
            **digests(out.read_bytes()),
        }
    )
    dups.append(
        {
            "a": "witness",
            "b": "witness_excerpt",
            "method": "audio",
            "distance": None,
            "of": None,
            "offset_ms": match["offset_ms"],
            "cut_at_ms": start,
            "aligned_landmarks": match["aligned_landmarks"],
            "shared_landmarks": match["shared_landmarks"],
            "verdict": "proposed",
            "derived": True,
        }
    )
    return derived, dups


def build_search(photos: dict) -> dict:
    run = json.loads(RUN.read_text())
    queries = {
        q["id"]: q for q in json.loads((RET / "queries.json").read_text())["queries"]
    }
    media = {}
    for name in ("scan_page.png", "aleppo_checkpoint.png", "aleppo_souk.jpg"):
        src = next(d / name for d in (RET, PUBLIC) if (d / name).exists())
        with Image.open(src) as im:
            if src.parent == PUBLIC:
                media[name] = save_image(im, f"ret_{src.stem}.jpg", "JPEG", 1024)
            else:
                media[name] = save_image(im, f"ret_{name}")
    media["speech.wav"] = waveform(RET / "speech.wav", "ret_speech_wave.png")
    media["speech_excerpt.wav"] = waveform(
        RET / "speech_excerpt.wav", "ret_speech_excerpt_wave.png"
    )
    media["tone.wav"] = waveform(RET / "tone.wav", "ret_tone_wave.png")

    out = []
    for row in run["queries"]:
        q = queries[row["id"]]
        relevant = {r["file"]: r for r in q["relevant"]}
        top = {
            "file": row["top_file"],
            "score": row["top_score"],
            "rank": 1,
            "hit": row["top_file"] in relevant,
        }
        if top["hit"] and row.get("location_hit"):
            r = relevant[row["top_file"]]
            # The run records only that the top location overlapped this gold span.
            top["location"] = (
                {"chars": [r["start"], r["end"]]}
                if r["unit"] == "chars"
                else {"ms": [r["start"], r["end"]]}
                if r["unit"] == "ms"
                else {"bbox": r["bbox"]}
                if r["unit"] == "bbox"
                else {"whole": True}
            )
        if row["top_file"] in media:
            top["media"] = media[row["top_file"]]
        item = {
            "id": row["id"],
            "type": row["type"],
            "text": q["text"],
            "results": [top],
        }
        if "rank" in row:
            item["relevant_rank"] = row["rank"]
            item["location_hit"] = row["location_hit"]
            item["expected"] = [
                {
                    "file": r["file"],
                    "media": media.get(r["file"]),
                    **({"text": r["expected"]} if "expected" in r else {}),
                }
                for r in q["relevant"]
            ]
        out.append(item)
    models = (ROOT / "docs/models.md").read_text()
    runtime = re.search(r"Ollama \d+\.\d+\.\d+", models)
    return {
        "model": run["model"],
        **({"runtime": runtime.group(0)} if runtime else {}),
        "dimensions": run["dimensions"],
        "items": run["items"],
        "note": run["note"],
        "credits": [
            photos["checkpoint"]["credit"] | {"file": "aleppo_checkpoint.png"},
            photos["souk"]["credit"] | {"file": "aleppo_souk.jpg"},
        ],
        "summary": {
            **run["positive"],
            "by_type": run["by_type"],
            "negative": run["negative"],
            "query_latency": run["query_latency"],
        },
        "queries": out,
    }


CLIPS = {
    # id: (file in acceptance/public, Commons title, crop that removes letterboxing)
    "jdeideh_2015": (
        "jdeideh_2015.webm",
        "File:Jdeideh Jdayde Aleppo Avril 2015.webm",
        None,
    ),
    "jdeideh_2017": (
        "jdeideh_2017.webm",
        "File:Jdeideh Alep - Waqf complexes of Ibshir Pasha and Bahram Pasha - Sept 2017.webm",
        "crop=720:404:0:86",  # ffmpeg cropdetect on the first five seconds
    ),
}
# id: (file in acceptance/public, Commons title). Real photographs of Aleppo; the
# captions say only what the Commons page says.
PHOTOS = {
    "street": ("aleppo_building.jpg", "File:Immeuble bombardé à Alep.jpg"),
    "souk": ("aleppo_souk.jpg", "File:Le souk d'Alep bombardé.jpg"),
    "checkpoint": (
        "aleppo_checkpoint.png",
        "File:A Syrian Arab Army checkpoint abandoned due to a massive rebel offensive, December 2024.png",
    ),
}
POSTER_S = {"jdeideh_2015": 5, "jdeideh_2017": 14}
# The 2015 upload carries an armed group's emblems in its top-left corner. Everything
# shown on the page is built from a copy with that corner blurred; fingerprints,
# hashes and search are measured on the unaltered files.
BLUR = {
    "jdeideh_2015": "split[a][b];[b]crop=160:95:20:5,boxblur=10:3[c];[a][c]overlay=20:5"
}
# Re-upload copies of the 2015 clip: id -> (how, input options, filter, video rate options).
COPIES = {
    "v_lowrate": (
        "re-encoded at 120 kbit/s and 320 pixels wide",
        [],
        "scale=320:-2",
        ["-b:v", "120k"],
    ),
    "v_trim": ("trimmed to start 4 seconds in", ["-ss", "4"], "null", ["-crf", "30"]),
    "v_logo": (
        "a solid logo block burned into the lower right corner",
        [],
        "drawbox=x=iw-150:y=ih-80:w=120:h=52:color=white@0.9:t=fill,drawbox=x=iw-150:y=ih-80:w=120:h=52:color=black:t=4",
        ["-crf", "30"],
    ),
    "v_crop": (
        "cropped to the central 75% of the picture",
        [],
        "crop=iw*3/4:ih*3/4,scale=480:-2",
        ["-crf", "30"],
    ),
    "v_mirror": ("mirrored left to right", [], "hflip", ["-crf", "30"]),
}
QUERIES = [
    "explosion over the old city rooftops",
    "a cloud of dust over the houses",
    "ruined stone domes",
    "دمار في الحي القديم",
    "انفجار فوق أسطح المدينة",
]


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


def mp4(src: Path, out: Path, pre=(), vf="null", rate=("-crf", "27")) -> Path:
    ffmpeg(
        *pre,
        "-i",
        str(src),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        *rate,
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        "48k",
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-bitexact",
        str(out),
    )
    return out


def sprite(path: Path, n: int, name: str) -> str:
    """One strip of thumbnails at the fingerprint's own frame selection."""
    ffmpeg(
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-vf",
        f"select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,1),scale=240:135,tile={n}x1",
        "-frames:v",
        "1",
        "-q:v",
        "6",
        str(MEDIA / name),
    )
    return f"media/{name}"


def credit(title: str) -> dict:
    pages = json.loads((PUBLIC / "commons.json").read_text())["query"]["pages"]
    page = next(p for p in pages.values() if p["title"] == title)
    info = page["imageinfo"][0]
    meta = {k: v["value"] for k, v in info["extmetadata"].items()}
    author = re.sub(r"<[^>]+>", "", meta["Artist"]).strip().splitlines()[0].strip()
    if author.startswith("Unknown author"):
        author = "Unknown author"
    return {
        "title": title.removeprefix("File:"),
        "author": author,
        "description": re.sub(r"<[^>]+>", "", meta.get("ImageDescription", "")).strip(),
        "credit": re.sub(r"<[^>]+>", "", meta.get("Credit", "")).strip(),
        "licence": meta["LicenseShortName"],
        "licence_url": meta.get("LicenseUrl")
        or info["descriptionurl"],  # public domain has no licence URL
        "source": info["descriptionurl"],
        "date": meta["DateTimeOriginal"],
        "sha1": info["sha1"],
    }


def build_photos() -> dict:
    out = {}
    for pid, (name, title) in PHOTOS.items():
        src = PUBLIC / name
        cred = credit(title)
        assert hashlib.sha1(src.read_bytes()).hexdigest() == cred["sha1"], name  # noqa: S324
        out[pid] = {"id": pid, "name": cred["title"], "credit": cred}
        if pid == "street":
            with Image.open(src) as im:
                out[pid]["media"] = save_image(im, "street_photo.jpg", "JPEG", 1024)
    return out


def build_video() -> dict:
    clips, fps = [], {}
    for cid, (name, title, crop) in CLIPS.items():
        src = PUBLIC / name
        cred = credit(title)
        assert hashlib.sha1(src.read_bytes()).hexdigest() == cred["sha1"], name  # noqa: S324
        vf = ",".join(x for x in (crop, BLUR.get(cid), "scale=640:-2") if x)
        web = mp4(src, MEDIA / f"{cid}.mp4", vf=vf)
        ffmpeg(
            "-ss",
            str(POSTER_S[cid]),
            "-i",
            str(web),
            "-frames:v",
            "1",
            "-q:v",
            "4",
            str(MEDIA / f"{cid}.jpg"),
        )
        fps[cid] = compute.fingerprint(src, "video/webm")
        clips.append(
            {
                "id": cid,
                "media": rel(web),
                "poster": f"media/{cid}.jpg",
                "poster_s": POSTER_S[cid],
                "duration_ms": round(
                    float(
                        subprocess.run(
                            [
                                "ffprobe",
                                "-v",
                                "error",
                                "-show_entries",
                                "format=duration",
                                "-of",
                                "csv=p=0",
                                str(src),
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                        ).stdout
                    )
                    * 1000
                ),
                "place": "Jdeideh quarter, Aleppo",
                "credit": cred,
                "frames_ms": [f["ms"] for f in fps[cid]["visual"]],
                "sprite": sprite(web, len(fps[cid]["visual"]), f"{cid}_strip.jpg"),
                "audio_status": fps[cid]["audio_status"],
                **digests(src.read_bytes()),
            }
        )

    ref = fps["jdeideh_2015"]
    original = PUBLIC / CLIPS["jdeideh_2015"][0]
    tmp = Path(tempfile.mkdtemp())
    # Same transforms applied to a blurred master, so the blur follows the emblem.
    blurred = mp4(
        original, tmp / "blurred.mp4", vf=BLUR["jdeideh_2015"], rate=("-crf", "16")
    )
    copies = []
    for vid, (how, pre, vf, rate) in COPIES.items():
        out = mp4(original, tmp / f"{vid}.mp4", pre, vf, rate)
        shown = mp4(blurred, MEDIA / f"{vid}.mp4", pre, vf, rate)
        fp = compute.fingerprint(out, "video/mp4")
        pic = compute.match_frames(
            fp["visual"], ref["visual"], Config.PDQ_DISTANCE, Config.PDQ_QUALITY
        )
        sound = audio.align(fp["audio"], ref["audio"])
        row = {
            "id": vid,
            "from": "jdeideh_2015",
            "how": how,
            "media": rel(shown),
            "derived": True,
            "frames_ms": [f["ms"] for f in fp["visual"]],
            "sprite": sprite(shown, len(fp["visual"]), f"{vid}_strip.jpg"),
            "picture": None,
            "sound": sound,
            **digests(out.read_bytes()),
        }
        if pic:
            offsets = sorted(b - a for a, b in pic["sample_pairs"])
            row["picture"] = pic | {
                "sample_pairs": [list(x) for x in pic["sample_pairs"]],
                "offset_ms": offsets[len(offsets) // 2],
            }
        copies.append(row)

    return {
        "display_note": "The emblems in the 2015 clip's top-left corner are blurred "
        "on this page. Fingerprints, hashes and search were measured on the "
        "unaltered files.",
        "clips": clips,
        "copies": copies,
        "threshold": {
            "distance": Config.PDQ_DISTANCE,
            "of": 256,
            "quality": Config.PDQ_QUALITY,
        },
        "places": [
            {
                "date": c["credit"]["date"],
                "place": c["place"],
                "clip": c["id"],
                "poster": c["poster"],
            }
            for c in sorted(clips, key=lambda c: c["credit"]["date"])
        ],
        **({"search": s} if (s := video_search(clips)) else {}),
    }


def video_search(clips: list[dict]) -> dict | None:
    import httpx

    try:
        version = httpx.get(f"{OLLAMA}/api/version", timeout=5).json()["version"]
    except httpx.TransportError:
        print("Ollama is not reachable; the video search section is omitted.")
        return None
    items = []
    for c in clips:
        parts, _ = frames.parts(PUBLIC / CLIPS[c["id"]][0], "video/webm", 0, 1000)
        items += [
            (c["id"], p["location"]["ms"][0], p["data"])
            for p in parts
            if p["kind"] == "frame"
        ]
    body = httpx.post(
        f"{OLLAMA}/api/embed",
        json={
            "model": MODEL,
            "input": [embeddings.content("image", d) for *_, d in items]
            + [embeddings.QUERY_PREFIX + q for q in QUERIES],
            "dimensions": DIMENSIONS,
        },
        timeout=600,
    ).json()
    vecs = np.array([compute.vector(v, DIMENSIONS) for v in body["embeddings"]])
    frame_v, query_v = vecs[: len(items)], vecs[len(items) :]
    out = []
    for q, qv in zip(QUERIES, query_v, strict=True):
        scores = frame_v @ qv
        top = np.argsort(-scores, kind="stable")[:3]
        out.append(
            {
                "text": q,
                "lang": "ar" if re.search("[\u0600-\u06ff]", q) else "en",
                "results": [
                    {
                        "clip": items[i][0],
                        "ms": items[i][1],
                        "score": round(float(scores[i]), 3),
                    }
                    for i in top
                ],
            }
        )
    return {
        "model": body["model"],
        "runtime": f"Ollama {version}",
        "frames": len(items),
        "every_s": 3,
        "queries": out,
    }


def source_name(key: str) -> str:
    """A demo source key as the reader sees it: the file name where the seed has one."""
    from stk.monitor.seed_full import FILES

    return Path(FILES[key][0]).name if key in FILES else key


def text_of(value) -> str:
    return value.get("text", "") if isinstance(value, dict) else str(value)


def build_connections(live: dict) -> dict:
    names = live["names"]
    files = live["files"]

    def member(m: dict) -> dict:
        return {
            "name": m["name"],
            "file": source_name(files[m["source"]]),
            # The transcript line, not the recording, holds the span.
            "derived": m["span_blob"] != m["source"],
            "prop": m["prop"],
            "value": m["display"],
            "when": m["when"],
            "tier": m["tier"],
            "sensitive": m["sensitive"],
            "excerpt": m["excerpt"],
            "span": m["span"],
        }

    found = [
        {
            k: f[k]
            for k in (
                "kind",
                "label",
                "reason",
                "why",
                "band",
                "month",
                "window",
                "features",
            )
        }
        | {
            "places": [p.get("en") or p.get("ar") for p in f["places"]],
            "people": len(f["people"]),
            "members": [member(m) for m in f["members"]],
        }
        for f in live["findings"]
    ]

    def row(r: dict) -> dict:
        v = r["value"]
        if r["prop"] == "incident":
            what = text_of(v["title"])
        elif r["prop"] == "occurred_at":
            what = names[v["place"]][0]
        elif r["prop"] == "involved":
            what = names[v["person"]][-1]
            role = v["role"]
        elif r["prop"] == "supported_by":
            what = "one accepted member claim"
        else:
            what = v["verdict"] + (f": {v['reason']}" if v.get("reason") else "")
        return {
            "hash": r["hash"],
            "prop": r["prop"],
            "what": what,
            "valid": r["valid"],
        } | ({"role": role} if r["prop"] == "involved" else {})

    gestures = []
    for g in live["gestures"]:
        sig = g["sig"]
        gestures.append(
            {
                "rows": [row(r) for r in g["rows"]],
                "batch": sig["batch"],
                "verified_offline": all(r["verified"] for r in g["rows"]),
                "sig": {
                    k: sig[k]
                    for k in (
                        "challenge",
                        "client_data_json",
                        "authenticator_data",
                        "signature",
                        "public_key",
                        "rp_id",
                        "origin",
                        "demo",
                    )
                },
            }
        )
    from stk.ledger.offline_sigs import GESTURE_DOMAIN

    return {
        "case": "case-demo-02",
        "findings": found,
        "incidents": [i["title"] for i in live["incidents"]],
        "gestures": gestures,
        "gesture_domain": GESTURE_DOMAIN.decode(),
        "per_kind": Config.FINDINGS_PER_KIND,
        "reveals": live["reveals"],
        "access_records": live["access_records"],
    }


def build_story(live: dict) -> dict:
    st = live["story"]
    names = live["names"]
    files = live["files"]
    points = [
        {
            k: p[k]
            for k in (
                "id",
                "prop",
                "place",
                "place_id",
                "from",
                "to",
                "tier",
                "sensitive",
            )
        }
        | {
            "file": source_name(p["file"]),
            "en": (names.get(f"place:{p['place_id']}") or [None])[0],
        }
        for p in st["points"]
        if p.get("place_id")
    ]
    assert all(files.get(p["source"]) for p in st["points"])
    pending = [
        {k: f[k] for k in ("kind", "label", "reason", "band", "window")}
        for f in st["suggestions"]
    ]
    used = {p["place_id"] for p in points}
    lonlats = [live["places"][i]["lonlat"] for i in used]
    pad = 0.12
    box = (
        min(x for x, _ in lonlats) - pad,
        min(y for _, y in lonlats) - pad,
        max(x for x, _ in lonlats) + pad,
        max(y for _, y in lonlats) + pad,
    )
    admin = json.loads(
        (ROOT / "stk/places/fixtures/codab/syr_admin2.geojson").read_text()
    )

    def touches(geom) -> bool:
        flat = np.array([pt for poly in polygons(geom) for ring in poly for pt in ring])
        return not (
            flat[:, 0].max() < box[0]
            or flat[:, 0].min() > box[2]
            or flat[:, 1].max() < box[1]
            or flat[:, 1].min() > box[3]
        )

    areas = [
        {
            "name": f["properties"]["adm2_name"],
            "name_ar": f["properties"].get("adm2_name1"),
            "pcode": f["properties"]["adm2_pcode"],
            "rings": [ring for poly in polygons(f["geometry"]) for ring in poly],
        }
        for f in admin["features"]
        if touches(f["geometry"])
    ]
    source = (ROOT / "stk/places/fixtures/codab/SOURCE.txt").read_text()
    return {
        "title": st["title"],
        "rows": st["rows"],
        "points": points,
        "pending": pending,
        "incidents": st["incidents"],
        "places": {i: live["places"][i] for i in used},
        "map": {
            "box": [round(v, 3) for v in box],
            "areas": areas,
            "boundaries": "OCHA COD-AB Syria admin 2 (v02, valid on 2020-12-17), "
            "simplified test subset; "
            + re.search(r"Licence: (.+?)\.\n", source).group(1),
        },
        "attribution": st["attribution"],
    }


def polygons(geom: dict) -> list:
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]


def build_sheet(live: dict) -> dict:
    sh = live["sheet"]
    table = sh["table"]
    claims = [
        {
            "row": c["span"]["row"],
            "column": c["span"]["column"],
            # A whole-cell claim has no character range.
            "chars": [c["span"]["start"], c["span"]["end"]]
            if "start" in c["span"]
            else None,
            "prop": c["prop"],
            "text": text_of(c["value"]),
            "norm": c["value"].get("norm") if isinstance(c["value"], dict) else None,
            "kind": c["value"].get("kind") if isinstance(c["value"], dict) else None,
            "valid": [c["from"], c["to"]],
            "tier": c["tier"],
        }
        for r in sh["proposals"]["rows"]
        for c in r["claims"]
    ]
    for c in (c for c in claims if c["chars"]):
        cell = table["rows"][c["row"] - 2][c["column"] - 1]
        assert cell[c["chars"][0] : c["chars"][1]] == c["text"], (cell, c)
    status = (ROOT / "docs/status.md").read_text()
    measured = re.search(r"(Measured once on `messy_people\.csv`.+?columns)", status)
    return {
        "file": "messy_people.csv",
        "dataset": sh["versions"][0]["dataset"],
        "header": table["header"],
        "rows": table["rows"],
        "mapping": sh["versions"][0]["mapping"],
        "key_column": sh["versions"][0]["key_column"],
        "versions": [
            {"number": v["number"], "file": v["file"], "diff": v["diff"]}
            for v in sh["versions"]
        ],
        "claims": claims,
        "fixed_output": True,
        "measured": re.sub(r"`", "", measured.group(1)) + ".",
    }


def build_grouped(live: dict) -> dict:
    groups_out = []
    for q in live["search"]:
        groups = {}
        for name, g in q["result"]["groups"].items():
            if not g["items"]:
                continue
            groups[name] = {
                "total": g["total"],
                "exact": g["exact"],
                "items": [
                    {
                        "title": source_name(i["title"])
                        if i["type"] != "place" and i["title"] in live["files"].values()
                        else i["title"],
                        "alt": i.get("alt"),
                        "subtitle": i.get("subtitle"),
                        "exact": i.get("exact"),
                        "signals": (i.get("why") or {}).get("signals", []),
                        "matched": (i.get("why") or {}).get("matched"),
                        "masked": i.get("masked", False),
                        "snippet": i.get("snippet"),
                        "location": i.get("location") or None,
                    }
                    for i in g["items"]
                ],
            }
        groups_out.append(
            {
                "query": q["query"],
                "ms": q["ms"],
                "semantic": q["result"]["semantic"]["status"],
                "groups": groups,
            }
        )
    from acceptance.eval_search import BATTERY

    ev = (ROOT / "docs/search-eval.md").read_text()
    row = re.search(
        r"\| After also enriching case-demo-01 \| ([\d.]+) \| ([\d.]+) \| (\d+) \| ([\d.]+) \| (\d+) / (\d+) ms \|",
        ev,
    )
    gold = re.search(r"recall@1 ([\d.]+), recall@5 ([\d.]+) over (\d+) positive", ev)
    hardware = re.search(r"Results, (\d{4}-\d\d-\d\d)\n\n(.+?), `", ev)
    return {
        "queries": groups_out,
        "battery": {
            "scored": sum(1 for b in BATTERY if b[2]),
            "hit_at_1": float(row.group(1)),
            "hit_at_3": float(row.group(2)),
            "missed": int(row.group(3)),
            "p50_ms": int(row.group(5)),
            "p95_ms": int(row.group(6)),
            "date": hardware.group(1),
            "setup": hardware.group(2),
        },
        "gold": {
            "recall_at_1": float(gold.group(1)),
            "recall_at_5": float(gold.group(2)),
            "queries": int(gold.group(3)),
        },
    }


def build_speech() -> dict:
    """Run the app's transcription code on the witness clip with the downloaded
    model, as the `transcribe` worker tool does, and score it against the script."""
    import time

    from stk import speech
    from tests.test_speech import wer, words

    clip = DATA / "witness_clip.ogg"
    model, provenance = speech.load_model()
    duration, _ = speech.probe(clip)
    samples = speech.decode(clip, 0, duration)
    t0 = time.perf_counter()
    lang = speech.language(model, samples)
    t1 = time.perf_counter()
    segments, complete = speech.recognise(model, samples, lang["language"], 0, 1e12)
    t2 = time.perf_counter()
    assert complete
    script = expected["witness_clip.ogg"]["sentences"]
    heard = " ".join(s["text"] for s in segments)
    reference = " ".join(s["text"] for s in script)
    chip = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
    ).stdout.strip()
    return {
        "file": "witness_clip.ogg",
        "audio": "media/witness_clip.ogg",
        "duration_ms": duration,
        "model": {
            k: provenance[k]
            for k in ("model", "repo", "revision", "weights_sha256", "runtime")
            if k in provenance
        },
        "device": {
            k: provenance[k] for k in ("device", "compute_type") if k in provenance
        },
        "hardware": chip,
        "language": lang,
        "segments": [
            {
                "ms": s["ms"],
                "text": s["text"],
                "words": [{"w": w["w"], "ms": w["ms"]} for w in s.get("words", [])],
            }
            for s in segments
        ],
        "script": [
            {"ms": [s["start_ms"], s["end_ms"]], "text": s["text"]} for s in script
        ],
        "wer": round(wer(reference, heard), 3),
        "words": len(words(reference)),
        "decode_s": round(t2 - t1, 1),
        "rtf": round((t2 - t1) / (duration / 1000), 2),
        "language_s": round(t1 - t0, 1),
        "seen_at": next(
            {"ms": [f["start_ms"], f["end_ms"]], "spoken_as": f["spoken_as"]}
            for f in expected["witness_clip.ogg"]["facts"]
            if f["prop"] == "seen_at"
        ),
    }


def cells_of(line: str) -> list[str]:
    """Table cells, where a pipe inside backticks or after a backslash is text."""
    body = line.strip().strip("|")
    cells, cur, code = [], "", False
    for i, ch in enumerate(body):
        code ^= ch == "`"
        if ch == "|" and not code and body[i - 1] != "\\":
            cells.append(cur)
            cur = ""
        else:
            cur += ch
    return [c.strip().replace("\\|", "|") for c in (*cells, cur)]


def build_status() -> dict:
    status = (ROOT / "docs/status.md").read_text()
    rows = [
        cells_of(line)
        for line in status.splitlines()
        if line.startswith("| ") and not line.startswith(("| Area", "| ---"))
    ]
    verification = status.split("## Completed verification")[1].split("\n## ")[0]
    bullets = re.findall(r"^- (.+?(?:\n  .+)*)$", verification, re.M)
    # Developer tooling checks (`stk verify`, `stk smoke`) say nothing to readers.
    measured = [
        re.sub(r"\s+", " ", b).split(". ")[0].rstrip(".") + "."
        for b in bullets
        if not b.startswith("`")
    ]
    cells = {a: (i, lim) for a, i, lim in rows}
    ev = (ROOT / "docs/search-eval.md").read_text()
    models = (ROOT / "docs/models.md").read_text()
    # Measurements quoted from the docs that record them, so they move with the docs.
    metrics = []
    for label, text, pattern in (
        (
            "Analyst search battery",
            ev,
            r"(\| After also enriching case-demo-01 \|[^\n]+)",
        ),
        (
            "Grouped search",
            ev,
            r"Grouped `/api/search` \(`stk.search.find`, all groups\): (p50 \d+ ms, p95 \d+ ms)",
        ),
        (
            "Retrieval gold set",
            ev,
            r"(recall@1 [\d.]+, recall@5 [\d.]+ over \d+ positive\s+queries)",
        ),
        (
            "Speech to text, 33.7 s synthetic witness clip",
            models,
            r"(WER [\d.]+ \(\d+ of \d+ words\) after folding [^|]+?)\s*\|",
        ),
        (
            "Speech to text speed, Apple M2 CPU",
            models,
            r"(\d+\.\d s decoding in the worker \(RTF [\d.]+\))",
        ),
        (
            "Map tiles",
            cells["Maps"][0],
            r"Measured (on 100,000 synthetic claims.+?z5 p95 \d+ ms\))",
        ),
        (
            "Sources table",
            cells["Sources"][0],
            r"(at 20,000 sources every measured page took [^|]+? of query time)",
        ),
        (
            "Sheet extraction",
            cells["Sheets"][0],
            r"Measured once (on `messy_people\.csv`.+?columns)",
        ),
    ):
        found = re.search(pattern, text, re.S)
        assert found, pattern
        value = re.sub(r"\s+", " ", found.group(1)).replace("`", "")
        if label == "Analyst search battery":
            hit1, hit3, missed, _, p = [
                c.strip() for c in value.strip("| ").split("|")
            ][1:]
            value = f"hit at 1 {hit1}, hit at 3 {hit3}, {missed} missed, p50 / p95 {p}"
        metrics.append(f"{label}: {value}.")
    roadmap = (ROOT / "docs/roadmap.md").read_text()
    chunks = roadmap.split("## Further connected chunks")[1].strip().split("\n\n")[0]
    planned = [c.strip().rstrip(".") for c in re.sub(r"\s+", " ", chunks).split(";")]
    planned = [p[0].upper() + p[1:] + "." for p in planned]
    planned_tail = []
    spec = (
        ROOT / "docs/superpowers/specs/2026-10-24-web-research-capture-design.md"
    ).read_text()
    intent = re.sub(r"\s+", " ", spec.split("## Intent")[1].strip().split("\n\n")[0])
    planned_tail.append(
        "Web research and capture (design, not built): " + intent.split(". ")[0] + "."
    )
    gaps = roadmap.split("## Gaps to close")[1]
    for title in (
        "Descriptions and titles",
        "Labels with observed and alleged basis",
        "Typed relations with stated certainty",
    ):
        body = re.search(
            re.escape(f"**{title}.**") + r"(.+?)(?:\n\d+\. |\Z)", gaps, re.S
        ).group(1)
        planned_tail.append(
            f"{title}: "
            + re.sub(r"\s+", " ", body).strip().split(". ")[0].rstrip(".")
            + "."
        )
    return {
        "areas": [{"area": a, "does": i, "limits": lim} for a, i, lim in rows],
        # Feature measurements first; the suite results follow.
        "measured": metrics + measured,
        "planned": planned + [p.replace("`", "") for p in planned_tail],
        "updated": re.search(r"Updated (\d{4}-\d\d-\d\d)", status).group(1),
    }


def main():
    MEDIA.mkdir(parents=True, exist_ok=True)
    files = build_files()
    derived, dups = build_duplicates()
    live = build_live.live()
    photos = build_photos()
    data = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": files,
        "derived": derived,
        "people": build_people(),
        "duplicates": dups,
        "photos": photos,
        "search": build_search(photos),
        "video": build_video(),
        "connections": build_connections(live),
        "story": build_story(live),
        "sheet": build_sheet(live),
        "grouped_search": build_grouped(live),
        "speech": build_speech(),
        "status": build_status(),
    }
    (SITE / "data/case.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    )
    for p in sorted(MEDIA.iterdir()):
        assert p.stat().st_size <= MAX_BYTES or p.suffix in (".ogg", ".mp4"), p


if __name__ == "__main__":
    main()
