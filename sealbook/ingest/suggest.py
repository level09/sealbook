"""Which case a read batch belongs to, by rule: the people, places and dates its
claims name, compared with the other cases the uploader can open. Computed on
request and never stored. Nothing is filed without a click: the person either
starts a case or records chosen sources in another one.

Recording a source in another case adds an acquisition with the same bytes, the
way a repeat upload does, with a note naming the case it came from. Custody is
append-only, so the batch's own acquisitions stay and the source is then in both
cases; claims hang off the blob, so they show in both without being copied."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select

from sealbook.agents import noticer, read, tasks
from sealbook.cases.models import Case
from sealbook.discovery.evidence import granted
from sealbook.ledger import write
from sealbook.ledger.contracts import Prop
from sealbook.ledger.models import Acquisition
from sealbook.monitor.data import LOCATED
from sealbook.places import normalise
from sealbook.review.queue import where
from sealbook.search.arabic import name_keys

MIN_KEYS = 2  # a lone first name matches half of any case


def _ordered(short: tuple, long: tuple) -> bool:
    rest = iter(long)
    return all(any(k == o for o in rest) for k in short)


def _named(a: tuple, b: tuple) -> bool:
    """The Arabic normaliser's ordered name match on precomputed keys."""
    short, long = sorted((a, b), key=len)
    return len(short) >= MIN_KEYS and _ordered(short, long)


def _place_keys(c: read.Claim, places: dict) -> set:
    place = places.get(c.id)
    keys = {("text", normalise(c.text))} if c.text else set()
    if place is not None:
        keys |= {("place", place.id)} | {
            ("text", normalise(n)) for n in (place.name_en, place.name_ar) if n
        }
    return keys


def _windows(claims: list[read.Claim]) -> dict[str, tuple]:
    """Each source's dated span: from its earliest claim to its latest."""
    out: dict[str, tuple] = {}
    for c in claims:
        if c.lo:
            lo, hi = out.get(c.source, (c.lo, c.hi))
            out[c.source] = (min(lo, c.lo), max(hi, c.hi))
    return out


def _when(c: read.Claim, windows: dict) -> tuple | None:
    return (c.lo, c.hi) if c.lo else windows.get(c.source)


def _overlap(a: tuple | None, b: tuple | None) -> bool:
    return bool(a and b and a[0] <= b[1] and b[0] <= a[1])


def ties(mine: list[read.Claim], places: dict, theirs: list[read.Claim], their_places):
    """Per source of `mine`, what ties it to `theirs`: a name the normaliser matches
    (same identity cluster counts too), or a place both name on overlapping dates.
    Each hit is (kind, my claim, their claim)."""
    names = [(c, name_keys(c.text)) for c in theirs if c.prop == Prop.NAME and c.text]
    spots: dict = {}
    for c in theirs:
        if c.prop in LOCATED:
            for k in _place_keys(c, their_places):
                spots.setdefault(k, []).append(c)
    mine_w, their_w = _windows(mine), _windows(theirs)
    out: dict[str, list[tuple]] = {}
    for c in mine:
        hit = None
        if c.prop == Prop.NAME and c.text:
            keys = name_keys(c.text)
            hit = next(
                (
                    ("person", c, d)
                    for d, dk in names
                    if (c.person and c.person == d.person) or _named(keys, dk)
                ),
                None,
            )
        elif c.prop in LOCATED:
            when = _when(c, mine_w)
            hit = next(
                (
                    ("place", c, d)
                    for k in _place_keys(c, places)
                    for d in spots.get(k, ())
                    if _overlap(when, _when(d, their_w))
                ),
                None,
            )
        have = out.setdefault(c.source, []) if hit else []
        if hit and (hit[0], normalise(c.text)) not in {
            (k, normalise(x.text)) for k, x, _ in have
        }:
            have.append(hit)
    return {s: h for s, h in out.items() if h}


def _span(c: read.Claim) -> str:
    return where(c.span, c.span_blob, c.source, {}, {})["label"]


def _window_words(claims: list[read.Claim]) -> str | None:
    dated = [c for c in claims if c.lo]
    if not dated:
        return None
    return read.when(min(c.lo for c in dated), max(c.hi for c in dated))


def proposed_name(claims: list[read.Claim], places: dict) -> dict | None:
    """A rule-built case name from the most named place and the month of the
    earliest date it is named on, read only from ordinary sources: a case name is
    seen by everyone who can open cases."""
    open_ = [c for c in claims if c.prop in LOCATED and c.text and not c.sensitive]
    if not open_:
        return None
    label = {
        c.id: read.place_name(places[c.id]) if places.get(c.id) else c.text
        for c in open_
    }
    top, _ = Counter(label.values()).most_common(1)[0]
    basis = min(
        (c for c in open_ if label[c.id] == top),
        key=lambda c: (c.lo is None, c.lo or 0),
    )
    # An undated place takes its month from the earliest dated claim of its source.
    dated = (
        basis
        if basis.lo
        else min(
            (
                c
                for c in claims
                if c.source == basis.source and c.lo and not c.sensitive
            ),
            key=lambda c: c.lo,
            default=None,
        )
    )
    month = f", {read.MONTHS[dated.lo.month - 1]} {dated.lo.year}" if dated else ""
    return {"name": f"{top}{month}"[:60], "basis": basis, "dated": dated}


