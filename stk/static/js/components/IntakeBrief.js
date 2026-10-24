/* The intake brief for one batch: what arrived, who, where and when, what it
   connects to, what disagrees, and next steps that open the thing to do. Lines
   resting on a sensitive source this session has not revealed show locked, with a
   link to the source page where the logged reveal happens.

   import {IntakeBrief} from '/static/js/components/IntakeBrief.js';
   app.component('intake-brief', IntakeBrief);
   <intake-brief :batch="batchId" :compact="true"></intake-brief>

   compact: the Desk's version, without versions or the regenerate button. */
const SECTIONS = [
  {key: 'arrived', title: 'What arrived'},
  {key: 'who', title: 'Who'},
  {key: 'where', title: 'Where'},
  {key: 'when', title: 'When'},
  {key: 'connects', title: 'What it connects to'},
  {key: 'disagrees', title: 'What disagrees'},
  {key: 'next', title: 'Next steps'},
];

export const IntakeBrief = {
  name: 'IntakeBrief',
  props: {batch: {type: String, required: true}, compact: {type: Boolean, default: false}},
  data() {return {SECTIONS, data: null, error: '', busy: false, version: null, timer: null};},
  computed: {
    brief() {return this.data?.brief;},
    shown() {return this.brief ? SECTIONS.filter(s => (this.brief[s.key] || []).length || ['connects', 'disagrees'].includes(s.key)) : [];},
    cost() {
      const v = this.data?.version;
      if (!v) return '';
      if (v.fixed) return 'Fixed demo output, no model ran.';
      const parts = [`Written by ${v.model} on the ${v.route} route`];
      if (v.seconds != null) parts.push(`in ${Math.round(v.seconds)} s`);
      if (v.tokens) parts.push(`${v.tokens.toLocaleString()} tokens`);
      return parts.join(', ') + '.';
    },
    versions() {return (this.data?.versions || []).filter(v => v.status === 'done').map((v, i, all) => ({value: v.id, title: `Version ${all.length - i}, ${new Date(v.at).toLocaleString()}`}));},
  },
  watch: {batch() { this.load(); }},
  mounted() { this.load(); },
  beforeUnmount() { clearTimeout(this.timer); },
  methods: {
    async load() {
      clearTimeout(this.timer);
      try {
        const {data} = await axios.get('/api/agents/brief', {params: {batch: this.batch, version: this.version || undefined}});
        this.data = data; this.error = '';
        if (!this.version && data.version) this.version = data.version.id;
        if (data.pending) this.timer = setTimeout(() => this.load(), 4000);
      } catch (e) { this.error = e.response?.data?.description || e.message; }
    },
    async regenerate() {
      this.busy = true;
      try { await axios.post('/api/agents/brief', {item: {batch: this.batch}}); this.version = null; await this.load(); }
      catch (e) { this.error = e.response?.data?.message || e.response?.data?.description || e.message; }
      this.busy = false;
    },
    pick(id) { this.version = id; this.load(); },
  },
  template: `<div class="ib" :class="{'ib-compact': compact}" aria-live="polite">
    <p v-if="error" class="ib-muted">{{ error }}</p>
    <p v-else-if="!data" class="ib-muted">Loading the brief</p>
    <template v-else-if="!brief">
      <p v-if="data.pending" class="ib-muted"><v-progress-circular indeterminate size="14" width="2" class="me-1"></v-progress-circular> The brief is being written on the local model.</p>
      <p v-else-if="data.failed" class="ib-muted">The brief could not be written: {{ data.failed.error }}</p>
      <p v-else-if="data.missing.length" class="ib-muted">No brief: the agents are off until {{ data.missing.join(' and ') }} {{ data.missing.length > 1 ? 'are' : 'is' }} set on the server.</p>
      <p v-else class="ib-muted">No brief was written for this batch.</p>
      <v-btn v-if="!compact && !data.pending && !data.missing.length" size="small" variant="outlined" prepend-icon="ti ti-sparkles" :loading="busy" @click="regenerate">Write a brief</v-btn>
    </template>
    <template v-else>
      <section v-for="s in shown" :key="s.key" class="ib-sec">
        <h3>{{ s.title }}</h3>
        <p v-if="!brief[s.key].length" class="ib-muted">{{ s.key === 'disagrees' ? 'Nothing in this batch disagrees with an accepted claim.' : 'Nothing the brief could cite.' }}</p>
        <ul :class="{'ib-next': s.key === 'next'}">
          <li v-for="(line, i) in brief[s.key]" :key="i">
            <template v-if="line.locked.length"><i class="ti ti-lock" aria-hidden="true"></i> <span class="ib-muted">A line from a sensitive source, <bdi>{{ line.locked.map(x => x.title).join(', ') }}</bdi>.</span> <a :href="line.href">Show it on the source page</a></template>
            <template v-else>
              <a v-if="line.href && s.key === 'next'" :href="line.href" class="ib-act"><i class="ti ti-arrow-right" aria-hidden="true"></i> <span dir="auto">{{ line.text }}</span></a>
              <template v-else><span dir="auto">{{ line.text }}</span><a v-if="line.href" :href="line.href" class="ms-1">Open</a></template>
            </template>
          </li>
        </ul>
      </section>
      <div class="ib-foot">
        <span class="ib-muted"><i class="ti ti-robot" aria-hidden="true"></i> {{ cost }} Every line cites the claims it rests on; nothing here is accepted.</span>
        <template v-if="!compact">
          <v-select v-if="versions.length > 1" :model-value="version" :items="versions" density="compact" hide-details variant="outlined" class="ib-ver" aria-label="Brief version" @update:model-value="pick"></v-select>
          <v-btn size="small" variant="text" prepend-icon="ti ti-refresh" :loading="busy || !!data.pending" :disabled="!!data.missing.length" @click="regenerate">Write again</v-btn>
        </template>
      </div>
    </template>
  </div>`,
};

const STYLE = `
.ib { font-size: 13px; }
.ib-sec { margin: 0 0 10px; }
.ib-sec h3 { font-size: 12px; font-weight: 650; margin: 0 0 4px; color: var(--ink-2, inherit); text-transform: none; }
.ib-sec ul { margin: 0; padding-inline-start: 18px; }
.ib-sec ul.ib-next { list-style: none; padding-inline-start: 0; }
.ib-sec li { margin: 2px 0; line-height: 1.45; overflow-wrap: anywhere; }
.ib-act { display: inline-flex; gap: 6px; align-items: baseline; font-weight: 550; }
.ib-muted { color: var(--ink-3); }
.ib-foot { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; border-top: 1px solid var(--line); padding-top: 8px; margin-top: 4px; }
.ib-ver { max-width: 260px; }
.ib-compact .ib-sec { margin-bottom: 6px; }
`;

if (!document.getElementById('ib-style')) document.head.insertAdjacentHTML('beforeend', `<style id="ib-style">${STYLE}</style>`);
