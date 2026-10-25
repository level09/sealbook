/* Case map: who was where, when, in one case. A timeline of people (rows) over
   places (segments), the bands where two or more were at one place at once, the
   silences, and a map of places sized by the people there, with each person's moves
   as arrows. One time slider drives both; a place or a band opens who was there,
   where they came from and went to, each line with its sources. The path question
   asks the server for the shortest sourced chain between two people or places.
   Tier is texture, as on Stories: solid accepted, outlined corroborated, hatched
   unconfirmed. Nothing here decides; links go to the screens that do. */
import * as maplibregl from '/static/maplibre/maplibre-gl.mjs';
import {addClaimTiles, recolor} from '/static/js/maptiles.js';
import {addBasemap, recolorBasemap} from '/static/js/basemap.js';
import {MAP, PROP, fmtDay, fmtSpan} from '/static/js/stories.js';

const DAY = 864e5;
const day = s => Date.parse(s + 'T00:00:00Z');
const iso = t => new Date(t).toISOString().slice(0, 10);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const still = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const MONTH = (t, opts) => new Date(t).toLocaleDateString('en-GB', {timeZone: 'UTC', ...opts});
const placeName = p => p ? (p.en || p.ar) : '';
const placeBoth = p => p ? [p.en, p.ar].filter(Boolean).join(' / ') : '';
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
/* A claim's time in words, as the agents write it: "July 2013", "2013", "2 May 2013". */
export function when(a, b) {
  if (!b || a === b) return fmtDay(a);
  const [y, m, d] = a.split('-').map(Number), [y2, m2, d2] = b.split('-').map(Number);
  if (y === y2 && m === 1 && d === 1 && m2 === 12 && d2 === 31) return String(y);
  if (y === y2 && m === m2 && d === 1 && d2 === new Date(Date.UTC(y, m, 0)).getUTCDate()) return `${MONTHS[m - 1]} ${y}`;
  return fmtSpan(a, b);
}

/* The calendar unit holding `t`: [start, end] in ms, end inclusive. */
function unit(t, span) {
  const d = new Date(t), y = d.getUTCFullYear(), m = d.getUTCMonth();
  if (span === 'year') return [Date.UTC(y, 0, 1), Date.UTC(y, 11, 31)];
  const m0 = span === 'quarter' ? m - m % 3 : m, n = span === 'quarter' ? 3 : 1;
  return [Date.UTC(y, m0, 1), Date.UTC(y, m0 + n, 0)];
}
function unitLabel(w, span) {
  if (span === 'year') return MONTH(w[0], {year: 'numeric'});
  if (span === 'quarter') return `${MONTH(w[0], {month: 'short'})} to ${MONTH(w[1], {month: 'short', year: 'numeric'})}`;
  return MONTH(w[0], {month: 'long', year: 'numeric'});
}

/* The timeline: rows of people, a segment per claim at its place, bands over the
   people at one place at once, hatched silences, and a stub where the record stops. */
const HALO = {'paint-order': 'stroke', stroke: 'var(--panel)', 'stroke-width': 3, 'stroke-linejoin': 'round'};
const textW = (s, px = 10) => String(s).length * px * .56;

