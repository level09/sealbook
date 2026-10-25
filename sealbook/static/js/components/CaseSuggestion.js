/* Which case a read batch looks like (GET /ingest/batch/<id>/case): the cases this
   person can open whose people, places and dates the batch names, each tie cited
   by source and span, or a proposed name for a new case. Nothing is filed until a
   click; adding records the source in the other case and it stays where it is.

   import {CaseSuggestion} from '/static/js/components/CaseSuggestion.js';
   app.component('case-suggestion', CaseSuggestion);
   <case-suggestion :batch="batchId" @created="onCaseCreated"></case-suggestion> */
const n = (k, word, many = word + 's') => `${k} ${k === 1 ? word : many}`;
const err = e => e.response?.data?.message || e.response?.data?.description || e.message;

export const CaseSuggestion = {
  name: 'CaseSuggestion',
  props: {batch: {type: String, required: true}},
  emits: ['created'],
  data() {
    return {s: null, picked: [], choosing: false, kept: false, done: {elsewhere: null, new_case: null}, name: '', close: null, busy: '', error: ''};
  },
  computed: {
    e() { return this.s?.elsewhere; },
    nc() { return this.s?.new_case; },
    homeName() { return this.s?.home_name; },
  },
  mounted() { this.load(); },
  methods: {
    n,
    async load() {
      try {
        const {data} = await axios.get(`/ingest/batch/${this.batch}/case`);
        this.s = data;
        this.picked = (data.elsewhere?.sources || []).map(x => x.sha256);
        this.name = data.new_case?.name || '';
      } catch (e) { this.error = err(e); }
    },
    joined(list) { return list.map(x => x.title).join(', '); },
    async record(slug, sources) {
      const {data} = await axios.post(`/ingest/batch/${this.batch}/record`, {item: {case: slug, sources}});
      return data;
    },
    async add() {
      this.busy = 'add'; this.error = '';
      try {
        const got = await this.record(this.e.case, this.picked);
        this.done.elsewhere = {slug: this.e.case, name: this.e.name, ...got};
      } catch (e) { this.error = err(e); }
      this.busy = '';
    },
    async start(confirm = false) {
      this.busy = 'start'; this.error = '';
      try {
        const {data} = await axios.post('/api/cases', {item: {name: this.name, description: '', confirm}});
        this.$emit('created', data.item);
        const got = await this.record(data.item.slug, this.nc.sources.map(x => x.sha256));
        this.done.new_case = {slug: data.item.slug, name: data.item.name, ...got};
        this.close = null;
      } catch (e) {
        const d = e.response?.data;
        if (e.response?.status === 409) this.close = d;
        else this.error = err(e);
      }
      this.busy = '';
    },
    async useMatch() {
      // The existing case the person picked over a near-duplicate name.
      this.busy = 'start'; this.error = '';
      try {
        const m = this.close.match, got = await this.record(m.slug, this.nc.sources.map(x => x.sha256));
        this.done.new_case = {slug: m.slug, name: m.name, ...got};
        this.close = null;
      } catch (e) { this.error = err(e); }
      this.busy = '';
    },
    href(slug) { return `/sources?investigation=${encodeURIComponent(slug)}`; },
  },
  template: `<div v-if="s && (e || nc)" class="cs" aria-live="polite">
    <div v-if="e && !kept" class="cs-card">
      <template v-if="done.elsewhere">
        <p class="cs-line"><i class="ti ti-check" aria-hidden="true"></i> Recorded {{ n(done.elsewhere.recorded, 'source') }} in <bdi>{{ done.elsewhere.name }}</bdi><template v-if="done.elsewhere.already">; {{ n(done.elsewhere.already, 'was', 'were') }} there already</template>. {{ done.elsewhere.recorded === 1 ? 'It stays' : 'They stay' }} in <bdi>{{ homeName }}</bdi> too. <a :href="href(done.elsewhere.slug)">{{ done.elsewhere.recorded === 1 ? 'Open it there' : 'Open them there' }}</a></p>
      </template>
      <template v-else>
        <h3><i class="ti ti-robot r-ink is-agent" aria-hidden="true"></i><span>{{ e.sources.length }} of {{ n(s.read, 'source') }} look like <bdi>{{ e.name }}</bdi></span><span class="cs-by">Case suggestion, by rule</span></h3>
        <p class="cs-muted">They name people or places already in <bdi>{{ e.name }}</bdi><template v-if="e.window">, where those are dated {{ e.window }}</template>.</p>
        <ul class="cs-src">
          <li v-for="src in e.sources" :key="src.sha256">
            <v-checkbox-btn v-if="choosing" v-model="picked" :value="src.sha256" density="compact" :aria-label="'Add ' + src.title"></v-checkbox-btn>
            <div>
              <a class="r-ink is-source" :href="'/evidence/' + src.sha256" dir="auto">{{ src.title }}</a>
              <span v-for="(t, i) in src.ties" :key="i" class="cs-tie">
                <template v-if="t.masked"><i class="ti ti-lock r-ink state-sensitive" aria-hidden="true"></i> a {{ t.kind === 'person' ? 'name' : 'place' }} from a sensitive source, <a :href="t.href">show it on the source page</a></template>
                <template v-else><span class="r-ink" :class="t.kind === 'person' ? 'is-person' : 'is-place'"><i :class="t.kind === 'person' ? 'ti ti-user' : 'ti ti-map-pin'" aria-hidden="true"></i> <bdi>{{ t.value }}</bdi></span><template v-if="t.when">, {{ t.when }}</template></template>
                <cite>({{ t.where }})</cite>
              </span>
              <span v-if="src.more > src.ties.length" class="cs-muted">and {{ src.more - src.ties.length }} more</span>
            </div>
          </li>
        </ul>
        <p v-if="e.unmatched.length" class="cs-muted"><bdi>{{ joined(e.unmatched) }}</bdi> {{ e.unmatched.length === 1 ? 'matches' : 'match' }} nothing there.</p>
        <p class="cs-muted">Adding records each source in <bdi>{{ e.name }}</bdi> as well, with a note saying where it came from. It stays in <bdi>{{ homeName }}</bdi>: custody records are never changed, and its claims show in both cases.</p>
        <div class="cs-acts">
          <v-btn color="primary" variant="flat" :disabled="!picked.length" :loading="busy === 'add'" @click="add">Add {{ n(picked.length, 'source') }} to {{ e.name }}</v-btn>
          <v-btn variant="outlined" @click="kept = true">Keep only in {{ homeName }}</v-btn>
          <v-btn variant="text" :prepend-icon="choosing ? 'ti ti-check' : 'ti ti-list-check'" @click="choosing = !choosing">{{ choosing ? 'Done choosing' : 'Choose which' }}</v-btn>
        </div>
      </template>
    </div>
    <div v-if="nc" class="cs-card">
      <template v-if="done.new_case">
        <p class="cs-line"><i class="ti ti-check" aria-hidden="true"></i> Recorded {{ n(done.new_case.recorded, 'source') }} in <bdi>{{ done.new_case.name }}</bdi>. {{ done.new_case.recorded === 1 ? 'It stays' : 'They stay' }} in <bdi>{{ homeName }}</bdi> too. <a :href="href(done.new_case.slug)">{{ done.new_case.recorded === 1 ? 'Open it there' : 'Open them there' }}</a></p>
      </template>
      <template v-else>
        <h3><i class="ti ti-robot r-ink is-agent" aria-hidden="true"></i><span>{{ e ? 'Or start a case for the rest' : nc.sources.length === s.read ? 'This batch matches no case you can open' : nc.sources.length + ' of ' + n(s.read, 'source') + ' match no case you can open' }}</span><span class="cs-by">Case suggestion, by rule</span></h3>
        <p v-if="nc.basis" class="cs-muted">Suggested name from the place named most, <span class="r-ink is-place"><i class="ti ti-map-pin" aria-hidden="true"></i> <bdi>{{ nc.basis.value }}</bdi></span><cite>({{ nc.basis.source }}, {{ nc.basis.where }})</cite><template v-if="nc.dated">, dated {{ nc.dated.when }}<cite v-if="nc.dated.where !== nc.basis.where">({{ nc.dated.where }})</cite></template>. Edit it before the case is created.</p>
        <p v-else class="cs-muted">No ordinary source in it names a place, so no name is suggested. Type one.</p>
        <div class="cs-name">
          <v-text-field v-model="name" label="Case name" density="compact" variant="outlined" hide-details counter="60" maxlength="60" dir="auto" @update:model-value="close = null"></v-text-field>
          <v-btn color="primary" variant="flat" :disabled="!name.trim()" :loading="busy === 'start'" @click="start(false)">Start this case</v-btn>
        </div>
        <div v-if="close" class="cs-close" role="alert">
          <i class="ti ti-alert-triangle" aria-hidden="true"></i><span>{{ close.message }}</span>
          <v-btn size="small" variant="outlined" @click="useMatch">Add to {{ close.match.name }} instead</v-btn>
          <v-btn v-if="close.reason === 'close'" size="small" variant="text" @click="start(true)">Start a new case anyway</v-btn>
        </div>
        <p class="cs-muted">Starting it records {{ nc.sources.length === 1 ? 'this source' : 'these ' + nc.sources.length + ' sources' }} in the new case: <bdi>{{ joined(nc.sources) }}</bdi>. {{ nc.sources.length === 1 ? 'It stays' : 'They stay' }} in <bdi>{{ homeName }}</bdi> too.</p>
      </template>
    </div>
    <p v-if="error" class="cs-err" role="alert">{{ error }}</p>
  </div>`,
};

