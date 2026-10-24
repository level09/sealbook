"""Standing work and deduplicated evidence notices, separate from evidence."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)

from stk.extensions import Base
from stk.ledger.models import JSONB


def _now():
    return datetime.now(UTC)


class AgentTask(Base):
    __tablename__ = "agent_task"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    investigation = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    spec = Column(JSONB, nullable=False)
    every_seconds = Column(Integer, nullable=True)
    next_at = Column(DateTime(timezone=True), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_run_id = Column(Uuid, ForeignKey("run.id"), nullable=True)
    empty_streak = Column(Integer, nullable=False, default=0, server_default="0")
    failures = Column(Integer, nullable=False, default=0, server_default="0")
    paused_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        CheckConstraint("every_seconds > 0", name="positive_interval"),
        Index("ix_agent_task_due", "enabled", "next_at"),
        Index("ix_agent_task_investigation", "investigation"),
    )


class AgentWake(Base):
    __tablename__ = "agent_wake"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id = Column(Uuid, ForeignKey("agent_task.id"), nullable=False)
    key = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    payload = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    run_id = Column(Uuid, ForeignKey("run.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("task_id", "key"),
        Index("ix_agent_wake_pending", "task_id", "run_id"),
    )
