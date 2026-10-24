#!/usr/bin/env bash
# Refresh vendored frontend assets. stk has no build step, so these files are
# committed. Bump a version here, run this, then run `uv run quart smoke`.
set -euo pipefail

VUE_VERSION="3.5.40"
VUETIFY_VERSION="4.1.6"
AXIOS_VERSION="1.18.1"
TABLER_VERSION="3.45.0"
MAPLIBRE_VERSION="6.12.0"
PMTILES_VERSION="4.5.0"
FFLATE_VERSION="0.8.2"
PDFJS_VERSION="4.10.38"
NATURAL_EARTH_VERSION="5.1.2"

cd "$(dirname "$0")"
STATIC="stk/static"
CDN="https://cdn.jsdelivr.net/npm"

fetch() {
  echo "  $2"
  curl -fsSL "$1" -o "$2"
}

echo "vue $VUE_VERSION"
fetch "$CDN/vue@$VUE_VERSION/dist/vue.global.prod.js" "$STATIC/js/vue.min.js"

echo "vuetify $VUETIFY_VERSION"
fetch "$CDN/vuetify@$VUETIFY_VERSION/dist/vuetify.min.js" "$STATIC/js/vuetify.min.js"
fetch "$CDN/vuetify@$VUETIFY_VERSION/dist/vuetify.min.css" "$STATIC/css/vuetify.min.css"

echo "axios $AXIOS_VERSION"
fetch "$CDN/axios@$AXIOS_VERSION/dist/axios.min.js" "$STATIC/js/axios.min.js"

echo "pdfjs-dist $PDFJS_VERSION"
mkdir -p "$STATIC/pdfjs"
for f in build/pdf.min.mjs build/pdf.worker.min.mjs LICENSE; do
  fetch "$CDN/pdfjs-dist@$PDFJS_VERSION/$f" "$STATIC/pdfjs/$(basename "$f")"
done

echo "tabler icons $TABLER_VERSION"
mkdir -p "$STATIC/icons/fonts"
fetch "$CDN/@tabler/icons-webfont@$TABLER_VERSION/dist/tabler-icons.min.css" "$STATIC/icons/tabler-icons.min.css"
fetch "$CDN/@tabler/icons-webfont@$TABLER_VERSION/dist/fonts/tabler-icons.woff2" "$STATIC/icons/fonts/tabler-icons.woff2"

# Keep woff2 only; the published stylesheet also lists woff and ttf fallbacks
# that we do not vendor, and a missing font file fails `quart smoke`.
python3 - "$STATIC/icons/tabler-icons.min.css" <<'PY'
import re
import sys

path = sys.argv[1]
css = open(path).read()
css, count = re.subn(
    r'src:url\("\./fonts/tabler-icons\.woff2[^}]*?format\("truetype"\)',
    'src:url("./fonts/tabler-icons.woff2") format("woff2")',
    css,
    count=1,
)
if count != 1:
    raise SystemExit(f"tabler css @font-face changed shape, adjust {path}")
open(path, "w").write(css)
PY

echo "maplibre-gl $MAPLIBRE_VERSION"
mkdir -p "$STATIC/maplibre"
for f in maplibre-gl.mjs maplibre-gl-shared.mjs maplibre-gl-worker.mjs maplibre-gl.css; do
  fetch "$CDN/maplibre-gl@$MAPLIBRE_VERSION/dist/$f" "$STATIC/maplibre/$f"
done

# Offline basemap reader: the pmtiles protocol for MapLibre, with its one
# dependency beside it (used only where the browser lacks DecompressionStream).
echo "pmtiles $PMTILES_VERSION, fflate $FFLATE_VERSION"
fetch "$CDN/fflate@$FFLATE_VERSION/esm/browser.js" "$STATIC/maplibre/fflate.mjs"
curl -fsSL "$CDN/pmtiles@$PMTILES_VERSION/dist/esm/index.js" \
  | sed 's#from"fflate"#from"./fflate.mjs"#' > "$STATIC/maplibre/pmtiles.mjs"
grep -q 'from"./fflate.mjs"' "$STATIC/maplibre/pmtiles.mjs" \
  || { echo "pmtiles import of fflate changed shape, adjust vendor.sh"; exit 1; }
echo "  $STATIC/maplibre/pmtiles.mjs"

# Monitor base map: country outlines around Syria from Natural Earth (public
# domain), so the map renders with no tile server and no internet.
echo "natural earth $NATURAL_EARTH_VERSION"
curl -fsSL "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v$NATURAL_EARTH_VERSION/geojson/ne_50m_admin_0_countries.geojson" \
  | python3 -c '
import json, sys
keep = {"SYR", "LBN", "JOR", "ISR", "PSX", "TUR", "IRQ", "CYP"}
def r(c):
    return [r(x) for x in c] if isinstance(c[0], list) else [round(c[0], 3), round(c[1], 3)]
fs = [{"type": "Feature", "properties": {"iso": f["properties"]["ADM0_A3"], "name": f["properties"]["NAME"]},
       "geometry": {"type": f["geometry"]["type"], "coordinates": r(f["geometry"]["coordinates"])}}
      for f in json.load(sys.stdin)["features"] if f["properties"]["ADM0_A3"] in keep]
json.dump({"type": "FeatureCollection", "features": fs}, sys.stdout, separators=(",", ":"))
' > "$STATIC/maplibre/region.geojson"
echo "  $STATIC/maplibre/region.geojson"

cat > "$STATIC/VERSIONS.txt" <<EOF
vue $VUE_VERSION
vuetify $VUETIFY_VERSION
axios $AXIOS_VERSION
@tabler/icons-webfont $TABLER_VERSION
maplibre-gl $MAPLIBRE_VERSION
natural-earth-vector $NATURAL_EARTH_VERSION (ne_50m_admin_0_countries, monitor region)
EOF

echo
echo "Vendored:"
cat "$STATIC/VERSIONS.txt"
echo
echo "Next: uv run quart smoke"
