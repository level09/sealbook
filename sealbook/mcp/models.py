"""OAuth grants and tokens for MCP agents. Tokens are stored as sha256 of a
random secret, never in clear."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from stk.extensions import Base


def _now():
    return datetime.now(UTC)


class McpGrant(Base):
    """One approved authorization: a user lets one client act as one agent
    principal with these scopes. Revoking it kills every token it issued."""

    __tablename__ = "mcp_grant"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    agent_id = Column(Uuid, ForeignKey("principal.id"), nullable=False, unique=True)
    client_id = Column(Text, nullable=False)
    client_name = Column(Text, nullable=False)
    redirect_uri = Column(Text, nullable=False)
    scope = Column(Text, nullable=False)  # space-separated, as OAuth writes it
    resource = Column(Text, nullable=False)
    code_hash = Column(String(64), nullable=False, unique=True)
    code_challenge = Column(Text, nullable=False)
    code_expires_at = Column(DateTime(timezone=True), nullable=False)
    code_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class McpToken(Base):
    __tablename__ = "mcp_token"

    hash = Column(String(64), primary_key=True)
    grant_id = Column(Uuid, ForeignKey("mcp_grant.id"), nullable=False, index=True)
    kind = Column(
        Text,
        CheckConstraint("kind in ('access','refresh')", name="kind"),
        nullable=False,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # A refresh token is single use: rotation sets this, and a second use
    # revokes the whole grant.
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
