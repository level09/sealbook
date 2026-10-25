"""A suggested place on each unresolved name, with its reason. Rule-based and
computed on request: one gazetteer candidate; the same spelling confirmed in a case
the reader can open; several candidates, with the one the surrounding words favour;
then the place finder agent's approximate place. Nothing here writes or accepts: a
person confirms through the signed confirm flow."""

from __future__ import annotations

import csv
import io
import uuid
from collections import defaultdict

from sqlalchemy import func, literal, select

from sealbook import blobs
from sealbook.ledger.models import Blob, Payload, Statement
from sealbook.places import normalise, resolve
from sealbook.places.models import AdminArea, Place, PlaceName
from sealbook.settings import Config

AROUND = 160  # characters of context either side of a claim's span
QUOTE = 60  # at most this many characters either side of the words in a quote
STOPS = (".", "!", "?", "؟", "\n")
PREFIXES = ("محافظة ",)
SUFFIXES = (" governorate",)


def window(text: str, span: dict | None) -> str | None:
    """The words around a claim's span in its text: the passage a reader would see."""
    if not span or "start" not in span:
        return None
    a, b = span["start"], span["end"]
    return text[max(0, a - AROUND) : b + AROUND].strip() or None


def cell_row(text: str, span: dict) -> str | None:
    """A sheet claim's whole row, so the other columns count as surrounding words."""
    rows = list(csv.reader(io.StringIO(text)))
    i = span.get("row", 0) - 1
    return ", ".join(c for c in rows[i] if c.strip()) if 0 <= i < len(rows) else None


async def passages(db, ids) -> dict[uuid.UUID, str]:
    """Each claim's passage from its text source; images, audio and other media
    have none."""
    rows = (
        await db.execute(
            select(Statement.id, Statement.span, Blob)
            .join(Blob, Blob.sha256 == Statement.span_blob)
            .where(Statement.id.in_(list(ids)), Blob.mime.startswith("text/"))
        )
    ).all()
    out, texts = {}, {}
    for sid, span, blob in rows:
        if blob.sha256 not in texts:
            if not blobs.stored(blob, Config.BLOB_DIR):
                continue
            texts[blob.sha256] = blobs.read(blob, Config.BLOB_DIR).decode(
                errors="replace"
            )
        text = texts[blob.sha256]
        found = (
            cell_row(text, span)
            if blob.mime.startswith("text/csv") and span and "row" in span
            else window(text, span)
        )
        if found:
            out[sid] = found
    return out


def quote(text: str, term: str) -> str | None:
    """The sentence of `text` holding the first occurrence of `term`, any case,
    cut to whole words within QUOTE characters either side when it runs longer."""
    i = text.lower().find(term.lower())
    if i < 0:
        return None
    j = i + len(term)
    lo = max((text.rfind(s, 0, i) + 1 for s in STOPS), default=0)
    hi = min((k + 1 for s in STOPS if (k := text.find(s, j)) >= 0), default=len(text))
    if i - lo > QUOTE:
        lo = text.find(" ", i - QUOTE, i) + 1 or i
    if hi - j > QUOTE:
        hi = text.rfind(" ", j, j + QUOTE)
        hi = hi if hi > 0 else j
    return text[lo:hi].strip()


def area_terms(place: Place, areas: dict) -> list[str]:
    """The names of the governorate a place sits in, bare of "Governorate"."""
    out = []
    for n in (
        *(
            (place.governorate.name_ar, place.governorate.name_en)
            if place.governorate
            else ()
        ),
        *areas.get(place.admin1_id, ()),
    ):
        if not n:
            continue
        for p in PREFIXES:
            n = n.removeprefix(p)
        for s in SUFFIXES:
            if n.lower().endswith(s):
                n = n[: -len(s)]
        if len(n) >= 3 and n not in out:
            out.append(n)
    return out


def favour(cands: list[Place], areas: dict, claims: list[dict], texts: dict):
    """(place, quote, claim) when the passages name the governorate of exactly one
    candidate; None when they name none, or several."""
    hits = {}
    for p in cands:
        for c in claims:
            text = texts.get(uuid.UUID(c["id"]))
            words = text and next(
                (q for t in area_terms(p, areas) if (q := quote(text, t))), None
            )
            if words:
                hits.setdefault(p.id, (p, words, c))
                break
    return next(iter(hits.values())) if len(hits) == 1 else None


