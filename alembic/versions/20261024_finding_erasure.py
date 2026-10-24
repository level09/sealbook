"""finding keys: erasable members and features, an investigation, a stored salt"""

import os

import sqlalchemy as sa

from alembic import op

revision = "20261024_finding_erasure"
down_revision = "20261024_places"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("finding_key", "members", nullable=True)
    op.alter_column("finding_key", "features", nullable=True)
    op.add_column("finding_key", sa.Column("investigation", sa.Text()))
    op.execute(
        "update finding_key set investigation = run.inputs->>'investigation' "
        "from run where run.id = finding_key.run_id"
    )
    op.alter_column("finding_key", "investigation", nullable=False)
    op.create_index("ix_finding_key_investigation", "finding_key", ["investigation"])
    # One random salt per deployment, independent of SECRET_KEY, so rotating that
    # never changes a suppression key.
    op.create_table(
        "finding_salt",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("salt", sa.LargeBinary(32), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_finding_salt_single_row")),
    )
    op.get_bind().execute(
        sa.text("insert into finding_salt (id, salt) values (1, :salt)"),
        {"salt": os.urandom(32)},
    )


def downgrade():
    op.drop_table("finding_salt")
    op.drop_index("ix_finding_key_investigation", "finding_key")
    op.drop_column("finding_key", "investigation")
    op.execute(
        "update finding_key set members = '[]', features = '{}' where members is null"
    )
    op.alter_column("finding_key", "members", nullable=False)
    op.alter_column("finding_key", "features", nullable=False)
