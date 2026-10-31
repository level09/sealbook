"""Which column holds what, and what changed since the last version. Mapping is a
hint for the extraction run, never a write: a remembered choice is reused as is, an
unfamiliar column is matched first by its header words and the shape of its values
(no model needed), then the embedding model ranks what is left if it answers, and a
person confirms every column before anything is read."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass

import httpx2

from sealbook.discovery import embeddings
from sealbook.settings import Config
from sealbook.sheets import dates
from sealbook.sheets.parse import Sheet


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    about: str  # what the embedding compares a column against
    words: tuple[str, ...] = ()  # header words, Arabic and English


FIELDS = (
    Field(
        "key",
        "Row identifier",
        "record number, row id, serial, رقم، الرقم التسلسلي",
        ("no", "id", "number", "serial", "row", "record", "#", "رقم", "تسلسل", "م"),
    ),
    Field(
        "person",
        "Person, free text",
        "a person described in one cell: name with sex, age and job, بيانات الشخص",
        ("person", "victim", "شخص", "ضحيه", "بيانات الشخص", "معلومات الشخص"),
    ),
    Field(
        "name",
        "Name",
        "full name of the person, الاسم الكامل",
        ("name", "full name", "person name", "اسم", "اسم الكامل", "اسم الشخص"),
    ),
    Field("sex", "Sex", "male or female, ذكر أو أنثى، الجنس", ("sex", "gender", "جنس")),
    Field("age", "Age", "age in years, العمر", ("age", "عمر", "سن")),
    Field(
        "occupation",
        "Occupation",
        "job or profession, المهنة، العمل",
        ("occupation", "job", "profession", "work", "مهنه", "عمل", "وظيفه"),
    ),
    Field(
        "father_name",
        "Father's name",
        "name of the father, اسم الأب",
        ("father", "father name", "اب", "اسم الاب", "والد"),
    ),
    Field(
        "mother_name",
        "Mother's name",
        "name of the mother, اسم الأم",
        ("mother", "mother name", "ام", "اسم الام", "والده"),
    ),
    Field(
        "birth_date",
        "Birth date",
        "date of birth, تاريخ الولادة",
        (
            "birth date",
            "date of birth",
            "dob",
            "born",
            "birthday",
            "تاريخ الولاده",
            "تاريخ الميلاد",
            "مواليد",
            "ولاده",
        ),
    ),
    Field(
        "place_of_birth",
        "Place of birth",
        "where the person was born, مكان الولادة",
        (
            "place of birth",
            "birthplace",
            "birth place",
            "مكان الولاده",
            "مكان الميلاد",
            "محل الولاده",
        ),
    ),
    Field(
        "event",
        "What happened",
        "arrest, detention, death, disappearance, last seen, ماذا حدث، معلومات اضافية",
        (
            "what happened",
            "event",
            "incident",
            "description",
            "circumstances",
            "ما حدث",
            "ماذا حدث",
            "حدث",
            "حادثه",
            "وصف",
            "ظروف",
            "معلومات اضافيه",
        ),
    ),
    Field(
        "place",
        "Place",
        "town, city, area or facility, الموقع، المكان، المدينة",
        (
            "place",
            "location",
            "town",
            "city",
            "area",
            "where",
            "village",
            "مكان",
            "موقع",
            "مدينه",
            "منطقه",
            "بلده",
            "قريه",
        ),
    ),
    Field(
        "date",
        "Date",
        "when it happened, التاريخ",
        ("date", "when", "day", "year", "تاريخ", "يوم"),
    ),
    Field(
        "ignore",
        "Do not read",
        "not about a person, notes for the sheet keeper",
        ("notes", "note", "comments", "source", "ملاحظات", "تعليق", "مصدر"),
    ),
)
FIELD_KEYS = frozenset(f.key for f in FIELDS)
BY_KEY = {f.key: f for f in FIELDS}
SAMPLES = 5
SUGGESTIONS = 3
MODEL_WAIT = 20  # seconds; a person waits on the page while columns are matched


def norm(header: str) -> str:
    return " ".join(header.split()).casefold()


def _terms(text: str) -> tuple[str, ...]:
    """Words of a header with Arabic marks, hamza, taa marbuta and the article
    folded, so الأم, الام and أم compare equal."""
    t = dates._ARABIC_MARKS.sub("", text.translate(dates._DIGITS))
    t = t.translate(dates._HAMZA).replace("ة", "ه").casefold()
    t = re.sub(r"['’]s\b", "", t)  # father's name reads as father name
    out = []
    for w in re.findall(r"#|[^\W_]+", t):
        for article in ("وال", "بال", "ال"):
            if w.startswith(article) and len(w) - len(article) >= 2:
                w = w[len(article) :]
                break
        out.append(w)
    return tuple(out)


_WORDS = [(f, _terms(w)) for f in FIELDS for w in f.words]


def by_header(header: str) -> Field | None:
    """The field whose longest header word appears in the header; ties go to the
    earlier field."""
    have, best, size = _terms(header), None, 0
    for f, want in _WORDS:
        n = len(want)
        if n > size and any(have[i : i + n] == want for i in range(len(have) - n + 1)):
            best, size = f, n
    return best


SEX = frozenset({"ذكر", "انثى", "انثي", "أنثى", "male", "female", "m", "f"})
_SEX_WORD = re.compile(r"\b(?:male|female)\b|ذكر|انثى|أنثى|انثي", re.IGNORECASE)
_AGE = re.compile(r"(?<!\d)\d{1,2}(?!\d)|سنة|سنه|\byears?\b", re.IGNORECASE)
_INT = re.compile(r"\d+")


def shape(values: list[str]) -> str | None:
    """What the values of a column look like: a running number, unique codes,
    sex, dates, ages or a person described in one cell. Majorities, not all, so
    one messy cell does not hide the pattern."""
    vals = [v.translate(dates._DIGITS).strip() for v in values if v.strip()]
    if len(vals) < 2:
        return None
    ints = [int(v) for v in vals if _INT.fullmatch(v)]
    if len(ints) == len(vals):
        if all(b - a == 1 for a, b in zip(ints, ints[1:], strict=False)):
            return "sequence"
        # Ages repeat; numbers that never repeat are row numbers (codes, below).
        if len(set(ints)) < len(ints) and all(0 <= i <= 120 for i in ints):
            return "ages"
    if (
        len(set(vals)) == len(vals)
        and all(len(v) <= 16 and " " not in v and re.search(r"\d", v) for v in vals)
        and not any(dates.interval(v) for v in vals)
    ):
        return "codes"
    if sum(v.casefold() in SEX for v in vals) >= 0.8 * len(vals):
        return "sex"
    known = [v for v in vals if not dates.unknown(v)]
    if known and sum(bool(dates.interval(v)) for v in known) >= 0.6 * len(known):
        return "dates"
    described = [
        v
        for v in vals
        if len(v.split()) >= 2
        and re.search(r"[^\W\d_]", v)
        and (_SEX_WORD.search(v) or _AGE.search(v))
    ]
    if len(described) >= 0.5 * len(vals):
        return "people"
    return None


SHAPES = {
    "sequence": ("key", "values count up one by one"),
    "codes": ("key", "every value is a different number or code"),
    "sex": ("sex", "values are male or female"),
    "dates": ("date", "values look like dates"),
    "ages": ("age", "values are numbers up to 120"),
    "people": ("person", "cells hold a name with sex, age or job"),
}


def columns(sheet: Sheet) -> list[dict]:
    """Header, up to five non-empty sample values and the shape of all values per
    column, leaving out columns with neither a header nor values."""
    body = [sheet.rows[n - 1] for n in sheet.body]
    out = []
    for i, name in enumerate(sheet.head):
        if i + 1 in sheet.dropped:
            continue
        values = [r[i].strip() for r in body if r[i].strip()]
        out.append(
            {
                "column": i + 1,
                "header": name,
                "samples": values[:SAMPLES],
                "shape": shape(values),
            }
        )
    return out


def _option(f: Field, why: str, by: str, score: float | None = None) -> dict:
    return {"field": f.key, "label": f.label, "score": score, "why": why, "by": by}


def matched(c: dict) -> list[dict]:
    """Suggestions from the header words and the value shape, best first, each with
    its reason in words. The header wins when the two disagree."""
    head = by_header(c["header"])
    shaped = SHAPES.get(c.get("shape"))
    # An isolate keeps an Arabic header in its own order inside the English reason.
    said = f"header says \u2068{' '.join(c['header'].split())}\u2069"
    if head and shaped and shaped[0] == head.key:
        return [_option(head, f"{said} and {shaped[1]}", "rules")]
    out = [_option(head, said, "rules")] if head else []
    if shaped:
        out.append(_option(BY_KEY[shaped[0]], shaped[1], "rules"))
    return out


async def embed(texts: list[str]) -> list[list[float]]:
    """The boundary tests mock. Suggestions are not stored, so no space is bound."""
    if not Config.EMBEDDING_MODEL:
        raise ValueError("EMBEDDING_MODEL is not configured; map each column by hand")
    values, *_ = await embeddings._post(texts, [], Config.EMBEDDING_MODEL, None)
    return values


NO_MODEL = (
    "No model is set up to match columns by meaning, so columns were matched by "
    "their headers and values. Check each one."
)
MODEL_DOWN = (
    "The model that matches columns by meaning did not answer, so columns were "
    "matched by their headers and values. Check each one."
)


async def _ranked(unknown: list[dict]) -> None:
    vectors = await asyncio.wait_for(
        embed(
            [embeddings.DOCUMENT_PREFIX + f"{f.label}: {f.about}" for f in FIELDS]
            + [
                embeddings.QUERY_PREFIX + " | ".join([c["header"], *c["samples"]])
                for c in unknown
            ]
        ),
        MODEL_WAIT,
    )
    fields, queries = vectors[: len(FIELDS)], vectors[len(FIELDS) :]
    for c, q in zip(unknown, queries, strict=True):
        ranked = sorted(
            (
                (sum(a * b for a, b in zip(q, v, strict=True)), f)
                for f, v in zip(FIELDS, fields, strict=True)
            ),
            key=lambda x: -x[0],
        )
        c["suggestions"] = [
            _option(
                f, "close in meaning to its header and values", "model", round(s, 3)
            )
            for s, f in ranked[:SUGGESTIONS]
        ]


async def suggest(cols: list[dict], remembered: dict[str, str]) -> dict:
    """Per column: the remembered field (exact header, then the same header with
    spacing and case ignored), else suggestions from header words and value
    shapes, else the embedding model's ranking when it answers. Returns the columns
    and, when the model was needed and could not help, a note in words."""
    by_norm = {norm(h): f for h, f in remembered.items()}
    out, unknown = [], []
    for c in cols:
        field = remembered.get(c["header"]) or by_norm.get(norm(c["header"]))
        out.append(
            c | {"remembered": field, "suggestions": [] if field else matched(c)}
        )
        if not field and not out[-1]["suggestions"]:
            unknown.append(out[-1])
    note = None
    if unknown:
        try:
            await _ranked(unknown)
        except ValueError:
            note = NO_MODEL
        except (TimeoutError, OSError, httpx2.HTTPError):
            note = MODEL_DOWN
    return {"columns": out, "note": note}


def check(sheet: Sheet, key_column: str, mapping: dict[str, str]) -> None:
    """A confirmed mapping names every header once, only known fields, and the key."""
    headers = [h for i, h in enumerate(sheet.head) if i + 1 not in sheet.dropped]
    if len(set(headers)) != len(headers) or "" in headers:
        raise ValueError(
            f"every column needs a distinct, non-empty header in row {sheet.header}"
        )
    if set(mapping) != set(headers):
        raise ValueError("confirm a field for every column")
    if bad := set(mapping.values()) - FIELD_KEYS:
        raise ValueError(f"unknown fields: {', '.join(sorted(bad))}")
    if mapping.get(key_column) != "key":
        raise ValueError("the key column must be mapped to Row identifier")


def _index(sheet: Sheet, key_column: str) -> tuple[dict[str, int], dict[int, str]]:
    """Key to row number for valid rows, and row number to (key, reason) for invalid
    ones. A blank key, or a key on more than one row, makes every such row invalid."""
    k = list(sheet.head).index(key_column)
    keys = {n: sheet.rows[n - 1][k].strip() for n in sheet.body}
    counts = Counter(keys.values())
    valid, invalid = {}, {}
    for n, key in keys.items():
        if not key:
            invalid[n] = (key, "blank_key")
        elif counts[key] > 1:
            invalid[n] = (key, "duplicate_key")
        else:
            valid[key] = n
    return valid, invalid


def diff(
    current: Sheet,
    previous: Sheet | None,
    key_column: str,
    mapping: dict[str, str],
) -> list[dict]:
    """Every row of both versions as new, changed, unchanged, missing or invalid.
    Only columns mapped to something other than `ignore` are compared, by header,
    so a reordered column is not a change."""
    now, bad = _index(current, key_column)
    rows = [
        {"row": n, "key": key or None, "status": "invalid", "why": why}
        for n, (key, why) in bad.items()
    ]
    compared = [h for h, f in mapping.items() if f not in ("ignore", "key")]
    if previous is None or key_column not in previous.head:
        before, prev_cols = {}, {}
    else:
        before, _ = _index(previous, key_column)
        prev_cols = {h: i for i, h in enumerate(previous.head) if h}
    cur_cols = {h: i for i, h in enumerate(current.head) if h}
    for key, n in now.items():
        if key not in before:
            rows.append({"row": n, "key": key, "status": "new"})
            continue
        old = previous.rows[before[key] - 1]
        new = current.rows[n - 1]
        changed = [
            h
            for h in compared
            if new[cur_cols[h]].strip()
            != (old[prev_cols[h]].strip() if h in prev_cols else "")
        ]
        rows.append(
            {
                "row": n,
                "key": key,
                "status": "changed" if changed else "unchanged",
                "previous_row": before[key],
                "changed": changed,
            }
        )
    # A key still in the sheet, even on an invalid row, is not missing.
    present = set(now) | {key for key, _ in bad.values()}
    for key, n in before.items():
        if key not in present:
            rows.append(
                {"row": None, "key": key, "status": "missing", "previous_row": n}
            )
    return sorted(rows, key=lambda r: (r["row"] is None, r["row"] or r["previous_row"]))


def counts(rows: list[dict]) -> dict[str, int]:
    found = Counter(r["status"] for r in rows)
    return {s: found[s] for s in ("new", "changed", "unchanged", "missing", "invalid")}
