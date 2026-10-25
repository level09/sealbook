"""The single write path for chained rows, plus tier projection and DB verification.
Everything that lands in statement, acquisition or a sealed run goes through here."""

from __future__ import annotations

import base64
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import orjson
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import Text, cast, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from stk.user.models import User, WebAuthn

from sealbook.ledger import chain, offline_sigs
from sealbook.ledger import origins as origin_graph
from sealbook.ledger.blobmeta import span_check
from sealbook.ledger.contracts import (
    Event,
    GateInput,
    PolicyRow,
    PrincipalKind,
    Prop,
    Refused,
    StatementIn,
    Tier,
)
from sealbook.ledger.gate import gate, resolve
from sealbook.ledger.models import (
    Acquisition,
    AttestKey,
    Blob,
    Chain,
    Payload,
    Policy,
    Principal,
    Run,
    RunEvent,
    Statement,
    StatementTier,
)

# ponytail: one global advisory lock serialises chain appends; shard the chain per
# investigation if write throughput ever matters.
CHAIN_LOCK = 0x5EA1B00C


def row_dict(obj, kind: str) -> dict:
    return {name: getattr(obj, name) for name in chain.CHAINED_FIELDS[kind]}


async def lock_chain(session: AsyncSession) -> None:
    """Held until the transaction ends; taking it again in the same one is free."""
    await session.execute(text("select pg_advisory_xact_lock(:k)"), {"k": CHAIN_LOCK})


async def append_chain(
    session: AsyncSession, kind: str, ref: str, row_hash: str
) -> Chain:
    await lock_chain(session)
    last = (
        await session.execute(select(Chain).order_by(Chain.seq.desc()).limit(1))
    ).scalar()
    seq = last.seq + 1 if last else 1
    prev = last.hash if last else chain.GENESIS
    entry = Chain(
        seq=seq,
        kind=kind,
        ref=ref,
        row_hash=row_hash,
        prev_hash=prev,
        hash=chain.link(prev, seq, kind, row_hash),
    )
    session.add(entry)
    await session.flush()
    return entry


async def principal_for_user(
    session: AsyncSession, user_id: int, name: str
) -> Principal:
    """The human principal behind a logged-in user, created on first use."""
    found = (
        await session.execute(select(Principal).where(Principal.user_id == user_id))
    ).scalar()
    if found:
        return found
    principal = Principal(kind=PrincipalKind.HUMAN, name=name, user_id=user_id)
    session.add(principal)
    await session.flush()
    return principal


