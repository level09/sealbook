"""Rebuild the film's storyboard from site/film_board.tsv.

Writes one still per row to media/board/film-<n>.jpg, the board's buttons between the
film-board markers in index.html, and each case page's film start. Needs ffmpeg.

    python3 site/build_film_board.py
"""

import hashlib
import re
import subprocess
from pathlib import Path

SITE = Path(__file__).resolve().parent
FILM = SITE / "media/case_film.mp4"


def rows():
    lines = (SITE / "film_board.tsv").read_text(encoding="utf-8").splitlines()
    body = [line for line in lines if line and not line.startswith("#")][1:]
    for line in body:
        seek, still, label, *case = line.split("\t")
        yield float(seek), float(still), label, (case[0] if case else "")


def clock(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def main():
    board = []
    for n, (seek, still, label, case) in enumerate(rows(), 1):
        out = SITE / f"media/board/film-{n}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-ss",
                str(still),
                "-i",
                str(FILM),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2",
                "-q:v",
                "4",
                str(out),
            ],
            check=True,
        )
        board.append(
            f'    <li><button type="button" data-seek="{seek:g}"><img src="media/board/film-{n}.jpg" alt="" '
            f'loading="lazy" width="640" height="360"><span class="b-t mono">{clock(seek)}</span>'
            f'<span class="b-l">{label}</span></button></li>'
        )
        if case:
            page = SITE / f"cases/{case}.html"
            html = page.read_text(encoding="utf-8")
            html = re.sub(r'(href="\.\./index\.html#t=)[\d.]+', rf"\g<1>{seek:g}", html)
            html = re.sub(r'(data-start=")[\d.]+', rf"\g<1>{seek:g}", html)
            html = re.sub(
                r"(Watch this case in the film, from )\d+:\d\d",
                rf"\g<1>{clock(seek)}",
                html,
            )
            page.write_text(html, encoding="utf-8")
    # A new render gets new ?v= values, so no browser keeps the old film or captions.
    for page in (SITE / "index.html", SITE / "cases/four-names.html"):
        html = page.read_text(encoding="utf-8")
        for ext in ("mp4", "jpg", "vtt"):
            v = hashlib.sha1(
                (SITE / f"media/case_film.{ext}").read_bytes()
            ).hexdigest()[:8]
            html = re.sub(rf"(media/case_film\.{ext}\?v=)\w+", rf"\g<1>{v}", html)
        page.write_text(html, encoding="utf-8")
    index = SITE / "index.html"
    html = index.read_text(encoding="utf-8")
    html, count = re.subn(
        r"<!-- film-board -->.*?<!-- /film-board -->",
        lambda m: (
            "<!-- film-board -->\n" + "\n".join(board) + "\n    <!-- /film-board -->"
        ),
        html,
        flags=re.S,
    )
    if count != 1:
        raise SystemExit("index.html: film-board markers not found")
    index.write_text(html, encoding="utf-8")
    print(f"{len(board)} stills")


if __name__ == "__main__":
    main()
