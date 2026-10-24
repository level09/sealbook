"""package export, statement ids and revocation"""

revision = "4017429e62ab"
down_revision = "91103c253977"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


def upgrade() -> None:
    op.add_column(
        "package", sa.Column("export_sha256", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "package",
        sa.Column(
            "statement_ids",
            postgresql.ARRAY(sa.Uuid()).with_variant(sa.JSON(), "sqlite"),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column(
        "package", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_package_export_sha256"),
        "package",
        "blob",
        ["export_sha256"],
        ["sha256"],
    )
    # Packages finalized before this revision export their frozen zip unchanged.
    op.execute(
        "update package set export_sha256 = zip_sha256 where finalized_at is not null"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_package_export_sha256"), "package", type_="foreignkey")
    op.drop_column("package", "revoked_at")
    op.drop_column("package", "statement_ids")
    op.drop_column("package", "export_sha256")
