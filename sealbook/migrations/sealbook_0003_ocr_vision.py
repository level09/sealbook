"""OCR of ordinary sources prefers Google Cloud Vision text detection.

A text detection engine reads the text in a picture and returns nothing when there
is none; a generative model asked the same once described the picture instead. The
row lists vision first and the generative routes after it, used when Vision is not
configured. Sensitive sources keep the general sensitive row (local, cloud-zdr).
"""

from alembic import op

revision = "sealbook_0003"
down_revision = "sealbook_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "insert into policy (capability, doc_type, sensitivity, routes, auto_accept) "
        "values ('ocr', '*', 'ordinary', "
        "array['vision', 'local', 'cloud-zdr', 'cloud'], false)"
    )


def downgrade():
    op.execute(
        "delete from policy where capability = 'ocr' and doc_type = '*' "
        "and sensitivity = 'ordinary'"
    )