async def elsewhere(db, user_id: int, norms) -> dict[str, dict[uuid.UUID, list[str]]]:
    """Accepted links per spelling, as {norm: {place id: [case slugs]}}, from cases
    this person may open. Links in other cases are left out entirely, never named."""
    from sealbook.agents.tasks import can_read

    links = []
    for part in resolve._chunks(set(norms)):
        links += await resolve._accepted_links(
            db, Payload.value["name"].astext.in_(part)
        )
    homes = await resolve.investigations_of(db, [c for c, _ in links])
    ok = {
        slug: await can_read(db, user_id, slug)
        for slug in {s for s in homes.values() if s}
    }
    out: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for claim, value in links:
        slug = homes.get(claim)
        if slug and ok[slug]:
            cases = out[value["name"]][resolve._ref(value)]
            if slug not in cases:
                cases.append(slug)
    return out


def _cases(slugs: list[str], here: str | None) -> str:
    named = [s for s in slugs if s != here]
    if not named:
        return "for another claim in this case"
    return "in " + ", ".join(named[:2]) + (" and others" if len(named) > 2 else "")


async def annotate(db, user, items: list[dict], investigation: str | None) -> None:
    """Add a `suggestion` to each unresolved name, in order of evidence."""
    from sealbook.agents import approximate
    from sealbook.entities.data import _place_rows

    norms = [u["norm"] for u in items]
    cands = await resolve.candidates_many(db, norms)
    confirmed = await elsewhere(db, user.id, norms)
    several = [u for u in items if len(cands.get(u["norm"], [])) > 1]
    texts = await passages(
        db, [uuid.UUID(c["id"]) for u in several for c in u["claims"]]
    )
    admin1 = {p.admin1_id for u in several for p in cands[u["norm"]] if p.admin1_id}
    areas = {
        i: (ar, en)
        for i, ar, en in (
            await db.execute(
                select(AdminArea.id, AdminArea.name_ar, AdminArea.name_en).where(
                    AdminArea.id.in_(list(admin1))
                )
            )
        ).all()
    }
    rows = await _place_rows(db, {p for by in confirmed.values() for p in by})
    agent = await approximate.suggestions(db, user, items, investigation)
    for u in items:
        found = cands.get(u["norm"], [])
        mine = confirmed.get(u["norm"], {})
        also = (
            f"Confirmed for this spelling {_cases(next(iter(mine.values())), investigation)}."
            if len(mine) == 1
            else None
        )
        if len(found) == 1:
            s = {
                "kind": "gazetteer",
                "places": u["candidates"],
                "why": "Gazetteer spelling, one candidate."
                + (f" {also}" if also and next(iter(mine)) == found[0].id else ""),
            }
        elif len(mine) == 1 and (pid := next(iter(mine))) in rows:
            s = {
                "kind": "elsewhere",
                "places": [rows[pid]],
                "why": also
                + (
                    f" {len(found)} places have this spelling, so confirm each claim."
                    if found
                    else ""
                ),
            }
        elif found:
            hit = favour(found, areas, u["claims"], texts)
            s = {
                "kind": "several",
                "places": u["candidates"],
                "favoured": str(hit[0].id) if hit else None,
                "quote": hit[1] if hit else None,
                "cite": {"file": hit[2]["file"], "href": hit[2]["href"]}
                if hit
                else None,
                "why": f"{len(found)} places have this spelling. "
                + ("" if hit else "Nothing around the name favours one. ")
                + "Pick one for each claim.",
            }
        elif u["norm"] in agent:
            s = agent[u["norm"]]
        else:
            s = {
                "kind": "none",
                "places": [],
                "why": "The gazetteer has no place with this spelling.",
            }
        # Bulk confirm takes only one clear place; several candidates need a person
        # per claim, even when another case chose one of them.
        s["selectable"] = s["kind"] in ("gazetteer", "elsewhere") and len(found) < 2
        u["suggestion"] = s


async def named_in(db, text: str, skip: str | None = None) -> list[tuple[str, Place]]:
    """Places whose name occurs in `text` after normalising, longest name first, as
    (the name as written in the gazetteer, place)."""
    folded = normalise(text)
    rows = (
        await db.execute(
            select(PlaceName.name, PlaceName.norm, Place)
            .join(Place, Place.id == PlaceName.place_id)
            .where(
                func.length(PlaceName.norm) >= 3,
                func.strpos(literal(folded), PlaceName.norm) > 0,
            )
            .order_by(func.length(PlaceName.norm).desc(), Place.id)
        )
    ).all()
    out, seen = [], set()
    for name, norm, place in rows:
        if norm != skip and place.id not in seen:
            seen.add(place.id)
            out.append((name, place))
    return out
