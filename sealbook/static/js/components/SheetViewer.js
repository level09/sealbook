/* A spreadsheet source as a table, with every claim marked on the cell it was read
   from (and on the exact characters, when the span narrows to part of the cell).
   Rows and columns are numbered as the original file shows them, so a reviewer can
   find the cell there. Clicking a mark selects its claim.

   import {SheetViewer} from '/static/js/components/SheetViewer.js';
   app.component('sheet-viewer', SheetViewer);
   <sheet-viewer :table="t" :claims="claims" :sel="id" @select="..."></sheet-viewer>

   table: {sheet, header: [...], rows: [[...]], total} from GET /api/evidence/<sha>/sheet
   (rows start at file row first_row, the one below the header row). claims: [{id, span: {sheet, row,
   column, start?, end?}, tier, prop, label?}]. status: {row: 'new'|'changed'|...},
   changed: {row: [header, ...]}, only: row numbers to show. Slot `head` renders under
   each header: {column, header}. */
const CSS = `
.sv { position: relative; }
.sv-scroll { overflow: auto; border: 1px solid rgba(var(--v-border-color), .25); border-radius: 6px; }
.sv table { border-collapse: separate; border-spacing: 0; font-size: 13px; min-width: 100%; }
.sv th, .sv td { border-bottom: 1px solid rgba(var(--v-border-color), .18); border-inline-end: 1px solid rgba(var(--v-border-color), .12); padding: 4px 8px; vertical-align: top; text-align: start; }
.sv thead th { position: sticky; top: 0; z-index: 2; background: rgb(var(--v-theme-surface)); font-weight: 600; min-width: 140px; }
.sv thead .sv-letter { display: block; font-weight: 400; font-size: 11px; color: rgba(var(--v-theme-on-surface), .55); }
.sv .sv-n { position: sticky; inset-inline-start: 0; z-index: 1; background: rgb(var(--v-theme-surface)); color: rgba(var(--v-theme-on-surface), .6); font-variant-numeric: tabular-nums; min-width: 56px; white-space: nowrap; }
.sv thead .sv-n { z-index: 3; }
.sv td.sv-cell { max-width: 340px; overflow-wrap: anywhere; }
.sv td.sv-claimed { background: rgba(var(--v-theme-primary), .06); }
.sv td.sv-changed { box-shadow: inset 3px 0 0 rgb(var(--v-theme-warning)); }
.sv tr.sv-on td { background: rgba(var(--v-theme-primary), .1); }
.sv mark { background: none; color: inherit; border-bottom: 2px solid rgb(var(--v-theme-primary)); cursor: pointer; padding: 0 1px; }
.sv mark.tier-unconfirmed { border-bottom-style: dashed; }
.sv mark.tier-accepted { border-bottom-color: rgb(var(--v-theme-success)); }
.sv mark.tier-rejected { border-bottom-color: rgb(var(--v-theme-error)); text-decoration: line-through; }
.sv mark.on { outline: 2px solid rgb(var(--v-theme-primary)); outline-offset: 1px; border-radius: 2px; }
.sv mark:focus-visible { outline: 2px solid rgb(var(--v-theme-on-surface)); }
.sv-status { display: inline-block; margin-inline-start: 6px; font-size: 11px; padding: 0 6px; border-radius: 8px; border: 1px solid currentColor; }
.sv-status.new { color: rgb(var(--v-theme-primary)); }
.sv-status.changed { color: rgb(var(--v-theme-warning)); }
.sv-status.invalid { color: rgb(var(--v-theme-error)); }
.sv-status.unchanged { color: rgba(var(--v-theme-on-surface), .5); }
.sv-more { padding: 8px; font-size: 12px; color: rgba(var(--v-theme-on-surface), .6); }
`;

function css() {
  if (document.getElementById('sheet-viewer-css')) return;
  document.head.append(Object.assign(document.createElement('style'), {id: 'sheet-viewer-css', textContent: CSS}));
}

import {centre, pulse} from './focus.js';

const letter = n => {let s = ''; while (n) {const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = (n - 1 - r) / 26;} return s;};