export const CaseTimeline = {
  name: 'CaseTimeline',
  props: {
    rows: Array,        // [{key, label, named, points: [...], gaps: [...], end, pair}]
    bands: Array,       // [{id, people, from, to, label, names}]
    from: String, to: String,
    win: Array,         // [from, to] ms, or null for the whole case
    cursor: Number,
    sel: String,        // selected band id
    path: Object,       // {claims: Set, people: Set} or null
  },
  emits: ['band', 'point'],
  delimiters: ['[[', ']]'],
  data: () => ({W: 640, whole: false, HALO}),
  mounted() { this._ro = new ResizeObserver(() => { this.W = Math.max(300, this.$el.clientWidth); }); this._ro.observe(this.$el); },
  beforeUnmount() { this._ro.disconnect(); },
  template: `<div class="cm-tl">
    <div v-if="dense" class="d-flex justify-end mb-1"><button type="button" class="cm-zoom" @click="whole = !whole">[[ whole ? 'Fit the busy years' : 'Whole range, ' + outside + ' claim' + (outside === 1 ? '' : 's') + ' outside' ]]</button></div>
    <svg :viewBox="'0 0 ' + W + ' ' + H" :height="H" :width="W" style="display: block" role="img" :aria-label="'Timeline of ' + rows.length + ' people'">
      <defs><clipPath id="cm-clip"><rect :x="L" y="0" :width="W - L" :height="H"/></clipPath>
        <pattern id="cm-hatch-a" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.6" height="4" fill="var(--agent)"/></pattern>
        <pattern id="cm-hatch-i" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.6" height="4" fill="var(--ink)"/></pattern>
        <pattern id="cm-hatch-q" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1" height="5" fill="var(--ink-3)"/></pattern>
        <pattern id="cm-hatch-e" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.2" height="5" fill="var(--conflict-line)"/></pattern></defs>
      <g clip-path="url(#cm-clip)">
        <rect v-if="win" :x="x(win[0])" y="18" :width="Math.max(2, x(win[1] + DAY) - x(win[0]))" :height="H - 18" fill="var(--wash)"/>
        <g v-for="t in ticks" :key="t.x"><line :x1="t.x" :x2="t.x" y1="18" :y2="H" stroke="var(--line)"/><text :x="t.x + 3" y="12" font-size="10.5" fill="var(--ink-3)">[[ t.label ]]</text></g>
        <g v-for="b in laid" :key="b.id" class="cm-band" tabindex="0" role="button" :aria-label="b.title" @click="$emit('band', b.id)" @keydown.enter="$emit('band', b.id)">
          <title>[[ b.title ]]</title>
          <rect :x="b.x" :y="b.y" :width="b.w" :height="b.h" fill="var(--person-wash)" opacity=".7"/>
          <rect v-for="y in b.rows" :key="y" :x="b.x" :y="y - 11" :width="b.w" height="22" rx="3" fill="var(--person-wash)" :stroke="sel === b.id ? 'var(--person)' : 'var(--person-line)'" :stroke-width="sel === b.id ? 2 : 1"/>
        </g>
      </g>
      <g v-for="(r, i) in laidRows" :key="r.key" :opacity="path && !path.people.has(r.key) ? .35 : 1">
        <text x="0" :y="rowY(i) + 4" font-size="12" :fill="r.named ? 'var(--ink)' : 'var(--ink-3)'" font-weight="550" dir="auto"><title>[[ r.named ? r.label : r.label + ': name from a sensitive source; reveal it on the person page' ]]</title>[[ short(r.label) ]]</text>
        <text v-if="r.pair" x="0" :y="rowY(i) + 16" font-size="10" fill="var(--agent)"><title>[[ r.pair ]]</title>possibly two people</text>
        <line :x1="L" :x2="W" :y1="rowY(i)" :y2="rowY(i)" stroke="var(--line)" stroke-dasharray="1 3"/>
        <g clip-path="url(#cm-clip)">
          <g v-for="g in r.gaps" :key="'g' + g.from"><title>[[ g.days ]] days, no evidence</title>
            <rect :x="x(day(g.from)) + 2" :y="rowY(i) - 3" :width="Math.max(0, x(day(g.to)) - x(day(g.from)) - 4)" height="6" fill="url(#cm-hatch-q)"/></g>
          <g v-if="r.end"><title>[[ r.end.label ]]</title>
            <rect :x="r.end.x" :y="rowY(i) - 3" :width="r.end.w" height="6" fill="url(#cm-hatch-e)"/>
            <text v-if="r.end.text" :x="r.end.x + r.end.w + 4" :y="rowY(i) + 4" font-size="10" fill="var(--conflict)" v-bind="HALO">[[ r.end.short ]]</text></g>
          <g v-for="p in r.points" :key="p.id" class="cm-pt" @click="$emit('point', p)" :opacity="inWin(p) ? 1 : .35">
            <title>[[ p.title ]]</title>
            <rect :x="x(day(p.from))" :y="rowY(i) - 6" :width="Math.max(6, x(day(p.to) + DAY) - x(day(p.from)))" height="12" rx="2" v-bind="paint(p)"/>
            <rect v-if="path && path.claims.has(p.id)" :x="x(day(p.from)) - 3" :y="rowY(i) - 9" :width="Math.max(6, x(day(p.to) + DAY) - x(day(p.from))) + 6" height="18" rx="4" fill="none" stroke="var(--new)" stroke-width="2"/>
            <text v-if="p.showTag" :x="x(day(p.from))" :y="rowY(i) - 10" font-size="10" fill="var(--ink-2)" dir="auto" v-bind="HALO">[[ p.tag ]]</text>
          </g>
        </g>
      </g>
      <g clip-path="url(#cm-clip)"><text v-for="b in laid.filter(b => b.text)" :key="'t' + b.id" :x="b.x + 2" :y="b.labelY" font-size="10.5" fill="var(--person)" v-bind="HALO" style="pointer-events: none">[[ b.label ]]</text></g>
      <line v-if="cursor && win" clip-path="url(#cm-clip)" :x1="x(cursor)" :x2="x(cursor)" y1="16" :y2="H" stroke="var(--ink)" stroke-width="1.5"/>
    </svg></div>`,
  computed: {
    DAY: () => DAY,
    L() { return this.W < 520 ? 96 : 150; },
    step: () => 40,
    H() { return 34 + Math.max(1, this.rows.length) * this.step + 14; },
    full() { return [day(this.from), Math.max(day(this.to), day(this.from) + DAY)]; },
    // A few far records should not squeeze the rest: start on the 5th to 95th
    // percentile of dates, padded a month, when that is under 60% of the range.
    dense() {
      const ts = this.rows.flatMap(r => r.points.map(p => day(p.from))).sort((a, b) => a - b);
      if (ts.length < 8) return null;
      const q = f => ts[Math.min(ts.length - 1, Math.floor(f * ts.length))];
      const a = Math.max(this.full[0], q(0.05) - 31 * DAY), b = Math.min(this.full[1], q(0.95) + 62 * DAY);
      return b - a < 0.6 * (this.full[1] - this.full[0]) ? [a, b] : null;
    },
    t0() { return this.dense && !this.whole ? this.dense[0] : this.full[0]; },
    t1() { return this.dense && !this.whole ? this.dense[1] : this.full[1]; },
    outside() { return this.rows.reduce((n, r) => n + r.points.filter(p => day(p.to) < this.t0 || day(p.from) > this.t1).length, 0); },
    // Place names only where they fit after the previous one; the stub where a record stops is short, with its words beside it.
    laidRows() {
      return this.rows.map(r => {
        let edge = -Infinity;
        const points = r.points.map(p => {
          const x0 = this.x(day(p.from)), show = !!p.tag && x0 >= edge + 4;
          if (show) edge = x0 + textW(p.tag);
          return {...p, showTag: show};
        });
        let end = null;
        if (r.end) {
          const x0 = this.x(day(r.end.after) + DAY), w = Math.max(0, Math.min(48, this.W - x0 - 4));
          end = {...r.end, x: x0, w, text: x0 + w + 4 + textW(r.end.short) < this.W};
        }
        return {...r, points, end};
      });
    },
    // A band washes the span of its rows and outlines each member's row; its words go under the last member, where no earlier label is.
    laid() {
      const taken = [];
      return this.bands.map(b => {
        const ys = b.people.map(k => this.rows.findIndex(r => r.key === k)).filter(i => i >= 0);
        if (!ys.length) return null;
        const x0 = this.x(day(b.from)) - 3, x1 = this.x(day(b.to) + DAY) + 3;
        const y0 = this.rowY(Math.min(...ys)) - 11, y1 = this.rowY(Math.max(...ys)) + 11;
        const box = {x: x0, y: y1 + 1, w: textW(b.label, 10.5) + 6, h: 13};
        const text = !taken.some(t => box.x < t.x + t.w && t.x < box.x + box.w && box.y < t.y + t.h && t.y < box.y + box.h);
        if (text) taken.push(box);
        return {id: b.id, label: b.label, title: `${b.label}: ${b.names}. Open who was there`, text, labelY: y1 + 11,
          rows: ys.map(i => this.rowY(i)), x: x0, y: y0, w: Math.max(8, x1 - x0), h: y1 - y0};
      }).filter(Boolean);
    },
    ticks() {
      const out = [], d = new Date(this.t0), months = (this.t1 - this.t0) / 2.6e9;
      const every = months > 30 ? 12 : months > 12 ? 3 : 1;
      d.setUTCDate(1);
      if (every === 12) d.setUTCMonth(0);
      let last = -Infinity;
      while (d.getTime() <= this.t1) {
        const x = this.x(d.getTime());
        if (d.getTime() >= this.t0 && x - last >= 44) { out.push({x, label: MONTH(d.getTime(), every === 12 ? {year: 'numeric'} : {month: 'short', year: '2-digit'})}); last = x; }
        d.setUTCMonth(d.getUTCMonth() + every);
      }
      return out;
    },
  },
  methods: {
    day,
    short(s) { const n = this.L < 120 ? 13 : 21; return s.length > n ? s.slice(0, n - 1) + '…' : s; },
    rowY(i) { return 40 + i * this.step; },
    x(t) { return this.L + (t - this.t0) / (this.t1 - this.t0) * (this.W - this.L - 8); },
    inWin(p) { return !this.win || (day(p.from) <= this.win[1] && day(p.to) >= this.win[0]); },
    paint(p) {
      const c = p.agent ? 'var(--agent)' : 'var(--ink)';
      if (p.tier === 'unconfirmed') return {fill: p.agent ? 'url(#cm-hatch-a)' : 'url(#cm-hatch-i)', stroke: c, 'stroke-dasharray': '2 1.5'};
      if (p.tier === 'accepted') return {fill: 'var(--accepted)', stroke: p.agent ? 'var(--agent)' : 'var(--accepted)', 'stroke-width': p.agent ? 2 : 1};
      return {fill: p.agent ? 'var(--agent-wash)' : 'var(--panel)', stroke: 'var(--corroborated)', 'stroke-width': 1.6};
    },
  },
};

