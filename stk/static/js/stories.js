/* Story view (mockups/story.html): a timeline of rows left, a map of resolved points
   right, one time cursor driving both, and the evidence behind each point below.
   Tier is texture (solid accepted, outlined corroborated, hatched and dashed
   unconfirmed); who authored a point is hue (ink for people, violet for agents).
   Lines on the map join known places in time order. They are sequences, never routes. */
import * as maplibregl from '/static/maplibre/maplibre-gl.mjs';
import {addClaimTiles, recolor, setClaimQuery} from '/static/js/maptiles.js';
import {addBasemap, recolorBasemap} from '/static/js/basemap.js';

const DAY = 864e5;
const day = s => Date.parse(s + 'T00:00:00Z');
const iso = t => new Date(t).toISOString().slice(0, 10);
export const fmtDay = s => s ? new Date(day(s)).toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'}) : '';
export const fmtSpan = (a, b) => !b || a === b ? fmtDay(a) : `${fmtDay(a)} to ${fmtDay(b)}`;
export const PROP = {arrested_at: 'arrested at', detained_at: 'held at', detention_branch: 'held by branch', transferred_to: 'transferred to', seen_at: 'seen at', last_seen: 'last seen at'};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

/* Rows are people (or places); a gap label reads exactly "N days, no evidence". */
export const StoryTimeline = {
  name: 'StoryTimeline',
  props: {
    rows: Array,          // [{key, label, points: [{id, from, to, tier, agent, fork, title}], gaps: [{from, to, days}]}]
    from: String, to: String, cursor: String, sel: String,
    pending: {type: Array, default: () => []},    // [{id, from, to, rows, label}] hatched
    incidents: {type: Array, default: () => []},  // [{id, from, to, rows, label}] outlined
    compact: Boolean,
  },
  emits: ['pick', 'review'],
  delimiters: ['[[', ']]'],
  data: () => ({W: 640, view: null}),  // view: [t0, t1] chosen by zoom; null fits the dense part
  mounted() { this._ro = new ResizeObserver(() => { this.W = Math.max(300, this.$el.clientWidth); }); this._ro.observe(this.$el); },
  beforeUnmount() { this._ro.disconnect(); },
  template: `<div class="st-tl">
    <div v-if="!compact" class="st-zoom" role="group" aria-label="Time range">
      <button type="button" :disabled="span <= 31 * DAY" aria-label="Zoom in" title="Zoom in" @click="zoom(0.5)"><i class="ti ti-zoom-in" aria-hidden="true"></i></button>
      <button type="button" :disabled="whole" aria-label="Zoom out" title="Zoom out" @click="zoom(2)"><i class="ti ti-zoom-out" aria-hidden="true"></i></button>
      <button type="button" v-if="outside" @click="view = [full[0], full[1]]">Whole range, [[ outside ]] point[[ outside === 1 ? '' : 's' ]] outside</button>
    </div>
    <svg :viewBox="'0 0 ' + W + ' ' + H" :height="H" :width="W" style="display: block; overflow: visible" role="img" :aria-label="'Timeline of ' + rows.length + ' rows'">
      <defs><clipPath id="st-clip"><rect :x="L - 14" y="0" :width="W - L + 14" :height="H"/></clipPath><pattern id="st-hatch-a" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.6" height="4" fill="var(--agent)"/></pattern>
        <pattern id="st-hatch-i" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.6" height="4" fill="var(--ink)"/></pattern></defs>
      <g clip-path="url(#st-clip)">
      <g v-for="t in ticks" :key="t.x"><line :x1="t.x" :x2="t.x" y1="18" :y2="H" stroke="var(--line)"/><text :x="t.x + 3" y="12" font-size="10.5" fill="var(--ink-3)">[[ t.label ]]</text></g>
      <g v-for="b in laid.incidents" :key="'i' + b.id"><rect :x="b.x" :y="b.y" :width="b.w" :height="b.h" fill="var(--incident-wash)" stroke="var(--incident-line)" rx="3"/>
        <text :x="b.x + 4" :y="b.y + b.h - 4" font-size="10.5" fill="var(--incident)">[[ b.label ]]</text></g>
      <g v-for="b in laid.pending" :key="'f' + b.id" class="st-pend" style="cursor: pointer" tabindex="0" role="button" :aria-label="b.title + '. Open the checklist'" @click="$emit('review', b.id)" @keydown.enter="$emit('review', b.id)">
        <title>[[ b.title ]]. Open the checklist</title>
        <rect :x="b.x" :y="b.y" :width="b.w" :height="b.h" fill="url(#st-hatch-a)" opacity=".35" stroke="var(--agent)" stroke-dasharray="3 2" rx="3"/>
        <text v-if="b.text" :x="b.x + 4" :y="b.y + b.h - 4" font-size="10.5" fill="var(--agent)">[[ b.label ]]</text></g>
      </g>
      <g v-for="(r, i) in rows" :key="r.key">
        <text x="0" :y="rowY(i) + 4" font-size="12" :fill="r.named ? 'var(--ink)' : 'var(--ink-3)'" font-weight="550"><title>[[ r.named ? r.label : r.label + ': name from a sensitive source; reveal it on the person page' ]]</title>[[ short(r.label) ]]</text>
        <line :x1="L" :x2="W" :y1="rowY(i)" :y2="rowY(i)" stroke="var(--line)" stroke-dasharray="1 3"/>
        <g clip-path="url(#st-clip)">
        <g v-for="g in r.gaps" :key="g.from"><title>[[ g.days ]] days, no evidence</title>
          <line :x1="x(g.from) + 7" :x2="x(g.to) - 7" :y1="rowY(i)" :y2="rowY(i)" stroke="var(--ink-3)" stroke-width="1.5"/>
          <text v-if="!compact && x(g.to) - x(g.from) > 110" :x="(x(g.from) + x(g.to)) / 2" :y="rowY(i) - 7" font-size="10" text-anchor="middle" fill="var(--ink-3)">[[ g.days ]] days, no evidence</text></g>
        <g v-for="p in r.points" :key="p.id" class="st-pt" style="cursor: pointer" @click="$emit('pick', p)" :class="{on: sel === p.id}">
          <title>[[ p.title ]]</title>
          <path v-if="p.fork" :d="'M' + (x(p.from) - 11) + ' ' + rowY(i) + ' l10 -8 M' + (x(p.from) - 11) + ' ' + rowY(i) + ' l10 8'" stroke="var(--conflict)" stroke-width="1.6" fill="none"/>
          <rect v-if="p.to !== p.from" :x="x(p.from)" :y="rowY(i) - 5 + p.dy" :width="Math.max(6, x(p.to) - x(p.from))" height="10" rx="2" v-bind="paint(p)"/>
          <circle v-else :cx="x(p.from)" :cy="rowY(i) + p.dy" r="6" v-bind="paint(p)"/>
          <circle v-if="sel === p.id" :cx="x(p.from)" :cy="rowY(i) + p.dy" r="10" fill="none" stroke="var(--ink)" stroke-width="1.5"/>
        </g>
        </g>
      </g>
      <line v-if="cursor" clip-path="url(#st-clip)" :x1="x(cursor)" :x2="x(cursor)" y1="16" :y2="H" stroke="var(--ink)" stroke-width="1.5"/>
    </svg></div>`,
  computed: {
    L() { return this.W < 520 ? 84 : this.compact ? 110 : 130; },
    step() { return this.compact ? 30 : 42; },
    H() { return 30 + Math.max(1, this.rows.length) * this.step + 4; },
    DAY: () => DAY,
    laid() { const taken = []; return {incidents: this.bands(this.incidents, taken), pending: this.bands(this.pending, taken)}; },
    full() { const a = day(this.from); return [a, Math.max(day(this.to), a + DAY)]; },
    // A few far points should not squeeze the rest: start on the 5th to 95th
    // percentile of point dates, padded a month, when that is under 60% of the range.
    dense() {
      const ts = this.rows.flatMap(r => r.points.map(p => day(p.from))).sort((a, b) => a - b);
      if (this.compact || ts.length < 8) return null;
      const q = f => ts[Math.min(ts.length - 1, Math.floor(f * ts.length))];
      const a = Math.max(this.full[0], q(0.05) - 31 * DAY), b = Math.min(this.full[1], q(0.95) + 31 * DAY);
      return b - a < 0.6 * (this.full[1] - this.full[0]) ? [a, b] : null;
    },
    win() { return this.view || this.dense || this.full; },
    t0() { return this.win[0]; },
    t1() { return Math.max(this.win[1], this.t0 + DAY); },
    span() { return this.t1 - this.t0; },
    whole() { return this.t0 <= this.full[0] && this.t1 >= this.full[1]; },
    outside() { return this.rows.reduce((n, r) => n + r.points.filter(p => day(p.to || p.from) < this.t0 || day(p.from) > this.t1).length, 0); },
    ticks() {
      const out = [], d = new Date(this.t0), months = (this.t1 - this.t0) / 2.6e9;
      const every = months > 48 ? 12 : months > 18 ? 3 : 1, minGap = 44;
      d.setUTCDate(1);
      let last = -Infinity;
      while (d.getTime() <= this.t1) {
        const x = this.x(iso(d.getTime()));
        if (d.getTime() >= this.t0 && x - last >= minGap) { out.push({x, label: d.toLocaleDateString('en-GB', {month: 'short', year: every === 12 ? 'numeric' : '2-digit', timeZone: 'UTC'})}); last = x; }
        d.setUTCMonth(d.getUTCMonth() + every);
      }
      return out;
    },
  },
  watch: {from() { this.view = null; }, to() { this.view = null; }},
  methods: {
    zoom(f) {
      const mid = (this.t0 + this.t1) / 2, half = Math.max(15 * DAY, this.span * f / 2);
      const a = Math.max(this.full[0], mid - half), b = Math.min(this.full[1], mid + half);
      this.view = [a, b];
    },
    short(s) { const n = this.L < 100 ? 11 : 17; return s.length > n ? s.slice(0, n - 1) + '…' : s; },
    rowY(i) { return 36 + i * this.step; },
    x(s) { return this.L + (day(s) - this.t0) / (this.t1 - this.t0) * (this.W - this.L - 14); },
    // Tier is texture in its role hue; unconfirmed keeps the author's hue, a confirmed agent point violet ring or ground.
    paint(p) {
      const c = p.agent ? 'var(--agent)' : 'var(--ink)';
      if (p.tier === 'unconfirmed') return {fill: p.agent ? 'url(#st-hatch-a)' : 'url(#st-hatch-i)', stroke: c, 'stroke-dasharray': '2 1.5'};
      if (p.tier === 'accepted') return {fill: 'var(--accepted)', stroke: p.agent ? 'var(--agent)' : 'var(--accepted)', 'stroke-width': p.agent ? 2 : 1};
      return {fill: p.agent ? 'var(--agent-wash)' : 'var(--panel)', stroke: 'var(--corroborated)', 'stroke-width': 1.6};
    },
    // A band label is drawn only where it overlaps no earlier label of either list; its title always names it.
    bands(list, taken) {
      return list.map(f => {
        const ys = f.rows.map(k => this.rows.findIndex(r => r.key === k)).filter(i => i >= 0);
        if (!ys.length || !f.from) return null;
        const y0 = this.rowY(Math.min(...ys)) - 15, y1 = this.rowY(Math.max(...ys)) + 15, x0 = this.x(f.from) - 8;
        const box = {x: x0, y: y1 - 16, w: f.label.length * 5.8 + 8, h: 14};
        const text = !taken.some(t => box.x < t.x + t.w && t.x < box.x + box.w && box.y < t.y + t.h && t.y < box.y + box.h);
        if (text) taken.push(box);
        return {id: f.id, label: f.label, title: f.title || f.label, text, x: x0, y: y0, w: Math.max(26, this.x(f.to || f.from) - x0 + 8), h: y1 - y0};
      }).filter(Boolean);
    },
  },
};

