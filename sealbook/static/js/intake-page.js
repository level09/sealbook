/* Add evidence (/ingest/): pick a case, stage files, links, web results, a sheet or a
   voice note with a type, sensitivity and origin each, then watch the same rows go
   through their stages and end on the intake brief.

   The browser takes each file's SHA-256 before upload; the server refuses the bytes
   if its own differs. Stage states come from GET /ingest/batch/<id>. */
import {IntakeBrief} from '/static/js/components/IntakeBrief.js';
import {CaseSuggestion} from '/static/js/components/CaseSuggestion.js';
import {SheetColumns, sheetState, confirmRows, fill} from '/static/js/components/SheetColumns.js';
import {STEP} from '/static/js/components/SourceSteps.js';

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
const ICON = {done: 'ti ti-check', run: 'ti ti-loader-2', wait: 'ti ti-clock-pause', you: 'ti ti-hand-finger', fail: 'ti ti-x', skip: '', todo: ''};
const WORD = {done: 'done', run: 'working', wait: 'waiting for a model', you: 'waiting for you', fail: 'refused', skip: 'not needed', todo: 'not started'};
// Server step states (sealbook.ingest.batch.steps) to track states, and short labels.
const TRACK = {done: 'done', running: 'run', queued: 'todo', you: 'you', model: 'wait', failed: 'fail', later: 'todo', skipped: 'skip'};
const SHORT = {hashed: 'fingerprint', stored: 'stored', indexed: 'search', columns: 'columns', rows: 'rows', claims: 'claims', first_pass: 'first pass'};
// Tone of the row message per step state.
const TONED = {you: 'you', model: 'wait', failed: 'fail', running: 'run', queued: 'run'};
// The file a capture of each result kind is about: the rest (screenshot, network log)
// are kept with it but do not get rows of their own.
const PRIMARY = {web: 'page', images: 'media', videos: 'video'};
const SHEET = /\.(csv|xlsx)$/i;

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
      // Captures are stored by the worker, after start() has reread the counts.
      finished(done) { if (done) this.loadCases(); },
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

      // A case started from the suggestion: listed, but the batch keeps its case.
      async caseMade(c) {
        this.created.push(c.slug);
        await this.loadCases();
        this.caseList = [...new Set([...this.caseList, c.slug])].sort();
      },

      // --- staging ------------------------------------------------------------
      make(o) {
        return Vue.reactive({id: crypto.randomUUID(), sel: false, sens: true, origin: '', docBy: 'rule', doc: 'unknown', docWhy: '', sha: '', thumb: null,
          up: null, upError: '', acquisition: null, checked: false, dup: false, original: null, server: null, capture: null, sheetConfirmed: false, ...o});
      },
      chosen(e) { this.addFiles([...e.target.files]); e.target.value = ''; },
      addFiles(files, extra = {}) {
        const fresh = files.filter(f => !this.items.some(i => i.file && i.file.name === f.name && i.file.size === f.size));
        const made = fresh.map(f => {
          const kind = kindOf(f.name, f.type);
          return this.make({file: f, name: f.name, mime: f.type, kind, thumb: kind === 'image' ? URL.createObjectURL(f) : null,
            sheet: kind === 'sheet' ? sheetState(f.name) : null, ...extra});
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
        // The case's counts were read before this batch stored anything.
        this.loadCases();
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
          const caseOf = data.items.find(i => i.investigation)?.investigation;
          data.captures.forEach(c => this.items.push(this.make({result: {id: c.run, kind: c.kind, url: c.page}, url: c.page, name: c.title || this.shortUrl(c.page),
            kind: {web: 'web', images: 'image', videos: 'video'}[c.kind] || 'web', doc: c.doc_type, sens: c.sensitivity === 'sensitive', capture: c.run, up: 'done'})));
          data.items.filter(i => !i.capture).forEach(i => {
            const kind = kindOf(i.name, i.mime);
            // Tiles of sensitive sources wait for a reveal, so those keep the kind icon.
            const thumb = i.sensitivity === 'sensitive' ? null : `/api/evidence/${i.sha256}/thumb?size=160`;
            this.items.push(this.make({name: i.name, mime: i.mime, kind, thumb, doc: i.doc_type, sens: i.sensitivity === 'sensitive', acquisition: i.acquisition, sha: i.sha256, up: 'done',
              sheet: kind === 'sheet' ? sheetState(i.name) : null}));
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
          await confirmRows(it, {dataset: s.dataset, dataset_name: s.datasetName, key_column: key, mapping: {...s.map}, sheet: s.peek.table.sheet});
          it.sheetConfirmed = true;
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
      // A model was set up after the item arrived: queue its reading now.
      async readNow(it) {
        try { await axios.post(`/api/evidence/${it.sha}/analyse`, {item: {}}); this.poll(); } catch (e) { this.say(err(e), true); }
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
        // Once stored, the strip is the source's own steps, in the evidence page's words.
        if (v.track) return [...out, ...v.track];
        out.push({k: 'hashed', label: 'fingerprint', st: v.fp}, {k: 'stored', label: 'stored', st: v.stored},
          {k: 'read', label: KIND[it.kind].read, st: v.read}, {k: 'claims', label: 'claims', st: v.claims});
        return out;
      },
      view(it) {
        const s = it.server, c = it.captureState, todo = {fp: 'todo', stored: 'todo', read: 'todo', claims: 'todo', captured: 'todo'};
        if (it.up === 'fail') return {...todo, fp: it.result ? 'todo' : 'fail', captured: 'fail', tone: 'fail', msg: it.upError, end: true};
        if (it.result) {
          if (!c) return {...todo, captured: it.up === 'run' ? 'run' : 'todo', tone: 'run', msg: 'Sending to the capture browser on this server', end: false};
          if (c.status === 'failed') return {...todo, captured: 'fail', tone: 'fail', msg: `The capture did not finish: ${c.error || 'no reason was given'}. Nothing was stored, so there is nothing to fingerprint.`, end: true};
          if (c.status !== 'done') return {...todo, captured: c.status === 'running' ? 'run' : 'todo', tone: 'run', msg: c.status === 'running' ? 'Capturing the page with the browser on this server' : 'Waiting for the capture browser on this server', end: false};
          if (!s) return {...todo, captured: 'done', tone: 'run', msg: 'Captured. Storing what the browser saw', end: false};
        }
        if (!s) return {...todo, fp: it.up === 'run' ? 'run' : 'todo', tone: 'run', msg: it.up === 'run' ? (it.url && !it.result ? 'Fetching the address from this server' : 'Uploading; the server takes its own fingerprint') : 'Waiting to upload', end: false};
        const base = {captured: 'done', fp: 'done'};
        if (s.duplicate) return {...base, stored: 'skip', read: 'skip', claims: 'skip', tone: 'dup', msg: 'dup', end: true};
        // The columns were just confirmed here; the next poll brings the queued rows.
        const steps = s.steps.map(x => x.key === 'columns' && x.state === 'you' && it.sheetConfirmed ? {...x, state: 'done', words: 'Confirmed by you.'} : x);
        const track = steps.map(x => ({k: x.key, label: SHORT[x.key] || KIND[it.kind].read, st: TRACK[x.state], word: STEP[x.state].word}));
        const main = steps.filter(x => !x.side), sideFail = steps.find(x => x.side && x.state === 'failed');
        const first = main.find(x => ['you', 'model', 'failed'].includes(x.state)) || main.find(x => ['running', 'queued'].includes(x.state));
        // A failed side step (search indexing) is said after the main state, with its own retry.
        const side = sideFail ? ` ${sideFail.label}, ${STEP.failed.word}. ${sideFail.words}` : '';
        const out = {...base, track, side: !!sideFail, read: first?.action === 'read', end: !s.pending, kept: true};
        if (first) {
          // The words often open with the state already ("Queued. It starts..."); say it once.
          const said = first.words.toLowerCase().startsWith(STEP[first.state].word.toLowerCase());
          return {...out, tone: TONED[first.state], msg: `${first.label}${said ? '' : ', ' + STEP[first.state].word}. ${first.words}${side}`};
        }
        return {...out, tone: 'ok', msg: `${main.at(-1).words}${side}`};
      },
    },
  });
  app.component('sheet-columns', SheetColumns);
  app.component('intake-brief', IntakeBrief);
  app.component('case-suggestion', CaseSuggestion);
  registerStkComponents(app);
  app.use(Vuetify.createVuetify(config.vuetifyConfig)).mount('#app');
}

function lev(a, b) {
  const d = Array.from({length: a.length + 1}, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[a.length][b.length];
}