const STYLE = `
.cs { display: grid; gap: 10px; padding: 14px; }
.cs-card { border: 1px solid var(--agent-line); background: var(--agent-wash); border-radius: 6px; padding: 12px 14px; display: grid; gap: 8px; font-size: 13.5px; }
.cs-card h3 { margin: 0; font-size: 14px; font-weight: 650; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.cs-by { font-weight: 400; font-size: 12px; color: var(--agent); }
.cs-muted { color: var(--ink-2); margin: 0; line-height: 1.5; }
.cs-src { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.cs-src li { display: flex; gap: 6px; align-items: flex-start; line-height: 1.6; overflow-wrap: anywhere; }
.cs-tie { margin-inline-start: 10px; white-space: normal; }
.cs-tie + .cs-tie::before { content: ''; }
.cs cite { font-style: normal; font-size: 12px; color: var(--ink-3); margin-inline-start: 4px; }
.cs-acts, .cs-name, .cs-close { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.cs-name .v-text-field { flex: 1 1 260px; max-width: 420px; background: var(--panel); }
.cs-close { color: var(--due); font-size: 13px; }
.cs-line { margin: 0; }
.cs-err { color: var(--negative); margin: 0; }
`;

if (!document.getElementById('cs-style')) document.head.insertAdjacentHTML('beforeend', `<style id="cs-style">${STYLE}</style>`);
