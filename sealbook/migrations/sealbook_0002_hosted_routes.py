"""Hosted zero-retention models are the default route; a private endpoint stays an option.

Sensitive sources, agents and embeddings may use cloud-zdr (OpenRouter with zero
data retention) as well as local (a private OpenAI-compatible endpoint). Ingest
still prefers the most private route that has a model configured, so a deployment
that sets one up keeps everything on it.
"""

from alembic import op

revision = "sealbook_0002"
down_revision = "sealbook_0001"
branch_labels = None
depends_on = None

ROWS = (
    ("*", "*", "sensitive"),
    ("embed", "*", "*"),
    ("embed_actor", "*", "*"),
)


def upgrade():
    for capability, doc_type, sensitivity in ROWS:
        op.execute(
            "update policy set routes = array['local', 'cloud-zdr'] "
            f"where capability = '{capability}' and doc_type = '{doc_type}' "
            f"and sensitivity = '{sensitivity}' and routes = array['local']"
        )


def downgrade():
    for capability, doc_type, sensitivity in ROWS:
        op.execute(
            "update policy set routes = array['local'] "
            f"where capability = '{capability}' and doc_type = '{doc_type}' "
            f"and sensitivity = '{sensitivity}' and routes = array['local', 'cloud-zdr']"
        )
