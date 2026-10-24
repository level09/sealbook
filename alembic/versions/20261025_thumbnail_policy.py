"""Policy row for the local thumbnail tool."""

from alembic import op

revision = "20261025_thumbnail"
down_revision = "20261024_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "insert into policy (capability, doc_type, sensitivity, routes, auto_accept)"
        " values ('thumbnail', '*', '*', array['local'], false)"
    )


def downgrade():
    op.execute("delete from policy where capability = 'thumbnail'")
