"""Place records: GeoNames rows and people-recorded sites, found by normalised name."""

import re

from stk.search.arabic import normalize


def normalise(name: str) -> str:
    """The search normaliser plus case and whitespace folding, for exact name lookup."""
    return re.sub(r"\s+", " ", normalize(name)).strip().lower()