# --- statements ---------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    """A statement ready to commit. `hash` is what an agent signs and what a human's
    passkey assertion uses as its challenge; review cards render from `row`."""

    row: dict
    hash: str
    payload: dict
    existing: uuid.UUID | None = None


def _idem_key(s: StatementIn) -> str:
    # Keyed on the author, not the run: an agent retrying in a new run (every MCP
    # call is one) gets its existing statement back.
    return chain.sha256(
        chain.canon([s.author_id, s.subject, s.prop, s.value, s.span_blob, s.span])
    )


async def _existing(session: AsyncSession, idem_key: str) -> uuid.UUID | None:
    q = (
        select(Statement.id)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(Payload.idem_key == idem_key)
    )
    return (await session.execute(q)).scalar()


async def _refuse_if_blocked(
    session: AsyncSession,
    subject: str,
    prop: str,
    span_blob: str | None,
    value_key: str,
    own: uuid.UUID | None = None,
) -> None:
    """`own` is the author's identical earlier statement: a retry of it is not a
    duplicate, but a human rejection still refuses."""
    if prop == Prop.ACCESS:
        return  # access records are facts about use, not claims to review
    q = (
        select(
            Statement.id, StatementTier.tier, StatementTier.reason, Statement.span_blob
        )
        .join(Payload, Payload.commitment == Statement.commitment)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.subject == subject,
            Statement.prop == prop,
            Payload.value_key == value_key,
        )
    )
    for sid, tier, reason, blob in (await session.execute(q)).all():
        if reason == "human_reject":
            raise Refused("value was rejected by a human for this subject", sid)
        # The same value from another document is corroboration, not a duplicate.
        if tier == Tier.UNCONFIRMED and sid != own and blob == span_blob:
            raise Refused("the same value is already pending review", sid)


async def prepare(session: AsyncSession, s: StatementIn) -> Prepared:
    existing = await _existing(session, _idem_key(s))
    value_key = chain.sha256(chain.canon(s.value))
    await _refuse_if_blocked(
        session, s.subject, s.prop, s.span_blob, value_key, own=existing
    )
    if existing:
        return Prepared(row={}, hash="", payload={}, existing=existing)
    salt = os.urandom(32)
    row = {
        "id": uuid.uuid4(),
        "subject": s.subject,
        "prop": str(s.prop),
        "commitment": chain.commitment(salt, s.value),
        "span_blob": s.span_blob,
        "span": s.span,
        "author_id": s.author_id,
        "run_id": s.run_id,
        "valid_from": s.valid_from,
        "valid_to": s.valid_to,
        "supersedes": s.supersedes,
        "evidence": [str(e) for e in s.evidence],
        "based_on": s.based_on,
        "recorded_at": datetime.now(UTC),
    }
    payload = {
        "commitment": row["commitment"],
        "salt": salt,
        "value": s.value,
        "value_key": value_key,
        "idem_key": _idem_key(s),
    }
    return Prepared(row=row, hash=chain.row_hash("statement", row), payload=payload)


async def commit(
    session: AsyncSession, p: Prepared, sig: dict | None = None
) -> Statement:
    """Write a prepared statement. Agent statements need a run and a valid ed25519
    signature by that run's key. Webauthn assertions are verified by the caller
    before they reach here. The duplicate and refusal checks run again under the
    chain lock: prepare's run unlocked, so a concurrent writer may have landed since."""
    await lock_chain(session)
    if p.existing:
        # A retry returns the author's statement, unless a human has rejected that
        # value since prepare looked.
        st = await session.get(Statement, p.existing)
        value_key = await session.scalar(
            select(Payload.value_key).where(Payload.commitment == st.commitment)
        )
        await _refuse_if_blocked(
            session, st.subject, st.prop, st.span_blob, value_key, own=st.id
        )
        return st
    existing = await _existing(session, p.payload["idem_key"])
    await _refuse_if_blocked(
        session,
        p.row["subject"],
        p.row["prop"],
        p.row["span_blob"],
        p.payload["value_key"],
        own=existing,
    )
    if existing:
        return await session.get(Statement, existing)
    author = await session.get(Principal, p.row["author_id"])
    if author.kind == PrincipalKind.AGENT:
        run = await session.get(Run, p.row["run_id"]) if p.row["run_id"] else None
        if run is None or run.pubkey is None:
            raise Refused("agent statements need a run with a signing key")
        if (
            p.row["prop"] == Prop.INVOLVED
            and p.payload["value"].get("role") != "unspecified"
        ):
            raise Refused("agents may only record an involved role as unspecified")
        if not sig or sig.get("kind") != "ed25519":
            raise Refused("agent statements must be signed by their run")
        try:
            Ed25519PublicKey.from_public_bytes(run.pubkey).verify(
                bytes.fromhex(sig["sig"]), p.hash.encode()
            )
        except (InvalidSignature, ValueError) as exc:
            raise Refused("run signature does not verify") from exc
    session.add(Payload(**p.payload))
    stmt = Statement(**p.row, hash=p.hash, sig=sig)
    session.add(stmt)
    await session.flush()
    await append_chain(session, "statement", str(stmt.id), p.hash)
    await project(session, stmt)
    return stmt


async def append(session: AsyncSession, s: StatementIn, sign=None) -> Statement:
    """prepare + commit in one transaction. `sign(hash) -> sig dict` for agents."""
    p = await prepare(session, s)
    if p.existing:
        return await session.get(Statement, p.existing)
    return await commit(session, p, sign(p.hash) if sign else None)


