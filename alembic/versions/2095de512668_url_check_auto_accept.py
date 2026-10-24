"""url check auto accept"""

revision = "2095de512668"
down_revision = "cbfae67267ef"
branch_labels = None
depends_on = None


from alembic import op


def upgrade() -> None:
    # Re-fetching a URL and comparing hashes is deterministic, like hashing.
    op.execute(
        "insert into policy (capability, doc_type, sensitivity, routes, auto_accept) "
        "values ('url_check', '*', '*', '{}', true)"
    )


def downgrade() -> None:
    op.execute("delete from policy where capability = 'url_check'")
