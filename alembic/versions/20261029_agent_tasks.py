"""Standing tasks and deduplicated wakes."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261029_agent_tasks"
down_revision = "20261028_cases"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_task",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("every_seconds", sa.Integer(), nullable=True),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.Uuid(), sa.ForeignKey("run.id"), nullable=True),
        sa.Column("empty_streak", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("every_seconds > 0", name="positive_interval"),
    )
    op.create_index("ix_agent_task_user_id", "agent_task", ["user_id"])
    op.create_index("ix_agent_task_due", "agent_task", ["enabled", "next_at"])
    op.create_index("ix_agent_task_investigation", "agent_task", ["investigation"])
    op.create_table(
        "agent_wake",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("agent_task.id"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id"), nullable=True),
        sa.UniqueConstraint("task_id", "key"),
    )
    op.create_index("ix_agent_wake_pending", "agent_wake", ["task_id", "run_id"])


def downgrade():
    op.drop_table("agent_wake")
    op.drop_table("agent_task")
