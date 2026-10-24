"""stored notifications and work assignment"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "20261024_assignments"
down_revision = "20261024_finding_erasure"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_notification_user_created", "notification", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_notification_unread",
        "notification",
        ["user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_table(
        "assignment",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "assignee_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False
        ),
        sa.Column(
            "assigned_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False
        ),
        sa.Column("due", sa.Date()),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assignment_assignee_id", "assignment", ["assignee_id"])
    op.create_index(
        "ix_assignment_investigation", "assignment", ["investigation", "status"]
    )
    op.create_index(
        "uq_assignment_open_subject",
        "assignment",
        ["investigation", "subject"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "assignment_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "assignment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assignment.id"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("detail", JSONB(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assignment_event_assignment_id", "assignment_event", ["assignment_id"]
    )
    op.execute(
        "create trigger assignment_event_insert_only before update or delete "
        "on assignment_event for each row execute function ledger_insert_only()"
    )


def downgrade():
    op.drop_table("assignment_event")
    op.drop_table("assignment")
    op.drop_table("notification")
