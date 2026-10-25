"""Check the colour roles in shell.css: contrast in both themes and how far apart the
inks stay under deuteranopia and protanopia simulation (docs/design.md, Colour roles).

    uv run --with colorspacious --with coloraide --with numpy python -m acceptance.check_palette

Prints the contrast table and the closest pairs per simulation; exits 1 when an ink
falls under 4.5:1 on panel, page or its own wash, or a line under 3:1 on panel.
"""

import itertools
import re
import sys
from pathlib import Path

import colorspacious as cs
import numpy as np
from coloraide import Color

CSS = Path(__file__).parent.parent / "sealbook/static/css/shell.css"
ROLES = ["person", "place", "source", "incident", "agent", "accepted", "conflict"]
ROLES += ["negative", "sensitive", "new"]
SIMS = {
    "normal": None,
    "deutan": {"name": "sRGB1+CVD", "cvd_type": "deuteranomaly", "severity": 100},
    "protan": {"name": "sRGB1+CVD", "cvd_type": "protanomaly", "severity": 100},
}


def blocks(css):
    """Hex tokens per theme: plain :root blocks are light, dark-mode blocks dark."""
    found = re.findall(r"(:root(?:\.dark-mode, body\.dark-mode)?) \{([^}]*)\}", css)
    themes = {"light": {}, "dark": {}}
    for sel, body in found:
        theme = "dark" if "dark-mode" in sel else "light"
        themes[theme].update(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-f]{6})", body))
    return themes


def contrast(a, b):
    return Color(a).contrast(Color(b), method="wcag21")


def distances(inks, sim):
    pts = {}
    for k, h in inks.items():
        c = np.array(Color(h).convert("srgb").coords())
        if sim:
            c = np.clip(cs.cspace_convert(c, sim, "sRGB1"), 0, 1)
        pts[k] = cs.cspace_convert(c, "sRGB1", "CAM02-UCS")
    pairs = itertools.combinations(pts, 2)
    return sorted((float(np.linalg.norm(pts[a] - pts[b])), a, b) for a, b in pairs)


def main():
    bad = []
    for theme, t in blocks(CSS.read_text()).items():
        print(f"{theme}: ink on panel / page / wash, line on panel")
        for r in ROLES:
            ink, wash, line = t[r], t[r + "-wash"], t[r + "-line"]
            got = [
                contrast(ink, t["panel"]),
                contrast(ink, t["bg"]),
                contrast(ink, wash),
            ]
            ln = contrast(line, t["panel"])
            print(
                f"  {r:10} {ink} "
                + " / ".join(f"{x:4.1f}" for x in got)
                + f", line {ln:.1f}"
            )
            bad += [f"{theme} {r} ink {min(got):.2f}"] if min(got) < 4.5 else []
            bad += [f"{theme} {r} line {ln:.2f}"] if ln < 3 else []
        for name, sim in SIMS.items():
            d = distances({r: t[r] for r in ROLES}, sim)[:4]
            print(
                f"  {name:6} closest " + ", ".join(f"{a}/{b} {x:.1f}" for x, a, b in d)
            )
    if bad:
        print("Below AA:", *bad, sep="\n  ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
