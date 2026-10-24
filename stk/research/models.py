"""Per-case research switches, keyed by the case slug. A case with no row here has
research allowed and is not sensitive."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Text, Uuid

from stk.extensions import Base


def _now():
    return datetime.now(UTC)


class CaseSetting(Base):
    __tablename__ = "case_setting"

    investigation = Column(Text, primary_key=True)
    # A sensitive case never sends anything to a search or rendering service.
    sensitive = Column(Boolean, nullable=False, default=False)
    research = Column(Boolean, nullable=False, default=True)
    updated_by = Column(Uuid, ForeignKey("principal.id"), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, nullable=False)
