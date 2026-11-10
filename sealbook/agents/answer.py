"""One answer for a batch: what its recordings say, who they may be about, what they
agree on, that person's path, who else was there, what is missing and the one next
step. Composed on request from what is already stored (transcripts, claims, the
linker's open proposals, the gap rule); no model runs here and nothing is written
but the reader's access record.

Every line carries the claims it rests on. A line resting on a sensitive source the
reader has not revealed comes back locked, as in the intake brief."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import Text, cast, func, select

from sealbook.agents import brief, gaps, read
from sealbook.agents.read import FIXED, RULES, cluster_of, label, same_value, when
from sealbook.discovery.evidence import granted, kind_of, transcript_timeline
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Blob, Run, Statement
from sealbook.monitor.data import LOCATED
from sealbook.places import normalise
from sealbook.review.findings import month_label

FIELDS = (
    Prop.MOTHER_NAME,
    Prop.FATHER_NAME,
    Prop.BIRTH_DATE,
    Prop.PLACE_OF_BIRTH,
    Prop.SEX,
)
PLACES = LOCATED | {Prop.SEEN_AT, Prop.LAST_SEEN}
STEPS = {
    "transcribe": "Speech to text",
    "hash": "Fingerprint",
    "fingerprint": "Picture and sound fingerprints",
    "thumbnail": "Thumbnails",
    "index": "Search index",
    "embed": "Search by meaning",
    "ocr": "Text read from pictures",
    "extract": "Claims read from the text",
    "assess": "First pass",
    "link": "Links between sources",
    "brief": "Intake brief",
}
# The route by name, not a promise: what a provider keeps is its own policy.
HOSTED = {"cloud-zdr": "hosted model, zero-retention route", "cloud": "hosted model"}


def said(c) -> str:
    """A claim's value inside an English sentence: isolated, so Arabic keeps its own
    direction without reordering the words around it."""
    return f"\u2068{c.text}\u2069"


def clock(ms: int) -> str:
    return f"{ms // 60000}:{ms // 1000 % 60:02d}"


def where_ran(route, endpoint, model) -> str:
    if model == FIXED:
        return "recorded for the demo, no model ran"
    if model == RULES or route is None:
        return "this server"
    if route == "local":
        return "your own model server" if endpoint else "this server"
    return HOSTED.get(route, "hosted model")


async def show(db, user, batch: str) -> dict:
    g = await brief.gather(db, batch, user.id)
    case, mine, docs, shas = g["case"], g["mine"], g["docs"], g["shas"]
    by, shown = case.by_person(), set()
    heard = [s for s in shas if kind_of(docs[s]["mime"]) in ("audio", "video")]
    times = {s: await transcript_timeline(db, s) for s in heard}

    def cite(c) -> dict:
        ms = None
        if c.source in times and c.span and "start" in c.span:
            ms = next(
                (a for at, a, _ in reversed(times[c.source]) if at <= c.span["start"]),
                None,
            )
        href = f"/evidence/{c.source}" + (f"?at={ms}" if ms is not None else "")
        at = f" {clock(ms)}" if ms is not None else ""
        return {
            "label": docs[c.source]["title"] + at,
            "href": href,
            "play": bool(at),
            "at": at.strip(),
        }

    def line(text: str, *claims, **more) -> dict:
        hidden = sorted(
            {c.source for c in claims if c.sensitive and not granted(c.source)}
        )
        if hidden:
            return {
                "text": None,
                "locked": [
                    {"title": docs[s]["title"], "href": f"/evidence/{s}"}
                    for s in hidden
                ],
            }
        shown.update(str(c.id) for c in claims)
        return {
            "text": text,
            "locked": [],
            "cites": list({(x := cite(c))["href"]: x for c in claims}.values()),
            "draft": any(c.tier == Tier.UNCONFIRMED for c in claims),
        } | more

    def first(person, prop, among=None):
        return next(
            (
                c
                for c in (among or by.get(person, ()))
                if c.person == person and c.prop == prop
            ),
            None,
        )

    # What was said: each recording with its length and the line that names someone.
    recordings = []
    for sha in heard:
        runs = (
            await db.scalars(
                select(Run.raw_output_ref).where(
                    Run.tool == "transcribe",
                    Run.inputs["blobs"][0].astext == sha,
                    Run.raw_output_ref.is_not(None),
                )
            )
        ).all()
        parts = [read.read_output(await db.get(Blob, r)) for r in runs]
        segments = sorted(
            (s for p in parts for s in p["segments"]), key=lambda s: s["ms"][0]
        )
        named = next((c for c in mine if c.source == sha and c.prop == Prop.NAME), None)
        quote = None
        if named is not None and segments:
            ms = cite(named)["href"].partition("?at=")[2]
            seg = next((s for s in segments if str(s["ms"][0]) == ms), segments[0])
            quote = line(seg["text"], named, at=seg["ms"][0], clock=clock(seg["ms"][0]))
        recordings.append(
            {
                "sha256": sha,
                "title": docs[sha]["title"],
                "href": f"/evidence/{sha}",
                "duration": clock(max((p["duration_ms"] for p in parts), default=0)),
                "lines": len(segments),
                "waiting": not parts,
                "quote": quote,
            }
        )

    # Who this may be: the linker's open same-person proposals that rest on this batch.
    who = []
    same = set(
        await db.scalars(
            select(Statement.id).where(
                Statement.id.in_([sid for sid, _, _ in g["links"]]),
                Statement.prop == Prop.SAME_AS,
            )
        )
    )
    for sid, subject, value in g["links"]:
        if sid not in same:
            continue
        a, b = (
            cluster_of(case.groups, subject),
            cluster_of(case.groups, value["person"]),
        )
        new, old = (a, b) if first(a, Prop.NAME, mine) else (b, a)
        here, there = first(new, Prop.NAME, mine), first(old, Prop.NAME)
        if here is None or there is None:
            continue
        checks = []
        for prop in FIELDS:
            xs = [c for c in by[new] if c.prop == prop]
            ys = [c for c in by[old] if c.prop == prop]
            pair = next(
                ((x, y) for x in xs for y in ys if same_value(prop, x.text, y.text)),
                None,
            )
            if pair:
                checks.append(
                    line(f"{label(prop)} {said(pair[1])}", *pair, tag="agrees")
                )
            elif xs and ys:
                checks.append(
                    line(
                        f"{label(prop)}: {said(xs[0])} here, {said(ys[0])} there",
                        xs[0],
                        ys[0],
                        tag="differs",
                    )
                )
        # Others who carry the same name and are told apart by a field.
        for other in by:
            twin = first(other, Prop.NAME)
            if other in (new, old) or twin is None:
                continue
            if not same_value(Prop.NAME, twin.text, there.text):
                continue
            for prop in FIELDS:
                x, y = first(new, prop), first(other, prop)
                if x and y and not same_value(prop, x.text, y.text):
                    checks.append(
                        line(
                            f"Another {said(twin)}: {label(prop).lower()} {said(y)}",
                            twin,
                            y,
                            tag="not him",
                        )
                    )
                    break
        who.append(
            {
                "head": line(
                    f"{said(here)} may be {said(there)} in {docs[there.source]['title']}",
                    here,
                    there,
                ),
                "here": line(here.text, here),
                "there": line(there.text, there),
                "checks": checks,
                "why": value.get("why"),
                "review": f"/review?box=open&statement={sid}",
                "beside": cite(here)["href"]
                + ("&" if "?" in cite(here)["href"] else "?")
                + f"with={there.source}",
                "pair": (new, old),
            }
        )

    # Said in more than one recording: a person, a place or a month several sources give.
    again, told = {}, len({c.source for c in mine})
    for c in mine:
        keys = []
        if c.prop == Prop.NAME and c.person:
            keys.append((("person", c.person), f"{said(c)} is named"))
        if c.prop in PLACES:
            keys.append((("place", normalise(c.text)), f"{said(c)} is the place given"))
            if c.lo and c.hi and (c.lo.year, c.lo.month) == (c.hi.year, c.hi.month):
                month = month_label(c.lo.strftime("%Y-%m"))
                keys.append((("month", month), f"{month} is the time given"))
        for key, text in keys:
            again.setdefault(key, {"text": text, "by": {}})["by"].setdefault(
                c.source, c
            )
    again = [
        line(
            f"{v['text']} in {len(v['by'])} of {told}",
            *v["by"].values(),
            n=len(v["by"]),
        )
        for v in again.values()
        if len(v["by"]) > 1
    ]
    again.sort(key=lambda x: -x.get("n", 0))

    # His path, who else was there and what is missing, for the first proposal's pair
    # read as one person. Nothing is merged: this is what the case would hold if so.
    path, others, missing = [], [], None
    if who:
        new, old = who[0].pop("pair")
        pair = [*by[new], *by[old]]
        steps = {}
        for c in sorted(
            (c for c in pair if c.lo and c.prop in PLACES), key=lambda c: c.lo
        ):
            # Three sources giving one arrest in one month are one step, cited thrice.
            steps.setdefault(
                (c.prop, normalise(c.text), c.lo.year, c.lo.month), []
            ).append(c)
        for (prop, *_), told_by in steps.items():
            exact = min(told_by, key=lambda c: c.hi - c.lo)
            path.append(
                line(
                    f"{label(prop)} {said(exact)}",
                    *told_by,
                    when=when(exact.lo, exact.hi),
                )
            )
        spots = {normalise(c.text): c for c in pair if c.prop in PLACES}
        old_sources = {c.source for c in by[old]}
        for other, claims in by.items():
            if other in (new, old):
                continue
            for c in claims:
                mate = spots.get(normalise(c.text)) if c.prop in PLACES else None
                together = mate and (
                    read.overlap(c, mate)
                    or any(read.overlap(c, p) for p in pair if p.prop in PLACES)
                    or (not c.lo and c.source in old_sources)
                )
                name = first(other, Prop.NAME)
                if together and name:
                    others.append(line(name.text, name, c, where=c.text))
                    break
        joined = [
            SimpleNamespace(id=c.id, person="pair", prop=c.prop, lo=c.lo, hi=c.hi)
            for c in pair
        ]
        ends = [
            e
            for e in gaps.detect(joined, await gaps.event_kinds(db, pair))
            if e["window"][1] is None
        ]
        if ends:
            last = next(c for c in pair if c.id == ends[0]["bounds"][-1].id)
            missing = line(gaps.window_words(ends[0]["window"]), last)
    for w in who:
        w.pop("pair", None)

    # Where each step ran, from the runs themselves.
    rows = await db.execute(
        select(Run.tool, Run.route, Run.endpoint, Run.model_served, func.count())
        .where(
            Run.status == "done",
            Run.inputs["blobs"][0].astext.in_(shas)
            | (
                Run.tool.in_(("link", "brief")) & cast(Run.inputs, Text).contains(batch)
            ),
        )
        .group_by(Run.tool, Run.route, Run.endpoint, Run.model_served)
    )
    ran = {}
    for tool, route, endpoint, model, n in rows:
        key = (
            STEPS.get(tool, tool.replace("_", " ").capitalize()),
            where_ran(route, endpoint, model),
        )
        ran[key] = ran.get(key, 0) + n
    order = list(STEPS.values())
    ran = [
        {"step": s, "where": w, "runs": n, "here": w == "this server"}
        for (s, w), n in sorted(
            ran.items(),
            key=lambda kv: order.index(kv[0][0]) if kv[0][0] in order else 99,
        )
    ]

    nxt = (
        {"text": "Decide whether they are the same person", "href": who[0]["review"]}
        if who
        else {
            "text": f"Review the {len(mine)} claims read from this batch",
            "href": "/review",
        }
        if mine
        else None
    )
    if shown:
        await brief.log_view(db, user, None, sorted(shown), f"answer:{batch}")
    return {
        "batch": batch,
        "investigation": g["investigation"],
        "files": len(shas),
        "recordings": recordings,
        "who": who,
        "again": again,
        "path": path,
        "others": others,
        "missing": missing,
        "ran": ran,
        "next": nxt,
    }
