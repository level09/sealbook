/* Add evidence (/ingest/): pick a case, stage files, links, web results, a sheet or a
   voice note with a type, sensitivity and origin each, then watch the same rows go
   through their stages and end on the intake brief.

   The browser takes each file's SHA-256 before upload; the server refuses the bytes
   if its own differs. Stage states come from GET /ingest/batch/<id>. */
import {IntakeBrief} from '/static/js/components/IntakeBrief.js';

const KIND = {
  image: {label: 'Image', icon: 'ti ti-photo', read: 'text'},
  pdf: {label: 'PDF', icon: 'ti ti-file-type-pdf', read: 'pages'},
  audio: {label: 'Audio', icon: 'ti ti-volume', read: 'speech'},
  video: {label: 'Video', icon: 'ti ti-video', read: 'speech'},
  web: {label: 'Web page', icon: 'ti ti-world', read: 'text'},
  sheet: {label: 'Spreadsheet', icon: 'ti ti-table', read: 'rows'},
  text: {label: 'Text', icon: 'ti ti-file-text', read: 'text'},
  file: {label: 'File', icon: 'ti ti-file', read: 'contents'},
};
const TONE = {run: 'ti ti-loader-2', wait: 'ti ti-clock', you: 'ti ti-hand-finger', fail: 'ti ti-alert-circle', dup: 'ti ti-copy', ok: 'ti ti-check'};
const ICON = {done: 'ti ti-check', run: 'ti ti-loader-2', wait: 'ti ti-clock', fail: 'ti ti-x', skip: '', todo: ''};
const WORD = {done: 'done', run: 'working', wait: 'waiting for a model', fail: 'refused', skip: 'not needed', todo: 'not started'};
// Server read states to track states.
const READ = {done: 'done', running: 'run', queued: 'todo', failed: 'fail', skipped: 'skip', waiting: 'wait'};
// The file a capture of each result kind is about: the rest (screenshot, network log)
// are kept with it but do not get rows of their own.
const PRIMARY = {web: 'page', images: 'media', videos: 'video'};
const SHEET = /\.(csv|xlsx)$/i;

// Why confirmed rows were not queued, in words (the server's reason names routes).
const notRead = data => data.queued ? '' : /^no new/.test(data.reason || '') ? 'Every row is already in the last version, so there is nothing new to read.' : 'Waiting for a model: none on this server may read these rows. The file is stored and nothing is lost.';
/* Confirm a stored sheet's columns. A sensitive sheet is read only after the logged
   reveal, like on its evidence page; the person has just looked at its columns here,
   so the reveal is recorded first, then the rows are queued. */
