"""Gazetteer projection. GeoNames is CC BY: show attribution wherever it is displayed.

Geometry lives in PostGIS: `geom` is the shape (point, line or polygon, WGS 84) and
`centroid`, `lon` and `lat` are generated from it, so nothing can drift apart. Every
geometry says how precise it is and where it came from."""

import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.types import UserDefinedType

from stk.extensions import Base

GEO_SOURCES = (
    "geonames",
    "cod-ab",
    "geoboundaries",
    "analyst",
    "exif",
    "c2pa",
    "derived",
)
# Name kinds. "variant" is an alternate spelling whose source does not say which.
NAME_KINDS = (
    "official",
    "colloquial",
    "transliteration",
    "former",
    "facility_nickname",
    "variant",
)


class Geo(UserDefinedType):
    """A PostGIS column in SRID 4326. Written as EWKT ("SRID=4326;POINT(36 33)"),
    read back as EWKT; spatial work happens in SQL, not in Python."""

    cache_ok = True

    def __init__(self, kind: str = "geometry", shape: str = "Geometry"):
        self.kind, self.shape = kind, shape

    def get_col_spec(self, **kw):
        return f"{self.kind}({self.shape},4326)"

    def bind_expression(self, value):
        return func.ST_GeomFromEWKT(value)

    def column_expression(self, col):
        return func.ST_AsEWKT(col)


# Tables that need PostGIS; SQLite-only framework tests leave them out of create_all.
POSTGIS = {"info": {"postgis": True}}


def point(lon, lat) -> str:
    """EWKT for one WGS 84 point; the form `Place.geom` takes."""
    return f"SRID=4326;POINT({lon} {lat})"


def _xy(fn: str) -> str:
    return f"round({fn}(ST_Centroid(geom))::numeric, 6)"


class PlaceKind(Base):
    """The kind ladder. Admin kinds carry a country and a level, so a second
    country adds rows, not code. Facility kinds follow the points of interest
    analysts use (detention facilities, grave sites)."""

    __tablename__ = "place_kind"

    code = Column(String(20), primary_key=True)
    family = Column(String(12), nullable=False)  # admin, settlement or facility
    country = Column(String(2))  # ISO 3166-1 alpha-2 for admin kinds
    level = Column(SmallInteger)  # admin depth, 1 = governorate
    label = Column(Text, nullable=False)
    # A GeoNames point of this kind is no more precise than this.
    default_precision_m = Column(Integer)

    __table_args__ = (
        CheckConstraint(
            "family IN ('admin', 'settlement', 'facility')", name="family_known"
        ),
        CheckConstraint(
            "(family = 'admin') = (level IS NOT NULL)", name="level_only_admin"
        ),
    )


class Place(Base):
    __tablename__ = "place"
    # Generated columns come back with every insert and update, so async code never
    # lazy-loads them.
    __mapper_args__ = {"eager_defaults": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    geonames_id = Column(BigInteger, unique=True)
    name_ar = Column(Text, nullable=False)
    name_en = Column(Text)
    kind = Column(String(20), ForeignKey("place_kind.code"), nullable=False)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("place.id"), index=True)
    # Denormalised so a scope check never walks the tree.
    governorate_id = Column(UUID(as_uuid=True), ForeignKey("place.id"), index=True)
    geom = Column(Geo(), nullable=False)
    centroid = Column(
        Geo("geography", "Point"), Computed("ST_Centroid(geom)::geography")
    )
    lon = Column(Numeric(9, 6), Computed(_xy("ST_X")))
    lat = Column(Numeric(9, 6), Computed(_xy("ST_Y")))
    # Radius of uncertainty; NULL when the source gives none (an admin area).
    precision_m = Column(Integer)
    geo_source = Column(String(16), nullable=False)
    # The signed statement that set an analyst geometry.
    geo_statement_id = Column(UUID(as_uuid=True), ForeignKey("statement.id"))
    # Point in polygon on the newest boundary set; NULL outside every area.
    admin1_id = Column(UUID(as_uuid=True), ForeignKey("admin_area.id"), index=True)
    admin2_id = Column(UUID(as_uuid=True), ForeignKey("admin_area.id"), index=True)
    area = Column(JSON().with_variant(JSONB, "postgresql"))
    source = Column(String(20), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("principal.id"))

    governorate = relationship("Place", remote_side=[id], foreign_keys=[governorate_id])
    admin1 = relationship("AdminArea", foreign_keys=[admin1_id])
    names = relationship("PlaceName", back_populates="place")

    __table_args__ = (
        Index("ix_place_geom", "geom", postgresql_using="gist"),
        Index("ix_place_centroid", "centroid", postgresql_using="gist"),
        CheckConstraint("ST_IsValid(geom)", name="geom_valid"),
        CheckConstraint("precision_m > 0", name="precision_positive"),
        CheckConstraint(
            "geo_source IN (" + ", ".join(f"'{s}'" for s in GEO_SOURCES) + ")",
            name="geo_source_known",
        ),
        POSTGIS,
    )


class BoundarySet(Base):
    """One published version of an administrative boundary dataset, with the
    licence and attribution its maps must show."""

    __tablename__ = "boundary_set"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(16), nullable=False)  # a GEO_SOURCES value
    country = Column(String(2), nullable=False)
    version = Column(Text, nullable=False)
    valid_on = Column(Date, nullable=False)
    licence = Column(Text, nullable=False)
    attribution = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    # Over the imported files, so a re-import of the same bytes is a no-op.
    sha256 = Column(String(64), nullable=False)
    imported_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("source", "country", "version"), POSTGIS)


class AdminArea(Base):
    __tablename__ = "admin_area"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id = Column(
        UUID(as_uuid=True), ForeignKey("boundary_set.id"), nullable=False, index=True
    )
    pcode = Column(String(16), nullable=False)
    level = Column(SmallInteger, nullable=False)
    parent_pcode = Column(String(16))
    name_ar = Column(Text)
    name_en = Column(Text, nullable=False)
    geom = Column(Geo("geometry", "MultiPolygon"), nullable=False)

    __table_args__ = (
        UniqueConstraint("set_id", "pcode"),
        Index("ix_admin_area_geom", "geom", postgresql_using="gist"),
        POSTGIS,
    )


class PlaceName(Base):
    __tablename__ = "place_name"

    # The composite key is the (place_id, norm) uniqueness the importer relies on.
    place_id = Column(UUID(as_uuid=True), ForeignKey("place.id"), primary_key=True)
    norm = Column(Text, primary_key=True, index=True)
    name = Column(Text, nullable=False)
    lang = Column(String(8), nullable=False)
    kind = Column(String(20), nullable=False)
    # When the name was in use; NULL bounds are open.
    valid_from = Column(Date)
    valid_to = Column(Date)
    source = Column(String(20), nullable=False)

    place = relationship("Place", back_populates="names")

    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in NAME_KINDS) + ")",
            name="kind_known",
        ),
        CheckConstraint("valid_to >= valid_from", name="valid_order"),
        POSTGIS,
    )
