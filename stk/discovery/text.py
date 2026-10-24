"""Derived search keys with coordinates in the unchanged source text."""

import re
import unicodedata

from stk.search.arabic import normalize


def folded(source):
    out, coordinates = [], []
    start = 0
    while start < len(source):
        end = start + 1
        while end < len(source) and unicodedata.combining(source[end]):
            end += 1
        value = normalize(unicodedata.normalize("NFKC", source[start:end])).casefold()
        for char in value:
            if unicodedata.category(char) == "Cf":
                continue
            if char.isspace():
                if out and out[-1] == " ":
                    coordinates[-1] = (coordinates[-1][0], end)
                    continue
                char = " "
            out.append(char)
            coordinates.append((start, end))
        start = end
    return "".join(out), coordinates


def identifier(query):
    return bool(
        re.fullmatch(r"[\w./:-]+", query.strip()) and any(c.isdigit() for c in query)
    )


def first_match(source, query):
    boundary = identifier(query)

    def whole(text, start, end):
        return not boundary or (
            (start == 0 or not text[start - 1].isalnum())
            and (end == len(text) or not text[end].isalnum())
        )

    for exact in re.finditer(re.escape(query), source, re.IGNORECASE):
        if whole(source, *exact.span()):
            return {"start": exact.start(), "end": exact.end()}, "literal"
    haystack, positions = folded(source)
    needle = folded(query)[0].strip()
    if not needle:
        return None
    for match in re.finditer(re.escape(needle), haystack):
        if whole(haystack, *match.span()):
            return {
                "start": positions[match.start()][0],
                "end": positions[match.end() - 1][1],
            }, "normalized"
    return None
