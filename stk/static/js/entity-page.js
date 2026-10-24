/* One layout for every entity page (places, incidents): a header with names, type
   and key facts; the compact story; relations with their reason and tier or
   strength; a server-paged evidence table; thread and history in a right drawer.
   The page passes its API and anything of its own; the template is
   templates/entity/base.html. */
import * as maplibregl from '/static/maplibre/maplibre-gl.mjs';
import {prepare, sign} from '/static/js/monitor/passkey.js';
import {registerMediaViewers} from '/static/js/components/media.js';
import {circle} from '/static/js/components/EvidenceLocation.js';
import {addBasemap} from '/static/js/basemap.js';
import {addClaimTiles} from '/static/js/maptiles.js';
import {MAP, PROP, StoryTimeline, fmtDay, fmtSpan, timelineRows} from '/static/js/stories.js';

const DAY = 864e5;
const day = s => Date.parse(s + 'T00:00:00Z');
const iso = t => new Date(t).toISOString().slice(0, 10);
const ms = v => { const s = Math.max(0, Math.round((v || 0) / 1000)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; };
export const plural = (n, one, many) => `${(n ?? 0).toLocaleString()} ${n === 1 ? one : many || one + 's'}`;
export const human = s => (s || '').replaceAll('_', ' ');
export const spanLabel = s => !s ? 'whole item' : s.bbox ? `region on page ${s.page || 1}` : s.ms ? `${ms(s.ms[0])} to ${ms(s.ms[1])}` : s.row ? `row ${s.row}, column ${s.column}` : s.page ? `page ${s.page}` : s.start != null ? `characters ${s.start} to ${s.end}` : 'whole item';
export const metres = m => m == null ? null : m < 1000 ? `${m} m` : `${(m / 1000).toFixed(1).replace(/\.0$/, '')} km`;
export const placeName = p => p ? (p.name_en || p.en || p.name_ar || p.ar || 'Unnamed place') : 'Unnamed place';
const KINDS = {shared_presence: 'Shared presence', candidate_incident: 'Candidate incident'};

/* Sign what the server staged, after the reader has seen its rows. Shared by the
   entity pages and the places index. */
export const signing = {
  data: () => ({staged: null, signBusy: false, snack: {show: false, text: ''}}),
  methods: {
    say(text) { this.snack = {show: true, text}; },
    fail(err) { return err.response?.data?.error || err.response?.data?.description || err.message; },
    async stage(item, start, title, after) {
      this.signBusy = true;
      try { this.staged = {data: await prepare(item, start), title, after}; }
      catch (err) { this.say(err.message); }
      finally { this.signBusy = false; }
    },
    async confirmSign() {
      const {data, after} = this.staged;
      this.signBusy = true;
      try { await after(await sign(data)); } catch (err) { this.say(err.message); }
      finally { this.signBusy = false; this.staged = null; }
    },
  },
};

/* A small map of one entity: areas filled, uncertainty as a circle, points as dots,
   over the offline basemap and the claim tiles' boundary and uncertainty layers. */
export const EntityMap = {
  name: 'EntityMap',
  props: {
    shapes: {type: Array, default: () => []},  // [{geometry, precision_m}] in WGS 84
    query: {type: Object, default: () => ({})},
    dark: Boolean,
    label: String,
  },
  template: '<div class="en-map" role="img" :aria-label="label"></div>',
  mounted() { this.draw(); },
  beforeUnmount() { this.map?.remove(); },
  watch: {dark() { this.redraw(); }, shapes(a, b) { if (JSON.stringify(a) !== JSON.stringify(b)) this.redraw(); }},
  methods: {
    redraw() { this.map?.remove(); this.draw(); },
    color(name) { return getComputedStyle(this.$el).getPropertyValue(name).trim(); },
    features() {
      return this.shapes.flatMap(s => {
        const g = s.geometry, out = [{type: 'Feature', properties: {kind: g.type === 'Point' ? 'point' : 'area'}, geometry: g}];
        if (g.type === 'Point' && s.precision_m) out.push({type: 'Feature', properties: {kind: 'area'}, geometry: {type: 'Polygon', coordinates: [circle(g.coordinates, s.precision_m)]}});
        return out;
      });
    },
    draw() {
      const c = MAP[this.dark ? 'dark' : 'light'], ink = this.color('--place') || '#397247';
      const features = this.features();
      const map = new maplibregl.Map({container: this.$el, center: [37.2, 34.8], zoom: 5.6, dragRotate: false, pitchWithRotate: false, cooperativeGestures: true,
        style: {version: 8, sources: {}, layers: [{id: 'bg', type: 'background', paint: {'background-color': c.bg}}]}, attributionControl: {compact: true}});
      this.map = map;
      map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'bottom-right');
      map.on('load', async () => {
        map.addSource('region', {type: 'geojson', data: '/static/maplibre/region.geojson'});
        map.addLayer({id: 'land', type: 'fill', source: 'region', paint: {'fill-color': c.land}});
        map.addLayer({id: 'borders', type: 'line', source: 'region', paint: {'line-color': c.border, 'line-width': .8}});
        addClaimTiles(map, this.query, {border: c.border, ink: c.seq, muted: c.muted});
        await addBasemap(map, {bg: c.bg, land: c.land, border: c.border, muted: c.muted}, 'borders', ['land']);
        if (this.map !== map) return;  // redrawn while the basemap loaded
        map.addSource('me', {type: 'geojson', data: {type: 'FeatureCollection', features}});
        map.addLayer({id: 'me-area', type: 'fill', source: 'me', filter: ['==', ['get', 'kind'], 'area'], paint: {'fill-color': ink, 'fill-opacity': .12, 'fill-outline-color': ink}});
        map.addLayer({id: 'me-edge', type: 'line', source: 'me', filter: ['==', ['get', 'kind'], 'area'], paint: {'line-color': ink, 'line-width': 1.4, 'line-dasharray': [2, 1.5]}});
        map.addLayer({id: 'me-point', type: 'circle', source: 'me', filter: ['==', ['get', 'kind'], 'point'], paint: {'circle-radius': 6, 'circle-color': ink, 'circle-stroke-color': c.land, 'circle-stroke-width': 2}});
        const b = new maplibregl.LngLatBounds();
        const walk = x => typeof x[0] === 'number' ? b.extend(x) : x.forEach(walk);
        features.forEach(f => walk(f.geometry.coordinates));
        if (!b.isEmpty()) map.fitBounds(b, {padding: 36, maxZoom: features.some(f => f.properties.kind === 'area') ? 15 : 12, duration: 0});
      });
    },
  },
};

