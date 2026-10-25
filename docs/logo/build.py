"""Sealbook brand files: the unbroken seal mark, lockups, favicon and the site header mark.

Run from the repo root:
    uv run --with fonttools --with brotli --with uharfbuzz python docs/logo/build.py
"""

import io
import math
import re
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
FONT = ROOT / "site/fonts/Newsreader-normal-500-latin.woff2"
RINGS = (13, 8.5)


def wax(r=19.5, lobe=1.0, squash=0.0, phase=0.0):
    # Uneven low-frequency lobes: pressed wax, not a rosette badge. Every variant
    # has the same 96 points so SMIL can morph between them.
    pts = []
    for i in range(96):
        t = 2 * math.pi * i / 96
        rr = r * (1 + squash * math.cos(2 * t)) + lobe * (
            1.1 * math.cos(5 * t + 0.4 + phase)
            + 0.7 * math.cos(3 * t + 2.1 - phase)
            + 0.4 * math.cos(7 * t + 2 * phase)
        )
        pts.append(f"{24 + rr * math.cos(t):.2f} {24 + rr * math.sin(t):.2f}")
    return "M" + "L".join(pts) + "Z"


def jelly(u):
    # damped wobble: soft on arrival, exactly the rest shape at u=1
    env = math.exp(-4.5 * u) * (1 - u)
    a = 2 * math.pi * 2.2 * u
    return wax(
        r=19.5 * (1 - 0.12 * env * math.cos(a)),
        lobe=1 + 1.8 * env * math.cos(a),
        squash=0.12 * env * math.sin(a),
        phase=0.6 * env,
    )


def seal(mask_id, fill, animate=""):
    # rings are cut out of the wax, so the background shows through on any theme
    rings = "".join(
        f'<circle cx="24" cy="24" r="{r}" fill="none" stroke="#000" stroke-width="1.6"/>'
        for r in RINGS
    )
    return (
        f'<mask id="{mask_id}"><rect width="48" height="48" fill="#fff"/>{rings}</mask>'
        f'<path fill="{fill}" mask="url(#{mask_id})" d="{wax()}">{animate}</path>'
    )


def wordmark(text="Sealbook", size=31, x=58, baseline=34):
    font = TTFont(FONT)
    font.flavor = None  # harfbuzz cannot read woff2
    ttf = io.BytesIO()
    font.save(ttf)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hb.Font(hb.Face(ttf.getvalue())), buf, {"kern": True, "liga": True})
    glyphs, order = font.getGlyphSet(), font.getGlyphOrder()
    k = size / font["head"].unitsPerEm
    pen = SVGPathPen(glyphs, lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
    pen_x = 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        at = (k, 0, 0, -k, x + (pen_x + pos.x_offset) * k, baseline - pos.y_offset * k)
        glyphs[order[info.codepoint]].draw(TransformPen(pen, at))
        pen_x += pos.x_advance
    return pen.getCommands(), math.ceil(x + pen_x * k + 2)


def svg(width, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 48" role="img" '
        f'aria-label="Sealbook"><title>Sealbook</title>{body}</svg>\n'
    )


word, width = wordmark()
# site/css/site.css tokens: paper theme, night archive
LIGHT, DARK = ("#5732c2", "#1b1a17"), ("#b49dff", "#ece8de")


def lockup(accent, ink):
    return svg(width, seal("m", accent) + f'<path fill="{ink}" d="{word}"/>')


# 16px cut: one heavier ring, the double ring fills in at that size. Follows the OS
# like the browser tab around it.
favicon = svg(
    48,
    "<style>path{fill:#5732c2}@media (prefers-color-scheme:dark){path{fill:#b49dff}}</style>"
    '<mask id="m"><rect width="48" height="48" fill="#fff"/>'
    '<circle cx="24" cy="24" r="10" fill="none" stroke="#000" stroke-width="3.4"/></mask>'
    f'<path mask="url(#m)" d="{wax(r=21)}"/>',
)

files = {
    "sealbook/static/img/sealbook.svg": lockup(*LIGHT),
    "sealbook/static/img/sealbook-dark.svg": lockup(*DARK),
    "sealbook/static/img/sealbook-mark.svg": favicon,
    "site/favicon.svg": favicon,
    "docs/favicon.svg": favicon,
    "docs/logo/light.svg": lockup(*LIGHT),
    "docs/logo/dark.svg": lockup(*DARK),
}
for path, body in files.items():
    (ROOT / path).write_text(body)

# Site header mark: inline so it takes the theme colour, and the jelly settle waits for
# site.js to start it (skipped under reduced motion).
frames = [jelly(k / 40) for k in range(41)]
times = ";".join(f"{k / 40:.3f}" for k in range(41))
settle = (
    f'<animate attributeName="d" dur="1.3s" begin="indefinite" keyTimes="{times}" '
    f'values="{";".join(frames)}" fill="freeze"/>'
)
header = (
    '<svg viewBox="0 0 48 48" aria-hidden="true">'
    + seal("seal-rings", "var(--violet)", settle)
    + "</svg>"
)
index = ROOT / "site/index.html"
html, n = re.subn(
    r"(<!-- seal -->).*?(<!-- /seal -->)",
    lambda m: m[1] + header + m[2],
    index.read_text(),
    flags=re.S,
)
assert n == 1, "site/index.html needs one <!-- seal --><!-- /seal --> marker pair"
index.write_text(html)

assert jelly(1) == wax(), "the settle must end on the rest shape"
print("wrote", ", ".join([*files, "site/index.html header mark"]))
