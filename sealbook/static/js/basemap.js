/**
 * Offline basemap: a Protomaps PMTiles extract the app serves itself, read with HTTP
 * range requests through the pmtiles protocol, so no street a person looks at is
 * ever asked of a third party. Without the extract (`stk basemap` not run), maps keep
 * the vendored Natural Earth outlines. Fill and line layers only: MapLibre needs a
 * glyph server for text, and there is none.
 */
import * as maplibregl from '/static/maplibre/maplibre-gl.mjs';
import {Protocol} from '/static/maplibre/pmtiles.mjs';

let registered = false;
let about = null;

async function meta() {
  about ??= fetch('/api/map/basemap.json').then(r => (r.ok ? r.json() : {url: null})).catch(() => ({url: null}));
  return about;
}

/* Adds the basemap under `before` and hides `hide` (the outline land fill). Resolves
   true when the extract is there. `c` needs bg, land, border and muted colours. */
// MapLibre opens a compact attribution once its text arrives and folds it only on a
// drag; fold it when the map first settles so credits never cover the points.
function foldAttribution(map) {
  map.once('idle', () => {
    const el = map.getContainer().querySelector('.maplibregl-ctrl-attrib.maplibregl-compact');
    el?.classList.remove('maplibregl-compact-show');
    el?.removeAttribute('open');
  });
}

export async function addBasemap(map, c, before, hide = []) {
  foldAttribution(map);
  const {url, attribution} = await meta();
  if (!url) return false;
  if (!registered) {
    maplibregl.addProtocol('pmtiles', new Protocol().tile);
    registered = true;
  }
  map.addSource('basemap', {type: 'vector', url: `pmtiles://${location.origin}${url}`, attribution});
  const layer = spec => map.addLayer({source: 'basemap', ...spec}, before);
  layer({id: 'bm-earth', type: 'fill', 'source-layer': 'earth', paint: {'fill-color': c.land}});
  layer({id: 'bm-landuse', type: 'fill', 'source-layer': 'landuse', paint: {'fill-color': c.muted, 'fill-opacity': 0.06}});
  layer({id: 'bm-water', type: 'fill', 'source-layer': 'water', paint: {'fill-color': c.bg}});
  layer({id: 'bm-buildings', type: 'fill', 'source-layer': 'buildings', minzoom: 14, paint: {'fill-color': c.muted, 'fill-opacity': 0.18}});
  layer({id: 'bm-roads', type: 'line', 'source-layer': 'roads', filter: ['in', ['get', 'kind'], ['literal', ['highway', 'major_road', 'minor_road']]],
    paint: {'line-color': c.border, 'line-width': ['match', ['get', 'kind'], 'highway', 1.4, 'major_road', 0.9, 0.5]}});
  layer({id: 'bm-boundaries', type: 'line', 'source-layer': 'boundaries', filter: ['in', ['get', 'kind'], ['literal', ['country', 'region']]],
    paint: {'line-color': c.muted, 'line-width': 0.8, 'line-dasharray': [4, 2]}});
  for (const id of hide) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'none');
  return true;
}

export function recolorBasemap(map, c) {
  if (!map.getLayer('bm-earth')) return;
  map.setPaintProperty('bm-earth', 'fill-color', c.land);
  map.setPaintProperty('bm-landuse', 'fill-color', c.muted);
  map.setPaintProperty('bm-water', 'fill-color', c.bg);
  map.setPaintProperty('bm-buildings', 'fill-color', c.muted);
  map.setPaintProperty('bm-roads', 'line-color', c.border);
  map.setPaintProperty('bm-boundaries', 'line-color', c.muted);
}
