/* Research the open web for a person, place, incident or source.
   Proposes queries from values that may leave the building, lets the analyst edit
   them, shows exactly what will be sent, then a results gallery by type with
   "already in this case" marks, and Add as evidence with intake progress.

   import {ResearchDrawer} from '/static/js/components/ResearchDrawer.js';
   app.component('research-drawer', ResearchDrawer);
   <research-drawer v-model="researchOpen" :scope="'person:' + id" :case="caseSel" :cases="caseList"></research-drawer>

   Without the keys it says which settings are missing and does nothing else. */
const KIND_LABEL = {web: 'Pages', images: 'Images', videos: 'Videos'};
const STAGE = {hashed: 'stored', c2pa: 'C2PA', read: 'read', fingerprint: 'fingerprint', embed: 'search', linked: 'linked'};

export const ResearchDrawer = {
  name: 'ResearchDrawer',
  props: {
    modelValue: {type: Boolean, default: false},
    scope: {type: String, required: true},
    case: {type: String, default: ''},
    cases: {type: Array, default: () => []},
  },
  emits: ['update:modelValue'],
  data() {
    return {KIND_LABEL, fromCases: [], status: null, investigation: '', queries: [], kinds: ['web', 'images', 'videos'], loading: false, busy: false, error: '',
      sent: [], groups: null, from: '', to: '', dates: null, closest: null, subject: null, showAll: false, tab: 'web', picked: [], sensitivity: 'sensitive', batch: null, progress: null, _timer: null};
  },
  computed: {
    open: {get() { return this.modelValue; }, set(v) { this.$emit('update:modelValue', v); }},
    caseItems() { return [...new Set([...this.fromCases, ...this.cases, this.investigation])].filter(c => c && c !== 'all'); },
    missingSearch() { return this.status?.missing.search || []; },
    missingCapture() { return this.status?.missing.capture || []; },
    inert() { return !this.status || this.missingSearch.length > 0 || !!this.status.refusal; },
    ready() { return this.queries.filter(q => q.text.trim() && q.kinds.length); },
    calls() { return this.ready.reduce((n, q) => n + q.kinds.length, 0); },
    windowOk() { return (!this.from && !this.to) || (this.from && this.to && this.from <= this.to); },
    windowText() { return this.from && this.to ? `${this.from} to ${this.to}` : ''; },
    nothing() { return !!this.closest && !this.showAll; },
    cards() {
      if (!this.groups) return [];
      if (this.nothing) return Object.values(this.groups).flat().filter(r => r.id === this.closest);
      return this.groups[this.tab];
    },
    total() { return this.groups ? Object.values(this.groups).reduce((n, g) => n + g.length, 0) : 0; },
    done() { return this.progress && this.progress.captures.every(c => ['done', 'failed'].includes(c.status)) && (this.progress.intake.done || this.progress.captures.every(c => c.status === 'failed')); },
  },
  watch: {
    open(v) { if (v && !this.status) this.start(); },
    investigation() { this.loadStatus(); },
  },
  methods: {
    fail(e) { return e.response?.data?.message || e.response?.data?.description || e.message; },
    async start() {
      this.investigation = this.case && this.case !== 'all' ? this.case : (this.caseItems.length === 1 ? this.caseItems[0] : '');
      await this.loadStatus();
      if (this.missingSearch.length) return;
      this.loading = true;
      try {
        const {data} = await axios.post('/api/research/queries', {item: {scope: this.scope}});
        this.kinds = data.kinds;
        this.queries = data.queries.map(q => ({...q, kinds: [...q.kinds]}));
        if (!this.queries.length) this.addQuery();
        // Default to the case the proposed values come from.
        const from = this.queries.flatMap(q => q.why.flatMap(w => w.cases || []));
        if (!this.investigation && from.length) this.investigation = from[0];
        this.fromCases = [...new Set(from)];
      } catch (e) { this.error = this.fail(e); }
      this.loading = false;
    },
    async loadStatus() {
      try { this.status = (await axios.get('/api/research/status', {params: {investigation: this.investigation || undefined}})).data; }
      catch (e) { this.error = this.fail(e); }
    },
    addQuery() { this.queries.push({text: '', kinds: [...this.kinds], why: [], people: []}); },
    why(q) { return q.why.length ? q.why.map(w => `${w.label}: ${w.value}`).join(' · ') : 'Typed by you'; },
    async send() {
      this.busy = true; this.error = ''; this.groups = null; this.picked = []; this.showAll = false;
      try {
        const {data} = await axios.post('/api/research/search', {item: {scope: this.scope, investigation: this.investigation, queries: this.ready, from: this.from || undefined, to: this.to || undefined}});
        this.sent = data.sent; this.groups = data.groups; this.dates = data.window; this.closest = data.closest; this.subject = data.subject;
        this.tab = Object.keys(data.groups).find(k => data.groups[k].some(r => r.id === data.closest)) || Object.keys(data.groups).find(k => data.groups[k].length) || 'web';
      } catch (e) { this.error = this.fail(e); }
      this.busy = false;
    },
    toggle(id) { this.picked = this.picked.includes(id) ? this.picked.filter(x => x !== id) : [...this.picked, id]; },
    async add() {
      this.busy = true; this.error = '';
      try {
        const {data} = await axios.post('/api/research/capture', {item: {ids: this.picked, sensitivity: this.sensitivity}});
        this.batch = data.batch; this.picked = []; this.poll();
      } catch (e) { this.error = this.fail(e); }
      this.busy = false;
    },
    async poll() {
      clearTimeout(this._timer);
      try { this.progress = (await axios.get(`/api/research/batch/${this.batch}`)).data; }
      catch (e) { this.error = this.fail(e); return; }
      if (!this.done) this._timer = setTimeout(() => this.poll(), 2000);
    },
    async retry(run) {
      try { await axios.post(`/ingest/run/${run}/retry`, {item: {}}); this.poll(); }
      catch (e) { this.error = this.fail(e); }
    },
    stages(item) { return Object.entries(item.stages).filter(([, s]) => s !== 'skipped').map(([k, s]) => ({k: STAGE[k] || k, s})); },
  },
  beforeUnmount() { clearTimeout(this._timer); },
  template: `<v-navigation-drawer v-model="open" location="right" temporary width="560" class="rs" aria-label="Research the web">
    <v-toolbar density="compact" color="surface" border="b"><v-toolbar-title class="text-body-1 font-weight-bold">Research the web</v-toolbar-title>
      <v-btn icon="ti ti-x" variant="text" aria-label="Close" @click="open = false"></v-btn></v-toolbar>
    <div class="pa-3">
      <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3">{{ error }}</v-alert>
      <v-skeleton-loader v-if="!status && !error" type="list-item-two-line@3"></v-skeleton-loader>
      <template v-else-if="status && missingSearch.length">
        <v-empty-state icon="ti ti-world-off" title="Web research is not set up" class="px-0">
          <template #text><p>Searching needs a Brave Search API key, and adding pages as evidence needs Chromium on the server. An administrator sets the key in the server settings, or installs Chromium, and restarts the app and worker:</p>
            <ul class="rs-keys"><li v-for="k in missingCapture" :key="k"><code>{{ k }}</code></li></ul>
            <p class="muted">Until then nothing is sent anywhere. Operations docs explain the costs and limits.</p></template>
        </v-empty-state>
      </template>
      <template v-else-if="status">
        <v-select v-if="caseItems.length > 1 || !investigation" v-model="investigation" :items="caseItems" label="Case this research is for" density="compact" class="mb-2" hide-details></v-select>
        <v-alert v-if="status.refusal" type="warning" variant="tonal" density="compact" class="mb-3" icon="ti ti-lock">{{ status.refusal }}</v-alert>
        <template v-if="!groups">
          <p class="rs-note mb-2"><i class="ti ti-eye" aria-hidden="true"></i> These exact words go to Brave Search, a third party. Each query is logged with your name. Proposals use only accepted values from ordinary sources; edit or remove anything before sending.</p>
          <v-skeleton-loader v-if="loading" type="list-item-two-line@3"></v-skeleton-loader>
          <div v-for="(q, i) in queries" :key="i" class="rs-q">
            <div class="d-flex ga-1 align-center">
              <v-text-field v-model="q.text" :label="'Query ' + (i + 1)" density="compact" hide-details dir="auto" :disabled="inert" maxlength="300"></v-text-field>
              <v-btn icon="ti ti-trash" variant="text" size="small" :aria-label="'Remove query ' + (i + 1)" :disabled="inert" @click="queries.splice(i, 1)"></v-btn>
            </div>
            <v-chip-group v-model="q.kinds" multiple density="compact" :disabled="inert"><v-chip v-for="k in kinds" :key="k" :value="k" size="small" filter variant="outlined">{{ KIND_LABEL[k] }}</v-chip></v-chip-group>
            <div class="rs-why" dir="auto">{{ why(q) }}</div>
          </div>
          <div class="d-flex ga-2 mt-2 align-center">
            <v-text-field v-model="from" type="date" label="Pages from" density="compact" hide-details :disabled="inert" :error="!windowOk"></v-text-field>
            <v-text-field v-model="to" type="date" label="Pages to" density="compact" hide-details :disabled="inert" :error="!windowOk"></v-text-field>
          </div>
          <p v-if="!windowOk" class="rs-note mt-1" role="alert">Set both dates, the first no later than the second, or neither.</p>
          <p v-else-if="windowText" class="rs-note mt-1"><i class="ti ti-calendar" aria-hidden="true"></i> Brave will also be told to return pages dated {{ windowText }}. Only page searches use the dates; images and videos are not filtered by date.</p>
          <div class="d-flex ga-2 mt-2 flex-wrap align-center">
            <v-btn variant="text" size="small" prepend-icon="ti ti-plus" :disabled="inert || queries.length >= status.limits.max_queries" @click="addQuery">Add a query</v-btn>
            <v-spacer></v-spacer>
            <v-btn color="primary" variant="flat" prepend-icon="ti ti-send" :disabled="inert || !calls || !windowOk" :loading="busy" @click="send">Send {{ calls }} search{{ calls === 1 ? '' : 'es' }} to Brave</v-btn>
          </div>
          <p class="rs-note mt-2">Limit {{ status.limits.queries_per_hour }} searches an hour per person.</p>
        </template>
        <template v-else>
          <div class="d-flex align-center mb-2"><v-btn variant="text" size="small" prepend-icon="ti ti-arrow-left" @click="groups = null">Edit queries</v-btn><v-spacer></v-spacer><span class="muted">{{ total }} result{{ total === 1 ? '' : 's' }}<template v-if="dates"> · dated {{ dates[0] }} to {{ dates[1] }}</template></span></div>
          <v-alert v-if="nothing" type="warning" variant="tonal" density="compact" class="mb-2" icon="ti ti-search-off">Nothing found clearly mentions <bdi dir="auto">{{ subject }}</bdi>. The closest weak result is shown; it may not be about them. <v-btn size="x-small" variant="outlined" class="ms-1" @click="showAll = true">Show all {{ total }}</v-btn></v-alert>
          <div v-for="s in sent.filter(s => s.error)" :key="s.text + s.kind" class="rs-note mb-1"><i class="ti ti-alert-triangle" aria-hidden="true"></i> {{ KIND_LABEL[s.kind] }} for <bdi dir="auto">{{ s.text }}</bdi>: {{ s.error }}</div>
          <v-tabs v-if="!nothing" v-model="tab" density="compact" grow><v-tab v-for="k in kinds" :key="k" :value="k">{{ KIND_LABEL[k] }} <span class="ms-1 muted">{{ groups[k].length }}</span></v-tab></v-tabs>
          <div class="rs-grid mt-2">
            <v-card v-for="r in cards" :key="r.id" border flat class="rs-card" :class="{sel: picked.includes(r.id)}" @click="toggle(r.id)" role="checkbox" :aria-checked="picked.includes(r.id)" tabindex="0" @keydown.space.prevent="toggle(r.id)">
              <div class="rs-thumb"><img v-if="r.thumb" :src="'/api/research/thumb/' + r.id" alt="" loading="lazy"><i v-else :class="tab === 'videos' ? 'ti ti-video' : tab === 'images' ? 'ti ti-photo' : 'ti ti-world'" aria-hidden="true"></i>
                <v-checkbox-btn :model-value="picked.includes(r.id)" class="rs-check" density="compact" tabindex="-1" aria-hidden="true"></v-checkbox-btn></div>
              <div class="pa-2">
                <div class="rs-title" dir="auto">{{ r.title || r.url }}</div>
                <div class="rs-meta">{{ r.domain }}<template v-if="r.date"> · {{ r.date.slice(0, 10) }}</template></div>
                <div v-for="c in r.in_case" :key="c.kind + c.sha256" class="rs-held"><v-chip size="x-small" color="warning" variant="tonal" prepend-icon="ti ti-copy" :href="'/evidence/' + c.sha256" target="_blank" @click.stop>Already in this case</v-chip> <span>{{ c.why }}</span></div>
                <div v-if="r.grounded === false" class="rs-weak"><i class="ti ti-help-circle" aria-hidden="true"></i> may not be about <bdi dir="auto">{{ r.subject }}</bdi></div>
                <div class="rs-why">From <template v-for="(t, i) in r.queries" :key="t"><template v-if="i">, </template><bdi dir="auto">{{ t }}</bdi></template></div>
                <a :href="r.url" target="_blank" rel="noopener noreferrer nofollow" referrerpolicy="no-referrer" class="rs-link" @click.stop>Open the page <i class="ti ti-external-link" aria-hidden="true"></i></a>
              </div>
            </v-card>
          </div>
          <p v-if="!nothing && !groups[tab].length" class="rs-note">No {{ KIND_LABEL[tab].toLowerCase() }} found.</p>
          <div class="rs-bar">
            <template v-if="missingCapture.length"><span class="rs-note">Adding as evidence needs {{ missingCapture.join(', ') }} in the server settings.</span></template>
            <template v-else>
              <v-select v-model="sensitivity" :items="[{title: 'Sensitive (asks before showing)', value: 'sensitive'}, {title: 'Ordinary', value: 'ordinary'}]" density="compact" hide-details label="Sensitivity" style="max-width: 240px"></v-select>
              <v-spacer></v-spacer>
              <v-btn color="primary" variant="flat" prepend-icon="ti ti-download" :disabled="!picked.length || !!status.refusal" :loading="busy" @click="add">Add {{ picked.length || '' }} as evidence</v-btn>
            </template>
          </div>
          <p class="rs-note">A browser on this server visits each picked page once; its HTML, a full-page screenshot and the media on it are stored and hashed, then read like any other intake. Pages behind a login are refused.</p>
        </template>
        <div v-if="progress" class="mt-3">
          <h3 class="text-body-2 font-weight-bold mb-1">Capture <span class="muted">{{ done ? 'finished' : 'running' }}</span></h3>
          <div v-for="c in progress.captures" :key="c.run" class="rs-prog">
            <div><strong dir="auto">{{ c.title || c.page }}</strong> <span class="muted">{{ c.status }}</span></div>
            <div v-if="c.error" class="rs-note">{{ c.error }} <v-btn size="x-small" variant="outlined" @click="retry(c.run)">Retry</v-btn></div>
          </div>
          <div v-for="i in progress.intake.items" :key="i.acquisition" class="rs-prog">
            <a :href="'/evidence/' + i.sha256" dir="auto">{{ i.name }}</a><v-chip v-if="i.duplicate" size="x-small" class="ms-1" variant="tonal">same bytes already held</v-chip>
            <div class="d-flex flex-wrap ga-1 mt-1"><v-chip v-for="s in stages(i)" :key="s.k" size="x-small" :color="s.s === 'failed' ? 'error' : s.s === 'done' ? 'success' : undefined" variant="tonal">{{ s.k }} {{ s.s }}</v-chip></div>
            <div v-for="f in i.failures" :key="f.run" class="rs-note">{{ f.message }} <v-btn v-if="f.retryable" size="x-small" variant="outlined" @click="retry(f.run)">Retry</v-btn></div>
          </div>
          <p v-if="progress.intake.summary" class="rs-note">{{ progress.intake.summary.new_items }} new, {{ progress.intake.summary.exact_duplicates }} exact copies, {{ progress.intake.summary.possible_matches }} possible matches to review.</p>
        </div>
      </template>
    </div>
  </v-navigation-drawer>`,
  beforeCreate() {
    if (document.getElementById('research-drawer-css')) return;
    document.head.append(Object.assign(document.createElement('style'), {id: 'research-drawer-css', textContent: `
.rs-note { font-size: 12px; color: var(--ink-3, #62656a); }
.rs-keys { margin: 8px 0; padding-inline-start: 18px; text-align: start; }
.rs-q { padding: 8px 0; border-bottom: 1px solid var(--line, #e4e4e0); }
.rs-why { font-size: 12px; color: var(--ink-3, #62656a); overflow-wrap: anywhere; }
.rs-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.rs-card { cursor: pointer; }
.rs-card.sel { outline: 2px solid rgb(var(--v-theme-primary)); }
.rs-thumb { position: relative; height: 96px; display: grid; place-items: center; background: var(--wash, rgba(24,25,27,.06)); overflow: hidden; font-size: 24px; color: var(--ink-3, #62656a); }
.rs-thumb img { width: 100%; height: 100%; object-fit: cover; }
.rs-check { position: absolute; top: 0; inset-inline-end: 0; background: rgba(255,255,255,.8); border-radius: 4px; }
.rs-title { font-weight: 550; font-size: 13px; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.rs-meta { font-size: 12px; color: var(--ink-3, #62656a); }
.rs-held { font-size: 11px; margin-top: 4px; }
.rs-link { font-size: 12px; }
.rs-weak { font-size: 12px; margin-top: 4px; color: rgb(var(--v-theme-warning)); }
.rs-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; position: sticky; bottom: 0; background: rgb(var(--v-theme-surface)); padding: 8px 0; margin-top: 8px; border-top: 1px solid var(--line, #e4e4e0); }
.rs-prog { padding: 6px 0; border-bottom: 1px solid var(--line, #e4e4e0); }
`}));
  },
};
