"""Score extract_sheet proposals on acceptance/data/messy_people.csv against the
answers below, per field. Read-only against the configured database:

    uv run python -m acceptance.eval_sheet <extract_sheet run id over messy_people.csv>

A field counts as correct when a proposal pinned to that row's cells has the
expected value; extra counts proposals where the row states no such field. Places
are checked as "the quoted place contains the expected name", dates as the exact
interval. Sex is expected only where the row says it in words (ذكر, أنثى, male,
female); a proposal inferred from grammar or a first name counts as extra.
"""

import asyncio
import sys
from collections import Counter

from sqlalchemy import select

import stk.extensions as ext
from stk.app import create_app
from stk.ledger.models import Payload, Run, Statement

POSITION = {
    "arrested_at",
    "detained_at",
    "detention_branch",
    "transferred_to",
    "seen_at",
    "last_seen",
}
# row: name, sex, age, occupation, place (any position or event quote containing
# it), event kind, date interval of the row's main claim
ANSWERS = {
    2: ("سامر خليل النجار", "male", 29, "نجار", "حرستا", None, ("2013-05-12",) * 2),
    3: (
        "Rania Haddad",
        "female",
        41,
        "teacher",
        "Branch 999",
        None,
        ("2014-03-15",) * 2,
    ),
    4: (
        "يوسف عمر الحلبي",
        None,
        17,
        "طالب",
        "دوما",
        "killed",
        ("2013-03-01", "2013-03-31"),
    ),
    5: (
        "Khaled M.",
        "male",
        30,
        None,
        "Central Prison K",
        "released",
        ("2015-01-01", "2015-12-31"),
    ),
    6: (
        "ام سليم",
        None,
        60,
        "ربة منزل",
        "بابا عمرو",
        "displaced",
        ("2012-02-01", "2012-02-29"),
    ),
    7: ("Nour al-Din Sabbagh", None, 35, "pharmacist", "Zabadani", "missing", None),
    8: (
        "ليلى فؤاد العلي",
        "female",
        None,
        "ممرضة",
        "سجن ك المركزي",
        None,
        ("2014-07-03",) * 2,
    ),
    9: (
        "Omar Taha",
        "male",
        52,
        "engineer",
        "Douma",
        None,
        ("2013-07-01", "2013-07-31"),
    ),
    10: (
        "محمد",
        None,
        None,
        "عامل بناء",
        "جرمانا",
        "missing",
        ("2012-10-01", "2012-10-31"),
    ),
    11: ("Huda S.", "female", 23, None, "Homs", "wounded", ("2012-02-20",) * 2),
    12: (
        "عبد الله ياسين الكردي",
        "male",
        44,
        "سائق تكسي",
        "الباب",
        "defected",
        ("2012-01-01", "2012-12-31"),
    ),
    13: (
        "Salma Darwish",
        None,
        19,
        "student",
        "999",
        None,
        ("2013-05-01", "2013-05-31"),
    ),
    14: (
        "طارق نبيل حداد",
        None,
        38,
        "محامي",
        "سجن ك المركزي",
        None,
        ("2013-11-12",) * 2,
    ),
    15: ("Faten", None, None, "nurse", "Aleppo", None, ("2016-01-01", "2016-12-31")),
    16: ("حسن أديب قاسم", "male", 60, "مزارع", None, "missing", None),
}
FIELDS = ("name", "sex", "age", "occupation", "place", "event", "date")
# Position props the row's words support; any other position proposal is unsupported.
SUPPORTED = {
    2: {"arrested_at", "detained_at", "transferred_to"},
    3: {"last_seen", "seen_at"},
    5: {"detained_at"},
    8: {"last_seen", "seen_at"},
    9: {"arrested_at"},
    13: {"detained_at", "detention_branch"},
    14: {"transferred_to", "detained_at", "detention_branch"},
    15: {"seen_at", "last_seen"},
}


def positions(rows) -> Counter:
    out = Counter()
    for n, got in rows.items():
        for prop, *_ in got:
            if prop in POSITION:
                out["supported" if prop in SUPPORTED.get(n, ()) else "unsupported"] += 1
    out["rows_found"] = sum(
        any(p in SUPPORTED[n] for p, *_ in rows.get(n, [])) for n in SUPPORTED
    )
    return out


def score(rows: dict[int, list[tuple[str, dict, tuple]]]) -> dict[str, Counter]:
    out = {f: Counter() for f in FIELDS}
    for n, want in ANSWERS.items():
        got = {f: [] for f in FIELDS}
        for prop, value, when in rows.get(n, []):
            if prop in ("name", "occupation"):
                got[prop].append(value.get("text", "").strip())
            elif prop in ("sex", "age"):
                got[prop].append(value.get("norm"))
            if prop == "event":
                got["event"].append(value.get("kind"))
            if prop in POSITION or prop == "event":
                got["place"].append(value.get("text", ""))
                if when:
                    got["date"].append(when)
        for field, expected in zip(FIELDS, want, strict=True):
            seen = got[field]
            if expected is None:
                out[field]["extra"] += bool(seen) and field != "place"
                continue
            if field in ("place", "occupation"):
                hit = any(expected in s for s in seen)
            else:
                hit = (tuple(expected) if field == "date" else expected) in seen
            out[field]["correct" if hit else "missed"] += 1
            out[field]["wrong"] += not hit and bool(seen)
    return out


async def main(run_id: str) -> None:
    create_app()
    async with ext.async_session_factory() as s:
        q = (
            select(
                Statement.prop,
                Statement.span,
                Payload.value,
                Statement.valid_from,
                Statement.valid_to,
            )
            .join(Run, Run.id == Statement.run_id)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(
                Run.id == run_id,
                Statement.span.is_not(None),
            )
        )
        rows: dict[int, list] = {}
        for prop, span, value, lo, hi in (await s.execute(q)).all():
            if not span:  # located_at proposals hang off claims, not cells
                continue
            when = (lo.isoformat(), hi.isoformat()) if lo else None
            rows.setdefault(span["row"], []).append((prop, value, when))
    for field, c in score(rows).items():
        total = c["correct"] + c["missed"]
        print(
            f"{field:11} {c['correct']:2}/{total:<2} correct, {c['wrong']} wrong value, {c['extra']} extra"
        )
    p = positions(rows)
    print(
        f"positions   {p['supported']} supported, {p['unsupported']} unsupported, "
        f"{p['rows_found']}/{len(SUPPORTED)} rows with a supported one"
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
