/* Column preview of a sheet: the header found, a few rows, and a field per column
   that the person confirms. Before upload it is the peek of the local file (Add
   evidence); stored, it confirms the acquisition and queues the rows (Add evidence
   after upload, and the evidence page). Suggestions come from header words and
   value shapes first, then the embedding model; each says why, none is confirmed
   until a person confirms it. */

const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
const err = e => e.response?.data?.message || e.response?.data?.description || e.message;

// The per-item state SheetColumns works on.
export const sheetState = name => ({peek: null, map: {}, ok: {}, why: {}, sug: {}, open: true, dataset: null, datasetName: name.replace(/\.[^.]+$/, ''), busy: false, error: '', needReveal: false});

/* A sensitive sheet is read only after the logged reveal, like on its evidence page;
   the person has just looked at its columns, so the reveal is recorded first, then
   the rows are queued. */
export async function confirmRows(it, item) {
  const url = `/api/sheets/${it.acquisition}/confirm`;
  try { return (await axios.post(url, {item})).data; } catch (e) {
    if (e.response?.data?.reason !== 'reveal_required') throw e;
    await axios.post(`/api/evidence/${it.sha}/reveal`, {item: {}});
    return (await axios.post(url, {item})).data;
  }
}

// Field per column from a peek or stored preview: remembered, else the top suggestion
// with its reason. Nothing is marked confirmed unless it was remembered.
export function fill(s, data) {
  for (const c of data.columns) {
    const top = c.suggestions[0];
    s.map[c.header] = c.remembered || top?.field || null;
    if (c.remembered) { s.ok[c.header] = true; s.why[c.header] = 'remembered from the last version'; }
    else if (top) s.sug[c.header] = {why: top.why, by: top.by};
  }
  s.dataset = data.dataset;
  s.peek = data;
}

