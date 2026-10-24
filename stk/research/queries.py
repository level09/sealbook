"""Proposed web queries for a person, place, incident or source. Built only from
values this reader may see that came from ordinary (not sensitive) sources in cases
that allow research, at accepted or corroborated tier. Gazetteer names are public
reference data. The analyst edits the list before anything is sent.

A proposal is {"text", "kinds", "why": [{"label", "value", "statement"?}],
"people": [person refs whose values it used]}; "people" is what lets erasure find
the logged query later (stk.people.data.erase)."""

from __future__ import annotations

import re
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stk.ledger.contracts import Prop, Tier
from stk.ledger.models import Acquisition, Statement, StatementTier
from stk.ledger.write import current_clause
from stk.monitor.data import LOCATED, statement_rows
from stk.monitor.filter import statement_clause
from stk.people import data as people
from stk.places.models import Place
from stk.stories import data as stories

KINDS = ["web", "images", "videos", "youtube"]
MAX = 8
ARABIC = re.compile(r"[؀-ۿ]")
MONTHS = (
    "January February March April May June July August September October November "
    "December"
).split()


def script(text: str) -> str:
    return "ar" if ARABIC.search(text) else "en"


def _q(text: str, why: list[dict], people_refs=()) -> dict:
    return {
        "text": " ".join(text.split()),
        "kinds": list(KINDS),
        "why": why,
        "people": sorted(set(people_refs)),
    }


