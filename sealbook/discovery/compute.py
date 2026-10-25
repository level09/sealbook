"""Pure ranking and matching; measurements never promote an evidentiary claim."""

import json
import math
import re
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
import pdqhash
from PIL import Image, ImageOps

from sealbook.discovery import audio


def chunks(text: str, size=1800) -> list[tuple[int, int]]:
    if size < 1:
        raise ValueError("chunk size must be positive")
    out, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            split = text.rfind("\n", start + size // 2, end)
            if split >= 0:
                end = split + 1
        out.append((start, end))
        start = end
    return out


def vector(values, dimensions) -> list[float]:
    if len(values) != dimensions or any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
        for x in values
    ):
        raise ValueError(f"embedding needs {dimensions} finite numbers")
    norm = math.hypot(*values)
    if not norm:
        raise ValueError("embedding is a zero vector")
    return [x / norm for x in values]


def rrf(*rankings, k=60) -> list:
    scores = {}
    for ranking in rankings:
        for rank, key in enumerate(dict.fromkeys(ranking), 1):
            scores[key] = scores.get(key, 0) + 1 / (k + rank)
    return sorted(scores, key=lambda key: -scores[key])


def match_frames(query, candidate, distance=31, quality=50) -> dict | None:
    def unique(frames):
        return {f["hash"]: f for f in reversed(frames) if f["quality"] >= quality}

    q, c = unique(query), unique(candidate)
    pairs = [
        (a, b, d)
        for ah, a in q.items()
        for bh, b in c.items()
        if (d := (int(ah, 16) ^ int(bh, 16)).bit_count()) <= distance
    ]
    if not pairs:
        return None
    qa, cb = {a["hash"] for a, _, _ in pairs}, {b["hash"] for _, b, _ in pairs}
    return {
        "query_coverage": [len(qa), len(q)],
        "candidate_coverage": [len(cb), len(c)],
        "query_ms": [
            min(a["ms"] for a, _, _ in pairs),
            max(a["ms"] for a, _, _ in pairs),
        ],
        "candidate_ms": [
            min(b["ms"] for _, b, _ in pairs),
            max(b["ms"] for _, b, _ in pairs),
        ],
        "max_distance": max(d for _, _, d in pairs),
        "sample_pairs": sorted({(a["ms"], b["ms"]) for a, b, _ in pairs}),
    }


def _image(path: Path, ms=0) -> dict:
    with Image.open(path) as im:
        bits, quality = pdqhash.compute(
            np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
        )
    packed = np.packbits(bits).tobytes().hex()
    return {"hash": packed, "quality": int(quality), "ms": ms}


def fingerprint(path: Path, mime: str, max_seconds=3600) -> dict:
    if mime.startswith("image/"):
        return {"visual": [_image(path)], "audio": [], "audio_status": "not_applicable"}
    if not mime.startswith(("video/", "audio/")):
        raise ValueError("fingerprinting needs an image, audio or video")
    probe = subprocess.run(
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
    metadata = json.loads(probe.stdout)
    duration = float(metadata["format"]["duration"])
    streams = {row["codec_type"] for row in metadata["streams"]}
    if not math.isfinite(duration) or not 0 < duration <= max_seconds:
        raise ValueError(f"video duration must be between 0 and {max_seconds} seconds")
    with tempfile.TemporaryDirectory(prefix="sealbook-frames-") as directory:
        command = ["ffmpeg", "-nostdin", "-v", "info", "-i", str(path)]
        if "video" in streams:
            command += [
                "-map",
                "0:v:0",
                "-vf",
                "select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,1),showinfo",
                "-fps_mode",
                "vfr",
                str(Path(directory) / "%06d.png"),
            ]
        if "audio" in streams:
            command += [
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                str(audio.SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                str(Path(directory) / "audio.wav"),
            ]
        if not streams & {"audio", "video"}:
            raise ValueError("media has no audio or video stream")
        result = subprocess.run(command, check=True, capture_output=True, timeout=600)
        timestamps = [
            round(float(t) * 1000)
            for t in re.findall(rb"pts_time:([-\d.]+)", result.stderr)
        ]
        images = sorted(Path(directory).glob("*.png"))
        if len(images) != len(timestamps):
            raise ValueError("decoded frames do not match their source timestamps")
        frames = [_image(p, ms) for p, ms in zip(images, timestamps, strict=True)]
        marks = []
        status = "absent"
        if "audio" in streams:
            with wave.open(str(Path(directory) / "audio.wav")) as source:
                samples = (
                    np.frombuffer(
                        source.readframes(source.getnframes()), dtype="<i2"
                    ).astype(np.float32)
                    / 32768
                )
            marks = audio.landmarks(samples)
            status = "present" if marks else "no_landmarks"
    return {"visual": frames, "audio": marks, "audio_status": status}