export const SheetColumns = {
  props: {it: {type: Object, required: true}, fields: {type: Array, default: null}, stored: {type: Boolean, default: false}},
  emits: ['confirmed'],
  computed: {
    s() { return this.it.sheet; },
    p() { return this.s.peek; },
    cols() { return this.p ? this.p.columns : []; },
    options() { return this.fields || (this.p?.fields || []).map(f => ({value: f.key, title: f.label})); },
    allOk() { return this.cols.length > 0 && this.cols.every(c => this.s.ok[c.header] && this.s.map[c.header]); },
    unset() { return this.cols.filter(c => !this.s.map[c.header]).length; },
    key() { return this.cols.find(c => this.s.map[c.header] === 'key')?.header || null; },
    shown() {
      // Dropped columns (no header, no values) are not shown or read.
      const keep = this.p.table.header.map((h, i) => i + 1).filter(n => !this.p.table.dropped.includes(n));
      return keep.map(n => ({n, h: this.p.table.header[n - 1]}));
    },
    messy() {
      const t = this.p.titles, d = this.p.table.dropped.length, out = [];
      if (t) out.push(`Header found on row ${this.p.table.header_row} below ${plural(t, 'title row')}.`);
      if (d) out.push(`${plural(d, 'empty column')} dropped.`);
      return out.join(' ');
    },
    datasetItems() { return [...this.p.datasets.map(d => ({title: `${d.name}, ${plural(d.versions, 'version')}`, value: d.id})), {title: 'New dataset', value: null}]; },
    rtl() { return /[؀-ۿ]/.test(this.p.table.header.join('')); },
    waiting() { return this.cols.filter(c => !this.s.ok[c.header]).length; },
    ready() { return !!this.key && !this.unset && !!(this.s.dataset || this.s.datasetName.trim()); },
  },
  mounted() {
    // A sensitive sheet asks first instead of drawing a refused request.
    if (!this.it.file && !this.s.peek) { if (this.it.sens) this.s.needReveal = true; else this.loadStored(false); }
  },
  methods: {
    plural,
    // A stored sheet has no local file: read the stored columns, which for a
    // sensitive one is the logged reveal, asked for first.
    async loadStored(reveal) {
      this.s.error = '';
      try {
        if (reveal) await axios.post(`/api/evidence/${this.it.sha}/reveal`, {item: {}});
        const {data} = await axios.get(`/api/sheets/${this.it.acquisition}/preview`);
        fill(this.s, {...data, titles: data.table.header_row - 1, people: false});
        this.s.needReveal = false;
      } catch (e) {
        if (e.response?.data?.reason === 'reveal_required') this.s.needReveal = true;
        else this.s.error = `The columns could not be read: ${err(e)}`;
      }
    },
    set(h, f) { this.s.map[h] = f; this.s.ok[h] = true; this.s.why[h] = 'chosen by you'; },
    confirmAll() { this.cols.forEach(c => { if (this.s.map[c.header] && !this.s.ok[c.header]) { this.s.ok[c.header] = true; this.s.why[c.header] = 'confirmed'; } }); },
    async confirm() {
      this.confirmAll();
      this.s.busy = true; this.s.error = '';
      try {
        const item = {dataset: this.s.dataset, dataset_name: this.s.datasetName, key_column: this.key, mapping: {...this.s.map}, sheet: this.p.table.sheet};
        await confirmRows(this.it, item);
        this.it.sheetConfirmed = true;
        this.$emit('confirmed');
      } catch (e) { this.s.error = err(e); }
      this.s.busy = false;
    },
  },
  template: `<div class="sheet sheet-cols" :aria-label="'Columns of ' + it.name">
    <header>
      <template v-if="!p && s.needReveal"><span class="muted">This sheet is sensitive. Showing its columns records an access entry on its custody chain.</span><v-btn size="small" variant="outlined" prepend-icon="ti ti-eye" @click="loadStored(true)">Show the columns</v-btn></template>
      <span v-else-if="!p" class="muted">{{ s.error || (it.file ? 'Reading the columns on this device' : 'Reading the stored columns') }}</span>
      <template v-else>
        <span class="r-chip is-source"><i class="ti ti-table" aria-hidden="true"></i>{{ p.people ? 'Recognised as a list of people' : 'Spreadsheet' }}</span>
        <span><bdi>{{ /^sheet/i.test(p.table.name) ? p.table.name : 'Sheet ' + p.table.name }}</bdi>, {{ plural(p.table.total, 'row') }}, {{ plural(cols.length, 'column') }}</span>
        <span v-if="messy" class="muted">{{ messy }}</span>
        <v-spacer></v-spacer>
        <v-btn size="small" variant="text" :append-icon="s.open ? 'ti ti-chevron-up' : 'ti ti-chevron-down'" @click="s.open = !s.open">{{ s.open ? 'Hide preview' : 'Show preview' }}</v-btn>
      </template>
    </header>
    <template v-if="p && s.open">
      <p class="says">{{ p.people ? 'Each row becomes a proposed person with claims (name, mother\\'s name, place, date) for review. Rows that read like someone already in the case are flagged, never merged. ' : 'Each row is read for people and what happened to them, as proposals for review. ' }}Each column below is suggested with its reason. Check them; nothing is read until you confirm.</p>
      <p v-if="p.suggest_note" class="says"><i class="ti ti-info-circle" aria-hidden="true"></i> {{ p.suggest_note }}</p>
      <div class="ds">
        <v-select v-if="p.datasets.length" v-model="s.dataset" :items="datasetItems" density="compact" variant="outlined" hide-details label="Dataset" prepend-inner-icon="ti ti-stack-2"></v-select>
        <v-text-field v-if="!s.dataset" v-model="s.datasetName" density="compact" variant="outlined" hide-details label="New dataset name" dir="auto"></v-text-field>
        <span class="text-caption muted">{{ s.dataset ? 'Rows are compared with the last version; only new and changed rows are read.' : 'A new dataset remembers these columns for the next version of this sheet.' }}</span>
      </div>
      <div class="scroll" :dir="rtl ? 'rtl' : 'ltr'">
        <table>
          <thead><tr><th v-for="c in shown" :key="c.n" dir="auto">{{ c.h }}</th></tr></thead>
          <tbody>
            <tr class="map"><td v-for="c in shown" :key="c.n" dir="ltr">
              <v-select :model-value="s.map[c.h]" :items="options" density="compact" variant="outlined" hide-details :aria-label="'Column ' + c.h + ' holds'" @update:model-value="v => set(c.h, v)"></v-select>
              <div v-if="s.ok[c.h]" class="note ok"><i class="ti ti-check" aria-hidden="true"></i>{{ s.why[c.h] || 'confirmed' }}</div>
              <div v-else-if="s.map[c.h] && s.sug[c.h]" class="note" :class="s.sug[c.h].by === 'model' ? 'sug' : 'rule'"><i :class="s.sug[c.h].by === 'model' ? 'ti ti-sparkles' : 'ti ti-list-search'" aria-hidden="true"></i><span>suggested, {{ s.sug[c.h].why }}</span></div>
              <div v-else-if="s.map[c.h]" class="note rule"><i class="ti ti-list-search" aria-hidden="true"></i>suggested</div>
              <div v-else class="note due"><i class="ti ti-point" aria-hidden="true"></i>choose what it holds</div>
            </td></tr>
            <tr v-for="(r, ri) in p.table.rows" :key="ri"><td v-for="c in shown" :key="c.n" dir="auto" :class="{ ign: s.map[c.h] === 'ignore', blank: !r[c.n - 1] }">{{ r[c.n - 1] || 'blank' }}</td></tr>
          </tbody>
        </table>
      </div>
      <footer>
        <template v-if="!stored">
          <v-btn size="small" :variant="allOk ? 'outlined' : 'flat'" :color="allOk ? undefined : 'primary'" :prepend-icon="allOk ? 'ti ti-check' : 'ti ti-checks'" :disabled="!!unset" @click="confirmAll">{{ allOk ? 'Columns confirmed' : 'Confirm all ' + plural(cols.length, 'column') }}</v-btn>
          <span class="text-caption muted">{{ unset ? plural(unset, 'column') + ' still need a field. If you add it now, the file is stored and rows wait for you.' : !key ? 'Mark one column as Row identifier, so a later version can be compared row by row.' : allOk ? plural(p.table.total, 'row') + ' will be read after the file is stored.' : plural(waiting, 'column') + ' suggested. Check them, then confirm all. If you add the file first, it is stored and rows wait for you.' }}</span>
        </template>
        <template v-else>
          <v-btn size="small" color="primary" prepend-icon="ti ti-checks" :disabled="!ready" :loading="s.busy" @click="confirm">Confirm all {{ plural(cols.length, 'column') }} and read {{ plural(p.table.total, 'row') }}</v-btn>
          <span class="text-caption muted">{{ unset ? plural(unset, 'column') + ' still need a field.' : !key ? 'Mark one column as Row identifier first.' : !(s.dataset || s.datasetName.trim()) ? 'Name the dataset first.' : 'The file is stored. Rows are read after you confirm.' }}</span>
        </template>
        <span v-if="s.error" class="text-caption err" role="alert">{{ s.error }}</span>
      </footer>
    </template>
  </div>`,
};
