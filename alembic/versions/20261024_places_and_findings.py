"""place records, place names and discovery finding keys"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "20261024_places"
down_revision = "20261023_segments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "place",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("geonames_id", sa.BigInteger(), unique=True),
        sa.Column("name_ar", sa.Text(), nullable=False),
        sa.Column("name_en", sa.Text()),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("place.id")),
        sa.Column("governorate_id", UUID(as_uuid=True), sa.ForeignKey("place.id")),
        sa.Column("lon", sa.Numeric(9, 6), nullable=False),
        sa.Column("lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("area", JSONB()),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("principal.id")),
    )
    op.create_index("ix_place_parent_id", "place", ["parent_id"])
    op.create_index("ix_place_governorate_id", "place", ["governorate_id"])
    op.create_table(
        "place_name",
        sa.Column(
            "place_id", UUID(as_uuid=True), sa.ForeignKey("place.id"), primary_key=True
        ),
        sa.Column("norm", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("lang", sa.String(8), nullable=False),
    )
    op.create_index("ix_place_name_norm", "place_name", ["norm"])
    op.create_table(
        "finding_key",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("run.id"), nullable=False
        ),
        sa.Column("statement_id", UUID(as_uuid=True), sa.ForeignKey("statement.id")),
        sa.Column("members", JSONB(), nullable=False),
        sa.Column("features", JSONB(), nullable=False),
        sa.Column("notes", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_finding_key_run_id", "finding_key", ["run_id"])


def downgrade():
    op.drop_table("finding_key")
    op.drop_table("place_name")
    op.drop_table("place")
