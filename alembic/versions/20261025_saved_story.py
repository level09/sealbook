"""saved story selections per user"""

import sqlalchemy as sa

from alembic import op

revision = "20261025_saved_story"
down_revision = "20261025_sheets"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "saved_story",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("query", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_saved_story_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_story")),
    )
    op.create_index(
        op.f("ix_saved_story_user_id"), "saved_story", ["user_id"], unique=False
    )


def downgrade():
    op.drop_index(op.f("ix_saved_story_user_id"), table_name="saved_story")
    op.drop_table("saved_story")
