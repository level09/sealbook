"""A case study told in acts (docs/case-studies.md).

`case.json` may carry an ordered `acts` list. Each act is a moment of the
investigation: sources arrive, then its `steps` run in order, each a small named
handler registered by a module of this package. A new kind of step is a new
module here with a function under `@step("name")`; `load()` imports every module,
so nothing else is edited. A case without `acts` plays one act that does what the
seeder always did: planted claims, accepts, clusters, places, the agents the truth
scores, the intake brief and the conclusion.

Each act names in plain words what happened (`title`, `day`, `story`), the
capability that did the work (`shows`, from SHOWS) and where it shows in the app
(`see`, paths built from stable keys, never a raw id). `--until n` plays the
first n acts only, so the case stands exactly as it did after act n.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import click

# What an act demonstrates, in the words a reader of the case page sees.
SHOWS = (
    "reading",
    "first pass",
    "search",
    "same person",
    "contradiction",
    "gap",
    "copies",
    "standing question",
    "assignment",
    "peer review",
    "discovery",
    "sheet matching",
    "timeline",
    "map",
    "conclusion",
    "package",
)

Handler = Callable[["Study", dict, dict], Awaitable[None]]
STEPS: dict[str, Handler] = {}

# What a case without `acts` does, in the order the seeder always did it.
DEFAULT_STEPS = [
    "claims",
    "accept",
    "clusters",
    "places",
    "agents",
    "brief",
    "conclusion",
]


def step(name: str) -> Callable[[Handler], Handler]:
    """Register `fn` as the handler of the step called `name`."""

    def register(fn: Handler) -> Handler:
        if name in STEPS:
            raise RuntimeError(f"two handlers for the step {name!r}")
        STEPS[name] = fn
        return fn

    return register


def load() -> None:
    """Import every step module of this package, which registers its steps."""
    for mod in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{mod.name}")


@dataclass
class Study:
    """Everything the acts share while a case is played."""

    session: object
    app: object
    spec: dict
    root: Path
    s: object  # the Seeder: blobs, texts, the analyst's passkey
    owner: object
    analyst: object
    extractor: object
    collector: object
    batch: str | None
    channel: str
    claims: dict  # source key -> its planted claims
    arrived: list = field(default_factory=list)  # source keys, in arrival order
    pins: dict = field(default_factory=dict)  # claim key -> stand-in span
    text_of: dict = field(default_factory=dict)  # source key -> key of its text
    written: dict = field(default_factory=dict)  # claim key -> Statement
    accepted: list = field(default_factory=list)  # Statements a person accepted
    rows: dict = field(default_factory=dict)  # (record, subject) -> Statement
    subjects: dict = field(default_factory=dict)  # record -> person subject
    sites: dict | None = None  # place key -> Place, once imported
    confirmed: set = field(default_factory=set)  # statement ids placed
    joined: set = field(default_factory=set)  # clusters already linked
    others: dict = field(default_factory=dict)  # handle -> (Seeder, principal)
    tape: object = None  # recording.Tape, for sources the app reads
    missed: list = field(default_factory=list)  # planted keys the app did not read
    out: dict = field(default_factory=dict)

    @property
    def inv(self) -> str:
        return self.spec["investigation"]

    @property
    def records(self) -> dict:
        return self.spec.get("records") or {}

    def read(self, key: str) -> bool:
        """Whether the app reads source `key` itself instead of planted claims."""
        return bool(self.spec["sources"][key].get("read"))


def default_acts(spec: dict) -> list[dict]:
    return [
        {
            "title": spec["story"]["title"],
            "day": None,
            "story": spec["story"]["puzzle"],
            "shows": [],
            "sources": sorted(
                spec["sources"],
                key=lambda k: (spec["sources"][k]["date"] or "", k),
            ),
            "steps": DEFAULT_STEPS,
            "see": [],
        }
    ]


def check(spec: dict, acts: list[dict]) -> None:
    """Refuse an act list the player cannot play, before anything is written."""
    seen: set[str] = set()
    for n, act in enumerate(acts, start=1):
        for key in ("title", "story", "shows", "sources", "steps", "see"):
            if key not in act:
                raise click.ClickException(f"act {n} has no {key!r}")
        if bad := [s for s in act["shows"] if s not in SHOWS]:
            raise click.ClickException(f"act {n} shows unknown {bad}")
        for key in act["sources"]:
            if key not in spec["sources"]:
                raise click.ClickException(f"act {n}: no source {key!r}")
            if key in seen:
                raise click.ClickException(f"act {n}: {key!r} arrives twice")
            seen.add(key)
        for s in act["steps"]:
            name = s if isinstance(s, str) else s.get("do")
            if name not in STEPS:
                raise click.ClickException(f"act {n}: no step {name!r}")
    if missing := set(spec["sources"]) - seen:
        raise click.ClickException(f"sources in no act: {sorted(missing)}")


PATH = re.compile(r"\{(source|person|case):?([^}]*)\}")


async def see(st: Study, path: str) -> str:
    """An act's `see` path with its stable keys resolved: {source:KEY} the source's
    evidence id, {person:RECORD} the record's person id, {case} the case slug."""
    from sealbook.people import data as people

    def one(m: re.Match) -> str:
        what, key = m[1], m[2]
        if what == "case":
            return st.inv
        if what == "source":
            return st.s.blobs[key]
        subject = st.subjects.get(key)
        if subject is None:
            raise click.ClickException(f"no person for the record {key!r} yet")
        return people.pid(subject)

    return PATH.sub(one, path)


async def play(st: Study, acts: list[dict], until: int | None = None) -> list[dict]:
    """Play the acts in order, up to and including act `until`. Returns each played
    act with its `see` paths resolved."""
    from sealbook.monitor.study import arrive

    load()
    check(st.spec, acts)
    if until is not None and not 1 <= until <= len(acts):
        raise click.ClickException(f"--until must be 1 to {len(acts)}")
    played = []
    for act in acts[:until]:
        await arrive.sources(st, act["sources"])
        for s in act["steps"]:
            name, args = (s, {}) if isinstance(s, str) else (s["do"], s)
            await STEPS[name](st, act, args)
            await st.session.commit()
        played.append(act | {"see": [await see(st, p) for p in act["see"]]})
    return played
