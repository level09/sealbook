/* Where a source says it was photographed or filmed (GET
   /api/map/evidence/<sha>/locations): each claim as a point, its uncertainty as a
   circle and its bearing as a short line, on the offline basemap when there is one.
   An accepted claim is a solid dot, any other an outlined one; agent proposals
   take the agent hue. Renders nothing when the source carries no location.
   Mount: `app.component('evidence-location', EvidenceLocation)` and
   `<evidence-location :sha="it.sha256"></evidence-location>`. */
import * as maplibregl from '/static/maplibre/maplibre-gl.mjs';
import {addBasemap} from '/static/js/basemap.js';

const METHOD = {exif: 'camera metadata', c2pa: 'content credentials', analyst: 'analyst geolocation'};
const PROP = {photographed_at: 'Photographed at', filmed_at: 'Filmed at'};
// The story map's palette: this page has no map colour tokens of its own.
const MAP = {light: {bg: '#eef0f1', land: '#f9f9f7', border: '#c9c9c3', muted: '#8a8d93'}, dark: {bg: '#101215', land: '#181a1e', border: '#383c43', muted: '#7d8189'}};

// A geodesic-enough circle: 64 points at `m` metres around [lon, lat].
export function circle([lon, lat], m) {
  const k = m / 6371008.8, out = [];
  for (let i = 0; i <= 64; i++) {
    const a = (i / 64) * 2 * Math.PI;
    out.push([lon + (k * Math.sin(a) * 180) / Math.PI / Math.cos((lat * Math.PI) / 180), lat + (k * Math.cos(a) * 180) / Math.PI]);
  }
  return out;
}

function ahead([lon, lat], bearing, m) {
  const k = m / 6371008.8, b = (bearing * Math.PI) / 180;
  return [lon + (k * Math.sin(b) * 180) / Math.PI / Math.cos((lat * Math.PI) / 180), lat + (k * Math.cos(b) * 180) / Math.PI];
}

export const EvidenceLocation = {
  name: 'EvidenceLocation',
  props: {sha: {type: String, required: true}},
  delimiters: ['[[', ']]'],
  data: () => ({items: [], error: ''}),
  template: `<section v-if="items.length || error" class="px-3 pb-3" aria-label="Where this was made">
    <h3 class="pb-1">Location</h3>
    <p v-if="error" class="muted">[[ error ]]</p>
    <div ref="map" style="height: 220px; border-radius: 6px; overflow: hidden"></div>
    <ul class="muted" style="list-style: none; padding: 6px 0 0; margin: 0; font-size: 12.5px">
      <li v-for="l in items" :key="l.id">[[ label(l) ]]</li>
    </ul></section>`,
  async mounted() {
    try {
      this.items = (await axios.get(`/api/map/evidence/${this.sha}/locations`)).data.items;
    } catch (err) {
      this.error = err.response?.data?.description || 'Location could not be read';
      return;
    }
    if (this.items.length) this.$nextTick(() => this.draw());
  },
  beforeUnmount() { this.map?.remove(); },
  methods: {
    label(l) {
      const where = `${l.lat.toFixed(5)}, ${l.lon.toFixed(5)}`;
      const facing = l.bearing == null ? '' : `, facing ${l.bearing}°`;
      return `${PROP[l.prop] || l.prop} ${where} within ${l.precision_m} m${facing}: ${METHOD[l.method] || l.method}, ${l.tier}${l.agent ? ', read by a tool' : ''}`;
    },
    color(name) { return getComputedStyle(this.$el).getPropertyValue(name).trim() || '#555'; },
    async draw() {
      const ink = this.color('--ink'), agent = this.color('--agent');
      const c = MAP[this.$vuetify?.theme.global.current.dark ? 'dark' : 'light'];
      const first = [this.items[0].lon, this.items[0].lat];
      const map = new maplibregl.Map({container: this.$refs.map, center: first, zoom: 13, dragRotate: false, pitchWithRotate: false,
        style: {version: 8, sources: {}, layers: [{id: 'bg', type: 'background', paint: {'background-color': c.bg}}]},
        attributionControl: {compact: true}});
      this.map = map;
      map.on('load', async () => {
        if (!(await addBasemap(map, c))) {
          map.addSource('region', {type: 'geojson', data: '/static/maplibre/region.geojson'});
          map.addLayer({id: 'land', type: 'fill', source: 'region', paint: {'fill-color': c.land}});
        }
        const features = this.items.flatMap(l => {
          const at = [l.lon, l.lat], who = l.agent ? agent : ink;
          const out = [{type: 'Feature', properties: {kind: 'circle', who}, geometry: {type: 'Polygon', coordinates: [circle(at, l.precision_m)]}},
            {type: 'Feature', properties: {kind: 'point', who, solid: l.tier === 'accepted'}, geometry: {type: 'Point', coordinates: at}}];
          if (l.bearing != null) out.push({type: 'Feature', properties: {kind: 'bearing', who}, geometry: {type: 'LineString', coordinates: [at, ahead(at, l.bearing, Math.max(150, l.precision_m * 1.5))]}});
          return out;
        });
        map.addSource('loc', {type: 'geojson', data: {type: 'FeatureCollection', features}});
        map.addLayer({id: 'loc-circle', type: 'fill', source: 'loc', filter: ['==', ['get', 'kind'], 'circle'], paint: {'fill-color': ['get', 'who'], 'fill-opacity': 0.08, 'fill-outline-color': ['get', 'who']}});
        map.addLayer({id: 'loc-bearing', type: 'line', source: 'loc', filter: ['==', ['get', 'kind'], 'bearing'], paint: {'line-color': ['get', 'who'], 'line-width': 2}});
        map.addLayer({id: 'loc-point', type: 'circle', source: 'loc', filter: ['==', ['get', 'kind'], 'point'],
          paint: {'circle-radius': 6, 'circle-color': ['case', ['get', 'solid'], ['get', 'who'], this.color('--panel') || '#fff'], 'circle-stroke-color': ['get', 'who'], 'circle-stroke-width': 2}});
        const bounds = new maplibregl.LngLatBounds();
        for (const f of features) if (f.properties.kind === 'circle') f.geometry.coordinates[0].forEach(p => bounds.extend(p));
        map.fitBounds(bounds, {padding: 30, maxZoom: 16, duration: 0});
      });
    },
  },
};
