"""The `geotag` worker tool: where a photo or video says it was made.

Queued at intake for images and video. Reads EXIF GPS (images), the ISO 6709
location tag (video containers) and the C2PA `stds.exif` assertion, and proposes
each as an unconfirmed `photographed_at` or `filmed_at` on the blob. Embedded
coordinates are easy to strip or forge, so they are never accepted here: a person
reviews them like any claim. The value names the method and a precision no finer
than the file states."""

from __future__ import annotations

import asyncio
import json
import math
import re
from fractions import Fraction

from PIL import Image

from sealbook import blobs
from sealbook.discovery.service import _input
from sealbook.ledger import chain, write
from sealbook.ledger.contracts import EvidenceKind, Prop, Refused, StatementIn
from sealbook.settings import Config

# Metres assumed when a file gives coordinates but no error estimate: a phone GPS
# fix outdoors. A stated horizontal error, when larger, wins.
DEFAULT_PRECISION_M = 50
GPS_IFD = 0x8825
ISO6709 = re.compile(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")


def _deg(parts, ref) -> float | None:
    if not parts:
        return None
    d = sum(float(Fraction(str(p))) / 60**i for i, p in enumerate(parts))
    return -d if ref in ("S", "W") else d


def _point(method, lat, lon, error=None, bearing=None) -> dict | None:
    """A location dict, or None for no fix, (0, 0) or coordinates off the globe."""
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:
        return None  # cameras write zeros when they had no fix
    precision = max(DEFAULT_PRECISION_M, math.ceil(error or 0))
    out = {"method": method, "lon": lon, "lat": lat, "precision_m": precision}
    if bearing is not None and math.isfinite(bearing):
        out["bearing"] = round(bearing) % 360
    return out


def exif(path) -> dict | None:
    with Image.open(path) as im:
        gps = im.getexif().get_ifd(GPS_IFD)
    if not gps:
        return None

    def num(tag):
        return float(Fraction(str(gps[tag]))) if tag in gps else None

    return _point(
        "exif",
        _deg(gps.get(2), gps.get(1)),
        _deg(gps.get(4), gps.get(3)),
        num(31),  # GPSHPositioningError
        num(17),  # GPSImgDirection
    )


def iso6709(tag: str | None) -> dict | None:
    """ "+33.5138+036.2765+000.000/" as written by phones into MP4 and MOV."""
    m = ISO6709.match(tag or "")
    return _point("exif", float(m[1]), float(m[2])) if m else None


def _c2pa_coord(raw) -> float | None:
    """C2PA `stds.exif` writes XMP style "33,30.828N" or "33,30,49.7N"."""
    m = re.fullmatch(
        r"(\d+(?:\.\d+)?)(?:,(\d+(?:\.\d+)?))?(?:,(\d+(?:\.\d+)?))?([NSEW])",
        str(raw).strip(),
    )
    if not m:
        return None
    return _deg([p for p in m.groups()[:3] if p is not None], m[4])


def c2pa(report: dict | None) -> dict | None:
    """The active manifest's `stds.exif` GPS, from a c2patool JSON report."""
    if not report:
        return None
    active = report.get("manifests", {}).get(report.get("active_manifest"), {})
    for a in active.get("assertions", []):
        if a.get("label") != "stds.exif":
            continue
        d = a.get("data", {})
        error = d.get("exif:GPSHPositioningError")
        bearing = d.get("exif:GPSImgDirection")
        return _point(
            "c2pa",
            _c2pa_coord(d.get("exif:GPSLatitude")),
            _c2pa_coord(d.get("exif:GPSLongitude")),
            float(Fraction(str(error))) if error is not None else None,
            float(Fraction(str(bearing))) if bearing is not None else None,
        )
    return None


async def _run(*args) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out, err


async def c2pa_report(path) -> dict | None:
    """c2patool's report, or None when the file has no manifest or a container it
    cannot read; other failures raise, as intake's own check does."""
    code, out, err = await _run("c2patool", str(path))
    if code != 0:
        if b"No claim found" in err or b"Unsupported file type" in err:
            return None
        raise RuntimeError(f"c2patool failed ({code}): {err.decode()[:500]}")
    return json.loads(out)


async def video_tag(path) -> str | None:
    code, out, err = await _run(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format_tags",
        "-of",
        "json",
        str(path),
    )
    if code != 0:
        raise RuntimeError(f"ffprobe failed ({code}): {err.decode()[:500]}")
    tags = {
        k.lower(): v
        for k, v in json.loads(out).get("format", {}).get("tags", {}).items()
    }
    return tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")


def value(loc: dict) -> dict:
    """The statement value. Coordinates are decimal strings: canonical JSON has no
    floats."""
    out = {
        "geometry": {
            "type": "Point",
            "coordinates": [f"{loc['lon']:.6f}", f"{loc['lat']:.6f}"],
        },
        "precision_m": loc["precision_m"],
        "method": loc["method"],
    }
    if "bearing" in loc:
        out["bearing"] = loc["bearing"]
    return out


async def geotag(c) -> str | None:
    blob = await _input(c)
    path = await asyncio.to_thread(blobs.verified_path, blob, Config.BLOB_DIR)
    video = blob.mime.startswith("video/")
    found = [
        iso6709(await video_tag(path))
        if video
        else await asyncio.to_thread(exif, path),
        c2pa(await c2pa_report(path)),
    ]
    found = [f for f in found if f]
    await c.emit("input_read", locations=len(found))
    prop = Prop.FILMED_AT if video else Prop.PHOTOGRAPHED_AT
    written = []
    for loc in found:
        try:
            st = await write.append(
                c.s,
                StatementIn(
                    subject=f"blob:{blob.sha256}",
                    prop=prop,
                    value=value(loc),
                    author_id=c.run.principal_id,
                    run_id=c.run.id,
                    evidence=(EvidenceKind.C2PA_MANIFEST,)
                    if loc["method"] == "c2pa"
                    else (),
                ),
                sign=c.sign,
            )
        except Refused as exc:
            await c.emit("dropped", drop_reason=f"refused: {exc.reason}")
        else:
            written.append(str(st.id))
    await c.emit("statements_written", written=len(written))
    return chain.sha256(chain.canon(written))
