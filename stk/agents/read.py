"""What a proactive agent run reads in a case, and how it hands that to a model.

An agent reads the claims a person could open in the case: current, not rejected,
value not erased, source available. Sensitive sources are read too, which is why
these runs take the local route only (the policy for a run without a document).
The model never sees an id: each claim, source or candidate gets a short handle,
and an answer may only cite handles it was given."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

import orjson
from jsonschema import Draft202012Validator
from sqlalchemy import select

from stk import blobs
from stk.discovery.service import available
from stk.ledger import chain, write
from stk.ledger.contracts import EXTRACTED_PROPS, Prop, StatementIn, Tier
from stk.ledger.models import Blob, Payload, Principal, Statement, StatementTier
from stk.monitor import filter as mfilter
from stk.monitor.data import LOCATED, PERSONAL, Context, display
from stk.people import data as people
from stk.places import normalise, resolve
from stk.runs import routes
from stk.settings import Config

FIXED = "sealbook-demo-fixed-output"


@dataclass(frozen=True)
class Claim:
    id: uuid.UUID
    subject: str
    person: str | None  # identity cluster, "person:<smallest member>"
    prop: str
    text: str
    lo: date | None
    hi: date | None
    source: str  # the acquired blob
    span_blob: str | None
    span: dict | None
    tier: str
    sensitive: bool


@dataclass
class Case:
    investigation: str
    claims: list[Claim]
    docs: dict[str, dict]  # sha -> Context.doc plus mime
    places: dict[uuid.UUID, object]  # claim id -> Place or None
    groups: dict[str, frozenset]

    def by_person(self) -> dict[str, list[Claim]]:
        out: dict[str, list[Claim]] = {}
        for c in self.claims:
            if c.person:
                out.setdefault(c.person, []).append(c)
        return out

    def name(self, person: str, among=None) -> str | None:
        """The best name of a cluster: accepted first, then in reading order."""
        names = [
            c
            for c in (among or self.claims)
            if c.person == person and c.prop == Prop.NAME
        ]
        names.sort(key=lambda c: (c.tier != Tier.ACCEPTED, c.sensitive, str(c.id)))
        return names[0].text if names else None


def cluster_of(groups: dict, subject: str) -> str:
    p = people.pid(subject)
    return f"person:{min(groups.get(p, {p}))}"


async def case(db, investigation: str) -> Case:
    src = mfilter.source(mfilter.statement_blob()).label("src")
    rows = (
        await db.execute(
            select(Statement, StatementTier.tier, Payload.value, src)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            # Inner join: an erased value has no payload, so it is never read.
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(
                Statement.prop.in_(EXTRACTED_PROPS),
                StatementTier.tier != Tier.REJECTED,
                write.current_clause(),
                mfilter.statement_clause(
                    {"investigation": investigation, "time": None}
                ),
                available(mfilter.source(mfilter.statement_blob())),
            )
            .order_by(Statement.recorded_at, Statement.id)
        )
    ).all()
    shas = {r.src for r in rows}
    ctx = await Context.load(db, blobs=shas)
    mimes = dict(
        (await db.execute(select(Blob.sha256, Blob.mime).where(Blob.sha256.in_(shas))))
        .tuples()
        .all()
    )
    docs = {sha: ctx.doc(sha) | {"mime": mimes.get(sha, "")} for sha in shas}
    groups, _ = await people.identity(db)
    claims = [
        Claim(
            id=st.id,
            subject=st.subject,
            person=cluster_of(groups, st.subject)
            if st.subject.startswith("person:")
            else None,
            prop=st.prop,
            text=display(value, st.prop) or "",
            lo=st.valid_from,
            hi=st.valid_to or st.valid_from,
            source=sha,
            span_blob=st.span_blob,
            span=st.span,
            tier=tier,
            sensitive=docs[sha]["sensitive"],
        )
        for st, tier, value, sha in rows
    ]
    located = [c.id for c in claims if c.prop in LOCATED]
    places = await resolve.resolved(db, located, investigation) if located else {}
    return Case(investigation, claims, docs, places, groups)


def place_name(place) -> str:
    return place.name_en or place.name_ar


def same_value(prop: str, a: str, b: str) -> bool:
    a, b = normalise(a), normalise(b)
    if a == b:
        return True
    # A year-only entry equals a full date at the coarser precision.
    short, long = sorted((a, b), key=len)
    return prop == Prop.BIRTH_DATE and bool(short) and long.startswith(short + "-")


def overlap(a: Claim, b: Claim) -> bool:
    return bool(a.lo and b.lo and a.lo <= b.hi and b.lo <= a.hi)


MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def when(lo: date | None, hi: date | None) -> str | None:
    """A claim's time in words: "2 August 2013", "July 2013", "2013"."""
    if lo is None:
        return None
    hi = hi or lo
    if lo == hi:
        return f"{lo.day} {MONTHS[lo.month - 1]} {lo.year}"
    if (lo.month, lo.day, hi.month, hi.day) == (1, 1, 12, 31) and lo.year == hi.year:
        return str(lo.year)
    if lo.day == 1 and (lo.year, lo.month) == (hi.year, hi.month):
        return f"{MONTHS[lo.month - 1]} {lo.year}"
    return f"{when(lo, lo)} to {when(hi, hi)}"


