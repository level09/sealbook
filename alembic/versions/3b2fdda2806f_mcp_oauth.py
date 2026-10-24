"""mcp oauth"""

revision = "3b2fdda2806f"
down_revision = "2095de512668"
branch_labels = None
depends_on = None

import sqlalchemy as sa

from alembic import op


def upgrade() -> None:
    op.create_table(
        "mcp_grant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_name", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["principal.id"], name=op.f("fk_mcp_grant_agent_id")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_mcp_grant_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_grant")),
        sa.UniqueConstraint("agent_id", name=op.f("uq_mcp_grant_agent_id")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_mcp_grant_code_hash")),
    )
    op.create_index(
        op.f("ix_mcp_grant_user_id"), "mcp_grant", ["user_id"], unique=False
    )
    op.create_table(
        "mcp_token",
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("grant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind in ('access','refresh')", name=op.f("ck_mcp_token_kind")
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"], ["mcp_grant.id"], name=op.f("fk_mcp_token_grant_id")
        ),
        sa.PrimaryKeyConstraint("hash", name=op.f("pk_mcp_token")),
    )
    op.create_index(
        op.f("ix_mcp_token_grant_id"), "mcp_token", ["grant_id"], unique=False
    )
    # MCP access needs analyst or admin; roles are rows, so seed the new one.
    op.execute(
        "insert into role (name, description) select 'analyst', "
        "'Reads evidence and lets connected agents read and propose' "
        "where not exists (select 1 from role where name = 'analyst')"
    )


def downgrade() -> None:
    # The analyst role stays: users may hold it for more than MCP.
    op.drop_index(op.f("ix_mcp_token_grant_id"), table_name="mcp_token")
    op.drop_table("mcp_token")
    op.drop_index(op.f("ix_mcp_grant_user_id"), table_name="mcp_grant")
    op.drop_table("mcp_grant")