/* Timeline rows from story rows and points: forks from the steps, a title per point. */
export function timelineRows(rows, points) {
  const forks = new Set(rows.flatMap(r => r.steps.filter(s => s.fork).flatMap(s => s.ids)));
  const seen = {};
  return rows.map(r => ({
    key: r.key, label: r.label, named: r.named, gaps: r.gaps,
    points: points.filter(p => p.subject === r.key).map(p => {
      const fork = forks.has(p.id), n = fork ? (seen[r.key + p.from] = (seen[r.key + p.from] ?? -1) + 1) : 0;
      return {...p, fork, dy: fork ? (n % 2 ? 7 : -7) : 0,
        title: `${PROP[p.prop] || p.prop} ${p.place}, ${fmtSpan(p.from, p.to)}, ${p.tier}${p.inherited ? ', date from the document' : ''}${fork ? ', conflicts with another place that day' : ''}`};
    }),
  }));
}

export const MAP = {light: {bg: '#eef0f1', land: '#f9f9f7', border: '#c9c9c3', seq: '#18191b', muted: '#8a8d93'}, dark: {bg: '#101215', land: '#181a1e', border: '#383c43', seq: '#e7e7e4', muted: '#7d8189'}};
// The claim tiles draw only reference layers here (boundaries, uncertainty, footprints); the story's own numbered markers stay HTML.
const tileColors = c => ({border: c.border, ink: c.seq, muted: c.muted});
const basemapColors = c => ({bg: c.bg, land: c.land, border: c.border, muted: c.muted});

