"""Place records: GeoNames rows and people-recorded sites, found by normalised name."""

import re

from sealbook.search.arabic import normalize


def normalise(name: str) -> str:
    """The search normaliser plus case and whitespace folding, for exact name lookup."""
    return re.sub(r"\s+", " ", normalize(name)).strip().lower()


# Longest place name looked up in the gazetteer, in words.
MAX_WORDS = 5
WORD = re.compile(r"\w+")


def phrases(text: str, lo: int, hi: int) -> list[tuple[str, int, int]]:
    """Every run of 1 to MAX_WORDS words in text[lo:hi], normalised, longest first."""
    toks = list(WORD.finditer(text, lo, hi))
    out = []
    for n in range(MAX_WORDS, 0, -1):
        for i in range(len(toks) - n + 1):
            run = toks[i : i + n]
            out.append(
                (" ".join(normalise(t[0]) for t in run), run[0].start(), run[-1].end())
            )
    return out


def init_app(app):
    from sealbook.places.cli import places
    from sealbook.places.views import bp_places

    app.register_blueprint(bp_places)
    app.cli.add_command(places)
    app.navigation.append(
        {
            "title": "Places",
            "icon": "ti ti-map-pin",
            "hue": "is-place",
            "to": "/places",
            "match": "/places",
        }
    )
