"""Watchlists: a per-user subscription to a person mention. Matches are not
stored here; they are same_as proposals on the ledger, authored by the matcher."""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint

from stk.extensions import Base


def _now():
    return datetime.now(UTC)


class Watch(Base):
    __tablename__ = "watch"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    person = Column(Text, nullable=False, index=True)  # "person:<uuid>"
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "person", name="uq_watch_user_person"),
    )
