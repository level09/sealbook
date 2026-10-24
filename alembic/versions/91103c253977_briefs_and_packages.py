"""briefs and packages"""

revision = "91103c253977"
down_revision = "8b29dd33a36b"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


def upgrade() -> None:
    op.create_table(
        "brief",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"], ["user.id"], name=op.f("fk_brief_author_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief")),
    )
    op.create_table(
        "package",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("brief_id", sa.Uuid(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("zip_sha256", sa.String(length=64), nullable=False),
        sa.Column("statements", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["brief_id"], ["brief.id"], name=op.f("fk_package_brief_id")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], name=op.f("fk_package_created_by")
        ),
        sa.ForeignKeyConstraint(
            ["manifest_sha256"],
            ["blob.sha256"],
            name=op.f("fk_package_manifest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["zip_sha256"], ["blob.sha256"], name=op.f("fk_package_zip_sha256")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package")),
        sa.UniqueConstraint("manifest_sha256", name=op.f("uq_package_manifest_sha256")),
    )
    op.create_index(op.f("ix_package_brief_id"), "package", ["brief_id"], unique=False)
    op.create_table(
        "sentence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("brief_id", sa.Uuid(), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("statement_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.ForeignKeyConstraint(
            ["brief_id"], ["brief.id"], name=op.f("fk_sentence_brief_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sentence")),
    )
    op.create_index(
        op.f("ix_sentence_brief_id"), "sentence", ["brief_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sentence_brief_id"), table_name="sentence")
    op.drop_table("sentence")
    op.drop_index(op.f("ix_package_brief_id"), table_name="package")
    op.drop_table("package")
    op.drop_table("brief")
