"""Pure derivative generation: bytes in, small pictures out. Every derivative is
re-encoded from decoded pixels, so no EXIF, XMP or container metadata survives."""

import io
import json
import math
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageOps, ImageStat

VERSION = 1
PARAMS = {
    "sizes": [160, 480],
    "tile": "square centre crop",
    "webp_quality": 80,
    "strip_frames": 12,
    "strip_cell_width": 160,
    "pdf_pages": 50,
    "pdf_page_side": 240,
    "peaks": 800,
    "waveform_rate": 8000,
}
Image.MAX_IMAGE_PIXELS = 100_000_000


def webp(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "WEBP", quality=PARAMS["webp_quality"], method=4)
    return out.getvalue()


def png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def flat(image: Image.Image) -> Image.Image:
    """Orientation applied; alpha kept, every other mode becomes RGB."""
    image = ImageOps.exif_transpose(image)
    return image.convert("RGBA" if "A" in image.getbands() else "RGB")


def pair(image: Image.Image, top: bool = False) -> dict[str, bytes]:
    """The 480 (longest side) picture and the 160 square tile."""
    image = flat(image)
    big = image.copy()
    big.thumbnail((480, 480))
    tile = ImageOps.fit(
        image, (160, 160), Image.LANCZOS, centering=(0.5, 0.0 if top else 0.5)
    )
    return {"thumb_480": webp(big), "thumb_160": webp(tile)}


def image_files(path) -> tuple[dict, dict]:
    with Image.open(path) as image:
        image.load()
        size = image.size
        return pair(image), {"width": size[0], "height": size[1]}


# --- video -----------------------------------------------------------------------


def probe(path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    data = json.loads(result.stdout)
    duration = float(data["format"].get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("media has no usable duration")
    return {
        "duration": duration,
        "streams": {s["codec_type"] for s in data["streams"]},
    }


def frame_at(path, seconds: float, width: int) -> Image.Image:
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    if not result.stdout:
        raise ValueError(f"no frame decoded at {seconds:.2f}s")
    with Image.open(io.BytesIO(result.stdout)) as image:
        return image.convert("RGB")


def interest(frame: Image.Image) -> float:
    """Contrast of a candidate poster, zero for a near-black or near-white frame."""
    stat = ImageStat.Stat(frame.convert("L"))
    mean, spread = stat.mean[0], stat.stddev[0]
    return 0.0 if mean < 16 or mean > 240 else spread


def video_files(path, duration: float) -> tuple[dict, dict]:
    candidates = []
    for fraction in (0.1, 0.25, 0.5, 0.75):
        try:
            frame = frame_at(path, duration * fraction, 480)
        except (ValueError, subprocess.CalledProcessError):
            continue
        candidates.append((interest(frame), duration * fraction, frame))
    if not candidates:
        raise ValueError("no video frame could be decoded")
    # max() keeps the first of equal scores, so an all-dark clip uses the earliest.
    score, at, poster = max(candidates, key=lambda c: c[0])
    files = pair(poster)
    count = max(1, min(PARAMS["strip_frames"], int(duration)))
    cells, ms = [], []
    for i in range(count):
        at_s = duration * (i + 0.5) / count
        try:
            cells.append(frame_at(path, at_s, PARAMS["strip_cell_width"]))
        except (ValueError, subprocess.CalledProcessError):
            continue
        ms.append(round(at_s * 1000))
    if not cells:
        raise ValueError("no strip frame could be decoded")
    w, h = cells[0].size
    sprite = Image.new("RGB", (w * len(cells), h))
    frames = []
    for i, cell in enumerate(cells):
        sprite.paste(cell.resize((w, h)), (i * w, 0))
        frames.append({"ms": ms[i], "x": i * w})
    files["strip"] = webp(sprite)
    meta = {
        "duration_ms": round(duration * 1000),
        "poster_ms": round(at * 1000),
        "poster_contrast": round(score, 1),
        "strip": {"cell_width": w, "cell_height": h, "frames": frames},
    }
    return files, meta


# --- pdf -------------------------------------------------------------------------


def pdf_files(path) -> tuple[dict, dict]:
    import pypdfium2 as pdfium

    files, pages = {}, []
    with pdfium.PdfDocument(path) as document:
        total = len(document)
        if not total:
            raise ValueError("PDF has no pages")
        for i in range(min(total, PARAMS["pdf_pages"])):
            page = document[i]
            try:
                side = PARAMS["pdf_page_side"] if i else 960
                bitmap = page.render(scale=side / max(page.get_size()))
                try:
                    image = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
            finally:
                page.close()
            if i == 0:
                files |= pair(image, top=True)
            small = image.copy()
            small.thumbnail((PARAMS["pdf_page_side"],) * 2)
            pages.append(webp(small))
    files["pages"] = pages
    return files, {"page_count": total, "pages_rendered": len(pages)}


# --- audio -----------------------------------------------------------------------


def decode_peaks(path, duration: float) -> list[float]:
    rate = PARAMS["waveform_rate"]
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "s16le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=600,
    )
    samples = np.abs(np.frombuffer(result.stdout, dtype="<i2").astype(np.float32))
    if not samples.size:
        raise ValueError("audio decoded to no samples")
    buckets = min(PARAMS["peaks"], samples.size)
    peaks = np.array([b.max() for b in np.array_split(samples, buckets)]) / 32768
    top = peaks.max()
    return [round(float(p), 3) for p in (peaks / top if top else peaks)]


def waveform(peaks: list[float], width: int, height: int) -> bytes:
    """Mirrored bars on a transparent ground, one bar per two pixels."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    bars = width // 2
    for x in range(bars):
        lo, hi = (
            x * len(peaks) // bars,
            max(x * len(peaks) // bars + 1, (x + 1) * len(peaks) // bars),
        )
        level = max(peaks[lo:hi])
        half = max(1, round(level * (height / 2 - 1)))
        draw.rectangle(
            [x * 2, height // 2 - half, x * 2 + 1, height // 2 + half],
            fill=(110, 124, 150, 255),
        )
    return png(image)


def audio_files(path, duration: float) -> tuple[dict, dict]:
    peaks = decode_peaks(path, duration)
    files = {
        "thumb_480": waveform(peaks, 480, 120),
        "thumb_160": waveform(peaks, 160, 160),
        "peaks": json.dumps(
            {"duration_ms": round(duration * 1000), "peaks": peaks},
            separators=(",", ":"),
        ).encode(),
    }
    return files, {"duration_ms": round(duration * 1000), "buckets": len(peaks)}


def kind_of(mime: str, streams: set[str] | None = None) -> str | None:
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("video/"):
        return "video" if streams is None or "video" in streams else "audio"
    if mime.startswith("audio/"):
        return "audio"
    return None