async def subject_head(session: AsyncSession, subject: str) -> str | None:
    """Latest accepted claim or review that established its current acceptance."""
    fact = aliased(Statement)
    tier = aliased(StatementTier)
    accepted = (
        select(fact.id)
        .join(tier, tier.statement_id == fact.id)
        .where(fact.subject == subject, tier.tier == Tier.ACCEPTED)
    )
    q = (
        select(Statement.hash)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .join(
            Chain, (Chain.kind == "statement") & (Chain.ref == cast(Statement.id, Text))
        )
        .where(
            StatementTier.tier == Tier.ACCEPTED,
            or_(
                Statement.id.in_(accepted),
                (Statement.prop == Prop.REVIEW)
                & Statement.subject.in_(
                    select(func.concat("statement:", cast(fact.id, Text)))
                    .join(tier, tier.statement_id == fact.id)
                    .where(fact.subject == subject, tier.tier == Tier.ACCEPTED)
                ),
            ),
        )
        .order_by(Chain.seq.desc())
        .limit(1)
    )
    return (await session.execute(q)).scalar()


def current_clause():
    """An accepted correction replaces its predecessor in current factual views."""
    replacement = aliased(Statement)
    tier = aliased(StatementTier)
    return (
        ~select(replacement.id)
        .join(tier, tier.statement_id == replacement.id)
        .where(
            replacement.supersedes == Statement.id,
            tier.tier == Tier.ACCEPTED,
        )
        .exists()
    )


# --- tier projection -----------------------------------------------------------


async def policies(session: AsyncSession) -> list[PolicyRow]:
    rows = (await session.execute(select(Policy))).scalars()
    return [
        PolicyRow(
            capability=r.capability,
            doc_type=r.doc_type,
            sensitivity=r.sensitivity,
            routes=tuple(r.routes),
            auto_accept=r.auto_accept,
            min_origins=r.min_origins,
            calibration=r.calibration,
        )
        for r in rows
    ]


async def sig_problem(session: AsyncSession, st: Statement) -> str | None:
    """Why a stored signature does not hold, or None. `sig` sits outside the chain,
    so nothing may trust it unverified: it must sign the statement hash (an ATTEST
    signs the manifest hash it names), and a passkey must be one registered to the
    author's user."""
    sig = st.sig
    if not sig:
        return None
    digest = st.subject.removeprefix("blob:") if st.prop == Prop.ATTEST else st.hash
    run = await session.get(Run, st.run_id) if st.run_id else None
    pubkey = run.pubkey.hex() if run and run.pubkey else None
    try:
        offline_sigs.verify_statement_signature(digest, sig, pubkey)
        if sig.get("kind") != "webauthn":
            return None
        public_key = base64.urlsafe_b64decode(
            sig["public_key"] + "=" * (-len(sig["public_key"]) % 4)
        )
    except (ValueError, KeyError, TypeError) as exc:
        return str(exc)
    user_id = select(Principal.user_id).where(Principal.id == st.author_id)
    logged = select(AttestKey.id).where(
        AttestKey.user_id == user_id.scalar_subquery(),
        AttestKey.public_key == public_key,
    )
    registered = (
        select(WebAuthn.id)
        .join(User, User.fs_webauthn_user_handle == WebAuthn.user_id)
        .join(Principal, Principal.user_id == User.id)
        .where(Principal.id == st.author_id, WebAuthn.public_key == public_key)
    )
    # A removed passkey's key stays in the log, so its old signatures still hold.
    known = await session.scalar(select(logged.exists() | registered.exists()))
    return None if known else "passkey is not registered to the author"


async def _attested(session: AsyncSession, st: Statement) -> bool:
    return (
        bool(st.sig)
        and st.sig.get("kind") == "webauthn"
        and await sig_problem(session, st) is None
    )


