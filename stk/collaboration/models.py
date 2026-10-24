"""Stored notifications and work assignment. Neither holds evidence values:
notifications name items by kind and short id, assignments hold subject refs."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from stk.extensions import Base

NOTIFICATION_KINDS = (
    "assigned",
    "mention",
    "reply",
    "decision",
    "intake_done",
    "standing_match",
    "agent_paused",
)
PRIORITIES = ("low", "normal", "high")
STATUSES = ("open", "done", "returned")


def _now():
    return datetime.now(UTC)


class Notification(Base):
    __tablename__ = "notification"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    kind = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    subtitle = Column(Text, nullable=False, default="")
    link = Column(Text, nullable=False)
    subject = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_notification_user_created", "user_id", "created_at"),
        Index(
            "ix_notification_unread",
            "user_id",
            postgresql_where=read_at.is_(None),
        ),
    )


class Assignment(Base):
    __tablename__ = "assignment"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation = Column(Text, nullable=False)
    subject = Column(
        Text, nullable=False
    )  # blob:<sha> | statement:<uuid> | finding:<key>
    assignee_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    assigned_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    due = Column(Date, nullable=True)
    priority = Column(Text, nullable=False, default="normal")
    status = Column(Text, nullable=False, default="open")
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        Index("ix_assignment_investigation", "investigation", "status"),
        Index(
            "uq_assignment_open_subject",
            "investigation",
            "subject",
            unique=True,
            postgresql_where=status == "open",
        ),
    )


class AssignmentEvent(Base):
    """Append-only (a trigger refuses update and delete): who did what, when."""

    __tablename__ = "assignment_event"

    id = Column(
        BigInteger().with_variant(Integer(), "sqlite"), Identity(), primary_key=True
    )
    assignment_id = Column(
        UUID(as_uuid=True), ForeignKey("assignment.id"), nullable=False, index=True
    )
    actor_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    action = Column(Text, nullable=False)  # assigned|reassigned|status|updated
    detail = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    at = Column(DateTime(timezone=True), default=_now, nullable=False)
