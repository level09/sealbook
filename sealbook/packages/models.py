"""Briefs and packages. Briefs are mutable working drafts, outside the chain; what
a package discloses is fixed by its manifest blob, and its attestations, PACKAGE
and export records are ledger statements."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import relationship
from stk.extensions import Base

from sealbook.ledger.models import ARRAY


def _now():
    return datetime.now(UTC)


class Brief(Base):
    __tablename__ = "brief"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    title = Column(Text, nullable=False)
    audience = Column(Text, nullable=False)  # a key of packages.service.PROFILES
    author_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    sentences = relationship("Sentence", lazy="selectin", order_by="Sentence.ord")


class Sentence(Base):
    __tablename__ = "sentence"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    brief_id = Column(Uuid, ForeignKey("brief.id"), nullable=False, index=True)
    ord = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    statement_ids = Column(ARRAY(Uuid), nullable=False)


class Package(Base):
    """One build of a brief. The zip is kept as a blob so the bytes people attest
    and download never change after the build."""

    __tablename__ = "package"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    brief_id = Column(Uuid, ForeignKey("brief.id"), nullable=False, index=True)
    audience = Column(Text, nullable=False)
    manifest_sha256 = Column(
        String(64), ForeignKey("blob.sha256"), nullable=False, unique=True
    )
    zip_sha256 = Column(String(64), ForeignKey("blob.sha256"), nullable=False)
    statements = Column(Integer, nullable=False)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    finalized_at = Column(DateTime(timezone=True), nullable=True)
    # The frozen zip plus attestations.json, written at finalize.
    export_sha256 = Column(String(64), ForeignKey("blob.sha256"), nullable=True)
    # Every statement the manifest discloses, so erasure can find the package.
    statement_ids = Column(ARRAY(Uuid), nullable=False, server_default="{}")
    revoked_at = Column(DateTime(timezone=True), nullable=True)
