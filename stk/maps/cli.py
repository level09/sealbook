"""`stk basemap`: fetch a private, offline basemap extract once.

The app then serves it itself (`/api/map/basemap.pmtiles`), so no map view ever asks
a third party for tiles. The extract is OpenStreetMap data packaged by Protomaps
(ODbL; maps must credit OpenStreetMap contributors)."""

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

import click

from stk.settings import Config

BUILDS = "https://build-metadata.protomaps.dev/builds.json"
PLANET = "https://build.protomaps.com/{}"
# Syria with Lebanon, northern Jordan and the Turkish and Iraqi border areas.
BBOX = "34.9,32.0,42.6,37.6"
MAXZOOM = 14


def latest_build() -> str:
    # The metadata host refuses Python's default user agent.
    with urlopen(Request(BUILDS, headers={"User-Agent": "sealbook"}), timeout=60) as r:
        return json.load(r)[-1]["key"]


@click.command("basemap")
@click.option(
    "--build", help="Protomaps daily build, e.g. 20261023.pmtiles; default latest."
)
@click.option(
    "--bbox", default=BBOX, show_default=True, help="min lon,min lat,max lon,max lat"
)
@click.option(
    "--maxzoom", default=MAXZOOM, show_default=True, type=click.IntRange(0, 15)
)
def basemap(build, bbox, maxzoom):
    """Extract an offline basemap for the region into BASEMAP_PATH."""
    tool = shutil.which("pmtiles")
    if tool is None:
        raise click.ClickException(
            "needs the pmtiles CLI (brew install pmtiles, or go-pmtiles releases)"
        )
    build = build or latest_build()
    target = Path(Config.BASEMAP_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    click.echo(f"Extracting {bbox} to zoom {maxzoom} from {build}")
    subprocess.run(
        [
            tool,
            "extract",
            PLANET.format(build),
            str(partial),
            f"--bbox={bbox}",
            f"--maxzoom={maxzoom}",
        ],
        check=True,
    )
    digest = hashlib.sha256()
    with open(partial, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    partial.replace(target)
    about = {
        "build": build,
        "bbox": bbox,
        "maxzoom": maxzoom,
        "bytes": target.stat().st_size,
        "sha256": digest.hexdigest(),
        "fetched_at": datetime.now(UTC).isoformat(),
        "attribution": ATTRIBUTION,
    }
    target.with_suffix(".json").write_text(json.dumps(about, indent=2))
    click.echo(f"{target}: {about['bytes'] / 1e6:.0f} MB, sha256 {about['sha256']}")


ATTRIBUTION = "© OpenStreetMap contributors, Protomaps"
