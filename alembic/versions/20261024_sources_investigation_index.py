"""Index the investigation an acquisition belongs to.

The Sources list scopes every query by context->>'investigation'. Without an index
Postgres has no statistics for the expression, estimates 100 rows for a 20,000 row
case and picks nested loops that run for seconds."""

from alembic import op

revision = "20261024_sources"
down_revision = "20261024_assignments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "create index ix_acquisition_investigation"
        " on acquisition ((context->>'investigation'))"
    )


def downgrade():
    op.execute("drop index ix_acquisition_investigation")
