"""Ledger tables. Postgres only. blob, acquisition, statement and chain are
insert-only (enforced by trigger in the migration); run status is mutable but a
completed run's provenance is sealed into the chain."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.dialects.postgresql import UUID
from stk.extensions import Base

# ponytail: SQLite variants exist only so stk's own unit tests, which create_all on
# in-memory SQLite, still run. The ledger itself is Postgres only (triggers, locks).
JSONB = PG_JSONB().with_variant(JSON(), "sqlite")


def ARRAY(item):  # noqa: N802
    return PG_ARRAY(item).with_variant(JSON(), "sqlite")


def _now():
    return datetime.now(UTC)


class Blob(Base):
    __tablename__ = "blob"

    sha256 = Column(String(64), primary_key=True)
    md5 = Column(String(32), nullable=False)  # ICC e-Court "Digital Signature" field
    size = Column(BigInteger, nullable=False)
    mime = Column(Text, nullable=False)
    storage_ref = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)


class Principal(Base):
    __tablename__ = "principal"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind = Column(
        Text, CheckConstraint("kind in ('human','agent')", name="kind"), nullable=False
    )
    name = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=True, unique=True)
    pubkey = Column(LargeBinary, nullable=True)
    # An agent acting for a human; never the human's own identity.
    on_behalf_of = Column(UUID(as_uuid=True), ForeignKey("principal.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)


class Acquisition(Base):
    __tablename__ = "acquisition"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    blob_sha256 = Column(
        String(64), ForeignKey("blob.sha256"), nullable=False, index=True
    )
    # Where the bytes came from, normalised so one source counts once
    # (e.g. "url:https://...", "channel:telegram/xyz", "device:<serial>").
    origin = Column(Text, nullable=False, index=True)
    collector_id = Column(
        UUID(as_uuid=True), ForeignKey("principal.id"), nullable=False
    )
    at = Column(DateTime(timezone=True), nullable=False)
    context = Column(JSONB, nullable=False, default=dict)
    sensitivity = Column(Text, nullable=False, default="sensitive")
    doc_type = Column(Text, nullable=False, default="unknown")
    partner = Column(Text, nullable=True)

    # Every case-scoped list filters on this; without it Postgres has no statistics.
    __table_args__ = (
        Index("ix_acquisition_investigation", text("(context ->> 'investigation')")),
    )


class Run(Base):
    __tablename__ = "run"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id = Column(
        UUID(as_uuid=True), ForeignKey("principal.id"), nullable=False
    )
    tool = Column(Text, nullable=False)  # the capability, e.g. "ocr", "extract", "hash"
    route = Column(Text, nullable=True)
    endpoint = Column(Text, nullable=True)
    model_requested = Column(Text, nullable=True)
    model_served = Column(Text, nullable=True)
    provider_served = Column(Text, nullable=True)
    prompt_hash = Column(String(64), nullable=True)
    # {"blobs": [sha256, ...]} plus tool-specific keys; the monitor links runs to
    # documents through "blobs".
    inputs = Column(JSONB, nullable=False, default=dict)
    output_hash = Column(String(64), nullable=True)
    raw_output_ref = Column(Text, nullable=True)
    pubkey = Column(LargeBinary, nullable=True)  # per-run ed25519 key
    # Queue state: mutable, outside the chain.
    status = Column(Text, nullable=False, default="queued", index=True)
    available_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class Statement(Base):
    __tablename__ = "statement"

    id = Column(UUID(as_uuid=True), primary_key=True)
    subject = Column(Text, nullable=False, index=True)
    prop = Column(Text, nullable=False, index=True)
    commitment = Column(String(64), nullable=False)
    span_blob = Column(String(64), ForeignKey("blob.sha256"), nullable=True, index=True)
    span = Column(JSONB, nullable=True)
    author_id = Column(UUID(as_uuid=True), ForeignKey("principal.id"), nullable=False)
    run_id = Column(UUID(as_uuid=True), ForeignKey("run.id"), nullable=True, index=True)
    valid_from = Column(Date, nullable=True)
    valid_to = Column(Date, nullable=True)
    supersedes = Column(UUID(as_uuid=True), ForeignKey("statement.id"), nullable=True)
    evidence = Column(JSONB, nullable=False, default=list)
    based_on = Column(String(64), nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False)
    hash = Column(String(64), nullable=False, unique=True)
    # {"kind": "ed25519", "sig": hex} or {"kind": "webauthn", assertion fields...}
    sig = Column(JSONB, nullable=True)


class Payload(Base):
    """Statement values, keyed by commitment. Deletable only by the erasure
    procedure. Fingerprints live here so erasure removes them too."""

    __tablename__ = "payload"

    commitment = Column(String(64), primary_key=True)
    salt = Column(LargeBinary(32), nullable=False)
    value = Column(JSONB, nullable=False)
    value_key = Column(String(64), nullable=False, index=True)  # sha256(canon(value))
    idem_key = Column(String(64), nullable=False, unique=True)


class Chain(Base):
    __tablename__ = "chain"

    seq = Column(BigInteger, primary_key=True, autoincrement=False)
    kind = Column(Text, nullable=False)
    ref = Column(Text, nullable=False)
    row_hash = Column(String(64), nullable=False)
    prev_hash = Column(String(64), nullable=False)
    hash = Column(String(64), nullable=False, unique=True)
    at = Column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (UniqueConstraint("kind", "ref", name="uq_chain_kind_ref"),)


class StatementTier(Base):
    """Projection: gate() output per statement. Rebuildable, never authoritative."""

    __tablename__ = "statement_tier"

    statement_id = Column(
        UUID(as_uuid=True), ForeignKey("statement.id"), primary_key=True
    )
    tier = Column(Text, nullable=False, index=True)
    reason = Column(Text, nullable=False)
    computed_at = Column(DateTime(timezone=True), default=_now, nullable=False)


class Policy(Base):
    __tablename__ = "policy"

    id = Column(Integer, primary_key=True)
    capability = Column(Text, nullable=False)
    doc_type = Column(Text, nullable=False)
    sensitivity = Column(Text, nullable=False)
    routes = Column(ARRAY(Text), nullable=False, default=list)
    auto_accept = Column(Boolean, nullable=False, default=False)
    min_origins = Column(Integer, nullable=True)
    calibration = Column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("capability", "doc_type", "sensitivity", name="uq_policy_key"),
    )


class RunEvent(Base):
    __tablename__ = "run_event"

    id = Column(BigInteger, Identity(), primary_key=True)  # global SSE replay cursor
    run_id = Column(UUID(as_uuid=True), ForeignKey("run.id"), nullable=False)
    seq = Column(Integer, nullable=False)
    stage = Column(Text, nullable=False)
    counts = Column(JSONB, nullable=False, default=dict)
    drop_reason = Column(Text, nullable=True)
    causation_id = Column(UUID(as_uuid=True), nullable=True)
    correlation_id = Column(UUID(as_uuid=True), nullable=True)
    human_gesture = Column(Boolean, nullable=False, default=False)
    at = Column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_event_run_seq"),)


class AttestKey(Base):
    """Every passkey public key that has signed for a user, kept after the passkey
    is removed so the signatures it made still verify. Insert-only."""

    __tablename__ = "attest_key"

    id = Column(BigInteger, Identity(), primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    public_key = Column(LargeBinary, nullable=False)
    first_used_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "public_key", name="uq_attest_key_user_key"),
    )