class Handles:
    """Short names for what the model may cite. The model sees "c3", never an id."""

    def __init__(self):
        self.ref: dict[str, object] = {}
        self._of: dict[tuple, str] = {}
        self._n: dict[str, int] = {}

    def __call__(self, prefix: str, obj) -> str:
        key = (prefix, obj)
        if key not in self._of:
            self._n[prefix] = self._n.get(prefix, 0) + 1
            h = f"{prefix}{self._n[prefix]}"
            self.ref[h], self._of[key] = obj, h
        return self._of[key]


def claim_view(h: Handles, c: Claim, docs: dict) -> dict:
    return {
        "h": h("c", c.id),
        "field": c.prop.replace("_", " "),
        "value": c.text,
        "when": when(c.lo, c.hi),
        "source": docs[c.source]["title"],
        "decided": c.tier == Tier.ACCEPTED,
    }


def leaks(text: str, cited: set, case_claims: list[Claim]) -> bool:
    """A sentence names a personal value from a sensitive source it does not cite.
    Masking follows citations, so such a sentence would show a value to a reader
    who never revealed its source."""
    shown = normalise(text)
    sources = {c.source for c in case_claims if c.id in cited}
    # A value an ordinary source also gives is no secret of the sensitive one.
    open_values = {normalise(c.text) for c in case_claims if not c.sensitive}
    for c in case_claims:
        value = normalise(c.text)
        if (
            c.sensitive
            and c.prop in PERSONAL
            and c.source not in sources
            and len(value) >= 3
            and value not in open_values
            and value in shown
        ):
            return True
    return False


# --- model ----------------------------------------------------------------------


