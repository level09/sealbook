"""administrative boundary sets and areas, and each place's admin 1 and admin 2"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "20261026_boundaries"
down_revision = "20261026_postgis"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "boundary_set",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("valid_on", sa.Date(), nullable=False),
        sa.Column("licence", sa.Text(), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source", "country", "version", name=op.f("uq_boundary_set_source")
        ),
    )
    op.create_table(
        "admin_area",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("boundary_set.id", name=op.f("fk_admin_area_set_id")),
            nullable=False,
        ),
        sa.Column("pcode", sa.String(16), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("parent_pcode", sa.String(16)),
        sa.Column("name_ar", sa.Text()),
        sa.Column("name_en", sa.Text(), nullable=False),
        sa.UniqueConstraint("set_id", "pcode", name=op.f("uq_admin_area_set_id")),
    )
    op.execute("ALTER TABLE admin_area ADD COLUMN geom geometry(MultiPolygon, 4326)")
    op.execute("ALTER TABLE admin_area ALTER COLUMN geom SET NOT NULL")
    op.create_index("ix_admin_area_set_id", "admin_area", ["set_id"])
    op.create_index(
        "ix_admin_area_geom", "admin_area", ["geom"], postgresql_using="gist"
    )
    for col in ("admin1_id", "admin2_id"):
        op.add_column(
            "place",
            sa.Column(
                col,
                UUID(as_uuid=True),
                sa.ForeignKey("admin_area.id", name=op.f(f"fk_place_{col}")),
            ),
        )
        op.create_index(f"ix_place_{col}", "place", [col])


def downgrade():
    for col in ("admin2_id", "admin1_id"):
        op.drop_index(f"ix_place_{col}", "place")
        op.drop_column("place", col)
    op.drop_table("admin_area")
    op.drop_table("boundary_set")
