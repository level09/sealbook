"""Policy row for the local geotag tool: its location proposals stay unconfirmed."""

from alembic import op

revision = "20261026_geotag"
down_revision = "20261026_boundaries"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "insert into policy (capability, doc_type, sensitivity, routes, auto_accept)"
        " values ('geotag', '*', '*', array['local'], false)"
    )


def downgrade():
    op.execute("delete from policy where capability = 'geotag'")
