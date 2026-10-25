"""Sealbook initial schema: every table, plus what autogenerate cannot see.

Replaces the stk 16 fork's 28 revisions with their final state. Beyond the tables:
the postgis, vector and pg_trgm extensions, the append-only triggers on ledger
tables, and the reference rows: route policy, the analyst role, the place kind
ladder and the finding salt.
"""

import os

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import BIT, HALFVEC
from sqlalchemy.dialects import postgresql

revision = "sealbook_0001"
down_revision = None
branch_labels = ("sealbook",)
depends_on = "stk_0001"

EXTENSIONS = ("postgis", "vector", "pg_trgm")

# Rows are never changed or removed, and the tables are never truncated.
INSERT_ONLY = (
    "blob",
    "statement",
    "chain",
    "acquisition",
    "attest_key",
    "batch",
    "anchor",
)

# capability, doc_type, sensitivity, routes, auto_accept: in the order the old
# revisions inserted them, so ids match a database built by them.
POLICY = [
    ("*", "*", "sensitive", ["local"], False),
    ("*", "*", "ordinary", ["local", "cloud-zdr", "cloud"], False),
    ("hash", "*", "*", [], True),
    ("c2pa_verify", "*", "*", [], True),
    ("url_check", "*", "*", [], True),
    ("index", "*", "*", ["local"], False),
    ("embed", "*", "*", ["local"], False),
    ("embed_actor", "*", "*", ["local"], False),
    ("fingerprint", "*", "*", ["local"], False),
    ("pdf", "*", "*", ["local"], False),
    ("thumbnail", "*", "*", ["local"], False),
    ("geotag", "*", "*", ["local"], False),
    ("capture", "*", "*", ["local"], False),
]

KIND_COLUMNS = ("code", "family", "country", "level", "label", "default_precision_m")
KINDS = [
    ("governorate", "admin", "SY", 1, "Governorate", None),
    ("district", "admin", "SY", 2, "District", None),
    ("subdistrict", "admin", "SY", 3, "Subdistrict", None),
    ("populated", "settlement", None, None, "Populated place", 3000),
    ("neighbourhood", "settlement", None, None, "Neighbourhood", 1000),
    ("facility", "facility", None, None, "Facility", 250),
    ("detention", "facility", None, None, "Detention facility", 250),
    ("prison", "facility", None, None, "Prison", 250),
    ("hospital", "facility", None, None, "Hospital", 250),
    ("checkpoint", "facility", None, None, "Checkpoint", 100),
    ("school", "facility", None, None, "School", 250),
    ("market", "facility", None, None, "Market", 250),
    ("grave_site", "facility", None, None, "Grave site", 250),
    ("military_site", "facility", None, None, "Military site", 500),
    ("religious_site", "facility", None, None, "Religious site", 250),
    ("training_camp", "facility", None, None, "Training camp", 500),
]


