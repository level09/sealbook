"""Administrative boundaries from OCHA COD-AB (CC BY 3.0 IGO) and the point-in-polygon
assignment of every place to its governorate (admin 1) and district (admin 2).

The COD-AB GeoJSON export names each level's columns adm<N>_name (English),
adm<N>_name1 (Arabic) and adm<N>_pcode, with valid_on and version per feature."""

import hashlib
import io
import re
import uuid
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import orjson
from sqlalchemy import func, select, text

from stk.places.models import AdminArea, BoundarySet, Place

FIXTURE = Path(__file__).parent / "fixtures" / "codab"
URL = "https://data.humdata.org/dataset/cod-ab-syr"
LICENCE = "CC BY 3.0 IGO"
ATTRIBUTION = "Boundaries: UNCS, OCHA ROSC (COD-AB, CC BY 3.0 IGO)"
FILE = re.compile(r"admin([123])\.geojson$")


def read(path: Path) -> dict[int, bytes]:
    """Level -> GeoJSON bytes from a COD-AB GeoJSON zip or a directory of its files."""
    path = Path(path)
    if path.is_dir():
        files = {p.name: p.read_bytes() for p in sorted(path.iterdir())}
    else:
        with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as z:
            files = {n: z.read(n) for n in sorted(z.namelist())}
    out = {int(m[1]): raw for n, raw in files.items() if (m := FILE.search(n))}
    if 1 not in out:
        raise ValueError("no admin1 GeoJSON found (expected *admin1.geojson)")
    return out


def features(level: int, raw: bytes):
    for f in orjson.loads(raw)["features"]:
        p = f["properties"]
        yield {
            "pcode": p[f"adm{level}_pcode"],
            "parent": p.get(f"adm{level - 1}_pcode") if level > 1 else None,
            "name_en": p[f"adm{level}_name"],
            "name_ar": p.get(f"adm{level}_name1"),
            "geometry": orjson.dumps(f["geometry"]).decode(),
            "valid_on": p["valid_on"],
            "version": p["version"],
        }


async def import_set(db, path, country="SY", url=URL) -> dict:
    """One boundary set per (source, country, version). The same bytes again are a
    no-op; other bytes under a known version are refused. Flushes, the caller
    commits."""
    levels = read(path)
    digest = hashlib.sha256()
    for level in sorted(levels):
        digest.update(hashlib.sha256(levels[level]).digest())
    rows = {level: list(features(level, raw)) for level, raw in levels.items()}
    version = {r["version"] for level in rows.values() for r in level}
    valid_on = {r["valid_on"] for level in rows.values() for r in level}
    if len(version) != 1 or len(valid_on) != 1:
        raise ValueError("the files mix boundary versions")
    version, valid_on = version.pop(), date.fromisoformat(valid_on.pop()[:10])
    found = await db.scalar(
        select(BoundarySet).where(
            BoundarySet.source == "cod-ab",
            BoundarySet.country == country,
            BoundarySet.version == version,
        )
    )
    if found is not None:
        if found.sha256 != digest.hexdigest():
            raise ValueError(f"cod-ab {version} is already imported from other bytes")
        return {"set": str(found.id), "areas": 0, "places": await assign(db)}
    bset = BoundarySet(
        id=uuid.uuid4(),
        source="cod-ab",
        country=country,
        version=version,
        valid_on=valid_on,
        licence=LICENCE,
        attribution=ATTRIBUTION,
        url=url,
        sha256=digest.hexdigest(),
        imported_at=datetime.now(UTC),
    )
    db.add(bset)
    await db.flush()
    areas = 0
    for level, found in rows.items():
        for r in found:
            # Simplified or hand-edited rings can self-touch; keep only the areas.
            geom = func.ST_Multi(
                func.ST_CollectionExtract(
                    func.ST_MakeValid(
                        func.ST_SetSRID(func.ST_GeomFromGeoJSON(r["geometry"]), 4326)
                    ),
                    3,
                )
            )
            await db.execute(
                AdminArea.__table__.insert().values(
                    id=uuid.uuid4(),
                    set_id=bset.id,
                    pcode=r["pcode"],
                    level=level,
                    parent_pcode=r["parent"],
                    name_ar=r["name_ar"],
                    name_en=r["name_en"],
                    geom=geom,
                )
            )
            areas += 1
    return {"set": str(bset.id), "areas": areas, "places": await assign(db)}


def current_set():
    """The newest boundary set: assignments follow it, older sets stay for history.
    ponytail: one current set for every date; pick the set valid at the claim's date
    if boundaries are redrawn inside a case's time span."""
    return (
        select(BoundarySet.id)
        .order_by(BoundarySet.valid_on.desc(), BoundarySet.imported_at.desc())
        .limit(1)
        .scalar_subquery()
    )


async def assign(db, ids=None) -> int:
    """Set admin1_id and admin2_id of places (all, or `ids`) from the area of the
    current set covering each centroid; a centroid outside every area, or no set at
    all, leaves them NULL. Returns how many of those places now have an admin 1."""
    params = {"set": await db.scalar(select(current_set()))}
    where = ""
    if ids is not None:
        where, params["ids"] = "WHERE p.id = ANY(:ids)", list(ids)
    pick = """(SELECT a.id FROM admin_area a WHERE a.set_id = :set AND a.level = {}
        AND ST_Covers(a.geom, p.centroid::geometry) ORDER BY a.pcode LIMIT 1)"""
    await db.execute(
        text(
            f"UPDATE place p SET admin1_id = {pick.format(1)}, "
            f"admin2_id = {pick.format(2)} {where}"
        ),
        params,
    )
    q = select(func.count()).select_from(Place).where(Place.admin1_id.is_not(None))
    if ids is not None:
        q = q.where(Place.id.in_(params["ids"]))
    return await db.scalar(q)


async def attribution(db) -> str | None:
    return await db.scalar(
        select(BoundarySet.attribution).where(BoundarySet.id == current_set())
    )


async def assign_new(db, places) -> None:
    """Assign freshly flushed places and refresh their admin columns, so callers can
    read them without a lazy load."""
    await assign(db, [p.id for p in places])
    for p in places:
        await db.refresh(p, ["admin1_id", "admin2_id"])
