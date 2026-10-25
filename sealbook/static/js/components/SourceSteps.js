/* What happens next for one source: its steps in order (from the server's
   `steps`, sealbook.ingest.batch.steps), each with its state in words, an icon and
   the action that moves it on. A sheet whose columns wait for a person shows the
   column mapping right here. Emits `changed` after any action so the page reloads
   the item; the page's run stream keeps it live while something is queued. */
import {SheetColumns, sheetState} from '/static/js/components/SheetColumns.js';

// One word and icon per state, the same on Add evidence and the evidence page.
export const STEP = {
  done: {word: 'done', icon: 'ti ti-check'},
  running: {word: 'running now', icon: 'ti ti-loader-2'},
  queued: {word: 'queued', icon: 'ti ti-clock'},
  you: {word: 'waiting for you', icon: 'ti ti-hand-finger'},
  model: {word: 'waiting for a model', icon: 'ti ti-clock-pause'},
  failed: {word: 'did not finish', icon: 'ti ti-alert-triangle'},
  later: {word: 'not yet', icon: 'ti ti-dots'},
  skipped: {word: 'not needed', icon: 'ti ti-minus'},
};
export const LIVE = ['queued', 'running'];
const err = e => e.response?.data?.message || e.response?.data?.description || e.message;

export const SourceSteps = {
  props: {item: {type: Object, required: true}},
  emits: ['changed', 'said'],
  components: {SheetColumns},
  data() {
    const it = this.item;
    return {busy: '', sheetIt: Vue.reactive({name: it.title, sha: it.sha256, acquisition: it.acquisitions[0]?.id, sens: it.sensitive && !it.revealed, file: null, sheet: sheetState(it.title || 'sheet'), sheetConfirmed: false})};
  },
  computed: {
    steps() { return this.item.steps || []; },
    live() { return this.steps.some(s => LIVE.includes(s.state)); },
    summary() {
      const s = this.steps.find(x => ['you', 'model', 'failed'].includes(x.state) && !(x.side && x.state !== 'failed')) || this.steps.find(x => LIVE.includes(x.state));
      if (!s) return 'Nothing is waiting. Every step that applies is done.';
      return {you: 'Waiting for you', model: 'Waiting for a model', failed: 'A step needs a retry', running: 'Working on it now', queued: 'Queued, it continues on its own'}[s.state];
    },
  },
  methods: {
    STEP: s => STEP[s.state],
    async retry(s) {
      this.busy = s.key;
      try {
        for (const r of s.retry) await axios.post(`/ingest/run/${r}/retry`, {item: {}});
        this.$emit('changed');
      } catch (e) { this.$emit('said', err(e)); }
      this.busy = '';
    },
    async read(s) {
      this.busy = s.key;
      try { await axios.post(`/api/evidence/${this.item.sha256}/analyse`, {item: {}}); this.$emit('changed'); } catch (e) { this.$emit('said', err(e)); }
      this.busy = '';
    },
  },
  template: `<section class="steps" aria-labelledby="steps-h">
    <header><h3 id="steps-h">What happens next</h3><span class="muted" role="status" aria-live="polite">{{ summary }}</span></header>
    <ol>
      <li v-for="s in steps" :key="s.key" :class="'st-' + s.state">
        <i :class="STEP(s).icon" aria-hidden="true"></i>
        <div>
          <div><strong>{{ s.label }}</strong> <span class="st-word">{{ STEP(s).word }}</span><span v-if="s.side" class="muted">, side step</span></div>
          <div class="st-says">{{ s.words }}</div>
          <div v-if="s.retry?.length || s.action === 'read'" class="st-acts">
            <v-btn v-if="s.retry?.length" size="x-small" variant="outlined" prepend-icon="ti ti-refresh" :loading="busy === s.key" @click="retry(s)">Try again</v-btn>
            <v-btn v-if="s.action === 'read'" size="x-small" variant="outlined" prepend-icon="ti ti-player-play" :loading="busy === s.key" @click="read(s)">Read it now</v-btn>
          </div>
          <sheet-columns v-if="s.action === 'map' && sheetIt.acquisition" :it="sheetIt" :stored="true" @confirmed="$emit('changed')"></sheet-columns>
        </div>
      </li>
    </ol>
  </section>`,
};