async def _investigations(db: AsyncSession, shas: set[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    if shas:
        for sha, inv in await db.execute(
            select(
                Acquisition.blob_sha256, Acquisition.context["investigation"].astext
            ).where(Acquisition.blob_sha256.in_(shas))
        ):
            out.setdefault(sha, set()).add(inv or "")
    return out


async def usable(db: AsyncSession, rows: list[dict], blocked: set[str]) -> list[dict]:
    """Rows whose value may leave the building: shown to this reader, not erased,
    from an ordinary source, no acquisition of it in a blocked case."""
    shas = {r["doc"]["sha256"] for r in rows if r.get("doc")}
    cases = await _investigations(db, shas)
    return [
        r | {"cases": sorted(cases.get(r["doc"]["sha256"], set()) - {""})}
        for r in rows
        if r.get("doc")
        and not r["doc"]["sensitive"]
        and not r["masked"]
        and not r["erased"]
        and r["display"]
        and r["tier"] in (Tier.ACCEPTED, Tier.CORROBORATED)
        and not cases.get(r["doc"]["sha256"], set()) & blocked
    ]


def _why(r: dict, label: str) -> dict:
    return {
        "label": label,
        "value": r["display"],
        "statement": r["id"],
        "cases": r["cases"],
    }


def _from_rows(rows: list[dict], person_of) -> list[dict]:
    """Names alone, then a name with a place of the same script, then with its most
    frequent year."""
    names = [r for r in rows if r["prop"] == Prop.NAME]
    places = [r for r in rows if r["prop"] in LOCATED]
    years = Counter(r["from"][:4] for r in rows if r["from"])
    out, seen = [], set()

    def add(q):
        if q["text"].lower() not in seen:
            seen.add(q["text"].lower())
            out.append(q)

    unique = list({r["display"]: r for r in names}.values())
    for n in unique:
        add(_q(f'"{n["display"]}"', [_why(n, "Name")], [person_of(n)]))
    for n in unique:
        same = [p for p in places if script(p["display"]) == script(n["display"])]
        if same:
            p = Counter(x["display"] for x in same).most_common(1)[0][0]
            pr = next(x for x in same if x["display"] == p)
            add(
                _q(
                    f'"{n["display"]}" {p}',
                    [_why(n, "Name"), _why(pr, "Place")],
                    [person_of(n), person_of(pr)],
                )
            )
        if years:
            year = years.most_common(1)[0][0]
            add(
                _q(
                    f'"{n["display"]}" {year}',
                    [_why(n, "Name"), {"label": "Year", "value": year}],
                    [person_of(n)],
                )
            )
    return out[:MAX]


async def for_person(db: AsyncSession, ref: str, blocked: set[str]) -> list[dict]:
    found = await people.person(db, ref, "factual")
    if found is None:
        raise LookupError(ref)
    rows = await usable(db, [r for v in found["facts"].values() for r in v], blocked)
    canonical = f"person:{found['person']}"
    return _from_rows(rows, lambda r: canonical)


async def for_source(db: AsyncSession, sha: str, blocked: set[str]) -> list[dict]:
    rows = await statement_rows(
        db,
        Statement.span_blob == sha,
        Statement.prop.in_([Prop.NAME, *LOCATED]),
        StatementTier.tier.in_([Tier.ACCEPTED, Tier.CORROBORATED]),
        current_clause(),
        limit=500,
    )
    groups, _ = await people.identity(db)

    def person_of(r):
        if not r["subject"].startswith("person:"):
            return None
        members = groups.get(people.pid(r["subject"]))
        return f"person:{min(members)}" if members else r["subject"]

    out = _from_rows(await usable(db, rows, blocked), person_of)
    for q in out:
        q["people"] = [p for p in q["people"] if p]
    return out


def _place_names(p: Place) -> list[tuple[str, str]]:
    return [(n, lang) for n, lang in ((p.name_ar, "ar"), (p.name_en, "en")) if n]


async def for_place(db: AsyncSession, place_id: str) -> list[dict]:
    place = await db.get(Place, uuid.UUID(place_id))
    if place is None:
        raise LookupError(place_id)
    gov = await db.get(Place, place.governorate_id) if place.governorate_id else None
    out = []
    for name, lang in _place_names(place):
        why = [{"label": "Gazetteer name", "value": name}]
        out.append(_q(f'"{name}"', why))
        g = {lg: n for n, lg in _place_names(gov)}.get(lang) if gov else None
        if g and g != name:
            out.append(
                _q(f'"{name}" {g}', why + [{"label": "Governorate", "value": g}])
            )
    return out[:MAX]


async def for_incident(db: AsyncSession, ref: str, blocked: set[str]) -> list[dict]:
    groups, _ = await people.identity(db)
    found = await stories.incidents(db, groups, [ref])
    if not found:
        raise LookupError(ref)
    inc = found[0]
    out = []
    # The date comes from the incident's supporting claims: only when every one of
    # them rests on an ordinary source outside blocked cases.
    shas = set(
        (
            await db.execute(
                select(Statement.span_blob).where(
                    Statement.id.in_([uuid.UUID(s) for s in inc["statements"]])
                )
            )
        ).scalars()
    )
    acqs = (
        await db.execute(
            select(
                Acquisition.sensitivity, Acquisition.context["investigation"].astext
            ).where(Acquisition.blob_sha256.in_(shas))
        )
    ).all()
    dated = bool(acqs) and all(
        s != "sensitive" and (inv or "") not in blocked for s, inv in acqs
    )
    when = inc["window"][0] if inc["window"] and dated else None
    for place in inc["places"]:
        for lang in ("ar", "en"):
            if not place.get(lang):
                continue
            why = [{"label": "Incident place", "value": place[lang]}]
            text = f'"{place[lang]}"'
            if when:
                year, month = when[:4], int(when[5:7])
                stamp = year if lang == "ar" else f"{MONTHS[month - 1]} {year}"
                why.append({"label": "Incident date", "value": stamp})
                text = f"{text} {stamp}"
            out.append(_q(text, why))
    return out[:MAX]


async def for_case(db: AsyncSession, slug: str, blocked: set[str]) -> list[dict]:
    """Names in the case with a place or year, from the same shareable claims as a
    source's proposals; nothing from a blocked case."""
    if slug in blocked:
        return []
    rows = await statement_rows(
        db,
        statement_clause({"investigation": slug, "time": None}),
        Statement.prop.in_([Prop.NAME, *LOCATED]),
        StatementTier.tier.in_([Tier.ACCEPTED, Tier.CORROBORATED]),
        current_clause(),
        limit=500,
    )
    groups, _ = await people.identity(db)

    def person_of(r):
        if not r["subject"].startswith("person:"):
            return None
        members = groups.get(people.pid(r["subject"]))
        return f"person:{min(members)}" if members else r["subject"]

    out = _from_rows(await usable(db, rows, blocked), person_of)
    for q in out:
        q["people"] = [p for p in q["people"] if p]
    return out


async def propose(db: AsyncSession, scope: str, blocked: set[str]) -> list[dict]:
    kind, _, ref = scope.partition(":")
    if kind == "case":
        return await for_case(db, ref, blocked)
    if kind == "person":
        return await for_person(db, ref, blocked)
    if kind == "place":
        return await for_place(db, ref)
    if kind == "incident":
        return await for_incident(db, scope, blocked)
    if kind == "blob":
        return await for_source(db, ref, blocked)
    raise ValueError(
        f"research scope must be case, person, place, incident or blob: {scope}"
    )
