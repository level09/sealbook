"""`stk places import`: load GeoNames rows (SY.txt layout) into place and place_name."""

import csv
import re
import uuid
from decimal import Decimal
from pathlib import Path

import click
import stk.extensions as ext
from quart.cli import ScriptInfo
from sqlalchemy import select

from sealbook.places import boundaries, normalise
from sealbook.places.models import Place, PlaceKind, PlaceName, point

FIXTURE = Path(__file__).parent / "fixtures" / "syria_sample.tsv"
ARABIC = re.compile(r"[؀-ۿ]")
DEPTH = {"ADM1": "governorate", "ADM2": "district", "ADM3": "subdistrict"}
LEVEL = {"governorate": 1, "district": 2, "subdistrict": 3}


def kind_of(code: str, cls: str) -> str:
    if code in DEPTH:
        return DEPTH[code]
    if code == "PPLX":
        return "neighbourhood"
    return "populated" if cls == "P" else "facility"


def name_kind(name: str, lang: str, row: dict) -> str:
    """The row's own Arabic and English names are official; GeoNames does not say
    what its alternates are, beyond Latin ones being transliterations."""
    if name in (row["name_ar"], row["name_en"]):
        return "official"
    return "transliteration" if lang == "en" else "variant"


def parse(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            names = [r[1], r[2], *r[3].split(",")]
            names = [n.strip() for n in names if n.strip()]
            arabic = next((n for n in names if ARABIC.search(n)), r[1])
            rows.append(
                {
                    # An empty id marks a person-recorded site, matched by Arabic name.
                    "geonames_id": int(r[0]) if r[0] else None,
                    "name_ar": arabic,
                    "name_en": r[2] or None,
                    "names": names,
                    "kind": kind_of(r[7], r[6]),
                    "lat": Decimal(r[4]).quantize(Decimal("0.000001")),
                    "lon": Decimal(r[5]).quantize(Decimal("0.000001")),
                    "admin": tuple(r[10:13]),
                }
            )
    return rows


async def import_file(db, path) -> dict:
    """Upsert on geonames_id; flushes, the caller commits."""
    rows = parse(Path(path))
    existing = (await db.execute(select(Place))).scalars().all()
    by_geo = {p.geonames_id: p for p in existing if p.geonames_id}
    by_person = {p.name_ar: p for p in existing if p.source == "person"}
    precision = dict(
        (await db.execute(select(PlaceKind.code, PlaceKind.default_precision_m))).all()
    )
    imported = updated = 0
    placed = []
    for row in rows:
        geo = row["geonames_id"]
        place = by_geo.get(geo) if geo else by_person.get(row["name_ar"])
        fields = {
            "name_ar": row["name_ar"],
            "name_en": row["name_en"],
            "kind": row["kind"],
            "precision_m": precision[row["kind"]],
        }
        where = point(row["lon"], row["lat"])
        if place is None:
            place = Place(
                id=uuid.uuid4(),
                geonames_id=geo,
                source="geonames" if geo else "person",
                geom=where,
                geo_source="geonames" if geo else "analyst",
                **fields,
            )
            db.add(place)
            imported += 1
            changed = False
            new = True
        else:
            new = False
            changed = any(getattr(place, k) != v for k, v in fields.items())
            for k, v in fields.items():
                setattr(place, k, v)
            if (place.lon, place.lat) != (row["lon"], row["lat"]):
                place.geom = where
                changed = True
        placed.append((row, place, changed, new))
    await db.flush()

    # Parent is the deepest administrative area whose codes match this row's.
    area = {
        r["admin"][: LEVEL[r["kind"]]]: p for r, p, _, _ in placed if r["kind"] in LEVEL
    }
    for row, place, changed, new in placed:
        admin = row["admin"]
        parent = None
        for n in (3, 2, 1):
            candidate = area.get(admin[:n])
            if candidate is not None and candidate is not place:
                parent = candidate
                break
        gov = area.get(admin[:1])
        link = (parent.id if parent else None, gov.id if gov else None)
        if (place.parent_id, place.governorate_id) != link:
            place.parent_id, place.governorate_id = link
            changed = True
        known = {
            n.norm
            for n in (
                await db.execute(
                    select(PlaceName).where(PlaceName.place_id == place.id)
                )
            ).scalars()
        }
        for name in row["names"]:
            norm = normalise(name)
            if norm in known:
                continue
            known.add(norm)
            lang = "ar" if ARABIC.search(name) else "en"
            db.add(
                PlaceName(
                    place_id=place.id,
                    name=name,
                    norm=norm,
                    lang=lang,
                    kind=name_kind(name, lang, row),
                    source=place.source,
                )
            )
            changed = True
        if changed and not new:
            updated += 1
    await db.flush()
    await boundaries.assign(db, [p.id for _, p, _, _ in placed])
    return {"imported": imported, "updated": updated}


@click.group("places")
def places():
    """Gazetteer of places that findings and statements can point to."""


@places.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False), required=False)
@click.option("--fixture", is_flag=True, help="Load the committed synthetic sample.")
def import_cmd(path, fixture):
    """Import a GeoNames country file (e.g. SY.txt); safe to repeat."""
    if bool(path) == fixture:
        raise click.UsageError("give a path or --fixture, not both or neither")
    # Imported here: stk.cli imports this module, so a top-level import would cycle.
    from stk.cli.base import run_async

    click.get_current_context().ensure_object(ScriptInfo).load_app()

    async def _run():
        async with ext.async_session_factory() as db, db.begin():
            return await import_file(db, FIXTURE if fixture else path)

    result = run_async(_run())
    click.echo(f"imported {result['imported']}, updated {result['updated']}")


@places.command("boundaries")
@click.argument("path", type=click.Path(exists=True), required=False)
@click.option(
    "--fixture", is_flag=True, help="Load the committed four-governorate subset."
)
def boundaries_cmd(path, fixture):
    """Import OCHA COD-AB boundaries (the GeoJSON zip from HDX, or its unzipped
    folder) and set every place's governorate and district by point in polygon."""
    if bool(path) == fixture:
        raise click.UsageError("give a path or --fixture, not both or neither")
    from stk.cli.base import run_async

    click.get_current_context().ensure_object(ScriptInfo).load_app()

    async def _run():
        async with ext.async_session_factory() as db, db.begin():
            return await boundaries.import_set(
                db, boundaries.FIXTURE if fixture else path
            )

    result = run_async(_run())
    click.echo(
        f"boundary set {result['set']}: {result['areas']} new areas, "
        f"{result['places']} places inside an admin 1 area"
    )
