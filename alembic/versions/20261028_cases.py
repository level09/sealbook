"""Cases as records: one row per investigation string already in use"""

import sqlalchemy as sa

from alembic import op

revision = "20261028_cases"
down_revision = "20261027_research"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "case_record",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["principal.id"], name=op.f("fk_case_record_created_by")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_record")),
        sa.UniqueConstraint("slug", name=op.f("uq_case_record_slug")),
    )
    # Every string a case was known by. Nobody is recorded as its creator, and it
    # dates from its first acquisition (or now, for a name with none).
    op.execute(
        """
        insert into case_record (id, slug, name, description, created_by, created_at)
        select gen_random_uuid(), slug, slug, '', null, coalesce(min(at), now())
        from (
            select context->>'investigation' as slug, at from acquisition
            union all select investigation, null from case_setting
            union all select investigation, null from sheet_dataset
        ) known
        where coalesce(slug, '') <> ''
        group by slug
        """
    )


def downgrade():
    op.drop_table("case_record")