export const storyPage = boot => ({
  mixins: [layoutMixin],
  delimiters: config.delimiters,
  data() {
    return {
      boot, PROP, lens: boot.lens || 'filter', ref: boot.ref || null, s: null, error: '', busy: false,
      t: 0, playing: false, selId: null, detail: null, excerpt: null, excerptNote: '', tiers: [],
      personItems: [], placeItems: [], incidentItems: [], personQ: '', saved: [],
      finding: null, pkg: {open: false, title: '', audience: boot.audiences[0], busy: false},
      snack: false, snackText: '',
      shellHints: [['/', 'search'], ['meta+k', 'go to'], ['j', 'next point'], ['k', 'previous point'], ['p', 'play']],
    };
  },
  computed: {
    investigation() { return this.boot.investigation || this.caseSel || null; },
    query() { return Object.fromEntries(Object.entries({lens: this.lens, ref: this.ref, investigation: this.investigation, from: this.boot.from, to: this.boot.to}).filter(([, v]) => v)); },
    points() { return this.s ? this.s.points.filter(p => !this.tiers.length || this.tiers.includes(p.tier)) : []; },
    ordered() { return this.points.slice().sort((a, b) => a.from.localeCompare(b.from) || a.id.localeCompare(b.id)); },
    rows() { return this.s ? timelineRows(this.s.rows, this.points) : []; },
    range() {
      if (!this.ordered.length) return ['2013-01-01', '2014-01-01'];
      const lo = day(this.ordered[0].from), hi = Math.max(...this.ordered.map(p => day(p.to)), ...this.bandList.map(b => day(b.to || b.from)));
      const pad = Math.max(10, Math.round((hi - lo) / DAY / 20)) * DAY;
      return [iso(Math.min(lo, ...this.bandList.map(b => day(b.from))) - pad), iso(hi + pad)];
    },
    span() { return Math.round((day(this.range[1]) - day(this.range[0])) / DAY); },
    cursor() { return iso(day(this.range[0]) + this.t * DAY); },
    pending() { return this.s ? this.s.suggestions.map(f => ({id: f.id, from: f.window[0], to: f.window[1], rows: f.people, label: `${f.label.toLowerCase()}, pending`, title: `${f.label}, pending: ${f.reason}`})) : []; },
    incidentBands() { return this.s ? this.s.incidents.filter(i => i.window).map(i => ({id: i.id, from: i.window[0], to: i.window[1], rows: i.people, label: `${i.title}, ${i.sources} source${i.sources === 1 ? '' : 's'}`})) : []; },
    bandList() { return [...this.pending, ...this.incidentBands]; },
    sel() { return this.points.find(p => p.id === this.selId) || null; },
    rowLabel() { return Object.fromEntries((this.s?.rows || []).map(r => [r.key, r.label])); },
    sources() { return new Set(this.points.map(p => p.source)).size; },
    subtitle() {
      if (!this.s) return this.busy ? 'Loading' : '';
      if (!this.points.length) return `${this.s.rows.length} row${this.s.rows.length === 1 ? '' : 's'}, no dated points`;
      return `${this.rows.length} row${this.rows.length === 1 ? '' : 's'}, ${this.points.length} dated point${this.points.length === 1 ? '' : 's'} from ${this.sources} source${this.sources === 1 ? '' : 's'}, ${fmtSpan(this.ordered[0].from, this.ordered.at(-1).to)}`;
    },
    factual() { return this.points.filter(p => p.tier === 'accepted' || p.tier === 'corroborated'); },
    places() {
      // Numbered in order of first appearance: the sequence the markers show. One marker
      // per resolved place, so Aleppo and حلب share it and both spellings label it.
      const out = new Map();
      for (const p of this.ordered) if (p.lonlat && !out.has(p.place_id)) out.set(p.place_id, {n: out.size + 1, names: new Set(), lonlat: p.lonlat, points: []});
      for (const p of this.ordered) if (p.lonlat) { const pl = out.get(p.place_id); pl.names.add(p.place); pl.points.push(p); }
      return [...out.values()].map(({names, ...pl}) => ({...pl, place: [...names].join(' / ')}));
    },
    forkIds() { return new Set(this.rows.flatMap(r => r.points.filter(p => p.fork).map(p => p.id))); },
    entityHref() { return !this.ref ? null : this.lens === 'place' ? `/places/${this.ref}` : this.lens === 'incident' ? `/incidents/${this.ref.replace('incident:', '')}` : null; },
    lensTitle() { return {person: 'Person', group: 'Group', incident: 'Incident', place: 'Place', filter: 'Whole investigation'}[this.lens]; },
  },
  watch: {
    cursor() { this.drawMap(false); },
    points() { this.$nextTick(() => this.drawMap(true)); },
    isDark() { this.restyle(); },
    selId(id) { this.loadDetail(id); },
  },
  methods: {
    fmtDay, fmtSpan,
    say(text) { this.snackText = text; this.snack = true; },
    fail(err) { return err.response?.data?.description || err.response?.data?.message || err.message; },
    useCase() { if (this.lens === 'filter' || this.lens === 'place' || this.lens === 'incident') this.load(); this.loadPickers(); },
    async load() {
      this.busy = true; this.error = ''; this.stop();
      history.replaceState(null, '', '/stories?' + new URLSearchParams(this.query));
      try {
        this.s = (await axios.get('/api/stories', {params: this.query})).data;
        if (this.lens === 'person' && this.s.rows[0] && !this.personItems.some(i => i.value === this.ref)) this.personItems.unshift({title: this.s.rows[0].label, value: this.ref});
        this.t = this.span;
        this.selId = this.ordered[0]?.id || null;
      } catch (err) { this.s = null; this.error = this.fail(err); }
      this.busy = false;
      this.$nextTick(() => { this.initMap(); if (this.ready) setClaimQuery(this.map, this.query); this.drawMap(true); });
    },
    async loadPickers() {
      const inv = {params: {investigation: this.investigation || undefined}};
      const [places, incidents] = await Promise.allSettled([axios.get('/api/places', inv), axios.get('/api/discover/incidents', inv)]);
      if (places.status === 'fulfilled') this.placeItems = places.value.data.items.filter(p => p.statements).map(p => ({title: p.name_en ? `${p.name_en} / ${p.name_ar}` : p.name_ar, value: p.id}));
      if (incidents.status === 'fulfilled') this.incidentItems = incidents.value.data.items.map(i => ({title: i.title, value: i.id}));
    },
    async searchPeople(q) {
      this.personQ = q;
      const found = q && q.trim().length > 1 ? (await axios.get('/api/people/search', {params: {q}})).data.items : (await axios.get('/api/people')).data.items.slice(0, 40);
      const keep = this.personItems.filter(i => i.value === this.ref);
      this.personItems = [...keep, ...found.filter(p => p.person !== this.ref).map(p => ({title: `${p.name || 'Unnamed person ' + p.person.slice(0, 8)}, ${p.facts} facts`, value: p.person}))];
    },
    setLens(lens) {
      const keys = (this.s?.rows || []).map(r => r.key.replace('person:', ''));
      this.lens = lens;
      if (lens === 'person') this.ref = keys[0] || this.personItems[0]?.value || null;
      else if (lens === 'group') this.ref = keys.join(',') || null;
      else if (lens === 'place') this.ref = this.sel?.place_id || this.placeItems[0]?.value || null;
      else if (lens === 'incident') this.ref = this.s?.incidents[0]?.id || this.incidentItems[0]?.value || null;
      else this.ref = null;
      if (this.ref || lens === 'filter') this.load(); else { this.s = null; this.drawMap(true); }
    },
    pick(ref) { this.ref = ref; if (ref) this.load(); },
    select(p) {
      this.selId = p.id;
      const d = Math.round((day(p.from) - day(this.range[0])) / DAY);
      if (d > this.t) this.t = d;
      if (p.lonlat && this.map) this.map.easeTo({center: p.lonlat, duration: 400});
      this.$nextTick(() => document.querySelector(`[data-point="${p.id}"]`)?.scrollIntoView({block: 'nearest', inline: 'nearest'}));
    },
    move(d) { const i = this.ordered.findIndex(p => p.id === this.selId); const p = this.ordered[Math.max(0, Math.min(this.ordered.length - 1, i + d))]; if (p) this.select(p); },
    play() {
      if (this.playing) { this.stop(); return; }
      if (this.t >= this.span) this.t = 0;
      this.playing = true;
      this.timer = setInterval(() => {
        this.t = Math.min(this.span, this.t + Math.max(1, Math.round(this.span / 90)));
        if (this.t >= this.span) this.stop();
      }, 60);
    },
    stop() { clearInterval(this.timer); this.playing = false; },
    async loadDetail(id) {
      this.detail = null; this.excerpt = null; this.excerptNote = '';
      if (!id) return;
      const p = this.points.find(x => x.id === id);
      try { const d = (await axios.get(`/api/monitor/statement/${id}`)).data; if (this.selId === id) this.detail = d; } catch (err) { this.excerptNote = this.fail(err); }
      if (!p?.segment) { this.excerptNote = p?.href?.includes('page=') ? 'A region on the page; open it to see the scan' : 'Open the source to see the span'; return; }
      if (p.locked) { this.excerptNote = 'Sensitive source: reveal it on its evidence page to read the text (logged)'; return; }
      try {
        const e = (await axios.get(`/api/evidence/segments/${p.segment.id}/excerpt`, {params: {start: p.segment.start, end: p.segment.end}})).data;
        if (this.selId === id) this.excerpt = e;
      } catch (err) { this.excerptNote = err.response?.status === 403 ? 'Sensitive source: reveal it on its evidence page to read the text (logged)' : this.fail(err); }
    },
    openFinding(id) { this.finding = this.s.suggestions.find(f => f.id === id) || null; },
    memberLabel(m) { return this.rowLabel[m.subject] || this.rowLabel[this.s.points.find(p => p.id === m.statement)?.subject] || 'Unnamed person'; },
    async save() {
      try {
        await axios.post('/api/stories/saved', {item: this.query});
        this.say('Saved to your stories'); this.loadSaved();
      } catch (err) { this.say(this.fail(err)); }
    },
    async loadSaved() { try { this.saved = (await axios.get('/api/stories/saved')).data.items; } catch { this.saved = []; } },
    savedHref(q) { return '/stories?' + new URLSearchParams(Object.fromEntries(Object.entries(q).filter(([, v]) => v))); },
    savedLabel(q) { const n = q.ref ? q.ref.split(',').length : 0; return {person: `Person ${q.ref?.slice(0, 8)}`, group: `Group of ${n}`, incident: `Incident ${q.ref?.slice(9, 17)}`, place: `Place ${q.ref?.slice(0, 8)}`, filter: 'Whole investigation'}[q.lens] + (q.investigation ? `, ${q.investigation}` : ''); },
    async unsave(id) { await axios.delete(`/api/stories/saved/${id}`); this.loadSaved(); },
    openPackage() { this.pkg = {...this.pkg, open: true, title: `Story: ${this.s.title}`}; },
    async makePackage() {
      // One cited sentence per row, in the words of its own claims; the brief page is where a person rewrites them before the build.
      this.pkg.busy = true;
      try {
        const {data} = await axios.post('/api/packages/briefs', {item: {title: this.pkg.title, audience: this.pkg.audience}});
        for (const r of this.rows) {
          const pts = r.points.filter(p => p.tier === 'accepted' || p.tier === 'corroborated');
          if (!pts.length) continue;
          const text = `${r.label}: ${pts.map(p => `${PROP[p.prop] || p.prop} ${p.place}, ${fmtSpan(p.from, p.to)}`).join('; ')}.`;
          await axios.post(`/api/packages/briefs/${data.id}/sentences`, {item: {text, statement_ids: pts.map(p => p.id)}});
        }
        location.href = `/packages/brief/${data.id}`;
      } catch (err) { this.say(this.fail(err)); this.pkg.busy = false; }
    },
    initMap() {
      if (this.map || !this.$refs.map) return;
      const c = MAP[this.isDark ? 'dark' : 'light'];
      this.map = new maplibregl.Map({container: this.$refs.map, style: {version: 8, sources: {}, layers: [{id: 'bg', type: 'background', paint: {'background-color': c.bg}}]},
        center: [37.2, 34.8], zoom: 5.6, attributionControl: {compact: true, customAttribution: `Natural Earth, ${this.s?.attribution || 'GeoNames (CC BY 4.0)'}`}, dragRotate: false, pitchWithRotate: false});
      this.map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'bottom-right');
      this.map.touchZoomRotate.disableRotation();
      this.map.on('load', async () => {
        const region = await (await fetch('/static/maplibre/region.geojson')).json();
        this.map.addSource('region', {type: 'geojson', data: region});
        this.map.addLayer({id: 'land', type: 'fill', source: 'region', paint: {'fill-color': c.land}});
        this.map.addLayer({id: 'borders', type: 'line', source: 'region', paint: {'line-color': c.border, 'line-width': .8}});
        addClaimTiles(this.map, this.query, tileColors(c));
        await addBasemap(this.map, basemapColors(c), 'borders', ['land']);
        this.map.addSource('seq', {type: 'geojson', data: {type: 'FeatureCollection', features: []}});
        this.map.addLayer({id: 'seq', type: 'line', source: 'seq', paint: {'line-color': c.seq, 'line-width': 1.2, 'line-dasharray': [2, 2], 'line-opacity': .6}});
        this.ready = true;
        this.drawMap(true);
      });
    },
    restyle() {
      if (!this.ready) return;
      const c = MAP[this.isDark ? 'dark' : 'light'];
      this.map.setPaintProperty('bg', 'background-color', c.bg);
      this.map.setPaintProperty('land', 'fill-color', c.land);
      this.map.setPaintProperty('borders', 'line-color', c.border);
      this.map.setPaintProperty('seq', 'line-color', c.seq);
      recolor(this.map, tileColors(c));
      recolorBasemap(this.map, basemapColors(c));
    },
    drawMap(fit) {
      const map = this.map;
      if (!this.ready) return;
      (this.markers || []).forEach(m => m.remove());
      this.markers = [];
      for (const pl of this.places) {
        const live = pl.points.filter(p => p.from <= this.cursor), best = live.find(p => p.tier === 'accepted') || live[0] || pl.points[0];
        const el = document.createElement('button');
        el.type = 'button';
        el.className = `st-mk tier-${best.tier} ${best.agent ? 'by-agent' : 'by-person'}${live.length ? '' : ' future'}${pl.points.some(p => this.forkIds.has(p.id)) ? ' fork' : ''}`;
        el.innerHTML = `<span class="d">${pl.n}</span><span class="l"><bdi>${esc(pl.place)}</bdi> (${live.length} of ${pl.points.length})</span>`;
        el.title = `${pl.n}. ${pl.place}: ${pl.points.length} point${pl.points.length === 1 ? '' : 's'}`;
        el.setAttribute('aria-label', el.title);
        el.onclick = () => this.select(best);
        // Close places would print over each other: the second puts its label left, later ones step down.
        const near = this.markers.filter(m => { const l = m.getLngLat(); return Math.abs(l.lng - pl.lonlat[0]) < .15 && Math.abs(l.lat - pl.lonlat[1]) < .15; }).length;
        if (near % 2) el.style.flexDirection = 'row-reverse';
        const marker = new maplibregl.Marker({element: el, anchor: near % 2 ? 'right' : 'left', offset: [near % 2 ? 9 : -9, Math.floor(near / 2) * 22]});
        this.markers.push(marker.setLngLat(pl.lonlat).addTo(map));
      }
      // A line per row through its known places up to the cursor, in time order.
      const lines = this.rows.map(r => {
        const out = [];
        for (const p of r.points.filter(p => p.lonlat && p.from <= this.cursor).sort((a, b) => a.from.localeCompare(b.from))) {
          const last = out.at(-1);
          if (!last || last[0] !== p.lonlat[0] || last[1] !== p.lonlat[1]) out.push(p.lonlat);
        }
        return out;
      }).filter(c => c.length > 1);
      map.getSource('seq').setData({type: 'FeatureCollection', features: lines.map(c => ({type: 'Feature', geometry: {type: 'LineString', coordinates: c}}))});
      if (fit && this.places.length) {
        const xs = this.places.map(p => p.lonlat[0]), ys = this.places.map(p => p.lonlat[1]);
        map.fitBounds([[Math.min(...xs) - .6, Math.min(...ys) - .5], [Math.max(...xs) + .6, Math.max(...ys) + .5]], {duration: 0, maxZoom: 9, padding: 30});
      }
    },
    key(e) {
      if (TYPING(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'j') this.move(1);
      else if (e.key === 'k') this.move(-1);
      else if (e.key === 'p') this.play();
    },
  },
  mounted() {
    window.addEventListener('keydown', this.key);
    this.searchPeople('');
    this.loadPickers();
    this.loadSaved();
    this.load();
  },
  beforeUnmount() { window.removeEventListener('keydown', this.key); this.stop(); },
});
