"""Dev only: seed N synthetic located claims and time the claim tiles.

    createdb sealbook_map_scale
    SQLALCHEMY_DATABASE_URI=postgresql+asyncpg:///sealbook_map_scale \\
        uv run stk db upgrade
    SQLALCHEMY_DATABASE_URI=postgresql+asyncpg:///sealbook_map_scale \\
        uv run python -m stk.maps.scale --count 100000

Like `stk.sources.scale`, rows go straight into the insert-only ledger tables, so it
refuses any database whose name does not contain "scale". Every row is synthetic."""

from __future__ import annotations

import asyncio
import math
import random
import statistics
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import click
from sqlalchemy import insert, select, text

from stk.ledger.contracts import Prop, Tier
from stk.ledger.models import (
    Acquisition,
    Blob,
    Payload,
    Principal,
    Statement,
    StatementTier,
)
from stk.maps import tiles
from stk.monitor.data import LOCATED
from stk.places.models import Place, PlaceName, point
from stk.sources.scale import _hex, throwaway

INVESTIGATION = "case-scale-map"
# Syria's extent, where the claims fall.
LON, LAT = (35.8, 42.2), (32.4, 37.2)
CLAIM_TIERS = [Tier.ACCEPTED, Tier.CORROBORATED, Tier.UNCONFIRMED, Tier.REJECTED]


