"""Sheet bytes to rows of cell strings. Pure and deterministic: span validation and
every viewer parse the same bytes the same way, so a cell span always names the
same text. Rows, columns and sheets are numbered from 1, as a spreadsheet shows
them, so a reviewer can find the cell in the original file."""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache

CSV = "text/csv"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIMES = frozenset({CSV, XLSX})
MAX_ROWS, MAX_COLUMNS = 20000, 200
_BY_EXTENSION = {".csv": CSV, ".xlsx": XLSX}


TITLE_ROWS = 10  # the header is looked for in this many rows from the top


@dataclass(frozen=True)
class Sheet:
    name: str
    rows: tuple[tuple[str, ...], ...]  # padded to one width
    header: int = 1  # file row number of the header; rows above it are titles

    @property
    def head(self) -> tuple[str, ...]:
        return self.rows[self.header - 1] if self.rows else ()

    @property
    def body(self) -> range:
        """File row numbers below the header."""
        return range(self.header + 1, len(self.rows) + 1)

    @property
    def dropped(self) -> tuple[int, ...]:
        """Columns (from 1) with no header and nothing below it: not offered for
        mapping and never read."""
        return tuple(
            i + 1
            for i, h in enumerate(self.head)
            if not h.strip() and not any(self.rows[n - 1][i].strip() for n in self.body)
        )


def find_header(rows: list[list[str]]) -> int:
    """The first row, within TITLE_ROWS, that looks like a header: at least two
    filled cells and at least 60% as many as the fullest row near the top, all
    distinct, not all numbers. Title rows above it (one merged cell, a date line)
    fail the count. Deterministic, so every reader of the bytes agrees."""
    top = rows[: TITLE_ROWS * 2]
    filled = [[c.strip() for c in r if c.strip()] for r in top]
    widest = max((len(f) for f in filled), default=0)
    for n, cells in enumerate(filled[:TITLE_ROWS], 1):
        if (
            len(cells) >= max(2, 0.6 * widest)
            and len(set(cells)) == len(cells)
            and not all(c.replace(".", "").isdigit() for c in cells)
        ):
            return n
    return 1


def mime_for(filename: str | None, mime: str) -> str:
    """Browsers send CSV as text/csv, application/vnd.ms-excel or nothing; the
    extension decides for the two sheet formats."""
    name = (filename or "").lower()
    return next((m for ext, m in _BY_EXTENSION.items() if name.endswith(ext)), mime)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time() else value.isoformat()
    if isinstance(value, date | time):
        return value.isoformat()
    return str(value)


def _bounded(name: str, rows: list[list[str]]) -> Sheet:
    while rows and not any(rows[-1]):
        rows.pop()
    width = max((len(r) for r in rows), default=0)
    while width and not any(len(r) >= width and r[width - 1] for r in rows):
        width -= 1
    if len(rows) > MAX_ROWS or width > MAX_COLUMNS:
        raise ValueError(
            f"sheet {name!r} has {len(rows)} rows and {width} columns; "
            f"the limit is {MAX_ROWS} rows and {MAX_COLUMNS} columns"
        )
    padded = [(r + [""] * width)[:width] for r in rows]
    return Sheet(name, tuple(tuple(r) for r in padded), find_header(padded))


def _csv(data: bytes) -> list[Sheet]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "the CSV is not UTF-8; save it as CSV UTF-8 or as XLSX"
        ) from exc
    # The header line decides the delimiter: csv.Sniffer guesses differently on
    # small samples, and the guess must be the same on every read.
    header = text.split("\n", 1)[0]
    delimiter = max(",;\t", key=header.count)
    rows = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [_bounded("Sheet1", list(rows))]


def _xlsx(data: bytes) -> list[Sheet]:
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ValueError(f"not a readable XLSX workbook: {exc}") from exc
    try:
        return [
            _bounded(ws.title, [[_text(v) for v in row] for row in ws.values])
            for ws in book.worksheets
        ]
    finally:
        book.close()


@lru_cache(maxsize=32)
def read(data: bytes, mime: str) -> tuple[Sheet, ...]:
    if mime == CSV:
        return tuple(_csv(data))
    if mime == XLSX:
        return tuple(_xlsx(data))
    raise ValueError(f"not a sheet: {mime}")


def lengths(data: bytes, mime: str) -> list[list[list[int]]]:
    """Per sheet, per row, the length of each cell: what span validation needs."""
    return [[[len(c) for c in row] for row in s.rows] for s in read(data, mime)]


def cell(sheets: tuple[Sheet, ...], span: dict) -> str:
    """The text a cell span names (the whole cell, or its start:end part)."""
    text = sheets[span["sheet"] - 1].rows[span["row"] - 1][span["column"] - 1]
    if "start" in span:
        return text[span["start"] : span["end"]]
    return text


def letter(column: int) -> str:
    """1 -> A, 27 -> AA, as a spreadsheet labels columns."""
    out = ""
    while column:
        column, rest = divmod(column - 1, 26)
        out = chr(65 + rest) + out
    return out
