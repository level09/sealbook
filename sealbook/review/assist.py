"""Before you decide: what a reviewer keeps catching by hand, found by rules when a
claim opens. A place the passage names that no claim from the source holds, a value
an accepted claim contradicts, a subject who is probably already a person in the
case, and a claim with no date of its own. Each flag cites the words it rests on and
links to them. None accepts, rejects or writes anything.

Masking follows the rest of Review: words from the passage only when this session
may read the source, other claims only as `rows` already masked them, and nothing
from a case the reviewer cannot open."""

from __future__ import annotations

import re

from sqlalchemy import select

from sealbook import blobs
from sealbook.agents import read, tasks
from sealbook.discovery import evidence
from sealbook.discovery.models import Segment
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Blob, Payload, Statement, StatementTier
from sealbook.ledger.write import current_clause
from sealbook.monitor.data import LOCATED, POSITION, _apart
from sealbook.people import data as people
from sealbook.places import normalise, resolve
from sealbook.review import queue
from sealbook.settings import Config

# Longest place name looked up in the gazetteer, in words.
MAX_WORDS = 5
# Props a person has one value of: a second accepted value is a contradiction.
SINGLE = {
    Prop.BIRTH_DATE,
    Prop.PLACE_OF_BIRTH,
    Prop.FATHER_NAME,
    Prop.MOTHER_NAME,
    Prop.SEX,
}
# Claims that say when something happened, so a missing date matters.
DATED = POSITION | {Prop.TRANSFERRED_TO, Prop.EVENT}
PLACED = LOCATED | {Prop.PLACE_OF_BIRTH}
WORD = re.compile(r"\w+")
# A sentence ends at a line break or at end punctuation followed by a space.
STOP = re.compile(r"\n|(?<=[.!?؟])\s")
MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# ponytail: Western and Arabic-Indic digits, English month names; add Arabic month
# names when Arabic-dated passages show up without a flag.
DATE = re.compile(
    rf"\b(?:\d{{4}}-\d{{1,2}}(?:-\d{{1,2}})?|\d{{1,2}}[/.]\d{{1,2}}[/.]\d{{4}}"
    rf"|\d{{1,2}} {MONTH} \d{{4}}|{MONTH} \d{{1,2}},? \d{{4}}|{MONTH} \d{{4}})\b",
    re.IGNORECASE,
)


def words(text: str) -> str:
    """A name as space-separated normalised words, so "Al-Qusayr" reads "al qusayr"."""
    return " ".join(normalise(w) for w in WORD.findall(text))


def sentence(text: str, a: int, b: int) -> tuple[int, int]:
    """The sentence or line around text[a:b], widened to cover the whole span."""
    lo = max((m.end() for m in STOP.finditer(text, 0, a)), default=0)
    hi = next((m.start() for m in STOP.finditer(text, b)), len(text))
    return lo, max(hi, b)


def phrases(text: str, lo: int, hi: int) -> list[tuple[str, int, int]]:
    """Every run of 1 to MAX_WORDS words in text[lo:hi], normalised, longest first."""
    toks = list(WORD.finditer(text, lo, hi))
    out = []
    for n in range(MAX_WORDS, 0, -1):
        for i in range(len(toks) - n + 1):
            run = toks[i : i + n]
            out.append(
                (" ".join(normalise(t[0]) for t in run), run[0].start(), run[-1].end())
            )
    return out


def unheld_places(found, gazetteer: dict, held: set, held_ids: set) -> list[dict]:
    """Gazetteer names in `found` (phrases, longest first) that no claim holds: not
    inside a held value's words and not one of the held places. Overlapping shorter
    names lose to the longer one; each name once. Pure."""
    taken: list[tuple[int, int]] = []
    out, seen = [], set()
    for norm, a, b in found:
        places = gazetteer.get(norm)
        if not places or any(a < y and x < b for x, y in taken):
            continue
        taken.append((a, b))
        inside = any(f" {norm} " in f" {h} " for h in held)
        if inside or norm in seen or {p.id for p in places} & held_ids:
            continue
        seen.add(norm)
        out.append({"start": a, "end": b, "places": places})
    return out


def dates_near(text: str, lo: int, hi: int) -> list[tuple[str, int, int]]:
    return [(m[0], m.start(), m.end()) for m in DATE.finditer(text, lo, hi)]


