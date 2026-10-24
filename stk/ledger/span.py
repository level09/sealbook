"""Validate source spans against blob metadata. Pure: no I/O."""

from __future__ import annotations


def _range(start, end, limit, kind):
    if type(start) is not int or type(end) is not int:
        return False, f"invalid_{kind}_range"
    if start >= end:
        return False, f"{kind}_range_empty_or_reversed"
    if start < 0 or end > limit:
        return False, f"{kind}_range_out_of_bounds"
    return True, ""


def validate(span: dict, meta: dict) -> tuple[bool, str]:
    if not isinstance(span, dict):
        return False, "invalid_span"
    keys = set(span)
    if keys == {"page", "bbox"}:
        pages = meta["pages"]
        if pages is None:
            return False, "pages_required"
        page, bbox = span["page"], span["bbox"]
        if type(page) is not int:
            return False, "invalid_page"
        if not 1 <= page <= len(pages):
            return False, "page_out_of_range"
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or any(type(n) is not int for n in bbox)
        ):
            return False, "invalid_bbox"
        x0, y0, x1, y1 = bbox
        if x0 >= x1 or y0 >= y1:
            return False, "bbox_empty_or_reversed"
        width, height = pages[page - 1]
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            return False, "bbox_out_of_bounds"
        return True, ""
    if keys == {"start", "end"}:
        if meta["text_len"] is None:
            return False, "text_len_required"
        return _range(span["start"], span["end"], meta["text_len"], "char")
    if keys == {"ms"}:
        if meta["duration_ms"] is None:
            return False, "duration_ms_required"
        bounds = span["ms"]
        if not isinstance(bounds, list) or len(bounds) != 2:
            return False, "invalid_ms_range"
        return _range(*bounds, meta["duration_ms"], "ms")
    if keys in (CELL, CELL | {"start", "end"}):
        return _cell(span, meta.get("sheets"))
    return False, "invalid_span_keys"


CELL = frozenset({"sheet", "row", "column"})


def _cell(span: dict, sheets) -> tuple[bool, str]:
    """A spreadsheet cell, numbered from 1, optionally narrowed to characters
    start:end of the cell text. `sheets` is per sheet, per row, each cell's length."""
    if sheets is None:
        return False, "sheets_required"
    sheet, row, column = span["sheet"], span["row"], span["column"]
    if any(type(n) is not int for n in (sheet, row, column)):
        return False, "invalid_cell"
    if not 1 <= sheet <= len(sheets):
        return False, "sheet_out_of_range"
    rows = sheets[sheet - 1]
    if not 1 <= row <= len(rows) or not 1 <= column <= len(rows[row - 1]):
        return False, "cell_out_of_range"
    if "start" not in span:
        return True, ""
    return _range(span["start"], span["end"], rows[row - 1][column - 1], "char")
