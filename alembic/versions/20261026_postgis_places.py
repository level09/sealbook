"""PostGIS place geometry with precision and provenance, a kind ladder table, and
place names valid over time"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "20261026_postgis"
down_revision = "20261025_saved_story"
branch_labels = None
depends_on = None

# code, family, country, level, label, default precision in metres
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
XY = "round({}(ST_Centroid(geom))::numeric, 6)"


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    kind = op.create_table(
        "place_kind",
        sa.Column("code", sa.String(20), primary_key=True),
        sa.Column("family", sa.String(12), nullable=False),
        sa.Column("country", sa.String(2)),
        sa.Column("level", sa.SmallInteger()),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("default_precision_m", sa.Integer()),
        sa.CheckConstraint(
            "family IN ('admin', 'settlement', 'facility')",
            name=op.f("ck_place_kind_family_known"),
        ),
        sa.CheckConstraint(
            "(family = 'admin') = (level IS NOT NULL)",
            name=op.f("ck_place_kind_level_only_admin"),
        ),
    )
    op.bulk_insert(
        kind,
        [
            dict(
                zip(
                    (
                        "code",
                        "family",
                        "country",
                        "level",
                        "label",
                        "default_precision_m",
                    ),
                    row,
                    strict=True,
                )
            )
            for row in KINDS
        ],
    )
    op.create_foreign_key("fk_place_kind", "place", "place_kind", ["kind"], ["code"])

    op.execute("ALTER TABLE place ADD COLUMN geom geometry(Geometry, 4326)")
    op.execute("UPDATE place SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)")
    op.execute("ALTER TABLE place ALTER COLUMN geom SET NOT NULL")
    op.drop_column("place", "lon")
    op.drop_column("place", "lat")
    op.execute(
        "ALTER TABLE place ADD COLUMN centroid geography(Point, 4326) "
        "GENERATED ALWAYS AS (ST_Centroid(geom)::geography) STORED"
    )
    for col, fn in (("lon", "ST_X"), ("lat", "ST_Y")):
        op.execute(
            f"ALTER TABLE place ADD COLUMN {col} numeric(9, 6) "
            f"GENERATED ALWAYS AS ({XY.format(fn)}) STORED"
        )
    op.create_index("ix_place_geom", "place", ["geom"], postgresql_using="gist")
    op.create_index("ix_place_centroid", "place", ["centroid"], postgresql_using="gist")

    op.add_column("place", sa.Column("precision_m", sa.Integer()))
    op.add_column("place", sa.Column("geo_source", sa.String(16)))
    op.add_column(
        "place",
        sa.Column(
            "geo_statement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("statement.id", name="fk_place_geo_statement_id"),
        ),
    )
    op.execute(
        "UPDATE place SET geo_source = CASE source WHEN 'geonames' THEN 'geonames' "
        "ELSE 'analyst' END"
    )
    op.execute(
        "UPDATE place SET precision_m = k.default_precision_m FROM place_kind k "
        "WHERE k.code = place.kind"
    )
    op.alter_column("place", "geo_source", nullable=False)
    op.create_check_constraint(op.f("ck_place_geom_valid"), "place", "ST_IsValid(geom)")
    op.create_check_constraint(
        op.f("ck_place_precision_positive"), "place", "precision_m > 0"
    )
    op.create_check_constraint(
        op.f("ck_place_geo_source_known"),
        "place",
        "geo_source IN ('geonames', 'cod-ab', 'geoboundaries', 'analyst', 'exif', "
        "'c2pa', 'derived')",
    )

    op.add_column("place_name", sa.Column("kind", sa.String(20)))
    op.add_column("place_name", sa.Column("valid_from", sa.Date()))
    op.add_column("place_name", sa.Column("valid_to", sa.Date()))
    op.add_column("place_name", sa.Column("source", sa.String(20)))
    op.execute(
        "UPDATE place_name n SET source = p.source, kind = CASE "
        "WHEN n.name IN (p.name_ar, p.name_en) THEN 'official' "
        "WHEN n.lang = 'en' THEN 'transliteration' ELSE 'variant' END "
        "FROM place p WHERE p.id = n.place_id"
    )
    op.alter_column("place_name", "kind", nullable=False)
    op.alter_column("place_name", "source", nullable=False)
    op.create_check_constraint(
        op.f("ck_place_name_kind_known"),
        "place_name",
        "kind IN ('official', 'colloquial', 'transliteration', 'former', "
        "'facility_nickname', 'variant')",
    )
    op.create_check_constraint(
        op.f("ck_place_name_valid_order"), "place_name", "valid_to >= valid_from"
    )


def downgrade():
    for col in ("source", "valid_to", "valid_from", "kind"):
        op.drop_column("place_name", col)
    op.drop_constraint(op.f("ck_place_geo_source_known"), "place")
    op.drop_constraint(op.f("ck_place_precision_positive"), "place")
    op.drop_constraint(op.f("ck_place_geom_valid"), "place")
    for col in ("geo_statement_id", "geo_source", "precision_m"):
        op.drop_column("place", col)
    op.drop_index("ix_place_centroid", "place")
    op.drop_index("ix_place_geom", "place")
    op.add_column("place", sa.Column("lon_plain", sa.Numeric(9, 6)))
    op.add_column("place", sa.Column("lat_plain", sa.Numeric(9, 6)))
    op.execute("UPDATE place SET lon_plain = lon, lat_plain = lat")
    for col in ("lon", "lat", "centroid", "geom"):
        op.drop_column("place", col)
    op.alter_column("place", "lon_plain", new_column_name="lon", nullable=False)
    op.alter_column("place", "lat_plain", new_column_name="lat", nullable=False)
    op.drop_constraint("fk_place_kind", "place")
    op.drop_table("place_kind")
    # The extension stays: other objects in the database may use it.