def compare(home: read.Case, shas: list[str], others: dict[str, read.Case]) -> dict:
    """The suggestion for a batch whose sources are `shas`, read in `home`, against
    `others`: only cases the person can open may be passed in."""
    batch = [c for c in home.claims if c.source in shas]
    rest = [c for c in home.claims if c.source not in shas]
    read_ = [s for s in shas if any(c.source == s for c in batch)]
    stays = ties(batch, home.places, rest, home.places)
    hits = {
        slug: ties(
            batch, home.places, [c for c in o.claims if c.source not in shas], o.places
        )
        for slug, o in others.items()
    }
    out = {"read": len(read_), "elsewhere": None, "new_case": None}
    best = max(hits, key=lambda s: (len(hits[s]), s), default=None)
    if best and 2 * len(hits[best]) > len(read_) and len(hits[best]) > len(stays):
        out["elsewhere"] = {
            "case": best,
            "sources": hits[best],
            # When the claims tied to are dated, not the whole case's span.
            "window": _window_words([d for h in hits[best].values() for *_, d in h]),
            "unmatched": [s for s in read_ if s not in hits[best]],
        }
    tied = set(stays).union(*hits.values())
    loose = [s for s in read_ if s not in tied]
    # A batch into a case that held nothing before is already its own new case.
    if rest and 2 * len(loose) > len(read_):
        out["new_case"] = {
            "sources": loose,
            "name": proposed_name([c for c in batch if c.source in loose], home.places),
        }
    return out


# --- for the page -----------------------------------------------------------------


async def _batch(db, batch: str) -> list[Acquisition]:
    return list(
        await db.scalars(
            select(Acquisition)
            .where(Acquisition.context["batch"].astext == batch)
            .order_by(Acquisition.at, Acquisition.id)
        )
    )


def _source(docs: dict, sha: str) -> dict:
    d = docs[sha]
    return {"sha256": sha, "title": d["title"], "sensitive": d["sensitive"]}


def _hit(kind: str, c: read.Claim, docs: dict) -> dict:
    """One tie as shown: the value from this batch, masked when its sensitive
    source is not revealed in this session, with where it was read."""
    hidden = c.sensitive and not granted(c.source)
    return {
        "kind": kind,
        "value": None if hidden else c.text,
        "masked": hidden,
        "when": read.when(c.lo, c.hi),
        "source": docs[c.source]["title"],
        "where": _span(c),
        "href": f"/evidence/{c.source}",
    }


async def suggestion(db, user_id: int, batch: str) -> dict:
    acqs = await _batch(db, batch)
    home_slug = acqs[0].context.get("investigation") if acqs else None
    if not home_slug:
        return {"read": 0, "elsewhere": None, "new_case": None}
    shas = list(dict.fromkeys(a.blob_sha256 for a in acqs))
    home = await read.case(db, home_slug)
    slugs = await noticer.readable_cases(db, user_id, home_slug)
    others = {s: await read.case(db, s) for s in slugs}
    found = compare(home, shas, others)
    names = dict((await db.execute(select(Case.slug, Case.name))).tuples().all())
    docs = home.docs
    if e := found["elsewhere"]:
        e["name"] = names[e["case"]]
        e["sources"] = [
            _source(docs, sha)
            | {"ties": [_hit(k, c, docs) for k, c, _ in hits[:4]], "more": len(hits)}
            for sha, hits in e["sources"].items()
        ]
        e["unmatched"] = [_source(docs, s) for s in e["unmatched"]]
    if n := found["new_case"]:
        got = n["name"] or {"name": "", "basis": None, "dated": None}
        n["name"] = got["name"]
        n["basis"] = got["basis"] and _hit("place", got["basis"], docs)
        n["dated"] = got["dated"] and _hit("date", got["dated"], docs)
        n["sources"] = [_source(docs, s) for s in n["sources"]]
    return found | {"home": home_slug, "home_name": names.get(home_slug, home_slug)}


async def record(db, user, batch: str, target: str, chosen: list[str]) -> dict:
    """Record chosen batch sources in `target`: a new acquisition each, same bytes,
    with a note naming the case they came from. A source already there is left."""
    acqs = {a.blob_sha256: a for a in await _batch(db, batch)}
    if not acqs or not chosen or set(chosen) - set(acqs):
        raise ValueError("Pick sources from this batch.")
    home = next(iter(acqs.values())).context.get("investigation")
    if target == home:
        raise ValueError("These sources are already in that case.")
    if not await tasks.can_read(db, user.id, target):
        raise LookupError("There is no case by that name you can open.")
    collector = await write.principal_for_user(db, user.id, user.name or user.email)
    there = set(
        await db.scalars(
            select(Acquisition.blob_sha256).where(
                Acquisition.blob_sha256.in_(chosen),
                Acquisition.context["investigation"].astext == target,
            )
        )
    )
    names = dict((await db.execute(select(Case.slug, Case.name))).tuples().all())
    for sha in chosen:
        if sha in there:
            continue
        a = acqs[sha]
        await write.record_acquisition(
            db,
            blob_sha256=sha,
            origin=a.origin,
            collector_id=collector.id,
            at=datetime.now(UTC),
            context={
                "investigation": target,
                "note": f"Recorded from {names.get(home, home)}, where it was added "
                "first. It stays there too.",
                "recorded_from": {"acquisition": str(a.id), "investigation": home},
            }
            | {k: a.context[k] for k in ("filename", "title") if k in a.context},
            sensitivity=a.sensitivity,
            doc_type=a.doc_type,
            partner=a.partner,
        )
    return {"recorded": len(set(chosen) - there), "already": len(there & set(chosen))}
