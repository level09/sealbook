"""Speech to text of ordinary recordings prefers the hosted zero-retention route.

The local Whisper invented subtitle boilerplate on running water; a hosted
transcriber wrote nothing there and read the scripts better (docs/decisions.md,
2026-11-22). The row lists cloud-zdr first and the local model after it, used when
no hosted model is configured. Sensitive recordings keep the general sensitive row
(local, cloud-zdr), as sensitive scans do for OCR.
"""

from alembic import op

revision = "sealbook_0006"
down_revision = "sealbook_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "insert into policy (capability, doc_type, sensitivity, routes, auto_accept) "
        "values ('transcribe', '*', 'ordinary', array['cloud-zdr', 'local'], false)"
    )


def downgrade():
    op.execute(
        "delete from policy where capability = 'transcribe' and doc_type = '*' "
        "and sensitivity = 'ordinary'"
    )
