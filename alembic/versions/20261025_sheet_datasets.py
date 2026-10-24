"""recurring sheet datasets and their versions"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "20261025_sheets"
down_revision = "20261025_thumbnail"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sheet_dataset",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("key_column", sa.Text(), nullable=False),
        sa.Column("mapping", JSONB(), nullable=False),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("principal.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("investigation", "name", name="uq_sheet_dataset_name"),
    )
    op.create_index(
        "ix_sheet_dataset_investigation", "sheet_dataset", ["investigation"]
    )
    op.create_table(
        "sheet_version",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dataset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sheet_dataset.id"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "blob_sha256", sa.String(64), sa.ForeignKey("blob.sha256"), nullable=False
        ),
        sa.Column(
            "acquisition_id",
            UUID(as_uuid=True),
            sa.ForeignKey("acquisition.id"),
            nullable=False,
        ),
        sa.Column("sheet", sa.Integer(), nullable=False),
        sa.Column("key_column", sa.Text(), nullable=False),
        sa.Column("mapping", JSONB(), nullable=False),
        sa.Column("diff", JSONB(), nullable=False),
        sa.Column(
            "confirmed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("principal.id"),
            nullable=False,
        ),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("run.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dataset_id", "blob_sha256", name="uq_sheet_version_blob"),
        sa.UniqueConstraint("dataset_id", "number", name="uq_sheet_version_number"),
    )
    op.create_index("ix_sheet_version_dataset_id", "sheet_version", ["dataset_id"])
    op.create_index(
        "ix_sheet_version_acquisition_id", "sheet_version", ["acquisition_id"]
    )


def downgrade():
    op.drop_table("sheet_version")
    op.drop_table("sheet_dataset")