export const SheetViewer = {
  name: 'SheetViewer',
  props: {
    table: {type: Object, required: true},
    claims: {type: Array, default: () => []},
    sel: {type: String, default: null},
    status: {type: Object, default: () => ({})},
    changed: {type: Object, default: () => ({})},
    only: {type: Array, default: null},
    height: {type: String, default: '520px'},
  },
  emits: ['select'],
  created: css,
  computed: {
    letters() {return this.table.header.map((_, i) => letter(i + 1));},
    // "row:column" -> claims on that cell of this sheet
    byCell() {
      const out = {};
      for (const c of this.claims) {
        const s = c.span;
        if (!s || s.row == null || (s.sheet || 1) !== (this.table.sheet || 1)) continue;
        (out[`${s.row}:${s.column}`] ||= []).push(c);
      }
      return out;
    },
    rows() {
      const keep = this.only ? new Set(this.only) : null;
      return this.table.rows.map((cells, i) => ({n: i + (this.table.first_row || 2), cells})).filter(r => !keep || keep.has(r.n));
    },
    selRow() {return this.claims.find(c => c.id === this.sel)?.span?.row ?? null;},
  },
  watch: {sel() {this.$nextTick(() => centre(this.$el.querySelector('.sv-scroll'), this.$el.querySelector('mark.on')));}},
  methods: {
    // Cell text cut at every claim boundary; a piece carries the claims covering it.
    pieces(text, claims) {
      const cuts = new Set([0, text.length]);
      const ranges = claims.map(c => {const a = c.span.start ?? 0, b = c.span.end ?? text.length; cuts.add(a); cuts.add(b); return {c, a, b};});
      const at = [...cuts].sort((x, y) => x - y);
      return at.slice(0, -1).map((a, i) => {
        const b = at[i + 1], on = ranges.filter(r => r.a <= a && r.b >= b).map(r => r.c);
        return {text: text.slice(a, b), claims: on};
      }).filter(p => p.text);
    },
    title(claims) {return claims.map(c => `${c.label || c.prop} (${c.tier})`).join(', ');},
    tier(claims) {const t = claims.map(c => c.tier); return 'tier-' + (t.includes('accepted') ? 'accepted' : t.every(x => x === 'rejected') ? 'rejected' : 'unconfirmed');},
    // Centre the claim's cell in the table's own scroll box and pulse it.
    reveal(id) {this.$nextTick(() => {const m = this.$el.querySelector('mark.on'); centre(this.$el.querySelector('.sv-scroll'), m); pulse(m?.closest('td'));});},
    pick(claims) {const i = claims.findIndex(c => c.id === this.sel); this.$emit('select', claims[(i + 1) % claims.length].id);},
  },
  template: `<div class="sv">
    <div class="sv-scroll" :style="{maxHeight: height}">
      <v-table density="compact" class="bg-transparent">
        <thead><tr>
          <th class="sv-n" scope="col">Row</th>
          <th v-for="(h, i) in table.header" :key="i" scope="col" dir="auto"><span class="sv-letter">{{ letters[i] }}</span>{{ h }}<slot name="head" :column="i + 1" :header="h"></slot></th>
        </tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.n" :class="{'sv-on': selRow === r.n}">
            <th class="sv-n" scope="row">{{ r.n }}<span v-if="status[r.n]" class="sv-status" :class="status[r.n]">{{ status[r.n] }}</span></th>
            <td v-for="(text, i) in r.cells" :key="i" dir="auto" class="sv-cell" :class="{'sv-claimed': byCell[r.n + ':' + (i + 1)], 'sv-changed': (changed[r.n] || []).includes(table.header[i])}">
              <template v-for="(p, k) in pieces(text, byCell[r.n + ':' + (i + 1)] || [])" :key="k"><mark v-if="p.claims.length" :class="[tier(p.claims), {on: p.claims.some(c => c.id === sel)}]" :title="title(p.claims)" tabindex="0" role="button" :aria-label="title(p.claims) + ': ' + p.text" @click="pick(p.claims)" @keydown.enter.prevent="pick(p.claims)">{{ p.text }}</mark><span v-else>{{ p.text }}</span></template>
            </td>
          </tr>
        </tbody>
      </v-table>
      <div v-if="table.total > table.rows.length" class="sv-more">Showing the first {{ table.rows.length }} of {{ table.total }} rows.</div>
    </div>
  </div>`,
};
