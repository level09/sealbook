"""Claims are read from text on the zero-retention route too.

sealbook_0002 opened cloud-zdr to sensitive sources, agents and embeddings but left
the extraction row on the private route alone, so a deployment without one read no
claims from any text, post, OCR text or PDF page, against the 2026-10-25 decision.
A private route still wins where it is set up: it comes first.
"""

from alembic import op

revision = "sealbook_0004"
down_revision = "sealbook_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "update policy set routes = array['local', 'cloud-zdr'] "
        "where capability = 'extract' and doc_type = '*' and sensitivity = '*' "
        "and routes = array['local']"
    )


def downgrade():
    op.execute(
        "update policy set routes = array['local'] "
        "where capability = 'extract' and doc_type = '*' and sensitivity = '*' "
        "and routes = array['local', 'cloud-zdr']"
    )