async function confirmRows(it, item) {
  const url = `/api/sheets/${it.acquisition}/confirm`;
  try { return (await axios.post(url, {item})).data; } catch (e) {
    if (e.response?.data?.reason !== 'reveal_required') throw e;
    await axios.post(`/api/evidence/${it.sha}/reveal`, {item: {}});
    return (await axios.post(url, {item})).data;
  }
}
// Field per column from a peek or stored preview: remembered, else the top suggestion.
function fill(s, data) {
  for (const c of data.columns) {
    s.map[c.header] = c.remembered || c.suggestions[0]?.field || null;
    if (c.remembered) { s.ok[c.header] = true; s.why[c.header] = 'remembered'; }
  }
  s.dataset = data.dataset;
  s.peek = data;
}
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
const size = n => n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(0.1, n / 1e3).toFixed(1)} KB`;
const err = e => e.response?.data?.message || e.response?.data?.description || e.message;

function kindOf(name, mime) {
  if (SHEET.test(name) || /csv|spreadsheetml/.test(mime)) return 'sheet';
  if (/^image\//.test(mime)) return 'image';
  if (/pdf/.test(mime) || /\.pdf$/i.test(name)) return 'pdf';
  if (/^audio\//.test(mime)) return 'audio';
  if (/^video\//.test(mime)) return 'video';
  if (/html/.test(mime)) return 'web';
  if (/^text\//.test(mime)) return 'text';
  return 'file';
}

async function sha256(file) {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

/* Column preview of a sheet: the header found, a few rows, and a field per column
   that the person confirms. Before upload it is the peek of the local file; after,
   it confirms the stored acquisition and queues the rows. */
const SheetColumns = {
  props: {it: {type: Object, required: true}, fields: {type: Array, required: true}, stored: {type: Boolean, default: false}},
  emits: ['confirmed'],
  computed: {
    s() { return this.it.sheet; },
    p() { return this.s.peek; },
    cols() { return this.p ? this.p.columns : []; },
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
  },
  mounted() {
    // A sensitive sheet asks first instead of drawing a refused request.
    if (!this.it.file && !this.s.peek) { if (this.it.sens) this.s.needReveal = true; else this.loadStored(false); }
  },
  methods: {
    plural,
    // A reopened batch has no local file: read the stored sheet, which for a
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
    set(h, f) { this.s.map[h] = f; this.s.ok[h] = true; },
    confirmAll() { this.cols.forEach(c => { if (this.s.map[c.header]) this.s.ok[c.header] = true; }); },
    async confirm() {
      this.s.busy = true; this.s.error = '';
      try {
        const item = {dataset: this.s.dataset, dataset_name: this.s.datasetName, key_column: this.key, mapping: {...this.s.map}, sheet: this.p.table.sheet};
        const data = await confirmRows(this.it, item);
        this.it.sheetConfirmed = true;
        this.it.sheetReason = notRead(data);
        this.$emit('confirmed');
      } catch (e) { this.s.error = err(e); }
      this.s.busy = false;
    },
  },
  template: `<div class="sheet" :aria-label="'Preview of ' + it.name">
    <header>
      <template v-if="!p && s.needReveal"><span class="muted">This sheet is sensitive. Showing its columns records an access entry on its custody chain.</span><v-btn size="small" variant="outlined" prepend-icon="ti ti-eye" @click="loadStored(true)">Show the columns</v-btn></template>
      <span v-else-if="!p" class="muted">{{ s.error || (it.file ? 'Reading the columns on this device' : 'Reading the stored columns') }}</span>
      <template v-else>
        <span class="r-chip is-source"><i class="ti ti-table" aria-hidden="true"></i>{{ p.people ? 'Recognised as a list of people' : 'Spreadsheet' }}</span>
        <span>Sheet <bdi>{{ p.table.name }}</bdi>, {{ plural(p.table.total, 'row') }}, {{ plural(cols.length, 'column') }}</span>
        <span v-if="messy" class="muted">{{ messy }}</span>
        <v-spacer></v-spacer>
        <v-btn size="small" variant="text" :append-icon="s.open ? 'ti ti-chevron-up' : 'ti ti-chevron-down'" @click="s.open = !s.open">{{ s.open ? 'Hide preview' : 'Show preview' }}</v-btn>
      </template>
    </header>
    <template v-if="p && s.open">
      <p class="says">{{ p.people ? 'Each row becomes a proposed person with claims (name, mother\\'s name, place, date) for review. Rows that read like someone already in the case are flagged, never merged. ' : 'Each row is read for people and what happened to them, as proposals for review. ' }}Say what each column holds; nothing is read until you confirm.</p>
      <p v-if="p.suggest_error" class="says" style="color: var(--due)">Suggestions are unavailable ({{ p.suggest_error }}). Choose each column by hand.</p>
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
              <v-select :model-value="s.map[c.h]" :items="fields" density="compact" variant="outlined" hide-details :aria-label="'Column ' + c.h + ' holds'" @update:model-value="v => set(c.h, v)"></v-select>
              <div v-if="s.ok[c.h]" class="note ok"><i class="ti ti-check" aria-hidden="true"></i>{{ s.why[c.h] || 'confirmed' }}</div>
              <div v-else-if="s.map[c.h]" class="note sug"><i class="ti ti-sparkles" aria-hidden="true"></i>suggested</div>
              <div v-else class="note" style="color: var(--due)"><i class="ti ti-point" aria-hidden="true"></i>choose what it holds</div>
            </td></tr>
            <tr v-for="(r, ri) in p.table.rows" :key="ri"><td v-for="c in shown" :key="c.n" dir="auto" :class="{ ign: s.map[c.h] === 'ignore', blank: !r[c.n - 1] }">{{ r[c.n - 1] || 'blank' }}</td></tr>
          </tbody>
        </table>
      </div>
      <footer>
        <template v-if="!stored">
          <v-btn size="small" :variant="allOk ? 'outlined' : 'flat'" :color="allOk ? undefined : 'primary'" :prepend-icon="allOk ? 'ti ti-check' : 'ti ti-checks'" :disabled="!!unset" @click="confirmAll">{{ allOk ? 'Columns confirmed' : 'Confirm all ' + plural(cols.length, 'column') }}</v-btn>
          <span class="text-caption muted">{{ unset ? plural(unset, 'column') + ' still need a field. If you add it now, the file is stored and rows wait for you.' : !key ? 'Mark one column as Row identifier, so a later version can be compared row by row.' : allOk ? plural(p.table.total, 'row') + ' will be read after the file is stored.' : plural(waiting, 'column') + ' suggested by the local model. If you add it now, the file is stored and rows wait for you.' }}</span>
        </template>
        <template v-else>
          <v-btn size="small" color="primary" prepend-icon="ti ti-player-play" :disabled="!key || !!unset || !(s.dataset || s.datasetName.trim())" :loading="s.busy" @click="confirmAll(); confirm()">Confirm the columns and read {{ plural(p.table.total, 'row') }}</v-btn>
          <span class="text-caption muted">{{ unset ? plural(unset, 'column') + ' still need a field.' : !key ? 'Mark one column as Row identifier first.' : 'The file is stored. Rows wait for you.' }}</span>
        </template>
        <span v-if="s.error" class="text-caption" style="color: var(--negative)" role="alert">{{ s.error }}</span>
      </footer>
    </template>
  </div>`,
};

export function mount(boot) {
  const app = Vue.createApp({
    mixins: [layoutMixin], delimiters: config.delimiters,
    data() {
      return {
        KIND, TONE, ICON, WORD, DOCS: boot.doc_types, FIELDS: boot.fields,
        cases: [], caseMenu: false, caseQ: '', created: [], nc: {open: false, name: '', description: '', busy: false, error: ''},
        items: [], batch: null, started: false, status: null, timer: null, overWell: false, dragging: 0,
        smart: '', origin: '', note: '', noteOpen: false,
        search: {open: false, q: '', sent: '', tab: 'web', groups: null, picked: [], busy: false, error: '', proposals: [], status: null},
        rec: {on: false, s: 0, lv: Array(18).fill(4), t: null, media: null, chunks: [], stream: null, analyser: null},
        toast: {show: false, text: '', error: false},
        mac: /Mac|iPhone|iPad/.test(navigator.platform),
        canRecord: !!(navigator.mediaDevices && window.MediaRecorder),
      };
    },
    computed: {
      cur() { return this.cases.find(c => c.slug === this.caseSel) || null; },
      caseSlug() { return this.cur?.slug || ''; },
      caseHits() { const q = this.caseQ.trim().toLowerCase(); return this.cases.filter(c => !q || `${c.name} ${c.description || ''}`.toLowerCase().includes(q)); },
      ncTaken() { const n = this.nc.name.trim().toLowerCase(); return this.cases.some(c => c.name.toLowerCase() === n || c.slug.toLowerCase() === n); },
      ncClose() {
        const n = this.nc.name.trim().toLowerCase();
        if (n.length < 4) return null;
        return this.cases.map(c => ({c, d: lev(c.name.toLowerCase(), n)})).filter(x => x.d <= 2 || x.c.name.toLowerCase().includes(n) || n.includes(x.c.name.toLowerCase())).sort((a, b) => a.d - b.d)[0]?.c || null;
      },
      recentOrigins() { return (this.cur?.origins || []).map(o => o.startsWith('device:') ? o.slice(7) : o); },
      smartLinks() { return this.smart.split(/\s+/).filter(w => /^https?:\/\/\S+\.\S+/i.test(w)); },
      smartIsLink() { return this.smartLinks.length > 0; },
      selN() { return this.items.filter(i => i.sel).length; },
      sensN() { return this.items.filter(i => i.sens).length; },
      ready() { return this.items.every(i => !i.file || i.sha); },
      phase() { return !this.started ? 'compose' : this.finished ? 'done' : 'running'; },
      finished() { return !!this.status?.done && this.items.every(i => this.view(i).end); },
      composeHint() {
        const k = {};
        this.items.forEach(i => { const l = KIND[i.kind].label.toLowerCase(); k[l] = (k[l] || 0) + 1; });
        return Object.entries(k).map(([l, n]) => plural(n, l)).join(', ');
      },
      tally() {
        const t = {done: 0, run: 0, wait: 0, you: 0, dup: 0, fail: 0, retry: 0};
        this.items.forEach(i => { const v = this.view(i), k = v.tone === 'ok' ? 'done' : v.tone === 'fail' && v.kept ? 'retry' : v.tone; t[k]++; });
        return t;
      },
      listTitle() {
        const n = this.items.length, c = this.cur?.name || '';
        if (this.phase === 'compose') return `${plural(n, 'item')} ready to add`;
        if (this.phase === 'running') return `Adding ${plural(n, 'item')} to ${c}`;
        return `Added ${this.items.filter(i => i.acquisition).length} of ${plural(n, 'item')} to ${c}`;
      },
      claimsN() { return this.items.reduce((n, i) => n + (i.dup ? 0 : i.server?.claims || 0), 0); },
      storedN() { return this.items.filter(i => i.acquisition && !i.dup).length; },
      searchBlocked() {
        if (!this.cur) return 'Choose a case first: each search is logged on the case it is for.';
        const st = this.search.status;
        if (!st) return '';
        if (st.missing.search.length) return `Web search is not set up on this server (${st.missing.search.join(', ')}).`;
        return st.refusal || '';
      },
    },
    watch: {
      caseSel() { if (this.search.open) this.loadSearch(); },
    },
    async mounted() {
      if (this.caseSel === 'all') this.caseSel = null;
      await this.loadCases();
      const reopen = new URLSearchParams(location.search).get('batch');
      if (reopen) await this.reopen(reopen);
      window.addEventListener('dragenter', this.onDragEnter);
      window.addEventListener('dragleave', this.onDragLeave);
      window.addEventListener('dragover', this.onDragOver);
      window.addEventListener('drop', this.onWindowDrop);
      document.addEventListener('paste', this.onPaste);
    },
    beforeUnmount() {
      clearTimeout(this.timer);
      window.removeEventListener('dragenter', this.onDragEnter);
      window.removeEventListener('dragleave', this.onDragLeave);
      window.removeEventListener('dragover', this.onDragOver);
      window.removeEventListener('drop', this.onWindowDrop);
      document.removeEventListener('paste', this.onPaste);
    },
    methods: {
      plural, size,
      say(text, error = false) { this.toast = {show: true, text, error}; },
      // Shell contract: the switcher picks the case while nothing has been added yet.
      useCase(v) { if (!this.started) this.caseSel = v; },
      async loadCases() {
        try {
          const {data} = await axios.get('/api/cases');
          this.cases = [...data.items.map(c => ({...c, fresh: this.created.includes(c.slug)}))];
        } catch (e) { this.say(err(e), true); }
      },
      docTitle(v) { return this.DOCS.find(d => d.value === v)?.title || v; },
      ago(iso) {
        const s = (Date.now() - new Date(iso)) / 1000;
        if (s < 90) return 'just now';
        if (s < 3600) return `${Math.round(s / 60)} min ago`;
        if (s < 86400 * 2) return `${Math.round(s / 3600)} h ago`;
        return `${Math.round(s / 86400)} days ago`;
      },
      caseMeta(c) {
        if (c.fresh && !c.sources) return c.description || 'No sources yet';
        const parts = [plural(c.sources, 'source'), plural(c.people, 'person', 'people')];
        return parts.join(', ') + (c.last_added ? `, last added ${this.ago(c.last_added)}` : '');
      },
      originWords(o) { return (o || '').replace(/^(device|url):/, ''); },
      quoted(q) { return /^".*"$/.test(q) ? q : `"${q}"`; },
      // Providers send ISO stamps or words ("3 days ago"); only the first is reformatted.
      day(d) { return /^\d{4}-\d\d-\d\d/.test(d) ? new Date(d).toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric'}) : d; },
      shortUrl(u) { return (u || '').replace(/^https?:\/\//, '').replace(/\/$/, ''); },
      clock(s) { return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; },
      pickCase(c) { this.switchCase(c.slug); this.caseMenu = false; this.caseQ = ''; },
      openNewCase() { this.nc = {open: true, name: this.caseHits.length ? '' : this.caseQ.trim(), description: '', busy: false, error: ''}; this.caseMenu = false; },
      async createCase() {
        this.nc.busy = true; this.nc.error = '';
        try {
          // The person has seen the close-name warning, so creating anyway is on purpose.
          const {data} = await axios.post('/api/cases', {item: {name: this.nc.name, description: this.nc.description, confirm: !!this.ncClose}});
          this.created.push(data.item.slug);
          await this.loadCases();
          this.caseList = [...new Set([...this.caseList, data.item.slug])].sort();
          this.switchCase(data.item.slug);
          this.nc.open = false;
          this.say(`Created ${data.item.name}`);
        } catch (e) { this.nc.error = err(e); }
        this.nc.busy = false;
      },

      // --- staging ------------------------------------------------------------
      make(o) {
        return Vue.reactive({id: crypto.randomUUID(), sel: false, sens: true, origin: '', docBy: 'rule', doc: 'unknown', docWhy: '', sha: '', thumb: null,
          up: null, upError: '', acquisition: null, checked: false, dup: false, original: null, server: null, capture: null, sheetConfirmed: false, sheetReason: '', ...o});
      },
      chosen(e) { this.addFiles([...e.target.files]); e.target.value = ''; },
      addFiles(files, extra = {}) {
        const fresh = files.filter(f => !this.items.some(i => i.file && i.file.name === f.name && i.file.size === f.size));
        const made = fresh.map(f => {
          const kind = kindOf(f.name, f.type);
          return this.make({file: f, name: f.name, mime: f.type, kind, thumb: kind === 'image' ? URL.createObjectURL(f) : null,
            sheet: kind === 'sheet' ? {peek: null, map: {}, ok: {}, why: {}, open: true, dataset: null, datasetName: f.name.replace(/\.[^.]+$/, ''), busy: false, error: '', needReveal: false} : null, ...extra});
        });
        this.items.push(...made);
        // Look up by id after each await: the list holds proxies.
        const ids = made.map(m => m.id), find = id => this.items.find(i => i.id === id);
        made.forEach(m => sha256(m.file).then(h => { const it = find(m.id); if (it) it.sha = h; }));
        const plain = made.filter(m => m.kind !== 'sheet' && !extra.doc);
        if (plain.length) axios.post('/ingest/suggest', {items: plain.map(m => ({name: m.name, mime: m.mime}))}).then(({data}) => data.items.forEach((s, n) => {
          const it = find(plain[n].id);
          if (it && it.docBy !== 'you') { it.doc = s.doc_type; it.docWhy = s.why; }
        })).catch(e => this.say(err(e), true));
        made.filter(m => m.kind === 'sheet').forEach(m => this.peek(m.id));
        if (this.started) ids.forEach(id => this.send(find(id)));
        return made;
      },
      async peek(id) {
        const it = this.items.find(i => i.id === id), form = new FormData();
        form.append('file', it.file);
        if (this.caseSlug) form.append('investigation', this.caseSlug);
        try {
          const {data} = await axios.post('/ingest/sheet/peek', form);
          const s = this.items.find(i => i.id === id)?.sheet;
          if (!s) return;
          fill(s, data);
          const item = this.items.find(i => i.id === id);
          if (item.docBy !== 'you') { item.doc = data.suggestion.doc_type; item.docWhy = data.suggestion.why; }
        } catch (e) {
          const s = this.items.find(i => i.id === id)?.sheet;
          if (s) s.error = `The columns could not be read: ${err(e)}`;
        }
      },
      addLinks(urls) {
        urls.forEach(u => {
          if (this.items.some(i => i.url === u)) return;
          const v = /\.(webm|mp4|mov)(\?|$)/i.test(u), im = /\.(jpe?g|png|webp|gif)(\?|$)/i.test(u);
          const it = this.make({url: u, name: this.shortUrl(u).slice(0, 90), kind: v ? 'video' : im ? 'image' : 'web', doc: v ? 'video' : im ? 'photo' : 'web_post', docWhy: 'Suggested from the address'});
          this.items.push(it);
          if (this.started) this.send(this.items.at(-1));
        });
      },
      remove(it) { if (it.thumb?.startsWith('blob:')) URL.revokeObjectURL(it.thumb); this.items = this.items.filter(i => i !== it); },
      bulk(what, v) {
        const sel = this.items.filter(i => i.sel);
        if (what === 'remove') { sel.forEach(i => this.remove(i)); return; }
        sel.forEach(i => { if (what === 'doc') { i.doc = v; i.docBy = 'you'; } else i.sens = v; });
        this.say(`${plural(sel.length, 'item')} updated`);
      },
      submitSmart() {
        const t = this.smart.trim();
        if (!t) return;
        if (this.smartIsLink) { this.addLinks(this.smartLinks); this.smart = ''; }
        else { this.openSearch(); this.search.q = t; this.smart = ''; this.runSearch({text: t}); }
      },

      // --- web search ------------------------------------------------------------
      async openSearch() {
        this.search.open = true;
        await this.loadSearch();
        this.$nextTick(() => document.getElementById('web-q')?.focus());
      },
      async loadSearch() {
        this.search = {...this.search, status: null, proposals: [], groups: null, picked: [], sent: '', error: ''};
        if (!this.cur) return;
        try {
          this.search.status = (await axios.get('/api/research/status', {params: {investigation: this.caseSlug}})).data;
          if (this.searchBlocked) return;
          this.search.proposals = (await axios.post('/api/research/queries', {item: {scope: `case:${this.caseSlug}`}})).data.queries.slice(0, 4);
        } catch (e) { this.search.error = err(e); }
      },
      async runSearch(q) {
        if (!this.search.status) await this.loadSearch();
        if (this.searchBlocked || !q.text.trim()) return;
        this.search.busy = true; this.search.error = '';
        try {
          const query = {text: q.text.trim(), kinds: ['web', 'images', 'videos'], why: q.why || [], people: q.people || []};
          const {data} = await axios.post('/api/research/search', {item: {scope: `case:${this.caseSlug}`, investigation: this.caseSlug, queries: [query]}});
          this.search.q = query.text;
          this.search.sent = query.text;
          this.search.groups = data.groups;
          this.search.picked = [];
          this.search.tab = ['web', 'images', 'videos'].find(k => data.groups[k].length) || 'web';
          const failed = data.sent.filter(s => s.error);
          if (failed.length) this.search.error = failed.map(s => `${s.kind}: ${s.error}`).join('; ');
        } catch (e) { this.search.error = err(e); }
        this.search.busy = false;
      },
      isPicked(r) { return this.search.picked.includes(r.id); },
      toggle(r) { const p = this.search.picked; p.includes(r.id) ? p.splice(p.indexOf(r.id), 1) : p.push(r.id); },
      addPicked() {
        const all = Object.values(this.search.groups).flat();
        this.search.picked.map(id => all.find(r => r.id === id)).forEach(r => {
          if (this.items.some(i => i.result?.id === r.id)) return;
          const kind = {web: 'web', images: 'image', videos: 'video'}[r.kind];
          this.items.push(this.make({result: r, url: r.url, name: r.title || this.shortUrl(r.url), kind, thumb: r.thumb ? `/api/research/thumb/${r.id}` : null,
            doc: {web: 'web_post', images: 'photo', videos: 'video'}[r.kind], docWhy: 'Suggested from the result type'}));
        });
        this.say(`${plural(this.search.picked.length, 'result')} added to the list`);
        this.search.open = false;
      },

      // --- voice note ---------------------------------------------------------
      async startRec() {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({audio: true});
          const media = new MediaRecorder(stream), chunks = [];
          media.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
          const ctx = new AudioContext(), analyser = ctx.createAnalyser();
          analyser.fftSize = 64;
          ctx.createMediaStreamSource(stream).connect(analyser);
          const bins = new Uint8Array(analyser.frequencyBinCount);
          media.start();
          const t0 = Date.now();
          this.rec = {on: true, s: 0, lv: Array(18).fill(4), media, chunks, stream, ctx, t: setInterval(() => {
            analyser.getByteFrequencyData(bins);
            this.rec.s = Math.floor((Date.now() - t0) / 1000);
            this.rec.lv = this.rec.lv.map((_, i) => 4 + Math.round((bins[i] || 0) / 255 * 16));
          }, 120)};
        } catch (e) { this.say(`The microphone could not be opened: ${e.message}`, true); }
      },
      stopRec(keep) {
        const r = this.rec;
        clearInterval(r.t);
        r.media.onstop = () => {
          r.stream.getTracks().forEach(t => t.stop());
          r.ctx.close();
          if (!keep) return;
          const type = r.media.mimeType || 'audio/webm', ext = /mp4/.test(type) ? 'm4a' : /ogg/.test(type) ? 'ogg' : 'webm';
          const name = `voice_note_${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')}.${ext}`;
          this.addFiles([new File(r.chunks, name, {type: type.split(';')[0]})], {doc: 'voice_note', docWhy: 'Recorded here', docBy: 'rule'});
        };
        r.media.stop();
        this.rec = {...this.rec, on: false};
      },

      // --- drop and paste -------------------------------------------------------
      onDragEnter(e) { if ([...(e.dataTransfer?.types || [])].includes('Files')) this.dragging++; },
      onDragLeave() { this.dragging = Math.max(0, this.dragging - 1); },
      onDragOver(e) { e.preventDefault(); },
      onWindowDrop(e) {
        e.preventDefault(); this.dragging = 0;
        const files = [...(e.dataTransfer?.files || [])];
        if (files.length && (this.phase !== 'done')) this.addFiles(files);
      },
      onDrop(e) { this.overWell = false; this.dragging = 0; this.addFiles([...(e.dataTransfer?.files || [])]); },
      onPaste(e) {
        if (this.phase === 'done') return;
        const files = [...(e.clipboardData?.files || [])];
        if (files.length) { e.preventDefault(); this.addFiles(files); this.say(`${plural(files.length, 'file')} pasted`); return; }
        const tag = e.target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA') return;
        const t = e.clipboardData?.getData('text') || '';
        if (!t.trim()) return;
        e.preventDefault(); this.smart = t.trim(); this.$nextTick(() => this.submitSmart());
      },

      // --- sending and progress ---------------------------------------------------
      originFields(it) {
        const o = (it.origin || this.origin || '').trim(), m = o.match(/^(device|channel):(.*)$/);
        return m ? [m[1], m[2]] : ['device', o || 'upload'];
      },
      async start() {
        if (!this.cur || !this.ready) return;
        this.batch = crypto.randomUUID();
        this.started = true;
        this.items.forEach(i => { i.sel = false; });
        window.scrollTo({top: 0, behavior: 'smooth'});
        for (const it of [...this.items]) await this.send(it);
        this.poll();
      },
      addMore(e) { this.addFiles([...e.target.files]); e.target.value = ''; },
      async send(it) {
        const id = it.id, find = () => this.items.find(i => i.id === id);
        it.up = 'run'; it.upError = '';
        const common = {investigation: this.caseSlug, batch: this.batch, doc_type: it.doc, sensitivity: it.sens ? 'sensitive' : 'ordinary'};
        try {
          if (it.result) {
            const {data} = await axios.post('/api/research/capture', {item: {ids: [it.result.id], ...common}});
            Object.assign(find(), {capture: data.runs[0], up: 'done'});
          } else if (it.url) {
            const {data} = await axios.post('/ingest/url', {item: {url: it.url, note: this.note, ...common}});
            Object.assign(find(), {acquisition: data.acquisition_id, sha: data.sha256, dup: !data.new_blob, up: 'done'});
          } else {
            const form = new FormData(), [kind, origin] = this.originFields(it);
            form.append('file', it.file);
            Object.entries({...common, origin_kind: kind, origin, note: this.note, sha256: it.sha}).forEach(([k, v]) => v && form.append(k, v));
            const {data} = await axios.post('/ingest/file', form), r = data.items[0];
            Object.assign(find(), {acquisition: r.acquisition_id, sha: r.sha256, dup: !r.new_blob, checked: true, up: 'done'});
            const cur = find();
            if (cur.sheet?.peek && !cur.dup && Object.keys(cur.sheet.map).length && cur.sheet.peek.columns.every(c => cur.sheet.ok[c.header])) await this.confirmSheet(cur);
          }
        } catch (e) { Object.assign(find(), {up: 'fail', upError: err(e) || 'The upload did not finish. Check the connection and try again.'}); }
        if (this.started) this.poll();
      },
      // /ingest/?batch=<id>: the rows of a batch added earlier, from the server alone.
      async reopen(batch) {
        try {
          const {data} = await axios.get(`/ingest/batch/${batch}`);
          const sheet = name => ({peek: null, map: {}, ok: {}, why: {}, open: true, dataset: null, datasetName: name.replace(/\.[^.]+$/, ''), busy: false, error: '', needReveal: false});
          const caseOf = data.items.find(i => i.investigation)?.investigation;
          data.captures.forEach(c => this.items.push(this.make({result: {id: c.run, kind: c.kind, url: c.page}, url: c.page, name: c.title || this.shortUrl(c.page),
            kind: {web: 'web', images: 'image', videos: 'video'}[c.kind] || 'web', doc: c.doc_type, sens: c.sensitivity === 'sensitive', capture: c.run, up: 'done'})));
          data.items.filter(i => !i.capture).forEach(i => {
            const kind = kindOf(i.name, i.mime);
            // Tiles of sensitive sources wait for a reveal, so those keep the kind icon.
            const thumb = i.sensitivity === 'sensitive' ? null : `/api/evidence/${i.sha256}/thumb?size=160`;
            this.items.push(this.make({name: i.name, mime: i.mime, kind, thumb, doc: i.doc_type, sens: i.sensitivity === 'sensitive', acquisition: i.acquisition, sha: i.sha256, up: 'done',
              sheet: kind === 'sheet' ? sheet(i.name) : null}));
          });
          if (caseOf) this.caseSel = caseOf;
          this.batch = batch;
          this.started = true;
          this.poll();
        } catch (e) { this.say(err(e), true); }
      },
      async confirmSheet(it) {
        const s = it.sheet, key = s.peek.columns.find(c => s.map[c.header] === 'key')?.header;
        if (!key) return;
        try {
          const data = await confirmRows(it, {dataset: s.dataset, dataset_name: s.datasetName, key_column: key, mapping: {...s.map}, sheet: s.peek.table.sheet});
          it.sheetConfirmed = true;
          it.sheetReason = notRead(data);
        } catch (e) { s.error = err(e); }
      },
      async poll() {
        clearTimeout(this.timer);
        if (!this.batch) return;
        try {
          const {data} = await axios.get(`/ingest/batch/${this.batch}`);
          this.status = data;
          const byAcq = Object.fromEntries(data.items.map(s => [s.acquisition, s]));
          for (const it of this.items) {
            if (it.capture) {
              const c = data.captures.find(x => x.run === it.capture);
              it.server = null;
              it.captureState = c;
              const primary = data.items.find(s => s.capture === it.capture && s.role === PRIMARY[it.result.kind]);
              if (primary) Object.assign(it, {acquisition: primary.acquisition, sha: primary.sha256, dup: primary.duplicate, original: primary.original});
            }
            const s = it.acquisition && byAcq[it.acquisition];
            if (s) Object.assign(it, {server: s, dup: s.duplicate, original: s.original, sheetConfirmed: it.sheetConfirmed || s.sheet_confirmed});
          }
        } catch (e) { this.say(err(e), true); }
        if (!this.finished) this.timer = setTimeout(() => this.poll(), 1500);
      },
      async retry(it) {
        if (it.up === 'fail') return this.send(it);
        const runs = it.captureState?.status === 'failed' ? [it.captureState.run] : (it.server?.failures || []).filter(f => f.retryable).map(f => f.run);
        try {
          for (const r of runs) await axios.post(`/ingest/run/${r}/retry`, {item: {}});
          this.poll();
        } catch (e) { this.say(err(e), true); }
      },
      reset() {
        clearTimeout(this.timer);
        this.items.forEach(i => { if (i.thumb?.startsWith('blob:')) URL.revokeObjectURL(i.thumb); });
        Object.assign(this, {items: [], batch: null, started: false, status: null, note: '', noteOpen: false});
        this.loadCases();
      },

      // --- what each row says -------------------------------------------------------
      stagesOf(it) {
        const v = this.view(it), out = [];
        if (it.result) out.push({k: 'captured', label: 'captured', st: v.captured});
        else if (it.url) out.push({k: 'captured', label: 'fetched', st: v.fp === 'todo' && it.up === 'run' ? 'run' : v.fp});
        out.push({k: 'hashed', label: 'fingerprint', st: v.fp}, {k: 'stored', label: 'stored', st: v.stored},
          {k: 'read', label: KIND[it.kind].read, st: v.read}, {k: 'claims', label: 'claims', st: v.claims}, {k: 'linked', label: 'linked', st: v.linked});
        return out;
      },
      view(it) {
        const s = it.server, c = it.captureState, todo = {fp: 'todo', stored: 'todo', read: 'todo', claims: 'todo', linked: 'todo', captured: 'todo'};
        if (it.up === 'fail') return {...todo, fp: it.result ? 'todo' : 'fail', captured: 'fail', tone: 'fail', msg: it.upError, end: true};
        if (it.result) {
          if (!c) return {...todo, captured: it.up === 'run' ? 'run' : 'todo', tone: 'run', msg: 'Sending to the capture browser on this server', end: false};
          if (c.status === 'failed') return {...todo, captured: 'fail', tone: 'fail', msg: `The capture did not finish: ${c.error || 'no reason was given'}. Nothing was stored, so there is nothing to fingerprint.`, end: true};
          if (c.status !== 'done') return {...todo, captured: c.status === 'running' ? 'run' : 'todo', tone: 'run', msg: c.status === 'running' ? 'Capturing the page with the browser on this server' : 'Waiting for the capture browser on this server', end: false};
          if (!s) return {...todo, captured: 'done', tone: 'run', msg: 'Captured. Storing what the browser saw', end: false};
        }
        if (!s) return {...todo, fp: it.up === 'run' ? 'run' : 'todo', tone: 'run', msg: it.up === 'run' ? (it.url && !it.result ? 'Fetching the address from this server' : 'Uploading; the server takes its own fingerprint') : 'Waiting to upload', end: false};
        const base = {captured: 'done', fp: 'done'};
        if (s.duplicate) return {...base, stored: 'skip', read: 'skip', claims: 'skip', linked: 'skip', tone: 'dup', msg: 'dup', end: true};
        let read = READ[s.stages.read] || 'todo';
        const sheetWaits = s.sheet && !it.sheetConfirmed;
        if (sheetWaits) read = 'todo';
        const claims = read === 'done' ? 'done' : read === 'skip' ? 'skip' : 'todo';
        const linked = sheetWaits || read === 'fail' ? 'todo' : s.pending ? (read === 'done' || read === 'skip' ? 'run' : 'todo') : 'done';
        const st = {...base, stored: 'done', read, claims, linked};
        if (s.failures.length) {
          const f = s.failures[0];
          // A stage failed after the file was stored: it needs a retry, not refused.
          const at = f.stage === 'read' ? {} : {linked: 'fail'};
          return {...st, ...at, tone: 'fail', kept: true, msg: `${f.message}${f.detail ? ' ' + f.detail : ''}`, end: !s.pending};
        }
        if (sheetWaits) return {...st, tone: 'you', msg: it.sheetReason || 'Stored. Rows wait for you: say what each column holds, then confirm.', end: true};
        if (s.waiting) return {...st, tone: 'wait', msg: s.waiting, end: !s.pending};
        if (read === 'run') {
          const p = s.read_progress, part = p ? (p.unit === 'ms' ? `, ${this.clock(Math.floor(p.completed / 1000))} of ${this.clock(Math.floor(p.total / 1000))}` : `, ${p.completed} of ${plural(p.total, p.unit.replace(/s$/, ''))}`) : '';
          return {...st, tone: 'run', msg: `Reading ${KIND[it.kind].read} on the ${it.sens ? 'local' : 'allowed'} model${part}`, end: false};
        }
        if (s.pending) return {...st, tone: 'run', msg: read === 'todo' ? `Queued for reading ${KIND[it.kind].read}` : 'Fingerprinting the content and indexing it for search', end: false};
        if (it.sheetReason) return {...st, tone: /^Waiting/.test(it.sheetReason) ? 'wait' : 'ok', msg: it.sheetReason, end: true};
        const n = s.claims;
        return {...st, tone: 'ok', end: true, msg: n ? `${plural(n, 'claim')} proposed, waiting for review.` : read === 'skip' ? 'Stored with its fingerprint. Nothing in it is read automatically.' : 'Read. No claims proposed.'};
      },
    },
  });
  app.component('sheet-columns', SheetColumns);
  app.component('intake-brief', IntakeBrief);
  registerStkComponents(app);
  app.use(Vuetify.createVuetify(config.vuetifyConfig)).mount('#app');
}

function lev(a, b) {
  const d = Array.from({length: a.length + 1}, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[a.length][b.length];
}
