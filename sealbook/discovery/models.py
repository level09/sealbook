"""Rebuildable search indexes. The signed run and its artifact remain authoritative."""

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import BIT, HALFVEC
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from stk.extensions import Base

DIMENSIONS = 768


class MediaFrame(Base):
    __tablename__ = "media_frame"

    id = Column(
        BigInteger().with_variant(Integer(), "sqlite"), Identity(), primary_key=True
    )
    blob_sha256 = Column(
        String(64), ForeignKey("blob.sha256"), nullable=False, index=True
    )
    run_id = Column(
        UUID(as_uuid=True), ForeignKey("run.id"), nullable=False, index=True
    )
    ms = Column(Integer, nullable=False)
    quality = Column(Integer, nullable=False)
    hash = Column(BIT(256).with_variant(String(256), "sqlite"), nullable=False)

    __table_args__ = (
        UniqueConstraint("blob_sha256", "ms", name="uq_media_frame_blob_ms"),
        Index(
            "ix_media_frame_hash",
            "hash",
            postgresql_using="hnsw",
            postgresql_ops={"hash": "bit_hamming_ops"},
        ),
    )


class SearchChunk(Base):
    __tablename__ = "evidence_chunk"

    blob_sha256 = Column(String(64), ForeignKey("blob.sha256"), primary_key=True)
    start = Column(Integer, primary_key=True)
    end = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    normalized = Column(Text, nullable=False, server_default="")
    run_id = Column(
        UUID(as_uuid=True), ForeignKey("run.id"), nullable=False, index=True
    )

    __table_args__ = (
        Index(
            "ix_evidence_chunk_text",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
        Index(
            "ix_evidence_chunk_normalized",
            "normalized",
            postgresql_using="gin",
            postgresql_ops={"normalized": "gin_trgm_ops"},
        ),
    )


class Segment(Base):
    __tablename__ = "evidence_segment"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_sha256 = Column(
        String(64), ForeignKey("blob.sha256"), nullable=False, index=True
    )
    kind = Column(Text, nullable=False)
    ordinal = Column(Integer, nullable=False)
    text_sha256 = Column(String(64), ForeignKey("blob.sha256"), index=True)
    start = Column(Integer, nullable=False, server_default="0")
    end = Column(Integer)
    location = Column(JSON, nullable=False)
    artifact_sha256 = Column(String(64), ForeignKey("blob.sha256"))
    run_id = Column(UUID(as_uuid=True), ForeignKey("run.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "source_sha256",
            "kind",
            "ordinal",
            "start",
            name="uq_evidence_segment_location",
        ),
    )


class IndexProgress(Base):
    __tablename__ = "index_progress"

    source_sha256 = Column(String(64), ForeignKey("blob.sha256"), primary_key=True)
    tool = Column(Text, primary_key=True)
    part = Column(Text, primary_key=True)
    unit = Column(Text, nullable=False)
    completed = Column(Integer, nullable=False)
    total = Column(Integer, nullable=False)
    run_id = Column(UUID(as_uuid=True), ForeignKey("run.id"), nullable=False)


class EmbeddingSpace(Base):
    __tablename__ = "embedding_space"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(64), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    model_requested = Column(Text, nullable=False)
    model_served = Column(Text, nullable=False)
    dimensions = Column(Integer, nullable=False)
    recipe = Column(JSON, nullable=False)
    reference = Column(JSON, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index(
            "uq_embedding_space_active",
            "key",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active"),
        ),
    )


class SegmentEmbedding(Base):
    __tablename__ = "segment_embedding"

    segment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("evidence_segment.id", ondelete="CASCADE"),
        primary_key=True,
    )
    space_id = Column(
        UUID(as_uuid=True), ForeignKey("embedding_space.id"), primary_key=True
    )
    run_id = Column(UUID(as_uuid=True), ForeignKey("run.id"), nullable=False)
    vector = Column(HALFVEC(DIMENSIONS).with_variant(JSON(), "sqlite"), nullable=False)

    __table_args__ = (
        Index(
            "ix_segment_embedding_vector",
            "vector",
            postgresql_using="hnsw",
            postgresql_ops={"vector": "halfvec_cosine_ops"},
        ),
    )


class AudioLandmark(Base):
    __tablename__ = "audio_landmark"

    blob_sha256 = Column(String(64), ForeignKey("blob.sha256"), primary_key=True)
    hash = Column(BigInteger, primary_key=True, index=True)
    time = Column(Integer, primary_key=True)
    run_id = Column(
        UUID(as_uuid=True), ForeignKey("run.id"), nullable=False, index=True
    )


class EntityEmbedding(Base):
    __tablename__ = "entity_embedding"

    subject = Column(Text, primary_key=True)
    investigation = Column(Text, primary_key=True)
    space_id = Column(
        UUID(as_uuid=True), ForeignKey("embedding_space.id"), primary_key=True
    )
    content_hash = Column(String(64), nullable=False)
    run_id = Column(
        UUID(as_uuid=True), ForeignKey("run.id"), nullable=False, index=True
    )
    vector = Column(HALFVEC(DIMENSIONS).with_variant(JSON(), "sqlite"), nullable=False)

    __table_args__ = (
        Index(
            "ix_entity_embedding_vector",
            "vector",
            postgresql_using="hnsw",
            postgresql_ops={"vector": "halfvec_cosine_ops"},
        ),
    )


class FindingKey(Base):
    """Stable identity of a discovery finding, so a decision outlives the run."""

    __tablename__ = "finding_key"

    key = Column(String(64), primary_key=True)  # salted hash
    kind = Column(Text, nullable=False)
    # Part of the key material: a decision in one investigation never reaches another.
    investigation = Column(Text, nullable=False, index=True)
    state = Column(Text, nullable=False)  # deferred|open|accepted|rejected
    rank = Column(Integer, nullable=False)
    run_id = Column(
        UUID(as_uuid=True), ForeignKey("run.id"), nullable=False, index=True
    )
    statement_id = Column(UUID(as_uuid=True), ForeignKey("statement.id"))
    # Statement ids only, never values. Erasure clears both, keeping key and state.
    members = Column(JSON().with_variant(JSONB, "postgresql"))
    features = Column(JSON().with_variant(JSONB, "postgresql"))
    notes = Column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )  # statement ids added after rejection
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class FindingSalt(Base):
    """The deployment's one finding-key salt, created by its migration. Never logged."""

    __tablename__ = "finding_salt"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id = Column(Integer, primary_key=True)
    salt = Column(LargeBinary(32), nullable=False)
