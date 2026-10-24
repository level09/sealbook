"""located search and embedding references"""

revision = "20261023_segments"
down_revision = "20261023_discovery"
branch_labels = None
depends_on = None

import unicodedata

import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC

from alembic import op


def _search_key(source):
    # Freeze the migration's data conversion independently of later application edits.
    original, replacement = "أإآٱىة٠١٢٣٤٥٦٧٨٩ًٌٍَُِّْـ", "اااايه0123456789"
    mapping = {
        ord(char): replacement[i] if i < len(replacement) else None
        for i, char in enumerate(original)
    }
    out, start = [], 0
    while start < len(source):
        end = start + 1
        while end < len(source) and unicodedata.combining(source[end]):
            end += 1
        for char in (
            unicodedata.normalize("NFKC", source[start:end])
            .translate(mapping)
            .casefold()
        ):
            if unicodedata.category(char) == "Cf":
                continue
            char = " " if char.isspace() else char
            if char == " " and out and out[-1] == " ":
                continue
            out.append(char)
        start = end
    return "".join(out)


def upgrade() -> None:
    op.create_table(
        "embedding_space",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("model_requested", sa.Text(), nullable=False),
        sa.Column("model_served", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("recipe", sa.JSON(), nullable=False),
        sa.Column("reference", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_embedding_space")),
    )
    op.create_index(
        "uq_embedding_space_active",
        "embedding_space",
        ["key"],
        unique=True,
        postgresql_where=sa.text("active"),
        sqlite_where=sa.text("active"),
    )
    op.create_table(
        "evidence_segment",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=True),
        sa.Column("start", sa.Integer(), server_default="0", nullable=False),
        sa.Column("end", sa.Integer(), nullable=True),
        sa.Column("location", sa.JSON(), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_sha256"],
            ["blob.sha256"],
            name=op.f("fk_evidence_segment_artifact_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_evidence_segment_run_id")
        ),
        sa.ForeignKeyConstraint(
            ["source_sha256"],
            ["blob.sha256"],
            name=op.f("fk_evidence_segment_source_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["text_sha256"],
            ["blob.sha256"],
            name=op.f("fk_evidence_segment_text_sha256"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_segment")),
        sa.UniqueConstraint(
            "source_sha256",
            "kind",
            "ordinal",
            "start",
            name="uq_evidence_segment_location",
        ),
    )
    op.create_index(
        op.f("ix_evidence_segment_source_sha256"),
        "evidence_segment",
        ["source_sha256"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evidence_segment_text_sha256"),
        "evidence_segment",
        ["text_sha256"],
        unique=False,
    )
    op.create_table(
        "index_progress",
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("part", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("completed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_index_progress_run_id")
        ),
        sa.ForeignKeyConstraint(
            ["source_sha256"],
            ["blob.sha256"],
            name=op.f("fk_index_progress_source_sha256"),
        ),
        sa.PrimaryKeyConstraint(
            "source_sha256", "tool", "part", name=op.f("pk_index_progress")
        ),
    )
    op.create_table(
        "segment_embedding",
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("vector", HALFVEC(768), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_segment_embedding_run_id")
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["evidence_segment.id"],
            name=op.f("fk_segment_embedding_segment_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["embedding_space.id"],
            name=op.f("fk_segment_embedding_space_id"),
        ),
        sa.PrimaryKeyConstraint(
            "segment_id", "space_id", name=op.f("pk_segment_embedding")
        ),
    )
    op.create_index(
        "ix_segment_embedding_vector",
        "segment_embedding",
        ["vector"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"vector": "halfvec_cosine_ops"},
    )
    op.create_table(
        "entity_embedding",
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("vector", HALFVEC(768), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_entity_embedding_run_id")
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["embedding_space.id"],
            name=op.f("fk_entity_embedding_space_id"),
        ),
        sa.PrimaryKeyConstraint(
            "subject", "investigation", "space_id", name=op.f("pk_entity_embedding")
        ),
    )
    op.create_index(
        op.f("ix_entity_embedding_run_id"), "entity_embedding", ["run_id"], unique=False
    )
    op.create_index(
        "ix_entity_embedding_vector",
        "entity_embedding",
        ["vector"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"vector": "halfvec_cosine_ops"},
    )
    op.add_column(
        "evidence_chunk",
        sa.Column("normalized", sa.Text(), server_default="", nullable=False),
    )
    connection = op.get_bind()
    existing = connection.execute(
        sa.text(
            "select blob_sha256, start, text from evidence_chunk"
        ).execution_options(stream_results=True)
    )
    for batch in existing.mappings().partitions(500):
        connection.execute(
            sa.text(
                "update evidence_chunk set normalized=:normalized where blob_sha256=:blob and start=:start"
            ),
            [
                {
                    "normalized": _search_key(row["text"]),
                    "blob": row["blob_sha256"],
                    "start": row["start"],
                }
                for row in batch
            ],
        )
    op.execute("""insert into evidence_segment (id, source_sha256, kind, ordinal, text_sha256, start, \"end\", location, run_id)
        select gen_random_uuid(), coalesce(a.blob_sha256, c.blob_sha256), 'text', 0, c.blob_sha256, c.start, c.\"end\", '{}', c.run_id
        from evidence_chunk c join run r on r.id = c.run_id
        left join acquisition a on a.id::text = r.inputs->>'acquisition'
        on conflict do nothing""")
    op.execute(
        "insert into policy (capability, doc_type, sensitivity, routes, auto_accept) values ('pdf', '*', '*', array['local'], false)"
    )
    op.create_index(
        "ix_evidence_chunk_normalized",
        "evidence_chunk",
        ["normalized"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"normalized": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.execute("delete from policy where capability = 'pdf'")
    op.drop_index(
        "ix_evidence_chunk_normalized",
        table_name="evidence_chunk",
        postgresql_using="gin",
        postgresql_ops={"normalized": "gin_trgm_ops"},
    )
    op.drop_column("evidence_chunk", "normalized")
    op.drop_table("entity_embedding")
    op.drop_table("segment_embedding")
    op.drop_table("index_progress")
    op.drop_index(
        op.f("ix_evidence_segment_text_sha256"), table_name="evidence_segment"
    )
    op.drop_index(
        op.f("ix_evidence_segment_source_sha256"), table_name="evidence_segment"
    )
    op.drop_table("evidence_segment")
    op.drop_index(
        "uq_embedding_space_active",
        table_name="embedding_space",
        postgresql_where=sa.text("active"),
        sqlite_where=sa.text("active"),
    )
    op.drop_table("embedding_space")
