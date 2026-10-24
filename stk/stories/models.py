"""Saved stories: a person's own list of story selections (spec call 7: a group is a
saved selection of people or a filter, not a new entity). Only the query is kept, never
a title or a name, so erasure has nothing to reach here; titles are recomputed live."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, Uuid

from stk.extensions import Base


def _now():
    return datetime.now(UTC)


class SavedStory(Base):
    __tablename__ = "saved_story"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    # {"lens", "ref", "investigation", "from", "to"}: the /stories query string.
    query = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
