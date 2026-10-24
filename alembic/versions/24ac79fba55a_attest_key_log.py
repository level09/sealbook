"""attest key log"""

revision = "24ac79fba55a"
down_revision = "4c369751ba96"
branch_labels = None
depends_on = None

import sqlalchemy as sa

from alembic import op


def upgrade() -> None:
    op.create_table(
        "attest_key",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("first_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_attest_key_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attest_key")),
        sa.UniqueConstraint("user_id", "public_key", name="uq_attest_key_user_key"),
    )
    # The log outlives removed passkeys; nothing rewrites or drops an entry.
    op.execute(
        "create trigger attest_key_insert_only before update or delete on attest_key "
        "for each row execute function ledger_insert_only()"
    )
    op.execute(
        "create trigger attest_key_no_truncate before truncate on attest_key "
        "for each statement execute function ledger_insert_only()"
    )


def downgrade() -> None:
    op.drop_table("attest_key")
