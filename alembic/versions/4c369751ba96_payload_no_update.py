"""payload no update"""

revision = "4c369751ba96"
down_revision = "4017429e62ab"
branch_labels = None
depends_on = None


from alembic import op


def upgrade() -> None:
    # Erasure deletes payloads; nothing may rewrite one, since its value is the
    # evidence the statement's commitment stands for.
    op.execute(
        "create trigger payload_no_update before update on payload "
        "for each row execute function ledger_insert_only()"
    )


def downgrade() -> None:
    op.execute("drop trigger payload_no_update on payload")
