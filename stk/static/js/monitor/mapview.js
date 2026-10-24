/**
 * Monitor map. No tiles from the internet: a background, Natural Earth outlines
 * vendored in region.geojson, and a 1 degree graticule. Given a tile query, points
 * and sequence lines come from the claim tiles, clustered by the server at low
 * zoom; without one (the person page), from the GeoJSON layer, clustered here.
 * MapLibre draws no text without a glyph server, so cluster counts and place names
 * are small HTML markers.
 */
import * as maplibregl from '/static/maplibre/maplibre-gl.mjs';
import {SOURCE, addClaimTiles, recolor, setClaimQuery} from '/static/js/maptiles.js';
import {addBasemap, recolorBasemap} from '/static/js/basemap.js';

const TIERS = ['accepted', 'corroborated', 'unconfirmed', 'rejected'];

function css(el, name) {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

// Tier is texture (solid, ring, hatch, cross) in its role hue; unconfirmed takes the
// author's hue, and an agent-authored point keeps a violet halo once confirmed.
function icon(tier, color, paper, halo) {
  const ratio = 2, size = 18 * ratio, c = size / 2, r = 6 * ratio;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (halo) {
    ctx.strokeStyle = halo;
    ctx.lineWidth = 1.5 * ratio;
    ctx.beginPath();
    ctx.arc(c, c, r + 2.2 * ratio, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.lineWidth = 1.5 * ratio;
  ctx.strokeStyle = ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(c, c, r, 0, Math.PI * 2);
  if (tier === 'accepted') {
    ctx.fill();
  } else if (tier === 'corroborated') {
    ctx.fillStyle = paper;
    ctx.fill();
    ctx.lineWidth = 2.5 * ratio;
    ctx.stroke();
  } else if (tier === 'unconfirmed') {
    ctx.fillStyle = paper;
    ctx.fill();
    ctx.save();
    ctx.clip();
    ctx.lineWidth = 1.2 * ratio;
    for (let x = -size; x < size * 2; x += 3.5 * ratio) {
      ctx.beginPath();
      ctx.moveTo(x, size);
      ctx.lineTo(x + size, 0);
      ctx.stroke();
    }
    ctx.restore();
    ctx.lineWidth = 1 * ratio;
    ctx.beginPath();
    ctx.arc(c, c, r, 0, Math.PI * 2);
    ctx.stroke();
  } else {
    ctx.moveTo(c - r, c - r);
    ctx.lineTo(c + r, c + r);
    ctx.moveTo(c + r, c - r);
    ctx.lineTo(c - r, c + r);
    ctx.stroke();
  }
  return {image: ctx.getImageData(0, 0, size, size), ratio};
}

function palette(el) {
  return {
    bg: css(el, '--map-water'),
    land: css(el, '--map-land'),
    landFocus: css(el, '--map-land-focus'),
    border: css(el, '--line-strong'),
    grid: css(el, '--line'),
    ink: css(el, '--ink'),
    muted: css(el, '--ink-3'),
    agent: css(el, '--agent'),
    accepted: css(el, '--accepted'),
    corroborated: css(el, '--corroborated'),
    negative: css(el, '--negative'),
    fork: css(el, '--fork'),
    paper: css(el, '--panel'),
  };
}

function setIcons(map, p) {
  for (const tier of TIERS) {
    for (const [who, author] of [['human', p.ink], ['agent', p.agent]]) {
      const color = {accepted: p.accepted, corroborated: p.corroborated, unconfirmed: author}[tier] || p.negative;
      const {image, ratio} = icon(tier, color, p.paper, who === 'agent' && tier !== 'unconfirmed' ? p.agent : null);
      const id = `pt-${tier}-${who}`;
      if (map.hasImage(id)) map.updateImage(id, image);
      else map.addImage(id, image, {pixelRatio: ratio});
    }
  }
}

function graticule() {
  const features = [];
  for (let lon = 30; lon <= 46; lon++) {
    features.push({type: 'Feature', geometry: {type: 'LineString', coordinates: [[lon, 28], [lon, 40]]}});
  }
  for (let lat = 28; lat <= 40; lat++) {
    features.push({type: 'Feature', geometry: {type: 'LineString', coordinates: [[30, lat], [46, lat]]}});
  }
  return {type: 'FeatureCollection', features};
}

function applyPalette(map, p) {
  map.setPaintProperty('bg', 'background-color', p.bg);
  map.setPaintProperty('graticule', 'line-color', p.grid);
  map.setPaintProperty('land', 'fill-color', ['case', ['==', ['get', 'iso'], 'SYR'], p.landFocus, p.land]);
  map.setPaintProperty('borders', 'line-color', p.border);
  map.setPaintProperty('track-seq', 'line-color', p.muted);
  map.setPaintProperty('track-fork', 'line-color', p.fork);
  if (map.getLayer('tile-seq')) map.setPaintProperty('tile-seq', 'line-color', p.muted);
  recolor(map, p);
  recolorBasemap(map, basemapColors(p));
  setIcons(map, p);
}

const basemapColors = p => ({bg: p.bg, land: p.landFocus, border: p.border, muted: p.muted});

function trackLines(tracks, places, selected, forksOnly) {
  const features = [];
  for (const [subject, t] of Object.entries(tracks)) {
    const on = subject === selected;
    t.steps.forEach((step, i) => {
      const next = t.steps[i + 1];
      // With tiles, sequences come from the server from zoom 8; the selected
      // subject's own line stays drawn here at every zoom.
      if (next && (!forksOnly || on)) {
        for (const a of step.places) for (const b of next.places) {
          if (a !== b) features.push({type: 'Feature', properties: {kind: 'seq', on}, geometry: {type: 'LineString', coordinates: [places[a], places[b]]}});
        }
      }
      if (step.fork) {
        step.places.forEach((a, j) => step.places.slice(j + 1).forEach(b => {
          features.push({type: 'Feature', properties: {kind: 'fork', on}, geometry: {type: 'LineString', coordinates: [places[a], places[b]]}});
        }));
      }
    });
  }
  return {type: 'FeatureCollection', features};
}

export function createMap(el, theme, {onPoint, onCluster}) {
  const p = palette(theme);
  const map = new maplibregl.Map({
    container: el,
    style: {version: 8, sources: {}, layers: [{id: 'bg', type: 'background', paint: {'background-color': p.bg}}]},
    center: [37.2, 34.6],
    zoom: 5.4,
    attributionControl: {compact: true, customAttribution: 'Natural Earth'},
    dragRotate: false,
    pitchWithRotate: false,
  });
  map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'bottom-right');
  map.touchZoomRotate.disableRotation();
  const state = {map, theme, clusters: new Map(), labels: [], fitted: false, ready: null, layer: null, tiled: false};

  state.ready = new Promise(resolve => map.on('load', async () => {
    const region = await (await fetch('/static/maplibre/region.geojson')).json();
    map.addSource('region', {type: 'geojson', data: region});
    map.addSource('graticule', {type: 'geojson', data: graticule()});
    map.addSource('points', {type: 'geojson', data: {type: 'FeatureCollection', features: []}, cluster: true, clusterRadius: 34, clusterMaxZoom: 11});
    map.addSource('tracks', {type: 'geojson', data: {type: 'FeatureCollection', features: []}});
    map.addLayer({id: 'graticule', type: 'line', source: 'graticule', paint: {'line-width': 0.5}});
    map.addLayer({id: 'land', type: 'fill', source: 'region'});
    map.addLayer({id: 'borders', type: 'line', source: 'region', paint: {'line-width': 0.8}});
    map.addLayer({id: 'track-seq', type: 'line', source: 'tracks', filter: ['==', ['get', 'kind'], 'seq'],
      paint: {'line-width': 1.5, 'line-dasharray': [2, 2], 'line-opacity': ['case', ['get', 'on'], 0.9, 0.3]}});
    map.addLayer({id: 'track-fork', type: 'line', source: 'tracks', filter: ['==', ['get', 'kind'], 'fork'],
      paint: {'line-width': 2, 'line-opacity': ['case', ['get', 'on'], 1, 0.35]}});
    map.addLayer({id: 'points', type: 'symbol', source: 'points', filter: ['!', ['has', 'point_count']],
      layout: {'icon-image': ['concat', 'pt-', ['get', 'tier'], '-', ['case', ['get', 'agent'], 'agent', 'human']],
               'icon-allow-overlap': true, 'icon-ignore-placement': true}});
    await addBasemap(map, basemapColors(p), 'borders', ['land', 'graticule']);
    applyPalette(map, p);
    for (const id of ['points', 'tile-points']) {
      map.on('click', id, e => onPoint(e.features[0].properties.id));
      map.on('mouseenter', id, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; });
    }
    map.on('render', () => syncClusters(state, onCluster));
    map.on('zoomend', () => el.classList.toggle('labels-on', map.getZoom() >= 7));
    resolve();
  }));
  return state;
}

// Tile clusters list their first ids; GeoJSON clusters ask the source for leaves.
function clusterFeatures(state) {
  const {map} = state;
  if (state.tiled) {
    return map.querySourceFeatures(SOURCE, {sourceLayer: 'points', filter: ['==', ['get', 'cluster'], true]})
      .map(f => ({id: f.properties.ids, count: f.properties.count, at: f.geometry.coordinates, ids: async () => f.properties.ids.split(',')}));
  }
  return map.querySourceFeatures('points').filter(f => f.properties.cluster).map(f => ({
    id: f.properties.cluster_id, count: f.properties.point_count, at: f.geometry.coordinates,
    ids: async () => (await map.getSource('points').getClusterLeaves(f.properties.cluster_id, Infinity, 0)).map(l => l.properties.id),
  }));
}

function syncClusters(state, onCluster) {
  const {map} = state;
  if (!map.getSource('points')) return;
  const seen = new Set();
  for (const f of clusterFeatures(state)) {
    const id = f.id;
    if (seen.has(id)) continue;
    seen.add(id);
    if (!state.clusters.has(id)) {
      const el = document.createElement('button');
      el.className = 'map-cluster';
      el.textContent = f.count;
      el.title = `${f.count} statements here: open them`;
      el.addEventListener('click', async e => {
        e.stopPropagation();
        onCluster(await f.ids());
      });
      state.clusters.set(id, new maplibregl.Marker({element: el}).setLngLat(f.at).addTo(map));
    }
  }
  for (const [id, marker] of state.clusters) {
    if (!seen.has(id)) {
      marker.remove();
      state.clusters.delete(id);
    }
  }
}

function tileLayers(state, query) {
  const {map} = state, p = palette(state.theme);
  addClaimTiles(map, query, p, 'track-fork');
  map.addLayer({id: 'tile-seq', type: 'line', source: SOURCE, 'source-layer': 'sequences',
    paint: {'line-width': 1.5, 'line-dasharray': [2, 2], 'line-color': p.muted}}, 'track-fork');
  map.addLayer({id: 'tile-points', type: 'symbol', source: SOURCE, 'source-layer': 'points',
    filter: ['!=', ['get', 'cluster'], true],
    layout: {'icon-image': ['concat', 'pt-', ['get', 'tier'], '-', ['case', ['get', 'agent'], 'agent', 'human']],
             'icon-allow-overlap': true, 'icon-ignore-placement': true}});
  state.tiled = true;
}

/* `query` ({investigation, from, to}) switches points and sequences to the claim tiles. */
export async function updateMap(state, layer, {showRejected, subject, query}) {
  await state.ready;
  const {map} = state;
  const points = layer.points.filter(pt => showRejected || pt.tier !== 'rejected');
  if (query) {
    const tiled = {...query, tier: ['accepted', 'corroborated', 'unconfirmed', ...(showRejected ? ['rejected'] : [])].join(',')};
    if (!state.tiled) tileLayers(state, tiled);
    else setClaimQuery(map, tiled);
    map.setPaintProperty('tile-seq', 'line-opacity', ['case', ['==', ['get', 'subject'], subject || ''], 0.9, 0.3]);
  }
  map.getSource('points').setData({
    type: 'FeatureCollection',
    features: state.tiled ? [] : points.map(pt => ({
      type: 'Feature',
      properties: {id: pt.id, tier: pt.tier, agent: pt.agent},
      geometry: {type: 'Point', coordinates: pt.lonlat},
    })),
  });
  map.getSource('tracks').setData(trackLines(layer.tracks, layer.places, subject, state.tiled));
  for (const m of state.clusters.values()) m.remove();
  state.clusters.clear();
  for (const m of state.labels) m.remove();
  state.labels = [...new Set(points.map(pt => pt.place))].map(place => {
    const el = document.createElement('span');
    el.className = 'map-label';
    el.dir = 'auto';
    el.textContent = place;
    return new maplibregl.Marker({element: el, anchor: 'left', offset: [10, 0]}).setLngLat(layer.places[place]).addTo(map);
  });
  if (!state.fitted && points.length) {
    const bounds = new maplibregl.LngLatBounds();
    points.forEach(pt => bounds.extend(pt.lonlat));
    map.fitBounds(bounds, {padding: 60, maxZoom: 8, duration: 0});
    state.fitted = true;
  }
}

export async function retheme(state) {
  await state.ready;
  applyPalette(state.map, palette(state.theme));
}