def apart(prop: str, a, b) -> bool:
    """Two resolved places that cannot both hold on one day, by the monitor's rule:
    different places farther apart than the larger of their precisions."""

    def point(p):
        lonlat = [float(p.lon), float(p.lat)] if p.lon is not None else None
        return {"place_id": str(p.id), "place": "", "prop": prop, "lonlat": lonlat}

    pa, pb = point(a), point(b)
    pa["precision_m"], pb["precision_m"] = a.precision_m, b.precision_m
    return _apart(pa, pb)


def contradicted(item: dict, related: list[dict], place_of) -> list[dict]:
    """Accepted claims of the same kind as `item` whose value differs. A one-value
    prop always compares. A position compares only on the same single day, with both
    places resolved and farther apart than their precision (a quarter and its city
    agree).
    Masked values never compare. `item` and `related` are review rows; `place_of`
    maps a claim id to its resolved place or None. Pure.
    ponytail: positions only on one exact day; the contradiction hunter reads ranges."""
    prop, mine = item["prop"], item["value"]["text"]
    if mine is None or not (prop in SINGLE or prop in POSITION):
        return []
    day = item["from"] if item["from"] and item["from"] == item["to"] else None
    out = []
    for r in related:
        text = r["value"]["text"]
        if r["tier"] != Tier.ACCEPTED or text is None or r["prop"] != prop:
            continue
        if prop in POSITION:
            a, b = place_of(item["id"]), place_of(r["id"])
            if not (day and r["from"] == day == r["to"] and a and b) or not apart(
                prop, a, b
            ):
                continue
        elif read.same_value(prop, mine, text):
            continue
        out.append(r)
    return out


def same_name(mine: list[read.Claim], others: list[read.Claim], blocked) -> dict:
    """Other person clusters with the same normalised full name (two words at least)
    as one of `mine` in another source, not set apart by an accepted not_same. Two
    rows of one register with one name are two people more often than one. One name
    claim per cluster. Cross-script and partial matches are the linker's work. Pure."""
    full: dict[str, set] = {}
    for m in mine:
        if len((w := words(m.text)).split()) >= 2:
            full.setdefault(w, set()).add(m.source)
    out: dict[str, read.Claim] = {}
    for o in others:
        own = full.get(words(o.text))
        if o.person in out or not own or o.source in own or blocked(o.person):
            continue
        out[o.person] = o
    return out


class Cites:
    """Where in a source some characters are, as a label and a link that opens the
    evidence page there."""

    def __init__(self, db):
        self.db, self.pages, self.timelines = db, {}, {}

    async def __call__(self, source: str, span_blob: str | None, span) -> dict:
        db = self.db
        if span_blob and span_blob != source and span_blob not in self.pages:
            self.pages[span_blob] = await db.scalar(
                select(Segment.location["page"].as_integer())
                .where(Segment.text_sha256 == span_blob)
                .limit(1)
            )
        if source not in self.timelines:
            mime = await db.scalar(select(Blob.mime).where(Blob.sha256 == source))
            self.timelines[source] = (
                await evidence.transcript_timeline(db, source)
                if (mime or "").startswith(("audio/", "video/"))
                else []
            )
        pages = {k: v for k, v in self.pages.items() if v}
        timelines = {k: v for k, v in self.timelines.items() if v}
        w = queue.where(span, span_blob, source, pages, timelines)
        href = f"/evidence/{source}"
        seg = (
            await db.scalar(
                select(Segment.id)
                .where(
                    Segment.text_sha256 == span_blob,
                    Segment.start <= span["start"],
                    Segment.end >= span["end"],
                )
                .limit(1)
            )
            if span and "start" in span and span_blob and "ms" not in w
            else None
        )
        if seg:
            href += f"?segment={seg}&start={span['start']}&end={span['end']}"
        elif "ms" in w:
            href += f"?at={w['ms'][0]}"
        elif "page" in w:
            href += f"?page={w['page']}"
        return {"label": w["label"], "href": href}


async def _open_links(db, groups) -> dict[frozenset, str]:
    """Open same_as proposals by the pair of clusters they join."""
    rows = await db.execute(
        select(Statement.id, Statement.subject, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.prop == Prop.SAME_AS,
            Statement.subject.startswith("person:"),
            StatementTier.tier.not_in([Tier.ACCEPTED, Tier.REJECTED]),
            current_clause(),
        )
    )
    return {
        frozenset(
            (read.cluster_of(groups, s), read.cluster_of(groups, v["person"]))
        ): str(i)
        for i, s, v in rows
    }


