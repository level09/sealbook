"""Recurring sheets. A dataset remembers the column mapping a person confirmed; a
version is one acquired sheet blob attached to it. No cell values are copied here:
the blob is the record, so erasure and access rules stay where they already are."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from stk.extensions import Base

JSONB_ = JSON().with_variant(JSONB, "postgresql")


class Dataset(Base):
    __tablename__ = "sheet_dataset"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    investigation = Column(Text, nullable=False, index=True)
    key_column = Column(Text, nullable=False)  # header text of the row identity
    mapping = Column(JSONB_, nullable=False, default=dict)  # header -> field
    created_by = Column(UUID(as_uuid=True), ForeignKey("principal.id"), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("investigation", "name", name="uq_sheet_dataset_name"),
    )


class DatasetVersion(Base):
    __tablename__ = "sheet_version"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id = Column(
        UUID(as_uuid=True), ForeignKey("sheet_dataset.id"), nullable=False, index=True
    )
    number = Column(Integer, nullable=False)
    blob_sha256 = Column(String(64), ForeignKey("blob.sha256"), nullable=False)
    acquisition_id = Column(
        UUID(as_uuid=True), ForeignKey("acquisition.id"), nullable=False, index=True
    )
    sheet = Column(Integer, nullable=False)
    # What the person confirmed for this version, as the extraction run read it.
    key_column = Column(Text, nullable=False)
    mapping = Column(JSONB_, nullable=False)
    diff = Column(JSONB_, nullable=False)  # counts only, no values
    confirmed_by = Column(
        UUID(as_uuid=True), ForeignKey("principal.id"), nullable=False
    )
    run_id = Column(UUID(as_uuid=True), ForeignKey("run.id"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("dataset_id", "blob_sha256", name="uq_sheet_version_blob"),
        UniqueConstraint("dataset_id", "number", name="uq_sheet_version_number"),
    )