/* The page app. `page`: {api: boot => url, evidence: boot => url, params: vm => {},
   storyHref: e => url, mixin: {...}} */
export function entityPage(boot, page) {
  const app = Vue.createApp({
    mixins: [layoutMixin, signing, page.mixin || {}],
    delimiters: config.delimiters,
    data() {
      return {
        boot, PROP, e: null, error: '', drawer: false, threadId: null, thread: null, threadBusy: false, comment: '',
        ev: {items: [], total: 0, loading: false, opts: {page: 1, itemsPerPage: 25, sortBy: [{key: 'claims', order: 'desc'}]}},
        evHeaders: [
          {title: '', key: 'thumb', sortable: false, width: 76},
          {title: 'Source', key: 'title'},
          {title: 'Where in it', key: 'where', sortable: false},
          {title: 'Claims', key: 'claims', align: 'end'},
          {title: 'Tier', key: 'tier'},
        ],
        shellHints: [['/', 'search'], ['meta+k', 'go to'], ['t', 'thread and history']],
      };
    },
    computed: {
      story() { return this.e?.story || null; },
      rows() { return this.story ? timelineRows(this.story.rows, this.story.points) : []; },
      range() {
        const pts = this.story?.points || [], bands = this.bands;
        if (!pts.length && !bands.length) return null;
        const lo = Math.min(...pts.map(p => day(p.from)), ...bands.map(b => day(b.from)));
        const hi = Math.max(...pts.map(p => day(p.to)), ...bands.map(b => day(b.to)));
        const pad = Math.max(10 * DAY, (hi - lo) / 20);
        return [iso(lo - pad), iso(hi + pad)];
      },
      bands() {
        return (this.story?.incidents || []).filter(i => i.window).map(i => ({id: i.id, from: i.window[0], to: i.window[1], rows: i.people, label: i.title}));
      },
      pending() {
        return (this.story?.suggestions || []).map(f => ({id: f.id, from: f.window[0], to: f.window[1], rows: f.people, label: `${f.label.toLowerCase()}, pending`}));
      },
      storyHref() { return page.storyHref(this.e); },
      evShown() { return this.$vuetify.display.xs ? this.evHeaders.filter(h => ['title', 'claims', 'tier'].includes(h.key)) : this.evHeaders; },
      storyLine() {
        const pts = this.story?.points || [];
        if (!pts.length) return '';
        const lo = pts.reduce((a, p) => p.from < a ? p.from : a, pts[0].from), hi = pts.reduce((a, p) => p.to > a ? p.to : a, pts[0].to);
        return `${plural(this.rows.length, 'person', 'people')}, ${plural(pts.length, 'dated point')}, ${fmtSpan(lo, hi)}`;
      },
      evQuery() { return page.params ? page.params(this) : {}; },
      gestures() {
        // History grouped by the one passkey gesture that signed it; loose rows stand alone.
        const out = [], by = {};
        for (const h of this.e?.history || []) {
          const k = h.gesture || h.id;
          if (!by[k]) out.push(by[k] = {key: k, gesture: h.gesture, rows: [], at: h.recorded_at, actor: h.actor});
          by[k].rows.push(h);
        }
        return out;
      },
    },
    watch: {drawer(open) { if (open && !this.threadId && this.e?.threads[0]) this.openThread(this.e.threads[0]); }},
    methods: {
      plural, human, fmtDay, fmtSpan, spanLabel, placeName, metres,
      kindLabel: k => KINDS[k] || human(k),
      tierText: t => t || 'no dated claim',
      async load() {
        try {
          this.e = (await axios.get(page.api(boot), {params: this.evQuery})).data;
          this.error = '';
          document.title = `${page.title(this.e)} · ${document.title.split(' · ').pop()}`;
        } catch (err) {
          this.e = null;
          this.error = err.response?.status === 404 ? page.missing : this.fail(err);
          return;
        }
        this.loadEvidence(this.ev.opts);
      },
      async loadEvidence(opts) {
        this.ev.opts = opts;
        const sort = opts.sortBy?.[0];
        this.ev.loading = true;
        try {
          const {data} = await axios.get(page.evidence(boot), {params: {...this.evQuery, page: opts.page, per_page: opts.itemsPerPage, sort: sort?.key || 'claims', desc: sort?.order === 'asc' ? 0 : 1}});
          this.ev.items = data.items; this.ev.total = data.total;
        } catch (err) { this.say(this.fail(err)); }
        finally { this.ev.loading = false; }
      },
      openRow(_, {item}) { location.href = item.href; },
      pickPoint(p) { if (p.href) location.href = p.href; },
      openFinding(id) { location.href = `/discover?f=${id}`; },
      async openThread(t) {
        this.threadId = t?.id || null; this.thread = null;
        if (!t?.visible) return;
        this.threadBusy = true;
        try { this.thread = (await axios.get(`/api/collaboration/requests/${t.id}`)).data; }
        catch (err) { this.say(this.fail(err)); }
        finally { this.threadBusy = false; }
      },
      async sendComment() {
        this.threadBusy = true;
        try {
          await axios.post(`/api/collaboration/requests/${this.thread.id}/comments`, {item: {text: this.comment}});
          this.comment = '';
          await this.openThread({id: this.thread.id, visible: true});
        } catch (err) { this.say(this.fail(err)); }
        finally { this.threadBusy = false; }
      },
      personLabel(id) { return (this.e.relations.people || []).find(p => p.person === id)?.label || 'a person'; },
      placeLabel(ref) { const id = (ref || '').replace('place:', ''); return placeName((this.e.relations.places || []).find(p => p.id === id) || (this.e.id === id ? this.e : null)); },
      historyText(h) {
        const v = h.display || '';
        return {
          review: () => `${v === 'reject' ? 'Rejected' : 'Accepted'} ${page.reviewed}`,
          retraction: () => 'Undid a signed statement',
          located_at: () => `Confirmed the place for the spelling \u201c${v}\u201d`,
          incident: () => `Named the incident \u201c${v}\u201d`,
          occurred_at: () => `Placed it at ${this.placeLabel(h.place)}${h.when ? ', ' + h.when : ''}`,
          involved: () => `${this.personLabel(h.person)}, ${v.toLowerCase() || 'involved'}`,
          supported_by: () => 'Linked a supporting claim',
        }[h.prop]?.() || human(h.prop);
      },
      threadLabel(t) { return `${t.visible ? (t.sender || 'Someone') + ': ' + (t.reason || '') : 'A private request'}${t.comments ? `, ${plural(t.comments, 'reply', 'replies')}` : ''}`; },
      undo(g) {
        this.stage({gesture: g.gesture}, '/api/review/undo', `Undo ${plural(g.rows.length, 'signed statement')}`, async () => {
          this.say(g.rows.some(h => h.prop === 'review') ? 'Undone and signed. The finding is back in Discover.' : 'Undone and signed.');
          await this.load();
        });
      },
      onKey(e) {
        if (TYPING(e.target) || e.metaKey || e.ctrlKey || e.altKey || e.target.closest('.v-overlay')) return;
        if (e.key === 't') { e.preventDefault(); this.drawer = !this.drawer; }
      },
      useCase() { if (page.followsCase) this.load(); },
    },
    mounted() { window.addEventListener('keydown', this.onKey); this.load(); },
    beforeUnmount() { window.removeEventListener('keydown', this.onKey); },
  });
  app.component('story-timeline', StoryTimeline).component('entity-map', EntityMap);
  registerMediaViewers(app);
  registerStkComponents(app);
  return app.use(Vuetify.createVuetify(config.vuetifyConfig));
}
export {fmtDay, fmtSpan};
