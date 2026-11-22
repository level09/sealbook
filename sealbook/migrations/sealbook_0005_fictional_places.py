"""Places can be marked invented.

A fictional case study's places carry coordinates in open sea so that no real town
reads as the scene. `place.fictional` says so explicitly, set by whoever records the
place (the case seeder from case.json), so maps draw them as a schematic instead of
dots on a real basemap. Nothing infers it from coordinates.
"""

import sqlalchemy as sa
from alembic import op

revision = "sealbook_0005"
down_revision = "sealbook_0004"
branch_labels = None
depends_on = "stk_0008"


def upgrade():
    op.add_column(
        "place",
        sa.Column("fictional", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade():
    op.drop_column("place", "fictional")