export const caseMapPage = boot => ({
  mixins: [layoutMixin],
  delimiters: config.delimiters,
  data() {
    return {
      boot, v: null, busy: false, error: '', news: [], newsBusy: false, live: false,
      t: 0, span: 'all', playing: false, sort: 'first', placeFilter: null,
      selected: null,   // {kind: 'place', id} | {kind: 'band', id}
      ask: {from: null, to: null, busy: false, answer: null},
      shellHints: [['/', 'search'], ['meta+k', 'go to'], ['p', 'play'], ['escape', 'clear the selection']],
    };
  },
  computed: {
    investigation() { return this.caseSel || null; },
    placeById() { return Object.fromEntries((this.v?.places || []).map(p => [p.id, p])); },
    labels() { return Object.fromEntries((this.v?.rows || []).map(r => [r.key, r.label])); },
    // On one day a transfer is where the person ends up, so it sorts last.
    points() { return (this.v?.points || []).slice().sort((a, b) => a.from.localeCompare(b.from) || (a.prop === 'transferred_to') - (b.prop === 'transferred_to') || a.id.localeCompare(b.id)); },
    byPerson() { const out = {}; for (const p of this.points) (out[p.subject] ||= []).push(p); return out; },
    ends() { const out = {}; for (const o of this.v?.open_ends || []) { const cur = out[o.person]; if (!cur || o.after > cur.after) out[o.person] = o; } return out; },
    partner() {
      const out = {};
      for (const [a, b] of this.v?.pairs || []) { (out[a] ||= []).push(b); (out[b] ||= []).push(a); }
      return out;
    },
    range() {
      if (!this.points.length) return [day('2013-01-01'), day('2014-01-01')];
      const lo = day(this.points[0].from), hi = Math.max(...this.points.map(p => day(p.to)));
      const pad = Math.max(10, Math.round((hi - lo) / DAY / 40)) * DAY;
      return [lo - pad, hi + pad];
    },
    days() { return Math.round((this.range[1] - this.range[0]) / DAY); },
    cursor() { return this.range[0] + this.t * DAY; },
    win() { return this.span === 'all' ? null : unit(this.cursor, this.span); },
    winLabel() { return this.win ? unitLabel(this.win, this.span) : 'The whole case'; },
    inWin() { const w = this.win; return w ? this.points.filter(p => day(p.from) <= w[1] && day(p.to) >= w[0]) : this.points; },
    placeItems() { return (this.v?.places || []).map(p => ({title: placeBoth(p), value: p.id})).sort((a, b) => a.title.localeCompare(b.title)); },
    // Rows in the chosen order; records an open proposal may join stay next to each other.
    rows() {
      if (!this.v) return [];
      const first = k => this.byPerson[k]?.[0];
      let keys = this.v.rows.map(r => r.key);
      if (this.placeFilter) keys = keys.filter(k => (this.byPerson[k] || []).some(p => p.place_id === this.placeFilter));
      const firstPlace = k => { const p = first(k); return p ? (p.place_id ? placeName(this.placeById[p.place_id]) : p.place) : '~'; };
      keys.sort((a, b) => this.sort === 'place'
        ? firstPlace(a).localeCompare(firstPlace(b)) || (first(a)?.from || '~').localeCompare(first(b)?.from || '~')
        : (first(a)?.from || '~').localeCompare(first(b)?.from || '~') || a.localeCompare(b));
      const out = [];
      for (const k of keys) {
        if (out.includes(k)) continue;
        out.push(k);
        for (const o of this.partner[k] || []) if (keys.includes(o) && !out.includes(o)) out.push(o);
      }
      return out.map(k => {
        const r = this.v.rows.find(x => x.key === k), end = this.ends[k];
        let lastPlace = null;
        return {
          key: k, label: r.label, named: r.named, gaps: r.gaps,
          pair: (this.partner[k] || []).length ? `An open same-person proposal joins this record with ${this.partner[k].map(o => this.labels[o]).join(' and ')}; they stay two rows until someone reviews it` : null,
          end: end ? {...end, short: `no record after ${fmtDay(end.after)}`} : null,
          points: (this.byPerson[k] || []).map(p => {
            const name = p.place_id ? placeName(this.placeById[p.place_id]) : p.place;
            const tag = name !== lastPlace ? name : null;
            lastPlace = name;
            return {...p, tag, title: `${PROP[p.prop] || p.prop} ${p.place_id ? placeBoth(this.placeById[p.place_id]) : p.place + ' (place not confirmed)'}, ${fmtSpan(p.from, p.to)}, ${p.tier}${p.inherited ? ', date from the document' : ''}. Source: ${p.file || 'unknown'}`};
          }),
        };
      });
    },
    bandList() { return (this.v?.bands || []).map(b => ({...b, names: b.people.map(k => this.labels[k]).join(', ')})); },
    // Who is at each place in the window, by person.
    present() {
      const out = {};
      for (const p of this.inWin) if (p.place_id) ((out[p.place_id] ||= {})[p.subject] ||= []).push(p);
      return out;
    },
    // Each person's moves between distinct resolved places, in time order.
    moves() {
      const out = [];
      for (const [k, pts] of Object.entries(this.byPerson)) {
        let prev = null;
        for (const p of pts.filter(p => p.place_id)) {
          if (prev && prev.place_id !== p.place_id) out.push({person: k, a: prev.place_id, b: p.place_id, leave: day(prev.to), arrive: day(p.from), from: prev, to: p});
          if (!prev || prev.place_id !== p.place_id || day(p.to) > day(prev.to)) prev = p;
        }
      }
      return out;
    },
    shownMoves() { const w = this.win; return w ? this.moves.filter(m => m.arrive >= w[0] && m.leave <= w[1]) : this.moves; },
    summary() {
      if (!this.v) return this.busy ? 'Loading' : '';
      const people = new Set(this.inWin.map(p => p.subject)).size, places = new Set(this.inWin.filter(p => p.place_id).map(p => p.place_id)).size;
      const w = this.win, together = this.bandList.filter(b => !w || (day(b.from) <= w[1] && day(b.to) >= w[0]));
      const at = new Map();
      for (const b of together) { const where = b.places.map(id => placeName(this.placeById[id])).join(' and '), how = b.label.split(',')[0]; if (!at.has(where) || how.startsWith('held')) at.set(where, how); }
      const held = at.size ? `; ${[...at].slice(0, 3).map(([where, how]) => `${how} at ${where}`).join(', ')}` : '';
      return `${this.winLabel}: ${plural(people, 'person', 'people')} at ${plural(places, 'place')}${held}`;
    },
    subtitle() {
      if (!this.v) return this.busy ? 'Loading' : '';
      if (!this.points.length) return 'No dated claims at a place in this case yet';
      const sources = new Set(this.points.map(p => p.source).filter(Boolean)).size;
      return `${plural(this.v.rows.length, 'person', 'people')}, ${plural(this.v.places.length, 'place')}, ${plural(sources, 'source')}, ${fmtSpan(this.points[0].from, this.points.at(-1).to)}`;
    },
    pathMark() {
      const a = this.ask.answer;
      if (!a?.found) return null;
      return {claims: new Set(a.steps.flatMap(s => s.claims)), people: new Set(a.steps.flatMap(s => s.nodes).filter(n => n.startsWith('person:'))), places: new Set(a.steps.flatMap(s => s.nodes).filter(n => n.startsWith('place:')).map(n => n.slice(6)))};
    },
    askItems() {
      return [...(this.v?.rows || []).map(r => ({title: r.label, value: r.key, props: {prependIcon: 'ti ti-user'}})),
        ...this.placeItems.map(p => ({title: p.title, value: 'place:' + p.value, props: {prependIcon: 'ti ti-map-pin'}}))];
    },
    // The open side panel: a place or a band, read as who was there and how they came and went.
    panel() {
      const s = this.selected;
      if (!s || !this.v) return null;
      if (s.kind === 'band') {
        const b = this.bandList.find(x => x.id === s.id);
        if (!b) return null;
        const w = [day(b.from), day(b.to)], ids = new Set(b.members);
        const here = {};
        for (const p of this.points) if (ids.has(p.id)) (here[p.subject] ||= []).push(p);
        return {title: b.label[0].toUpperCase() + b.label.slice(1), sub: `${b.places.map(id => placeName(this.placeById[id])).join(' and ')}, ${plural(b.people.length, 'person', 'people')} from ${plural(new Set(b.members.map(i => this.points.find(p => p.id === i)?.source)).size, 'source')}`,
          places: b.places, finding: b.finding, ...this.comings(here, w)};
      }
      const pl = this.placeById[s.id];
      if (!pl) return null;
      const here = this.present[s.id] || {}, n = Object.keys(here).length;
      const w = this.win || [this.range[0], this.range[1]];
      return {title: `${placeName(pl)} ${this.win ? 'in ' + this.winLabel : 'over the whole case'}`, sub: n ? `${plural(n, 'person', 'people')} here, from ${plural(new Set(Object.values(here).flat().map(p => p.source)).size, 'source')}` : 'Nobody on record here in this window',
        places: [s.id], approximate: pl.approximate ? pl.precision_m : false, ...this.comings(here, w)};
    },
  },
  watch: {
    cursor() { this.drawMap(false); },
    span() { this.drawMap(false); },
    rows() { this.$nextTick(() => this.drawMap(false)); },
    pathMark() { this.drawMap(false); },
    isDark() { this.restyle(); },
  },
  methods: {
    fmtDay, fmtSpan, placeName, placeBoth, isoDay: iso,
    fail(err) { return err.response?.data?.description || err.response?.data?.message || err.message; },
    useCase() { this.load(); },
    async load() {
      this.stop(); this.selected = null; this.ask = {from: null, to: null, busy: false, answer: null}; this.error = '';
      if (!this.investigation) { this.v = null; this.news = []; return; }
      this.busy = true;
      try {
        this.v = (await axios.get('/api/case-map', {params: {investigation: this.investigation}})).data;
        this.t = this.days; this.span = 'all';
      } catch (err) { this.v = null; this.error = this.fail(err); }
      this.busy = false;
      this.loadNews();
      this.listen();
      this.$nextTick(() => { this.initMap(); this.drawMap(true); });
    },
    async loadNews() {
      this.newsBusy = true;
      try { this.news = (await axios.get('/api/case-map/news', {params: {investigation: this.investigation}})).data.items; } catch { this.news = []; }
      this.newsBusy = false;
    },
    // The worker stream says something happened in this case; the news is reread, never pushed whole.
    listen() {
      this.es?.close();
      const filter = JSON.stringify({investigation: this.investigation, time: null});
      this.es = new EventSource('/monitor/stream?' + new URLSearchParams({filter}));
      this.es.onopen = () => { this.live = true; };
      this.es.onerror = () => { this.live = false; };
      this.es.addEventListener('run_event', () => { clearTimeout(this.newsTimer); this.newsTimer = setTimeout(() => this.loadNews(), 1500); });
    },
    ago(s) {
      const d = (Date.now() - Date.parse(s)) / 6e4;
      if (d < 1) return 'just now';
      if (d < 60) return `${Math.round(d)} min ago`;
      if (d < 24 * 60) return `${plural(Math.round(d / 60), 'hour')} ago`;
      if (d < 48 * 60) return 'yesterday';
      if (d < 7 * 24 * 60) return `${Math.round(d / 1440)} days ago`;
      return fmtDay(s.slice(0, 10));
    },
    // For people at a place in a window: what they did there, where they came from, where they went, and when the record stops.
    comings(here, w) {
      const there = [], from = [], to = [], none = [];
      for (const [k, pts] of Object.entries(here)) {
        const mine = this.byPerson[k], ids = new Set(pts.map(p => p.id)), first = pts[0], last = pts.at(-1);
        const placeIds = new Set(pts.map(p => p.place_id));
        there.push({key: k, name: this.labels[k], text: pts.map(p => `${(PROP[p.prop] || p.prop).replace(/ (at|to)$/, '')}, ${when(p.from, p.to)}`).join('; '), src: this.cites(pts)});
        const before = mine.filter(p => !ids.has(p.id) && p.from < first.from && !placeIds.has(p.place_id)).at(-1);
        if (before) from.push({key: k, name: this.labels[k], text: `${before.place_id ? placeName(this.placeById[before.place_id]) : before.place}, ${when(before.from, before.to)}`, src: this.cites([before])});
        else from.push({key: k, name: this.labels[k], text: 'no earlier record', src: []});
        const after = mine.find(p => !ids.has(p.id) && p.from > last.from && !placeIds.has(p.place_id));
        if (after) to.push({key: k, name: this.labels[k], text: `${PROP[after.prop] || after.prop} ${after.place_id ? placeName(this.placeById[after.place_id]) : after.place}, ${when(after.from, after.to)}`, src: this.cites([after])});
        const end = this.ends[k];
        if (end && day(end.after) >= day(first.from)) none.push({key: k, name: this.labels[k], text: end.label, src: this.cites(mine.filter(p => p.id === end.claim))});
      }
      return {there, from, to, none};
    },
    cites(pts) {
      const out = new Map();
      for (const p of pts) if (p.href && !out.has(p.href)) out.set(p.href, {href: p.href, title: p.file, locked: p.locked});
      return [...out.values()];
    },
    pickBand(id) { this.selected = {kind: 'band', id}; this.showPanel(); },
    pickPoint(p) {
      this.t = Math.round((day(p.from) - this.range[0]) / DAY);
      if (this.span === 'all') this.span = 'month';
      if (p.place_id) { this.selected = {kind: 'place', id: p.place_id}; this.showPanel(); }
    },
    pickPlace(id) { this.selected = {kind: 'place', id}; this.showPanel(); },
    showPanel() { if (this.isMobile) this.$nextTick(() => this.$refs.side?.scrollIntoView({block: 'start', behavior: still() ? 'auto' : 'smooth'})); },
    clear() { this.selected = null; this.ask.answer = null; },
    slide(v) { this.t = v; if (this.span === 'all') this.span = 'month'; },
    play() {
      if (this.playing) { this.stop(); return; }
      if (this.span === 'all') this.span = 'month';
      if (this.t >= this.days) this.t = 0;
      this.playing = true;
      // Reduced motion: the cursor steps a month at a time and nothing glides.
      const step = still() ? 30 : Math.max(1, Math.round(this.days / 240));
      this.timer = setInterval(() => {
        this.t = Math.min(this.days, this.t + step);
        if (this.t >= this.days) this.stop();
      }, still() ? 700 : 50);
      if (!still()) this.animate();
    },
    stop() { clearInterval(this.timer); cancelAnimationFrame(this.frame); this.playing = false; this.drawDots(); },
    async findPath() {
      if (!this.ask.from || !this.ask.to) return;
      this.ask.busy = true; this.ask.answer = null; this.selected = null;
      try {
        this.ask.answer = (await axios.get('/api/case-map/path', {params: {investigation: this.investigation, from: this.ask.from, to: this.ask.to}})).data;
      } catch (err) { this.ask.answer = {found: false, error: this.fail(err), steps: []}; }
      this.ask.busy = false;
      this.showPanel();
    },
    askLabel(v) { return this.askItems.find(i => i.value === v)?.title || ''; },
    initMap() {
      if (this.map || !this.$refs.map) return;
      const c = MAP[this.isDark ? 'dark' : 'light'];
      this.map = new maplibregl.Map({container: this.$refs.map, style: {version: 8, sources: {}, layers: [{id: 'bg', type: 'background', paint: {'background-color': c.bg}}]},
        center: [37.2, 34.8], zoom: 5.6, attributionControl: {compact: true, customAttribution: `Natural Earth, ${this.v?.attribution || 'GeoNames (CC BY 4.0)'}`}, dragRotate: false, pitchWithRotate: false});
      this.map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'bottom-right');
      this.map.touchZoomRotate.disableRotation();
      this.map.on('load', async () => {
        const region = await (await fetch('/static/maplibre/region.geojson')).json();
        const empty = {type: 'FeatureCollection', features: []};
        this.map.addSource('region', {type: 'geojson', data: region});
        this.map.addLayer({id: 'land', type: 'fill', source: 'region', paint: {'fill-color': c.land}});
        this.map.addLayer({id: 'borders', type: 'line', source: 'region', paint: {'line-color': c.border, 'line-width': .8}});
        // Reference layers only: boundaries, uncertainty circles and footprints for what this reader may see.
        addClaimTiles(this.map, {investigation: this.investigation}, {border: c.border, ink: c.seq, muted: c.muted});
        await addBasemap(this.map, {bg: c.bg, land: c.land, border: c.border, muted: c.muted}, 'borders', ['land']);
        this.map.addImage('head', this.arrowHead(c.seq));
        for (const id of ['moves', 'heads', 'places', 'dots']) this.map.addSource(id, {type: 'geojson', data: empty});
        this.map.addLayer({id: 'moves', type: 'line', source: 'moves', layout: {'line-cap': 'round'},
          paint: {'line-color': this.ink(c), 'line-width': ['+', 1, ['*', .8, ['get', 'n']]], 'line-opacity': ['case', ['get', 'now'], .85, .3]}});
        this.map.addLayer({id: 'heads', type: 'symbol', source: 'heads', layout: {'icon-image': 'head', 'icon-rotate': ['get', 'bearing'], 'icon-rotation-alignment': 'map', 'icon-allow-overlap': true, 'icon-size': .55}, paint: {'icon-opacity': ['case', ['get', 'now'], .9, .35]}});
        // An approximate place is drawn at least as large as its uncertainty and never with a hard rim, so it never reads as a point.
        this.map.addLayer({id: 'places', type: 'circle', source: 'places', paint: {
          'circle-radius': ['interpolate', ['exponential', 2], ['zoom'], ...[4, 6, 8, 10, 12, 14].flatMap(z => [z, ['max', ['get', 'r'], ['*', ['get', 'r0'], 2 ** z]]])],
          'circle-color': this.ink(c, '--place'), 'circle-opacity': ['case', ['get', 'approximate'], .16, ['>', ['get', 'n'], 0], .85, .12],
          'circle-stroke-color': this.ink(c, '--place'), 'circle-stroke-width': ['case', ['get', 'selected'], 3, ['get', 'approximate'], .8, 1.5],
          'circle-stroke-opacity': ['case', ['get', 'approximate'], .45, 1]}});
        this.map.addLayer({id: 'dots', type: 'circle', source: 'dots', paint: {'circle-radius': 4, 'circle-color': c.seq, 'circle-stroke-color': c.bg, 'circle-stroke-width': 1.5}});
        this.map.on('click', 'places', e => this.pickPlace(e.features[0].properties.id));
        this.map.on('mouseenter', 'places', () => { this.map.getCanvas().style.cursor = 'pointer'; });
        this.map.on('mouseleave', 'places', () => { this.map.getCanvas().style.cursor = ''; });
        this.ready = true;
        this.drawMap(true);
      });
    },
    // Map paint cannot read CSS variables: the path and kind hues are read once per theme.
    ink(c, kind) {
      const css = getComputedStyle(document.body), hue = n => css.getPropertyValue(n).trim();
      return ['case', ['get', 'path'], hue('--new'), kind ? hue(kind) : c.seq];
    },
    arrowHead(color) {
      const s = 24, cv = document.createElement('canvas'); cv.width = cv.height = s;
      const g = cv.getContext('2d'); g.fillStyle = color; g.beginPath(); g.moveTo(s / 2, 2); g.lineTo(s - 4, s - 4); g.lineTo(s / 2, s - 9); g.lineTo(4, s - 4); g.closePath(); g.fill();
      return g.getImageData(0, 0, s, s);
    },
    restyle() {
      if (!this.ready) return;
      const c = MAP[this.isDark ? 'dark' : 'light'];
      this.map.setPaintProperty('bg', 'background-color', c.bg);
      this.map.setPaintProperty('land', 'fill-color', c.land);
      this.map.setPaintProperty('borders', 'line-color', c.border);
      this.map.setPaintProperty('dots', 'circle-color', c.seq);
      this.map.setPaintProperty('dots', 'circle-stroke-color', c.bg);
      this.map.setPaintProperty('moves', 'line-color', this.ink(c));
      this.map.setPaintProperty('places', 'circle-color', this.ink(c, '--place'));
      this.map.setPaintProperty('places', 'circle-stroke-color', this.ink(c, '--place'));
      this.map.removeImage('head'); this.map.addImage('head', this.arrowHead(c.seq));
      recolor(this.map, {border: c.border, ink: c.seq, muted: c.muted});
      recolorBasemap(this.map, {bg: c.bg, land: c.land, border: c.border, muted: c.muted});
      this.drawMap(false);
    },
    drawMap(fit) {
      if (!this.ready) return;
      const map = this.map, path = this.pathMark;
      const sel = this.selected?.kind === 'place' ? this.selected.id : null;
      const places = (this.v?.places || []).map(p => {
        const n = Object.keys(this.present[p.id] || {}).length;
        // Metres per pixel at zoom 0 for 512 px tiles, at this latitude.
        const r0 = p.approximate ? (p.precision_m || 10000) / (40075016.686 * Math.cos(p.lonlat[1] * Math.PI / 180) / 512) : 0;
        return {type: 'Feature', geometry: {type: 'Point', coordinates: p.lonlat}, properties: {id: p.id, n, r: n ? 6 + 5 * Math.sqrt(n) : 4, r0, approximate: p.approximate, selected: p.id === sel, path: !!path?.places.has(p.id)}};
      });
      map.getSource('places').setData({type: 'FeatureCollection', features: places});
      // One arrow per pair of places, as wide as the people who made that move.
      const agg = new Map(), shown = new Set(this.shownMoves);
      for (const m of this.moves) {
        const k = m.a + '>' + m.b, e = agg.get(k) || {a: m.a, b: m.b, n: 0, now: false, path: false};
        if (shown.has(m)) { e.n += 1; e.now = true; }
        if (path && path.claims.has(m.from.id) && path.claims.has(m.to.id)) e.path = true;
        agg.set(k, e);
      }
      const lines = [], heads = [];
      for (const e of agg.values()) {
        const a = this.placeById[e.a].lonlat, b = this.placeById[e.b].lonlat;
        if (!e.now && this.win) continue;
        lines.push({type: 'Feature', geometry: {type: 'LineString', coordinates: [a, b]}, properties: {n: Math.max(1, e.n), now: e.now || !this.win, path: e.path}});
        const at = [a[0] + (b[0] - a[0]) * .62, a[1] + (b[1] - a[1]) * .62];
        heads.push({type: 'Feature', geometry: {type: 'Point', coordinates: at}, properties: {bearing: this.bearing(a, b), now: e.now || !this.win}});
      }
      map.getSource('moves').setData({type: 'FeatureCollection', features: lines});
      map.getSource('heads').setData({type: 'FeatureCollection', features: heads});
      this.drawLabels();
      this.drawDots();
      if (fit && places.length) {
        const xs = places.map(p => p.geometry.coordinates[0]), ys = places.map(p => p.geometry.coordinates[1]);
        map.fitBounds([[Math.min(...xs) - .5, Math.min(...ys) - .4], [Math.max(...xs) + .5, Math.max(...ys) + .4]], {duration: 0, maxZoom: 9, padding: 40});
      }
    },
    bearing(a, b) {
      const y = lat => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
      return Math.atan2(b[0] - a[0], (y(b[1]) - y(a[1])) * 180 / Math.PI) * 180 / Math.PI;
    },
    // Labels are HTML: the app has no glyph server for map text.
    drawLabels() {
      (this.labelMarks || []).forEach(m => m.remove());
      this.labelMarks = [];
      for (const p of this.v?.places || []) {
        const n = Object.keys(this.present[p.id] || {}).length;
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'cm-lbl' + (n ? '' : ' none');
        el.innerHTML = `<bdi>${esc(placeName(p))}</bdi>${n ? ` <span>${n}</span>` : ''}`;
        el.title = `${placeBoth(p)}: ${plural(n, 'person', 'people')} ${this.win ? 'in ' + this.winLabel : 'over the whole case'}${p.approximate ? `, known only to within ${p.precision_m ? (p.precision_m >= 1000 ? p.precision_m / 1000 + ' km' : p.precision_m + ' m') : 'an area'}` : ''}`;
        el.setAttribute('aria-label', el.title);
        el.onclick = () => this.pickPlace(p.id);
        const near = this.labelMarks.filter(m => { const l = m.getLngLat(); return Math.abs(l.lng - p.lonlat[0]) < .12 && Math.abs(l.lat - p.lonlat[1]) < .12; }).length;
        this.labelMarks.push(new maplibregl.Marker({element: el, anchor: near % 2 ? 'right' : 'left', offset: [near % 2 ? -14 : 14, Math.floor(near / 2) * 20]}).setLngLat(p.lonlat).addTo(this.map));
      }
    },
    // Dots ride the arrows only while the slider plays; a paused or reduced-motion map shows the arrows alone.
    drawDots() {
      if (!this.ready) return;
      const feats = [];
      if (this.playing && !still()) {
        for (const m of this.moves) {
          const leave = Math.min(m.leave, m.arrive - 20 * DAY), f = (this.cursor - leave) / (m.arrive - leave);
          if (f < 0 || f > 1) continue;
          const a = this.placeById[m.a].lonlat, b = this.placeById[m.b].lonlat;
          feats.push({type: 'Feature', geometry: {type: 'Point', coordinates: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]}, properties: {}});
        }
      }
      this.map.getSource('dots').setData({type: 'FeatureCollection', features: feats});
    },
    animate() { this.drawDots(); if (this.playing) this.frame = requestAnimationFrame(() => this.animate()); },
    key(e) {
      if (TYPING(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'p') this.play();
      else if (e.key === 'Escape') this.clear();
    },
  },
  mounted() {
    window.addEventListener('keydown', this.key);
    this.load();
  },
  beforeUnmount() { window.removeEventListener('keydown', this.key); this.stop(); this.es?.close(); },
});