async def ask(c, name: str, system: str, payload: dict, schema: dict, fixed) -> dict:
    """One structured call on the run's route, or the fixed answer when the run asked
    for the demo model. Returns {"answer", "valid", "seconds", tokens}; an answer
    that breaks the schema is reported invalid and used as empty."""
    started = time.monotonic()
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": orjson.dumps(payload).decode()},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        },
        "temperature": 0,
        # Local thinking models spend the context deliberating; the answer here is
        # a short structured judgement over what we hand them.
        **({"reasoning_effort": "none"} if c.run.route == "local" else {}),
    }
    usage = {}
    if c.run.model_requested == FIXED:
        answer = fixed(payload)
        raw = orjson.dumps({"model": FIXED, "answer": answer, "demo": True})
        c.run.endpoint, c.run.model_served = "demo:fixed-output", FIXED
        c.run.provider_served = "demo"
        c.run.raw_output_ref = (
            await blobs.put(c.s, raw, "application/json", Config.BLOB_DIR)
        ).sha256
        await c.emit("model_called", calls=1)
    else:
        served = await c.call(body)
        usage = served.data.get("usage") or {}
        try:
            answer = orjson.loads(routes.content(served))
        except orjson.JSONDecodeError:
            answer = None
    errors = (
        ["not JSON"]
        if answer is None
        else [e.message for e in Draft202012Validator(schema).iter_errors(answer)]
    )
    if errors:
        await c.emit("model_output", drop_reason=f"invalid: {errors[0]}"[:300])
    return {
        "answer": answer if not errors else None,
        "valid": not errors,
        "raw": c.run.raw_output_ref,
        "seconds": round(time.monotonic() - started, 1),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


def prompt_hash(*parts) -> str:
    return chain.sha256(chain.canon(list(parts)))


async def store(c, output: dict) -> str:
    """The run's output as a stored JSON blob; its hash is what the run seals."""
    blob = await blobs.put(
        c.s,
        orjson.dumps(output, option=orjson.OPT_SORT_KEYS),
        "application/json",
        Config.BLOB_DIR,
    )
    return blob.sha256


def read_output(blob: Blob) -> dict:
    return orjson.loads(blobs.read(blob, Config.BLOB_DIR))


# --- who runs it ------------------------------------------------------------------


async def agent_for(db, name: str, user_id: int) -> Principal:
    """The agent principal acting for this person: its proposals show as
    "<name> for <person>", and it reads under that person's access."""
    human = await db.scalar(select(Principal).where(Principal.user_id == user_id))
    if human is None:
        raise LookupError("this person has no principal yet")
    found = await db.scalar(
        select(Principal).where(
            Principal.kind == "agent",
            Principal.name == name,
            Principal.on_behalf_of == human.id,
        )
    )
    if found:
        return found
    agent = Principal(kind="agent", name=name, on_behalf_of=human.id)
    db.add(agent)
    await db.flush()
    return agent


async def log_read(c, subject: str, ids) -> None:
    """The run's reading on the custody record, as an access by the agent for its
    person, written in the run that read."""
    await write.append(
        c.s,
        StatementIn(
            subject=subject,
            prop=Prop.ACCESS,
            value={
                "action": "read",
                "at": datetime.now(UTC).isoformat(),
                "run": str(c.run.id),
                "read": sorted(str(i) for i in ids),
            },
            author_id=c.run.principal_id,
            run_id=c.run.id,
        ),
        sign=c.sign,
    )


def note(what: str) -> str:
    return (
        f"demo: {what} from the fixed answer, no model ran; synthetic output for "
        "showing the workflow and for tests, not a measurement"
    )


async def incidents(db, investigation: str, groups: dict) -> list[dict]:
    """Accepted incidents tied to the case, with their places and windows and the
    people involved or proposed (any tier but rejected)."""
    from stk.review.findings import incidents as listed

    found = await listed(db, investigation)
    if not found:
        return []
    subjects = [i["id"] for i in found]
    rows = (
        await db.execute(
            select(
                Statement.subject,
                Statement.prop,
                Payload.value,
                Statement.valid_from,
                Statement.valid_to,
                StatementTier.tier,
            )
            .join(Payload, Payload.commitment == Statement.commitment)
            .join(StatementTier, StatementTier.statement_id == Statement.id)
            .where(
                Statement.subject.in_(subjects),
                Statement.prop.in_([Prop.OCCURRED_AT, Prop.INVOLVED]),
                StatementTier.tier != Tier.REJECTED,
            )
        )
    ).all()
    out = {i["id"]: i | {"places": {}, "people": set()} for i in found}
    for subject, prop, value, lo, hi, tier in rows:
        inc = out[subject]
        if prop == Prop.OCCURRED_AT and tier == Tier.ACCEPTED:
            place = uuid.UUID(value["place"].removeprefix("place:"))
            inc["places"][place] = (lo, hi or lo)
        elif prop == Prop.INVOLVED:
            inc["people"].add(cluster_of(groups, value["person"]))
    return list(out.values())