async def seed(db, count: int, places: int = 2_000, rng_seed: int = 11) -> None:
    rng = random.Random(rng_seed)
    now = datetime.now(UTC)
    agent = {"id": uuid.uuid4(), "kind": "agent", "name": "synthetic-extractor"}
    human = {"id": uuid.uuid4(), "kind": "human", "name": "synthetic-analyst"}
    await db.execute(insert(Principal), [agent, human])
    # Places cluster around a few towns, as real claims do.
    towns = [(rng.uniform(*LON), rng.uniform(*LAT)) for _ in range(40)]
    sites = []
    for i in range(places):
        cx, cy = rng.choice(towns)
        sites.append(
            {
                "id": uuid.uuid4(),
                "name_ar": f"موقع تجريبي {i}",
                "name_en": f"Synthetic site {i}",
                "kind": "facility",
                "geom": point(
                    round(cx + rng.gauss(0, 0.08), 6), round(cy + rng.gauss(0, 0.08), 6)
                ),
                "precision_m": rng.choice([100, 250, 1000]),
                "geo_source": "analyst",
                "source": "person",
            }
        )
    names = [
        {
            "place_id": s["id"],
            "norm": f"synthetic site {i}",
            "name": s["name_en"],
            "lang": "en",
            "kind": "official",
            "source": "person",
        }
        for i, s in enumerate(sites)
    ]
    shas = [_hex(rng) for _ in range(max(1, count // 20))]
    blobs = [
        {
            "sha256": sha,
            "md5": _hex(rng, 32),
            "size": 1000,
            "mime": "text/plain",
            "storage_ref": "synthetic",
            "created_at": now,
        }
        for sha in shas
    ]
    acqs = [
        {
            "id": uuid.uuid4(),
            "blob_sha256": sha,
            "origin": "synthetic:map-scale",
            "collector_id": human["id"],
            "at": now,
            "context": {"investigation": INVESTIGATION, "synthetic": True},
            "sensitivity": "ordinary",
            "doc_type": "register",
        }
        for sha in shas
    ]
    people = [f"person:{uuid.uuid4()}" for _ in range(count // 8)]
    stmts, tier_rows, payloads = [], [], []

    def statement(subject, prop, value, author, **extra):
        sid, commitment = uuid.uuid4(), _hex(rng)
        stmts.append(
            {
                "id": sid,
                "subject": subject,
                "prop": prop,
                "commitment": commitment,
                "author_id": author,
                "recorded_at": now,
                "hash": _hex(rng),
                "evidence": [],
                "span_blob": None,
                "valid_from": None,
                "valid_to": None,
                **extra,
            }
        )
        payloads.append(
            {
                "commitment": commitment,
                "salt": rng.randbytes(32),
                "value": value,
                "value_key": _hex(rng),
                "idem_key": _hex(rng),
            }
        )
        return sid

    located = sorted(LOCATED)
    for _ in range(count):
        n = rng.randrange(places)
        day = date(2012, 1, 1) + timedelta(days=rng.randrange(2500))
        sid = statement(
            rng.choice(people),
            rng.choice(located),
            {"text": f"Synthetic site {n}", "lang": "en"},
            agent["id"],
            span_blob=rng.choice(shas),
            span={"start": 0, "end": 10},
            valid_from=day,
            valid_to=day,
        )
        tier_rows.append(
            {
                "statement_id": sid,
                "tier": rng.choice(CLAIM_TIERS),
                "reason": "synthetic",
            }
        )
        # Nine in ten claims carry their own confirmed place; the rest resolve, or
        # not, through a confirmed name variant.
        if rng.random() < 0.9:
            link = statement(
                f"statement:{sid}",
                Prop.LOCATED_AT,
                {"place": f"place:{sites[n]['id']}", "name": f"synthetic site {n}"},
                human["id"],
            )
            tier_rows.append(
                {"statement_id": link, "tier": Tier.ACCEPTED, "reason": "synthetic"}
            )
    for model, rows in (
        (Place, sites),
        (PlaceName, names),
        (Blob, blobs),
        (Acquisition, acqs),
        (Payload, payloads),
        (Statement, stmts),
        (StatementTier, tier_rows),
    ):
        for i in range(0, len(rows), 5_000):
            await db.execute(insert(model), rows[i : i + 5_000])
    await db.execute(text("analyze"))


def _tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


async def measure(db, zooms=(5, 7, 9, 11, 13), per_zoom: int = 12, seed: int = 3):
    """Time tiles over Syria per zoom, as the endpoint serves them: the first
    request of a view builds the reader's model (cold), the rest reuse it (warm).
    Returns {zoom: (p50 ms, p95 ms, mean bytes)}, "all", and "cold" build times."""
    rng = random.Random(seed)
    f = {"investigation": INVESTIGATION, "time": None}
    tiers = [t for t in tiles.TIERS if t != Tier.REJECTED]
    cold = []
    for _ in range(5):
        start = time.perf_counter()
        model = await tiles.build(db, f, tiers)
        cold.append((time.perf_counter() - start) * 1000)
    out, every = {}, []
    for z in zooms:
        took, sizes = [], []
        for _ in range(per_zoom):
            x, y = _tile_of(rng.uniform(*LON), rng.uniform(*LAT), z)
            start = time.perf_counter()
            body = await tiles.tile(db, model, z, x, y)
            took.append((time.perf_counter() - start) * 1000)
            sizes.append(len(body))
        every += took
        out[z] = (_pct(took, 50), _pct(took, 95), round(statistics.mean(sizes)))
    out["all"] = (_pct(every, 50), _pct(every, 95), None)
    out["cold"] = (_pct(cold, 50), _pct(cold, 95), len(model.points))
    return out


def _pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, math.ceil(p / 100 * len(xs)) - 1)], 1)


@click.command()
@click.option("--count", default=100_000, show_default=True)
@click.option(
    "--measure-only", is_flag=True, help="Time tiles on an already seeded db."
)
def main(count, measure_only):
    import stk.extensions as ext
    from stk.app import create_app
    from stk.settings import Config

    if not throwaway(Config.SQLALCHEMY_DATABASE_URI):
        raise click.UsageError(
            "refusing: point SQLALCHEMY_DATABASE_URI at a throwaway database whose"
            " name contains 'scale'; ledger rows can never be deleted"
        )
    create_app()

    async def run():
        if not measure_only:
            async with ext.async_session_factory() as db, db.begin():
                if await db.scalar(select(Place.id).limit(1)):
                    raise click.UsageError("the database already has places")
                await seed(db, count)
        async with ext.async_session_factory() as db:
            result = await measure(db)
        await ext.engine.dispose()
        return result

    for z, (p50, p95, size) in asyncio.run(run()).items():
        extra = ""
        if z == "cold":
            extra = f", model of {size:,} points"
        elif size is not None:
            extra = f", mean {size:,} bytes"
        click.echo(f"z{z}: p50 {p50} ms, p95 {p95} ms{extra}")


if __name__ == "__main__":
    main()
