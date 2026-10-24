"""A case as a record. `slug` is the investigation string acquisitions, datasets,
findings and filters already carry, so it never changes once anything uses it;
`name` is what people read and may differ later."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Text, Uuid

from stk.extensions import Base


class Case(Base):
    __tablename__ = "case_record"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    slug = Column(Text, nullable=False, unique=True)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default="", server_default="")
    # Null for cases backfilled from acquisitions: nobody is known to have made them.
    created_by = Column(Uuid, ForeignKey("principal.id"), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