class Geo(sa.types.UserDefinedType):
    """A PostGIS column spelled out, so this revision never imports app models."""

    cache_ok = True

    def __init__(self, spec):
        self.spec = spec

    def get_col_spec(self, **kw):
        return self.spec


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(f"create extension if not exists {extension}")

    op.create_table(
        "batch",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("first_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("root", sa.String(length=64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_batch")),
        sa.UniqueConstraint("first_seq", name=op.f("uq_batch_first_seq")),
        sa.UniqueConstraint("last_seq", name=op.f("uq_batch_last_seq")),
    )
    op.create_table(
        "blob",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("md5", sa.String(length=32), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("storage_ref", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_blob")),
    )
    op.create_table(
        "boundary_set",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("valid_on", sa.Date(), nullable=False),
        sa.Column("licence", sa.Text(), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_boundary_set")),
        sa.UniqueConstraint(
            "source", "country", "version", name=op.f("uq_boundary_set_source")
        ),
        info={"postgis": True},
    )
    op.create_table(
        "chain",
        sa.Column("seq", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_chain")),
        sa.UniqueConstraint("hash", name=op.f("uq_chain_hash")),
        sa.UniqueConstraint("kind", "ref", name="uq_chain_kind_ref"),
    )
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
        "finding_salt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("salt", sa.LargeBinary(length=32), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_finding_salt_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finding_salt")),
    )
    op.create_table(
        "payload",
        sa.Column("commitment", sa.String(length=64), nullable=False),
        sa.Column("salt", sa.LargeBinary(length=32), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("value_key", sa.String(length=64), nullable=False),
        sa.Column("idem_key", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("commitment", name=op.f("pk_payload")),
        sa.UniqueConstraint("idem_key", name=op.f("uq_payload_idem_key")),
    )
    op.create_index(
        op.f("ix_payload_value_key"), "payload", ["value_key"], unique=False
    )
    op.create_table(
        "place_kind",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("family", sa.String(length=12), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("level", sa.SmallInteger(), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("default_precision_m", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(family = 'admin') = (level IS NOT NULL)",
            name=op.f("ck_place_kind_level_only_admin"),
        ),
        sa.CheckConstraint(
            "family IN ('admin', 'settlement', 'facility')",
            name=op.f("ck_place_kind_family_known"),
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_place_kind")),
    )
    op.create_table(
        "policy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("sensitivity", sa.Text(), nullable=False),
        sa.Column(
            "routes",
            postgresql.ARRAY(sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("auto_accept", sa.Boolean(), nullable=False),
        sa.Column("min_origins", sa.Integer(), nullable=True),
        sa.Column(
            "calibration",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy")),
        sa.UniqueConstraint(
            "capability", "doc_type", "sensitivity", name="uq_policy_key"
        ),
    )
    op.create_table(
        "admin_area",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("set_id", sa.UUID(), nullable=False),
        sa.Column("pcode", sa.String(length=16), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("parent_pcode", sa.String(length=16), nullable=True),
        sa.Column("name_ar", sa.Text(), nullable=True),
        sa.Column("name_en", sa.Text(), nullable=False),
        sa.Column("geom", Geo("geometry(MultiPolygon,4326)"), nullable=False),
        sa.ForeignKeyConstraint(
            ["set_id"], ["boundary_set.id"], name=op.f("fk_admin_area_set_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_area")),
        sa.UniqueConstraint("set_id", "pcode", name=op.f("uq_admin_area_set_id")),
        info={"postgis": True},
    )
    op.create_index(
        "ix_admin_area_geom",
        "admin_area",
        ["geom"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_index(
        op.f("ix_admin_area_set_id"), "admin_area", ["set_id"], unique=False
    )
    op.create_table(
        "anchor",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("receipt", sa.LargeBinary(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(receipt is null) <> (error is null)", name=op.f("ck_anchor_outcome")
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["batch.id"], name=op.f("fk_anchor_batch_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anchor")),
        # Column-level checks in the model, which autogenerate leaves out.
        sa.CheckConstraint(
            "kind in ('ots','tsa','partner')", name=op.f("ck_anchor_kind")
        ),
    )
    op.create_index(op.f("ix_anchor_batch_id"), "anchor", ["batch_id"], unique=False)
    op.create_table(
        "assignment",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by", sa.Integer(), nullable=False),
        sa.Column("due", sa.Date(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assigned_by"], ["user.id"], name=op.f("fk_assignment_assigned_by")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["user.id"], name=op.f("fk_assignment_assignee_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignment")),
    )
    op.create_index(
        op.f("ix_assignment_assignee_id"), "assignment", ["assignee_id"], unique=False
    )
    op.create_index(
        "ix_assignment_investigation",
        "assignment",
        ["investigation", "status"],
        unique=False,
    )
    op.create_index(
        "uq_assignment_open_subject",
        "assignment",
        ["investigation", "subject"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
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
    op.create_table(
        "brief",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"], ["user.id"], name=op.f("fk_brief_author_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief")),
    )
    op.create_table(
        "notification",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_notification_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification")),
    )
    op.create_index(
        "ix_notification_unread",
        "notification",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "ix_notification_user_created",
        "notification",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "principal",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("pubkey", sa.LargeBinary(), nullable=True),
        sa.Column("on_behalf_of", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["on_behalf_of"], ["principal.id"], name=op.f("fk_principal_on_behalf_of")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_principal_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_principal")),
        sa.UniqueConstraint("user_id", name=op.f("uq_principal_user_id")),
        # Column-level checks in the model, which autogenerate leaves out.
        sa.CheckConstraint("kind in ('human','agent')", name=op.f("ck_principal_kind")),
    )
    op.create_table(
        "saved_story",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("query", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_saved_story_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_story")),
    )
    op.create_index(
        op.f("ix_saved_story_user_id"), "saved_story", ["user_id"], unique=False
    )
    op.create_table(
        "watch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("person", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_watch_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watch")),
        sa.UniqueConstraint("user_id", "person", name="uq_watch_user_person"),
    )
    op.create_index(op.f("ix_watch_person"), "watch", ["person"], unique=False)
    op.create_index(op.f("ix_watch_user_id"), "watch", ["user_id"], unique=False)
    op.create_table(
        "acquisition",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("blob_sha256", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("collector_id", sa.UUID(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("sensitivity", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("partner", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["blob_sha256"], ["blob.sha256"], name=op.f("fk_acquisition_blob_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["collector_id"], ["principal.id"], name=op.f("fk_acquisition_collector_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_acquisition")),
    )
    op.create_index(
        op.f("ix_acquisition_blob_sha256"), "acquisition", ["blob_sha256"], unique=False
    )
    op.create_index(
        "ix_acquisition_investigation",
        "acquisition",
        [sa.literal_column("(context ->> 'investigation')")],
        unique=False,
    )
    op.create_index(
        op.f("ix_acquisition_origin"), "acquisition", ["origin"], unique=False
    )
    op.create_table(
        "assignment_event",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "detail",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["user.id"], name=op.f("fk_assignment_event_actor_id")
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["assignment.id"],
            name=op.f("fk_assignment_event_assignment_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignment_event")),
    )
    op.create_index(
        op.f("ix_assignment_event_assignment_id"),
        "assignment_event",
        ["assignment_id"],
        unique=False,
    )
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
    op.create_table(
        "case_setting",
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("sensitive", sa.Boolean(), nullable=False),
        sa.Column("research", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["principal.id"], name=op.f("fk_case_setting_updated_by")
        ),
        sa.PrimaryKeyConstraint("investigation", name=op.f("pk_case_setting")),
    )
    op.create_table(
        "mcp_grant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_name", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["principal.id"], name=op.f("fk_mcp_grant_agent_id")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_mcp_grant_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_grant")),
        sa.UniqueConstraint("agent_id", name=op.f("uq_mcp_grant_agent_id")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_mcp_grant_code_hash")),
    )
    op.create_index(
        op.f("ix_mcp_grant_user_id"), "mcp_grant", ["user_id"], unique=False
    )
    op.create_table(
        "package",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("brief_id", sa.Uuid(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("zip_sha256", sa.String(length=64), nullable=False),
        sa.Column("statements", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("export_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "statement_ids",
            postgresql.ARRAY(sa.Uuid()).with_variant(sa.JSON(), "sqlite"),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["brief_id"], ["brief.id"], name=op.f("fk_package_brief_id")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], name=op.f("fk_package_created_by")
        ),
        sa.ForeignKeyConstraint(
            ["export_sha256"], ["blob.sha256"], name=op.f("fk_package_export_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["manifest_sha256"],
            ["blob.sha256"],
            name=op.f("fk_package_manifest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["zip_sha256"], ["blob.sha256"], name=op.f("fk_package_zip_sha256")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package")),
        sa.UniqueConstraint("manifest_sha256", name=op.f("uq_package_manifest_sha256")),
    )
    op.create_index(op.f("ix_package_brief_id"), "package", ["brief_id"], unique=False)
    op.create_table(
        "run",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("principal_id", sa.UUID(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("model_requested", sa.Text(), nullable=True),
        sa.Column("model_served", sa.Text(), nullable=True),
        sa.Column("provider_served", sa.Text(), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "inputs",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("raw_output_ref", sa.Text(), nullable=True),
        sa.Column("pubkey", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name=op.f("fk_run_principal_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run")),
    )
    op.create_index(op.f("ix_run_status"), "run", ["status"], unique=False)
    op.create_table(
        "sentence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("brief_id", sa.Uuid(), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "statement_ids",
            postgresql.ARRAY(sa.Uuid()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["brief_id"], ["brief.id"], name=op.f("fk_sentence_brief_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sentence")),
    )
    op.create_index(
        op.f("ix_sentence_brief_id"), "sentence", ["brief_id"], unique=False
    )
    op.create_table(
        "sheet_dataset",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("key_column", sa.Text(), nullable=False),
        sa.Column(
            "mapping",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["principal.id"], name=op.f("fk_sheet_dataset_created_by")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sheet_dataset")),
        sa.UniqueConstraint("investigation", "name", name="uq_sheet_dataset_name"),
    )
    op.create_index(
        op.f("ix_sheet_dataset_investigation"),
        "sheet_dataset",
        ["investigation"],
        unique=False,
    )
    op.create_table(
        "agent_task",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "spec",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("every_seconds", sa.Integer(), nullable=True),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.Uuid(), nullable=True),
        sa.Column("empty_streak", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "every_seconds > 0", name=op.f("ck_agent_task_positive_interval")
        ),
        sa.ForeignKeyConstraint(
            ["last_run_id"], ["run.id"], name=op.f("fk_agent_task_last_run_id")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_agent_task_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_task")),
    )
    op.create_index(
        "ix_agent_task_due", "agent_task", ["enabled", "next_at"], unique=False
    )
    op.create_index(
        "ix_agent_task_investigation", "agent_task", ["investigation"], unique=False
    )
    op.create_index(
        op.f("ix_agent_task_user_id"), "agent_task", ["user_id"], unique=False
    )
    op.create_table(
        "audio_landmark",
        sa.Column("blob_sha256", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.BigInteger(), nullable=False),
        sa.Column("time", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["blob_sha256"], ["blob.sha256"], name=op.f("fk_audio_landmark_blob_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_audio_landmark_run_id")
        ),
        sa.PrimaryKeyConstraint(
            "blob_sha256", "hash", "time", name=op.f("pk_audio_landmark")
        ),
    )
    op.create_index(
        op.f("ix_audio_landmark_hash"), "audio_landmark", ["hash"], unique=False
    )
    op.create_index(
        op.f("ix_audio_landmark_run_id"), "audio_landmark", ["run_id"], unique=False
    )
    op.create_table(
        "entity_embedding",
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column(
            "vector", HALFVEC(dim=768).with_variant(sa.JSON(), "sqlite"), nullable=False
        ),
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
    op.create_table(
        "evidence_chunk",
        sa.Column("blob_sha256", sa.String(length=64), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized", sa.Text(), server_default="", nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["blob_sha256"], ["blob.sha256"], name=op.f("fk_evidence_chunk_blob_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_evidence_chunk_run_id")
        ),
        sa.PrimaryKeyConstraint("blob_sha256", "start", name=op.f("pk_evidence_chunk")),
    )
    op.create_index(
        "ix_evidence_chunk_normalized",
        "evidence_chunk",
        ["normalized"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"normalized": "gin_trgm_ops"},
    )
    op.create_index(
        op.f("ix_evidence_chunk_run_id"), "evidence_chunk", ["run_id"], unique=False
    )
    op.create_index(
        "ix_evidence_chunk_text",
        "evidence_chunk",
        ["text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
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
        "mcp_token",
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("grant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["grant_id"], ["mcp_grant.id"], name=op.f("fk_mcp_token_grant_id")
        ),
        sa.PrimaryKeyConstraint("hash", name=op.f("pk_mcp_token")),
        # Column-level checks in the model, which autogenerate leaves out.
        sa.CheckConstraint(
            "kind in ('access','refresh')", name=op.f("ck_mcp_token_kind")
        ),
    )
    op.create_index(
        op.f("ix_mcp_token_grant_id"), "mcp_token", ["grant_id"], unique=False
    )
    op.create_table(
        "media_frame",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("blob_sha256", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("ms", sa.Integer(), nullable=False),
        sa.Column("quality", sa.Integer(), nullable=False),
        sa.Column(
            "hash",
            BIT(length=256).with_variant(sa.String(length=256), "sqlite"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["blob_sha256"], ["blob.sha256"], name=op.f("fk_media_frame_blob_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_media_frame_run_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_frame")),
        sa.UniqueConstraint("blob_sha256", "ms", name="uq_media_frame_blob_ms"),
    )
    op.create_index(
        op.f("ix_media_frame_blob_sha256"), "media_frame", ["blob_sha256"], unique=False
    )
    op.create_index(
        "ix_media_frame_hash",
        "media_frame",
        ["hash"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"hash": "bit_hamming_ops"},
    )
    op.create_index(
        op.f("ix_media_frame_run_id"), "media_frame", ["run_id"], unique=False
    )
    op.create_table(
        "run_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column(
            "counts",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("drop_reason", sa.Text(), nullable=True),
        sa.Column("causation_id", sa.UUID(), nullable=True),
        sa.Column("correlation_id", sa.UUID(), nullable=True),
        sa.Column("human_gesture", sa.Boolean(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_run_event_run_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_event")),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_event_run_seq"),
    )
    op.create_table(
        "sheet_version",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("dataset_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("blob_sha256", sa.String(length=64), nullable=False),
        sa.Column("acquisition_id", sa.UUID(), nullable=False),
        sa.Column("sheet", sa.Integer(), nullable=False),
        sa.Column("key_column", sa.Text(), nullable=False),
        sa.Column(
            "mapping",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "diff",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("confirmed_by", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["acquisition_id"],
            ["acquisition.id"],
            name=op.f("fk_sheet_version_acquisition_id"),
        ),
        sa.ForeignKeyConstraint(
            ["blob_sha256"], ["blob.sha256"], name=op.f("fk_sheet_version_blob_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["principal.id"],
            name=op.f("fk_sheet_version_confirmed_by"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["sheet_dataset.id"],
            name=op.f("fk_sheet_version_dataset_id"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_sheet_version_run_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sheet_version")),
        sa.UniqueConstraint("dataset_id", "blob_sha256", name="uq_sheet_version_blob"),
        sa.UniqueConstraint("dataset_id", "number", name="uq_sheet_version_number"),
    )
    op.create_index(
        op.f("ix_sheet_version_acquisition_id"),
        "sheet_version",
        ["acquisition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sheet_version_dataset_id"),
        "sheet_version",
        ["dataset_id"],
        unique=False,
    )
    op.create_table(
        "statement",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("prop", sa.Text(), nullable=False),
        sa.Column("commitment", sa.String(length=64), nullable=False),
        sa.Column("span_blob", sa.String(length=64), nullable=True),
        sa.Column(
            "span",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("supersedes", sa.UUID(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("based_on", sa.String(length=64), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column(
            "sig",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["principal.id"], name=op.f("fk_statement_author_id")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_statement_run_id")
        ),
        sa.ForeignKeyConstraint(
            ["span_blob"], ["blob.sha256"], name=op.f("fk_statement_span_blob")
        ),
        sa.ForeignKeyConstraint(
            ["supersedes"], ["statement.id"], name=op.f("fk_statement_supersedes")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_statement")),
        sa.UniqueConstraint("hash", name=op.f("uq_statement_hash")),
    )
    op.create_index(op.f("ix_statement_prop"), "statement", ["prop"], unique=False)
    op.create_index(op.f("ix_statement_run_id"), "statement", ["run_id"], unique=False)
    op.create_index(
        op.f("ix_statement_span_blob"), "statement", ["span_blob"], unique=False
    )
    op.create_index(
        op.f("ix_statement_subject"), "statement", ["subject"], unique=False
    )
    op.create_table(
        "agent_wake",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_agent_wake_run_id")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["agent_task.id"], name=op.f("fk_agent_wake_task_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_wake")),
        sa.UniqueConstraint("task_id", "key", name=op.f("uq_agent_wake_task_id")),
    )
    op.create_index(
        "ix_agent_wake_pending", "agent_wake", ["task_id", "run_id"], unique=False
    )
    op.create_table(
        "finding_key",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("investigation", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("statement_id", sa.UUID(), nullable=True),
        sa.Column(
            "members",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "features",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "notes",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("fk_finding_key_run_id")
        ),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["statement.id"], name=op.f("fk_finding_key_statement_id")
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_finding_key")),
    )
    op.create_index(
        op.f("ix_finding_key_investigation"),
        "finding_key",
        ["investigation"],
        unique=False,
    )
    op.create_index(
        op.f("ix_finding_key_run_id"), "finding_key", ["run_id"], unique=False
    )
    op.create_table(
        "place",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("geonames_id", sa.BigInteger(), nullable=True),
        sa.Column("name_ar", sa.Text(), nullable=False),
        sa.Column("name_en", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("governorate_id", sa.UUID(), nullable=True),
        sa.Column("geom", Geo("geometry(Geometry,4326)"), nullable=False),
        sa.Column(
            "centroid",
            Geo("geography(Point,4326)"),
            sa.Computed(
                "ST_Centroid(geom)::geography",
            ),
            nullable=True,
        ),
        sa.Column(
            "lon",
            sa.Numeric(precision=9, scale=6),
            sa.Computed(
                "round(ST_X(ST_Centroid(geom))::numeric, 6)",
            ),
            nullable=True,
        ),
        sa.Column(
            "lat",
            sa.Numeric(precision=9, scale=6),
            sa.Computed(
                "round(ST_Y(ST_Centroid(geom))::numeric, 6)",
            ),
            nullable=True,
        ),
        sa.Column("precision_m", sa.Integer(), nullable=True),
        sa.Column("geo_source", sa.String(length=16), nullable=False),
        sa.Column("geo_statement_id", sa.UUID(), nullable=True),
        sa.Column("admin1_id", sa.UUID(), nullable=True),
        sa.Column("admin2_id", sa.UUID(), nullable=True),
        sa.Column(
            "area",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "geo_source IN ('geonames', 'cod-ab', 'geoboundaries', 'analyst', 'exif', 'c2pa', 'derived')",
            name=op.f("ck_place_geo_source_known"),
        ),
        sa.CheckConstraint("ST_IsValid(geom)", name=op.f("ck_place_geom_valid")),
        sa.CheckConstraint("precision_m > 0", name=op.f("ck_place_precision_positive")),
        sa.ForeignKeyConstraint(
            ["admin1_id"], ["admin_area.id"], name=op.f("fk_place_admin1_id")
        ),
        sa.ForeignKeyConstraint(
            ["admin2_id"], ["admin_area.id"], name=op.f("fk_place_admin2_id")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["principal.id"], name=op.f("fk_place_created_by")
        ),
        sa.ForeignKeyConstraint(
            ["geo_statement_id"],
            ["statement.id"],
            name=op.f("fk_place_geo_statement_id"),
        ),
        sa.ForeignKeyConstraint(
            ["governorate_id"], ["place.id"], name=op.f("fk_place_governorate_id")
        ),
        sa.ForeignKeyConstraint(
            ["kind"], ["place_kind.code"], name=op.f("fk_place_kind")
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["place.id"], name=op.f("fk_place_parent_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_place")),
        sa.UniqueConstraint("geonames_id", name=op.f("uq_place_geonames_id")),
        info={"postgis": True},
    )
    op.create_index(op.f("ix_place_admin1_id"), "place", ["admin1_id"], unique=False)
    op.create_index(op.f("ix_place_admin2_id"), "place", ["admin2_id"], unique=False)
    op.create_index(
        "ix_place_centroid",
        "place",
        ["centroid"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_place_geom", "place", ["geom"], unique=False, postgresql_using="gist"
    )
    op.create_index(
        op.f("ix_place_governorate_id"), "place", ["governorate_id"], unique=False
    )
    op.create_index(op.f("ix_place_parent_id"), "place", ["parent_id"], unique=False)
    op.create_table(
        "segment_embedding",
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column(
            "vector", HALFVEC(dim=768).with_variant(sa.JSON(), "sqlite"), nullable=False
        ),
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
        "statement_tier",
        sa.Column("statement_id", sa.UUID(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["statement_id"],
            ["statement.id"],
            name=op.f("fk_statement_tier_statement_id"),
        ),
        sa.PrimaryKeyConstraint("statement_id", name=op.f("pk_statement_tier")),
    )
    op.create_index(
        op.f("ix_statement_tier_tier"), "statement_tier", ["tier"], unique=False
    )
    op.create_table(
        "place_name",
        sa.Column("place_id", sa.UUID(), nullable=False),
        sa.Column("norm", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("lang", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "kind IN ('official', 'colloquial', 'transliteration', 'former', 'facility_nickname', 'variant')",
            name=op.f("ck_place_name_kind_known"),
        ),
        sa.CheckConstraint(
            "valid_to >= valid_from", name=op.f("ck_place_name_valid_order")
        ),
        sa.ForeignKeyConstraint(
            ["place_id"], ["place.id"], name=op.f("fk_place_name_place_id")
        ),
        sa.PrimaryKeyConstraint("place_id", "norm", name=op.f("pk_place_name")),
        info={"postgis": True},
    )
    op.create_index(op.f("ix_place_name_norm"), "place_name", ["norm"], unique=False)
    raw_sql()
    seed()


def downgrade() -> None:
    op.drop_index(op.f("ix_place_name_norm"), table_name="place_name")
    op.drop_table("place_name")
    op.drop_index(op.f("ix_statement_tier_tier"), table_name="statement_tier")
    op.drop_table("statement_tier")
    op.drop_index(
        "ix_segment_embedding_vector",
        table_name="segment_embedding",
        postgresql_using="hnsw",
        postgresql_ops={"vector": "halfvec_cosine_ops"},
    )
    op.drop_table("segment_embedding")
    op.drop_index(op.f("ix_place_parent_id"), table_name="place")
    op.drop_index(op.f("ix_place_governorate_id"), table_name="place")
    op.drop_index("ix_place_geom", table_name="place", postgresql_using="gist")
    op.drop_index("ix_place_centroid", table_name="place", postgresql_using="gist")
    op.drop_index(op.f("ix_place_admin2_id"), table_name="place")
    op.drop_index(op.f("ix_place_admin1_id"), table_name="place")
    op.drop_table("place")
    op.drop_index(op.f("ix_finding_key_run_id"), table_name="finding_key")
    op.drop_index(op.f("ix_finding_key_investigation"), table_name="finding_key")
    op.drop_table("finding_key")
    op.drop_index("ix_agent_wake_pending", table_name="agent_wake")
    op.drop_table("agent_wake")
    op.drop_index(op.f("ix_statement_subject"), table_name="statement")
    op.drop_index(op.f("ix_statement_span_blob"), table_name="statement")
    op.drop_index(op.f("ix_statement_run_id"), table_name="statement")
    op.drop_index(op.f("ix_statement_prop"), table_name="statement")
    op.drop_table("statement")
    op.drop_index(op.f("ix_sheet_version_dataset_id"), table_name="sheet_version")
    op.drop_index(op.f("ix_sheet_version_acquisition_id"), table_name="sheet_version")
    op.drop_table("sheet_version")
    op.drop_table("run_event")
    op.drop_index(op.f("ix_media_frame_run_id"), table_name="media_frame")
    op.drop_index(
        "ix_media_frame_hash",
        table_name="media_frame",
        postgresql_using="hnsw",
        postgresql_ops={"hash": "bit_hamming_ops"},
    )
    op.drop_index(op.f("ix_media_frame_blob_sha256"), table_name="media_frame")
    op.drop_table("media_frame")
    op.drop_index(op.f("ix_mcp_token_grant_id"), table_name="mcp_token")
    op.drop_table("mcp_token")
    op.drop_table("index_progress")
    op.drop_index(
        op.f("ix_evidence_segment_text_sha256"), table_name="evidence_segment"
    )
    op.drop_index(
        op.f("ix_evidence_segment_source_sha256"), table_name="evidence_segment"
    )
    op.drop_table("evidence_segment")
    op.drop_index(
        "ix_evidence_chunk_text",
        table_name="evidence_chunk",
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
    op.drop_index(op.f("ix_evidence_chunk_run_id"), table_name="evidence_chunk")
    op.drop_index(
        "ix_evidence_chunk_normalized",
        table_name="evidence_chunk",
        postgresql_using="gin",
        postgresql_ops={"normalized": "gin_trgm_ops"},
    )
    op.drop_table("evidence_chunk")
    op.drop_index(
        "ix_entity_embedding_vector",
        table_name="entity_embedding",
        postgresql_using="hnsw",
        postgresql_ops={"vector": "halfvec_cosine_ops"},
    )
    op.drop_index(op.f("ix_entity_embedding_run_id"), table_name="entity_embedding")
    op.drop_table("entity_embedding")
    op.drop_index(op.f("ix_audio_landmark_run_id"), table_name="audio_landmark")
    op.drop_index(op.f("ix_audio_landmark_hash"), table_name="audio_landmark")
    op.drop_table("audio_landmark")
    op.drop_index(op.f("ix_agent_task_user_id"), table_name="agent_task")
    op.drop_index("ix_agent_task_investigation", table_name="agent_task")
    op.drop_index("ix_agent_task_due", table_name="agent_task")
    op.drop_table("agent_task")
    op.drop_index(op.f("ix_sheet_dataset_investigation"), table_name="sheet_dataset")
    op.drop_table("sheet_dataset")
    op.drop_index(op.f("ix_sentence_brief_id"), table_name="sentence")
    op.drop_table("sentence")
    op.drop_index(op.f("ix_run_status"), table_name="run")
    op.drop_table("run")
    op.drop_index(op.f("ix_package_brief_id"), table_name="package")
    op.drop_table("package")
    op.drop_index(op.f("ix_mcp_grant_user_id"), table_name="mcp_grant")
    op.drop_table("mcp_grant")
    op.drop_table("case_setting")
    op.drop_table("case_record")
    op.drop_index(
        op.f("ix_assignment_event_assignment_id"), table_name="assignment_event"
    )
    op.drop_table("assignment_event")
    op.drop_index(op.f("ix_acquisition_origin"), table_name="acquisition")
    op.drop_index("ix_acquisition_investigation", table_name="acquisition")
    op.drop_index(op.f("ix_acquisition_blob_sha256"), table_name="acquisition")
    op.drop_table("acquisition")
    op.drop_index(op.f("ix_watch_user_id"), table_name="watch")
    op.drop_index(op.f("ix_watch_person"), table_name="watch")
    op.drop_table("watch")
    op.drop_index(op.f("ix_saved_story_user_id"), table_name="saved_story")
    op.drop_table("saved_story")
    op.drop_table("principal")
    op.drop_index("ix_notification_user_created", table_name="notification")
    op.drop_index(
        "ix_notification_unread",
        table_name="notification",
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.drop_table("notification")
    op.drop_table("brief")
    op.drop_table("attest_key")
    op.drop_index(
        "uq_assignment_open_subject",
        table_name="assignment",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_index("ix_assignment_investigation", table_name="assignment")
    op.drop_index(op.f("ix_assignment_assignee_id"), table_name="assignment")
    op.drop_table("assignment")
    op.drop_index(op.f("ix_anchor_batch_id"), table_name="anchor")
    op.drop_table("anchor")
    op.drop_index(op.f("ix_admin_area_set_id"), table_name="admin_area")
    op.drop_index(
        "ix_admin_area_geom", table_name="admin_area", postgresql_using="gist"
    )
    op.drop_table("admin_area")
    op.drop_table("policy")
    op.drop_table("place_kind")
    op.drop_index(op.f("ix_payload_value_key"), table_name="payload")
    op.drop_table("payload")
    op.drop_table("finding_salt")
    op.drop_index(
        "uq_embedding_space_active",
        table_name="embedding_space",
        postgresql_where=sa.text("active"),
        sqlite_where=sa.text("active"),
    )
    op.drop_table("embedding_space")
    op.drop_table("chain")
    op.drop_table("boundary_set")
    op.drop_table("blob")
    op.drop_table("batch")
    op.execute("drop function ledger_insert_only()")


def raw_sql():
    op.execute(
        "create function ledger_insert_only() returns trigger language plpgsql as $$\n"
        "        begin raise exception '% is insert-only', tg_table_name; end $$"
    )
    row = "for each row execute function ledger_insert_only()"
    for table in INSERT_ONLY:
        op.execute(
            f"create trigger {table}_insert_only before update or delete on {table} {row}"
        )
        op.execute(
            f"create trigger {table}_no_truncate before truncate on {table}"
            " for each statement execute function ledger_insert_only()"
        )
    # Assignment history only grows; erasure may delete a payload, never rewrite it.
    op.execute(
        "create trigger assignment_event_insert_only before update or delete"
        f" on assignment_event {row}"
    )
    op.execute(f"create trigger payload_no_update before update on payload {row}")


def seed():
    policy = sa.table(
        "policy",
        sa.column("capability"),
        sa.column("doc_type"),
        sa.column("sensitivity"),
        sa.column("routes", postgresql.ARRAY(sa.Text())),
        sa.column("auto_accept"),
    )
    op.bulk_insert(
        policy,
        [
            {
                "capability": c,
                "doc_type": d,
                "sensitivity": s,
                "routes": r,
                "auto_accept": a,
            }
            for c, d, s, r, a in POLICY
        ],
    )
    op.execute(
        "insert into role (name, description) select 'analyst', "
        "'Reads evidence and lets connected agents read and propose' "
        "where not exists (select 1 from role where name = 'analyst')"
    )
    kind = sa.table("place_kind", *map(sa.column, KIND_COLUMNS))
    op.bulk_insert(kind, [dict(zip(KIND_COLUMNS, row, strict=True)) for row in KINDS])
    # One random salt per deployment, independent of SECRET_KEY, so rotating that
    # never changes a suppression key.
    op.get_bind().execute(
        sa.text("insert into finding_salt (id, salt) values (1, :salt)"),
        {"salt": os.urandom(32)},
    )
