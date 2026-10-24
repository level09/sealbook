"""Anchor tables. Both insert-only by trigger: a retried anchor is a new row."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    LargeBinary,
    String,
    Text,
)

from stk.extensions import Base
from stk.ledger.models import _now


class Batch(Base):
    __tablename__ = "batch"

    id = Column(BigInteger, Identity(), primary_key=True)
    first_seq = Column(BigInteger, nullable=False, unique=True)
    last_seq = Column(BigInteger, nullable=False, unique=True)
    size = Column(BigInteger, nullable=False)
    root = Column(String(64), nullable=False)
    at = Column(DateTime(timezone=True), default=_now, nullable=False)


class Anchor(Base):
    __tablename__ = "anchor"

    id = Column(BigInteger, Identity(), primary_key=True)
    batch_id = Column(BigInteger, ForeignKey("batch.id"), nullable=False, index=True)
    kind = Column(
        Text,
        CheckConstraint("kind in ('ots','tsa','partner')", name="kind"),
        nullable=False,
    )
    # Where it went: calendar list, TSA URL or partner directory.
    target = Column(Text, nullable=False)
    receipt = Column(LargeBinary, nullable=True)
    error = Column(Text, nullable=True)
    at = Column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        CheckConstraint("(receipt is null) <> (error is null)", name="outcome"),
    )
