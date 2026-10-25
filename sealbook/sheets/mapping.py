"""Which column holds what, and what changed since the last version. Mapping is a
hint for the extraction run, never a write: a remembered choice is reused as is,
an unfamiliar column gets ranked suggestions from the local embedding model, and a
person confirms every column before anything is read."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sealbook.discovery import embeddings
from sealbook.settings import Config
from sealbook.sheets.parse import Sheet


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    about: str  # what the embedding compares a column against


FIELDS = (
    Field(
        "key", "Row identifier", "record number, row id, serial, رقم، الرقم التسلسلي"
    ),
    Field(
        "person",
        "Person, free text",
        "a person described in one cell: name with sex, age and job, بيانات الشخص",
    ),
    Field("name", "Name", "full name of the person, الاسم الكامل"),
    Field("sex", "Sex", "male or female, ذكر أو أنثى، الجنس"),
    Field("age", "Age", "age in years, العمر"),
    Field("occupation", "Occupation", "job or profession, المهنة، العمل"),
    Field("father_name", "Father's name", "name of the father, اسم الأب"),
    Field("mother_name", "Mother's name", "name of the mother, اسم الأم"),
    Field("birth_date", "Birth date", "date of birth, تاريخ الولادة"),
    Field(
        "place_of_birth", "Place of birth", "where the person was born, مكان الولادة"
    ),
    Field(
        "event",
        "What happened",
        "arrest, detention, death, disappearance, last seen, ماذا حدث، معلومات اضافية",
    ),
    Field("place", "Place", "town, city, area or facility, الموقع، المكان، المدينة"),
    Field("date", "Date", "when it happened, التاريخ"),
    Field("ignore", "Do not read", "not about a person, notes for the sheet keeper"),
)
FIELD_KEYS = frozenset(f.key for f in FIELDS)
SAMPLES = 5
SUGGESTIONS = 3


def norm(header: str) -> str:
    return " ".join(header.split()).casefold()


def columns(sheet: Sheet) -> list[dict]:
    """Header and up to five non-empty sample values per column, leaving out
    columns with neither a header nor values."""
    body = [sheet.rows[n - 1] for n in sheet.body]
    out = []
    for i, name in enumerate(sheet.head):
        if i + 1 in sheet.dropped:
            continue
        samples = [r[i].strip() for r in body if r[i].strip()][:SAMPLES]
        out.append({"column": i + 1, "header": name, "samples": samples})
    return out


async def embed(texts: list[str]) -> list[list[float]]:
    """The boundary tests mock. Suggestions are not stored, so no space is bound."""
    if not Config.EMBEDDING_MODEL:
        raise ValueError("EMBEDDING_MODEL is not configured; map each column by hand")
    values, *_ = await embeddings._post(texts, [], Config.EMBEDDING_MODEL, None)
    return values


async def suggest(cols: list[dict], remembered: dict[str, str]) -> list[dict]:
    """Per column: the remembered field (exact header, then the same header with
    spacing and case ignored), or the top ranked fields with their cosine score."""
    by_norm = {norm(h): f for h, f in remembered.items()}
    out, unknown = [], []
    for c in cols:
        field = remembered.get(c["header"]) or by_norm.get(norm(c["header"]))
        out.append(c | {"remembered": field, "suggestions": []})
        if not field:
            unknown.append(out[-1])
    if not unknown:
        return out
    vectors = await embed(
        [embeddings.DOCUMENT_PREFIX + f"{f.label}: {f.about}" for f in FIELDS]
        + [
            embeddings.QUERY_PREFIX + " | ".join([c["header"], *c["samples"]])
            for c in unknown
        ]
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
            {"field": f.key, "label": f.label, "score": round(s, 3)}
            for s, f in ranked[:SUGGESTIONS]
        ]
    return out


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
