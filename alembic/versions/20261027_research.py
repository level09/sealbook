"""Per-case research switches and the capture tool's policy row"""

import sqlalchemy as sa

from alembic import op

revision = "20261027_research"
down_revision = "20261026_geotag"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "case_setting",
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("sensitive", sa.Boolean(), nullable=False),
        sa.Column("research", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["principal.id"], name=op.f("fk_case_setting_updated_by")
        ),
        sa.PrimaryKeyConstraint("investigation", name=op.f("pk_case_setting")),
    )
    # Capture writes no claims itself; what it stores goes through normal intake.
    op.execute(
        "insert into policy (capability, doc_type, sensitivity, routes, auto_accept)"
        " values ('capture', '*', '*', array['local'], false)"
    )


def downgrade():
    op.execute("delete from policy where capability = 'capture'")
    op.drop_table("case_setting")
