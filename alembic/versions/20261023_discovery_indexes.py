"""Text index and perceptual fingerprint projections."""

import sqlalchemy as sa
from pgvector.sqlalchemy import BIT
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "20261023_discovery"
down_revision = "24ac79fba55a"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("create extension if not exists vector")
    op.execute("create extension if not exists pg_trgm")
    op.create_table(
        "audio_landmark",
        sa.Column(
            "blob_sha256", sa.String(64), sa.ForeignKey("blob.sha256"), primary_key=True
        ),
        sa.Column("hash", sa.BigInteger(), primary_key=True),
        sa.Column("time", sa.Integer(), primary_key=True),
        sa.Column("run_id", UUID(), sa.ForeignKey("run.id"), nullable=False),
    )
    for column in ("hash", "run_id"):
        op.create_index(f"ix_audio_landmark_{column}", "audio_landmark", [column])
    op.create_table(
        "evidence_chunk",
        sa.Column(
            "blob_sha256", sa.String(64), sa.ForeignKey("blob.sha256"), primary_key=True
        ),
        sa.Column("start", sa.Integer(), primary_key=True),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("run_id", UUID(), sa.ForeignKey("run.id"), nullable=False),
    )
    op.create_index("ix_evidence_chunk_run_id", "evidence_chunk", ["run_id"])
    op.create_index(
        "ix_evidence_chunk_text",
        "evidence_chunk",
        ["text"],
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
    op.create_table(
        "media_frame",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "blob_sha256", sa.String(64), sa.ForeignKey("blob.sha256"), nullable=False
        ),
        sa.Column("run_id", UUID(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("ms", sa.Integer(), nullable=False),
        sa.Column("quality", sa.Integer(), nullable=False),
        sa.Column("hash", BIT(256), nullable=False),
        sa.UniqueConstraint("blob_sha256", "ms", name="uq_media_frame_blob_ms"),
    )
    for column in ("blob_sha256", "run_id"):
        op.create_index(f"ix_media_frame_{column}", "media_frame", [column])
    op.create_index(
        "ix_media_frame_hash",
        "media_frame",
        ["hash"],
        postgresql_using="hnsw",
        postgresql_ops={"hash": "bit_hamming_ops"},
    )
    op.execute(
        "insert into policy (capability, doc_type, sensitivity, routes, auto_accept) values ('index', '*', '*', array['local'], false), ('embed', '*', '*', array['local'], false), ('embed_actor', '*', '*', array['local'], false), ('fingerprint', '*', '*', array['local'], false)"
    )


def downgrade():
    op.execute(
        "delete from policy where capability in ('index', 'embed', 'embed_actor', 'fingerprint')"
    )
    op.drop_table("media_frame")
    op.drop_table("evidence_chunk")
    op.drop_table("audio_landmark")