async def gate_input(
    session: AsyncSession, st: Statement
) -> tuple[GateInput, str, str, str]:
    author = await session.get(Principal, st.author_id)
    run = await session.get(Run, st.run_id) if st.run_id else None
    about = f"statement:{st.id}"
    meta = (
        await session.execute(
            select(Statement, Payload.value)
            .outerjoin(Payload, Payload.commitment == Statement.commitment)
            .where(
                Statement.subject == about,
                Statement.prop.in_([Prop.REVIEW, Prop.RETRACTION]),
            )
            .order_by(Statement.recorded_at)
        )
    ).all()
    # Undoing a review is an attested retraction of the review statement itself.
    undoes = (
        await session.execute(
            select(Statement).where(
                Statement.prop == Prop.RETRACTION,
                Statement.subject.in_(
                    [f"statement:{m.id}" for m, _ in meta if m.prop == Prop.REVIEW]
                ),
                Statement.sig["kind"].astext == "webauthn",
            )
        )
    ).scalars()
    undone = {u.subject for u in undoes if await _attested(session, u)}
    verdict, retracted = None, False
    for m, value in meta:
        if m.prop == Prop.RETRACTION and (
            m.author_id == st.author_id or await _attested(session, m)
        ):
            retracted = True
        elif (
            m.prop == Prop.REVIEW
            and await _attested(session, m)
            and value
            and f"statement:{m.id}" not in undone
        ):
            verdict = value["verdict"]
    acq = None
    origins = 0
    if st.span_blob:
        from sealbook.monitor.filter import source

        acq = (
            await session.execute(
                select(Acquisition)
                .where(Acquisition.blob_sha256 == source(st.span_blob))
                .order_by(Acquisition.at)
                .limit(1)
            )
        ).scalar()
        origins = await origin_graph.count(session, st)
    gi = GateInput(
        prop=Prop(st.prop),
        author_kind=PrincipalKind(author.kind),
        has_span=st.span_blob is not None and st.span is not None,
        span_valid=span_check(
            await session.get(Blob, st.span_blob) if st.span_blob else None, st.span
        )[0],
        attested=await _attested(session, st),
        verdict=verdict,
        retracted=retracted,
        distinct_origins=origins,
    )
    capability = run.tool if run else "human"
    return (
        gi,
        capability,
        acq.doc_type if acq else "none",
        acq.sensitivity if acq else "sensitive",
    )


async def project(session: AsyncSession, st: Statement) -> None:
    """Recompute tiers touched by a new statement: itself, the statement it reviews
    or retracts, and siblings whose corroboration count may have changed."""
    targets = {st.id}
    if st.prop in (Prop.REVIEW, Prop.RETRACTION) and st.subject.startswith(
        "statement:"
    ):
        about = uuid.UUID(st.subject.removeprefix("statement:"))
        targets.add(about)
        # Retracting a review changes the verdict on the statement it reviewed.
        reviewed = await session.get(Statement, about)
        if (
            st.prop == Prop.RETRACTION
            and reviewed.prop == Prop.REVIEW
            and reviewed.subject.startswith("statement:")
        ):
            targets.add(uuid.UUID(reviewed.subject.removeprefix("statement:")))
    if st.span_blob:
        value_key = (
            select(Payload.value_key)
            .where(Payload.commitment == st.commitment)
            .scalar_subquery()
        )
        siblings = await session.execute(
            select(Statement.id)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(
                Statement.subject == st.subject,
                Statement.prop == st.prop,
                Payload.value_key == value_key,
            )
        )
        targets.update(siblings.scalars())
    rows = await policies(session)

    async def recompute(sid):
        target = await session.get(Statement, sid)
        gi, capability, doc_type, sensitivity = await gate_input(session, target)
        tier, reason = gate(gi, resolve(rows, capability, doc_type, sensitivity))
        await session.execute(
            pg_insert(StatementTier)
            .values(
                statement_id=sid,
                tier=tier,
                reason=reason,
                computed_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=[StatementTier.statement_id],
                set_={"tier": tier, "reason": reason, "computed_at": datetime.now(UTC)},
            )
        )

    for sid in targets:
        await recompute(sid)

    async def refresh_support(changed):
        from sealbook.discovery.service import actor_changed

        affected = list(
            (
                await session.execute(
                    select(Statement)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .where(
                        tuple_(
                            Statement.subject, Statement.prop, Payload.value_key
                        ).in_(changed.correlate(None))
                    )
                )
            ).scalars()
        )
        for target in affected:
            await recompute(target.id)
            await actor_changed(session, target)

    reviewed = (
        [await session.get(Statement, sid) for sid in targets]
        if st.prop in (Prop.REVIEW, Prop.RETRACTION)
        else []
    )
    corrections = [row for row in reviewed if row.supersedes]
    if corrections:
        await refresh_support(
            select(Statement.subject, Statement.prop, Payload.value_key)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(
                Statement.id.in_(
                    [sid for row in corrections for sid in (row.id, row.supersedes)]
                )
            )
        )
    # A reviewed copy relationship can change corroboration on every matching claim.
    media = [row for row in reviewed if row.prop == Prop.MEDIA_MATCH]
    for match in media:
        value = await session.get(Payload, match.commitment)
        if value is None:
            continue
        from sealbook.monitor.filter import source

        components = await origin_graph.groups(
            session, {match.subject.removeprefix("blob:"), value.value["blob"]}
        )
        fact, payload = aliased(Statement), aliased(Payload)
        changed = (
            select(fact.subject, fact.prop, payload.value_key)
            .join(payload, payload.commitment == fact.commitment)
            .where(source(fact.span_blob).in_(list(components)))
        )
        await refresh_support(changed)


