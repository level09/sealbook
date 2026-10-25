/**
 * Claim tiles shared by the monitor and story maps: one vector source built per
 * request from what this reader may see (/api/map/tiles.json carries the query and
 * the attribution), and the reference layers drawn under each map's own points:
 * admin boundaries, uncertainty circles and facility footprints.
 */
export const SOURCE = 'claims';

export function tilesUrl(query) {
  const q = Object.fromEntries(Object.entries(query || {}).filter(([, v]) => v));
  return '/api/map/tiles.json?' + new URLSearchParams(q);
}

export function addClaimTiles(map, query, colors, before) {
  map.addSource(SOURCE, {type: 'vector', url: tilesUrl(query)});
  const layer = spec => map.addLayer({source: SOURCE, ...spec}, before);
  layer({id: 'admin', type: 'line', 'source-layer': 'admin',
    paint: {'line-color': colors.border, 'line-width': ['match', ['get', 'level'], 1, 1.1, 0.6], 'line-dasharray': [3, 2]}});
  layer({id: 'footprints', type: 'fill', 'source-layer': 'footprints',
    paint: {'fill-color': colors.ink, 'fill-opacity': 0.14, 'fill-outline-color': colors.ink}});
  layer({id: 'uncertainty', type: 'fill', 'source-layer': 'uncertainty',
    paint: {'fill-color': colors.ink, 'fill-opacity': 0.05, 'fill-outline-color': colors.muted}});
}

export function setClaimQuery(map, query) {
  const source = map.getSource(SOURCE);
  const url = tilesUrl(query);
  if (source && source.url !== url) source.setUrl(url);
}

export function recolor(map, colors) {
  if (!map.getLayer('admin')) return;
  map.setPaintProperty('admin', 'line-color', colors.border);
  map.setPaintProperty('footprints', 'fill-color', colors.ink);
  map.setPaintProperty('footprints', 'fill-outline-color', colors.ink);
  map.setPaintProperty('uncertainty', 'fill-color', colors.ink);
  map.setPaintProperty('uncertainty', 'fill-outline-color', colors.muted);
}