async def flags(db, user, item: dict, st: Statement, readable: bool) -> list[dict]:
    """The flags for one claim as `queue.claim` built it (`related` included)."""
    src = item["source"]
    out: list[dict] = []
    if not src or st.prop in queue.LINKS or st.prop == Prop.MEDIA_MATCH:
        return out
    sha, investigation = src["sha256"], src["investigation"]
    cite = Cites(db)
    # ponytail: reads the whole case per claim opened, as the agents do; cache per
    # investigation and ledger head if Review on a large case gets slow.
    case = await read.case(db, investigation) if investigation else None

    text = None
    if readable and st.span and "start" in st.span and "row" not in st.span:
        blob = await db.get(Blob, st.span_blob)
        if blob and blob.mime.startswith("text/"):
            text = blobs.read(blob, Config.BLOB_DIR).decode(errors="replace")
    lo, hi = sentence(text, st.span["start"], st.span["end"]) if text else (0, 0)

    if text and case:
        mine = [c for c in case.claims if c.source == sha and c.prop in PLACED]
        held = {words(c.text) for c in mine}
        held_ids = set()
        for c in mine:
            if (p := case.places.get(c.id)) is not None:
                held_ids |= {p.id, p.parent_id, p.governorate_id}
        found = phrases(text, lo, hi)
        gazetteer = await resolve.candidates_many(db, {n for n, _, _ in found})
        for hit in unheld_places(found, gazetteer, held, held_ids):
            places = hit["places"]
            out.append(
                {
                    "kind": "place",
                    "quote": text[hit["start"] : hit["end"]],
                    "place": {
                        "id": str(places[0].id),
                        "name": read.place_name(places[0]),
                    }
                    if len(places) == 1
                    else None,
                    "cite": await cite(sha, st.span_blob, hit),
                }
            )

    # Rows from another case are shown only if the reviewer may open that case.
    cases = {}
    places = {str(k): v for k, v in case.places.items()} if case else {}
    for r in contradicted(item, item["related"], places.get):
        other = (r["source"] or {}).get("investigation")
        if other not in cases:
            cases[other] = other is None or await tasks.can_read(db, user.id, other)
        if cases[other]:
            out.append(
                {
                    "kind": "differs",
                    "value": r["value"]["text"],
                    "id": r["id"],
                    "file": r["source"]["file"] if r["source"] else None,
                    "cite": await cite(r["source"]["sha256"], r["span_blob"], r["span"])
                    if r["source"]
                    else None,
                }
            )

    person = item["person"]
    if case and person and not person["masked"] and st.prop not in queue.IDENTITY:
        me = read.cluster_of(case.groups, st.subject)
        shown = lambda c: not c.sensitive or evidence.granted(c.source)  # noqa: E731
        names = [c for c in case.claims if c.prop == Prop.NAME and shown(c)]
        blocked_pairs = await people._not_same_pairs(db)
        members = lambda p: case.groups.get(people.pid(p), {people.pid(p)})  # noqa: E731

        def blocked(other):
            return any(
                frozenset((a, b)) in blocked_pairs
                for a in members(me)
                for b in members(other)
            )

        found = same_name(
            [c for c in names if c.person == me],
            [c for c in names if c.person != me],
            blocked,
        )
        links = await _open_links(db, case.groups) if found else {}
        for other, c in found.items():
            out.append(
                {
                    "kind": "person",
                    "person": {"id": other.removeprefix("person:"), "name": c.text},
                    "file": case.docs[c.source]["title"],
                    "cite": await cite(c.source, c.span_blob, c.span),
                    "proposal": links.get(frozenset((me, other))),
                }
            )

    if st.prop in DATED and st.valid_from is None:
        near = dates_near(text, lo, hi) if text else []
        if near:
            quote, a, b = near[0]
            out.append(
                {
                    "kind": "date",
                    "quote": quote,
                    "cite": await cite(sha, st.span_blob, {"start": a, "end": b}),
                }
            )
        elif item["inherited"]:
            out.append(
                {
                    "kind": "date",
                    "quote": None,
                    "document": item["from"],
                    "cite": {"label": src["file"], "href": f"/evidence/{sha}"},
                }
            )
    return out
