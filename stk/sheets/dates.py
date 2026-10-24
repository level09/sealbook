"""Messy date text to the interval it covers. Deterministic, so the model only
quotes the date and this table decides it: a day is one day, a month or a year is
its full span (spec call 6), "unknown" and anything unreadable are undated.
Numeric dates are read day first, as Syrian records write them, unless the second
number cannot be a month: 15/3/2013 and 3/15/2013 are both 15 March 2013."""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "0123456789" * 2)
_ARABIC_MARKS = re.compile(r"[ً-ٰٟـ]")
_HAMZA = str.maketrans("أإآٱى", "ااااي")

# Levantine and Egyptian/Gulf month names after _clean (hamza folded), and English.
MONTHS = {
    "كانون الثاني": 1,
    "شباط": 2,
    "اذار": 3,
    "نيسان": 4,
    "ايار": 5,
    "حزيران": 6,
    "تموز": 7,
    "اب": 8,
    "ايلول": 9,
    "تشرين الاول": 10,
    "تشرين الثاني": 11,
    "كانون الاول": 12,
    "يناير": 1,
    "فبراير": 2,
    "مارس": 3,
    "ابريل": 4,
    "مايو": 5,
    "يونيو": 6,
    "يوليو": 7,
    "اغسطس": 8,
    "سبتمبر": 9,
    "اكتوبر": 10,
    "نوفمبر": 11,
    "ديسمبر": 12,
}
for _i, _name in enumerate(
    "january february march april may june july august september october "
    "november december".split(),
    1,
):
    MONTHS[_name] = MONTHS[_name[:3]] = _i
MONTHS["sept"] = 9

UNKNOWN = {
    "",
    "?",
    "-",
    "unknown",
    "not known",
    "n/a",
    "na",
    "none",
    "غير معروف",
    "غير معلوم",
    "مجهول",
    "لا يعرف",
    "لا يوجد",
}
# Words around a date that do not change the interval it names.
_FILLER = re.compile(
    r"^(?:منذ|حوالي|حوالى|تقريبا|في|بتاريخ|تاريخ|عام|سنة|شهر|since|around|about|"
    r"circa|c\.|in|on|approx\.?|approximately)\s+|\s+(?:تقريبا|م)$"
)
_MONTH_WORD = "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True))
_YEAR = r"(19\d\d|20\d\d)"


def _clean(text: str) -> str:
    text = _ARABIC_MARKS.sub("", text.translate(_DIGITS)).translate(_HAMZA)
    text = re.sub(r"\s+", " ", text.replace("،", ",").strip().lower())
    while (shorter := _FILLER.sub("", text)) != text:
        text = shorter.strip()
    return text


def _day(y: int, m: int, d: int) -> tuple[date, date] | None:
    try:
        return date(y, m, d), date(y, m, d)
    except ValueError:
        return None


def _month(y: int, m: int) -> tuple[date, date] | None:
    if not 1 <= m <= 12:
        return None
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


def unknown(text: str | None) -> bool:
    return _clean(text or "") in UNKNOWN


def interval(text: str | None) -> tuple[date, date] | None:
    """The first and last day `text` can mean, or None when it names no date."""
    t = _clean(text or "")
    if t in UNKNOWN:
        return None
    if m := re.fullmatch(_YEAR + r"[-/.](\d{1,2})[-/.](\d{1,2})", t):
        return _day(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.]" + _YEAR, t):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        return _day(y, b, a) if b <= 12 else _day(y, a, b)
    if m := re.fullmatch(_YEAR + r"[-/.](\d{1,2})", t):
        return _month(int(m[1]), int(m[2]))
    if m := re.fullmatch(r"(\d{1,2})[-/.]" + _YEAR, t):
        return _month(int(m[2]), int(m[1]))
    if m := re.fullmatch(_YEAR, t):
        y = int(m[1])
        return date(y, 1, 1), date(y, 12, 31)
    if m := re.fullmatch(_YEAR + r"\s*[-/]\s*" + _YEAR, t):
        a, b = int(m[1]), int(m[2])
        return (date(a, 1, 1), date(b, 12, 31)) if a <= b else None
    if m := re.fullmatch(rf"(\d{{1,2}})\s+({_MONTH_WORD}),?\s+{_YEAR}", t):
        return _day(int(m[3]), MONTHS[m[2]], int(m[1]))
    if m := re.fullmatch(rf"({_MONTH_WORD})\s+(\d{{1,2}}),?\s+{_YEAR}", t):
        return _day(int(m[3]), MONTHS[m[1]], int(m[2]))
    if m := re.fullmatch(rf"({_MONTH_WORD}),?\s+{_YEAR}", t):
        return _month(int(m[2]), MONTHS[m[1]])
    return None
