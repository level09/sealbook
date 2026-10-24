"""Vague dates as day intervals. Overlap is a strength band, never a probability."""

from datetime import date

from stk.ledger.contracts import Prop


def interval(
    valid_from: date | None, valid_to: date | None, prop: Prop
) -> tuple[date, date] | None:
    if valid_from is None and valid_to is None:
        return None
    start = valid_from or valid_to
    if prop is Prop.LAST_SEEN:
        return start, start
    return start, valid_to or start


def overlap(a: tuple[date, date], b: tuple[date, date]) -> float:
    days = (min(a[1], b[1]) - max(a[0], b[0])).days + 1
    if days <= 0:
        return 0.0
    return days / max((x[1] - x[0]).days + 1 for x in (a, b))