# --- acquisitions and runs -----------------------------------------------------


async def record_acquisition(session: AsyncSession, **fields) -> Acquisition:
    acq = Acquisition(id=uuid.uuid4(), **fields)
    session.add(acq)
    await session.flush()
    await append_chain(
        session,
        "acquisition",
        str(acq.id),
        chain.row_hash("acquisition", row_dict(acq, "acquisition")),
    )
    return acq


async def seal_run(session: AsyncSession, run: Run, output_hash: str | None) -> Chain:
    """Mark a run done and chain its provenance manifest. After this, editing the
    run's route, model or inputs breaks verification."""
    run.output_hash = output_hash
    run.finished_at = datetime.now(UTC)
    run.status = "done"
    run.lease_until = None
    await session.flush()
    return await append_chain(
        session, "run", str(run.id), chain.row_hash("run", row_dict(run, "run"))
    )


async def emit(session: AsyncSession, e: Event) -> RunEvent:
    row = RunEvent(
        run_id=e.run_id,
        seq=e.seq,
        stage=e.stage,
        counts=e.counts,
        drop_reason=e.drop_reason,
        causation_id=e.causation_id,
        correlation_id=e.correlation_id,
        human_gesture=e.human_gesture,
    )
    session.add(row)
    await session.flush()
    await session.execute(
        text("select pg_notify('sealbook', :p)"),
        {
            "p": orjson.dumps(
                {"kind": "run_event", "id": row.id, "run_id": str(e.run_id)}
            ).decode()
        },
    )
    return row


# --- verification --------------------------------------------------------------


async def verify_db(session: AsyncSession) -> list[str]:
    """Recompute every chained row from the database and check the chain, each
    statement's stored hash and signature, and every surviving payload against
    its commitment."""
    entries = [
        chain.Entry(e.seq, e.kind, e.ref, e.row_hash, e.prev_hash, e.hash)
        for e in (await session.execute(select(Chain).order_by(Chain.seq))).scalars()
    ]
    recomputed = {}
    for kind, model in (
        ("statement", Statement),
        ("acquisition", Acquisition),
        ("run", Run),
    ):
        for obj in (await session.execute(select(model))).scalars():
            if kind == "run" and obj.finished_at is None:
                continue
            recomputed[(kind, str(obj.id))] = chain.row_hash(kind, row_dict(obj, kind))
    problems = chain.verify(entries, recomputed)
    chained = {(e.kind, e.ref) for e in entries}
    problems += [
        f"{k} {r} is not in the chain" for k, r in recomputed if (k, r) not in chained
    ]
    for st in (await session.execute(select(Statement))).scalars():
        if st.hash != recomputed[("statement", str(st.id))]:
            problems.append(f"statement {st.id}: stored hash does not match its row")
        author = await session.get(Principal, st.author_id)
        if author.kind == PrincipalKind.AGENT and not st.sig:
            problems.append(f"statement {st.id}: agent statement is unsigned")
        if why := await sig_problem(session, st):
            problems.append(f"statement {st.id}: signature fails: {why}")
    for commitment, salt, value in await session.execute(
        select(Payload.commitment, Payload.salt, Payload.value)
    ):
        if chain.commitment(salt, value) != commitment:
            problems.append(f"payload {commitment} does not match its commitment")
    return problems
