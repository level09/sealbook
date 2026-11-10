/* The intake brief for one batch: what arrived, who, where and when, what it
   connects to, what disagrees, and next steps that open the thing to do. Lines
   resting on a sensitive source this session has not revealed show locked, with a
   link to the source page where the logged reveal happens.

   import {IntakeBrief} from '/static/js/components/IntakeBrief.js';
   app.component('intake-brief', IntakeBrief);
   <intake-brief :batch="batchId" :compact="true"></intake-brief>

   compact: the Desk's version, without versions or the regenerate button.
   grid: kept for callers; the layout is the same everywhere now. */
const SECTIONS = [
  {key: 'arrived', title: 'What arrived', icon: 'ti ti-files', tile: true},
  {key: 'who', title: 'Who', icon: 'ti ti-users', tile: true},
  {key: 'where', title: 'Where', icon: 'ti ti-map-pin', tile: true},
  {key: 'when', title: 'When', icon: 'ti ti-calendar-event', tile: true},
  {key: 'connects', title: 'What it connects to', icon: 'ti ti-link'},
  {key: 'disagrees', title: 'What disagrees', icon: 'ti ti-arrows-diff'},
  {key: 'next', title: 'Next steps', icon: 'ti ti-list-check'},
];

export const IntakeBrief = {
  name: 'IntakeBrief',
  props: {batch: {type: String, required: true}, compact: {type: Boolean, default: false}, grid: {type: Boolean, default: false}},
  data() {return {SECTIONS, data: null, error: '', busy: false, version: null, timer: null};},
  computed: {
    brief() {return this.data?.brief;},
    tiles() {return this.shown.filter(s => s.tile);},
    cards() {return this.shown.filter(s => !s.tile && s.key !== 'next');},
    // One sentence before the detail: how much came in and how much of it is new.
    summary() {
      const b = this.brief, n = (k) => (b[k] || []).length, fresh = (b.who || []).filter(l => this.isNew(l.text)).length;
      const parts = [`${n('arrived')} ${n('arrived') === 1 ? 'source' : 'sources'} read`];
      if (n('who')) parts.push(`${n('who')} ${n('who') === 1 ? 'person' : 'people'} named${fresh ? ', ' + fresh + ' new to the case' : ''}`);
      if (n('connects')) parts.push(`${n('connects')} ${n('connects') === 1 ? 'link' : 'links'} to what the case holds`);
      if (n('disagrees')) parts.push(`${n('disagrees')} ${n('disagrees') === 1 ? 'disagreement' : 'disagreements'}`);
      return parts.join(' · ');
    },
    shown() {return this.brief ? SECTIONS.filter(s => (this.brief[s.key] || []).length || ['connects', 'disagrees'].includes(s.key)) : [];},
    cost() {
      const v = this.data?.version;
      if (!v) return '';
      if (v.fixed) return 'Fixed demo output, no model ran.';
      if (v.rules) return 'Counted and written by rules; no model is set up, so no links were looked for.';
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
    isNew(text) { return /\bnew to\b/.test(text || ''); },
    // A counted line is "name, what is known" or "title: what it is": the name leads.
    split(key, text) {
      const cut = text.indexOf(key === 'arrived' ? ': ' : ', ');
      if (cut < 0 || key === 'when') return [text.replace(/\.$/, ''), ''];
      return [text.slice(0, cut), text.slice(cut + 2).replace(/\.$/, '')];
    },
  },
  template: `<div class="ib" :class="{'ib-compact': compact}" aria-live="polite">
    <p v-if="error" class="ib-muted">{{ error }}</p>
    <p v-else-if="!data" class="ib-muted">Loading the brief</p>
    <template v-else-if="!brief">
      <p v-if="data.pending" class="ib-muted"><v-progress-circular indeterminate size="14" width="2" class="me-1"></v-progress-circular> The brief is being written.</p>
      <p v-else-if="data.failed" class="ib-muted">The brief could not be written: {{ data.failed.error }}</p>
      <p v-else class="ib-muted">No brief was written for this batch.</p>
      <v-btn v-if="!compact && !data.pending" size="small" variant="outlined" prepend-icon="ti ti-sparkles" :loading="busy" @click="regenerate">Write a brief</v-btn>
    </template>
    <template v-else>
      <header class="ib-top">
        <p class="ib-sum">{{ summary }}</p>
        <v-btn :href="'/ingest/answer?batch=' + batch" size="small" variant="flat" color="primary" append-icon="ti ti-arrow-right">Read it as one answer</v-btn>
      </header>
      <div class="ib-tiles">
        <section v-for="s in tiles" :key="s.key" class="ib-tile">
          <h3><i :class="s.icon" aria-hidden="true"></i>{{ s.title }}</h3>
          <ul>
            <li v-for="(line, i) in brief[s.key]" :key="i">
              <span v-if="line.locked.length" class="ib-lock"><i class="ti ti-lock" aria-hidden="true"></i> From a sensitive source. <a :href="line.href">Show it there</a></span>
              <component :is="line.href ? 'a' : 'span'" v-else :href="line.href" class="ib-item" :class="{fresh: isNew(line.text)}">
                <b dir="auto">{{ split(s.key, line.text)[0] }}</b><span v-if="split(s.key, line.text)[1]" dir="auto">{{ split(s.key, line.text)[1] }}</span>
              </component>
            </li>
          </ul>
        </section>
      </div>
      <div class="ib-cards">
        <section v-for="s in cards" :key="s.key" class="ib-card" :class="'ib-' + s.key">
          <h3><i :class="s.icon" aria-hidden="true"></i>{{ s.title }}</h3>
          <p v-if="!brief[s.key].length" class="ib-muted">{{ s.key === 'disagrees' ? 'Nothing in this batch disagrees with an accepted claim.' : 'Nothing the brief could cite.' }}</p>
          <ul>
            <li v-for="(line, i) in brief[s.key]" :key="i">
              <span v-if="line.locked.length" class="ib-lock"><i class="ti ti-lock" aria-hidden="true"></i> A line from a sensitive source, <bdi>{{ line.locked.map(x => x.title).join(', ') }}</bdi>. <a :href="line.href">Show it there</a></span>
              <template v-else><span dir="auto">{{ line.text }}</span> <a v-if="line.href" :href="line.href">Open</a></template>
            </li>
          </ul>
        </section>
      </div>
      <section v-if="(brief.next || []).length" class="ib-steps">
        <h3><i class="ti ti-list-check" aria-hidden="true"></i>Next steps</h3>
        <ol>
          <li v-for="(line, i) in brief.next" :key="i">
            <span v-if="line.locked.length" class="ib-lock"><i class="ti ti-lock" aria-hidden="true"></i> A step on a sensitive source. <a :href="line.href">Show it there</a></span>
            <a v-else :href="line.href" class="ib-step"><span class="ib-n">{{ i + 1 }}</span><span dir="auto">{{ split('when', line.text)[0] }}</span><i class="ti ti-arrow-right" aria-hidden="true"></i></a>
          </li>
        </ol>
      </section>
      <div class="ib-foot">
        <span class="ib-muted"><i class="ti ti-robot" aria-hidden="true"></i> {{ cost }} Every line cites the claims it rests on; nothing here is accepted.</span>
        <template v-if="!compact">
          <v-select v-if="versions.length > 1" :model-value="version" :items="versions" density="compact" hide-details variant="outlined" class="ib-ver" aria-label="Brief version" @update:model-value="pick"></v-select>
          <v-btn size="small" variant="text" prepend-icon="ti ti-refresh" :loading="busy || !!data.pending" @click="regenerate">Write again</v-btn>
        </template>
      </div>
    </template>
  </div>`,
};

const STYLE = `
.ib { font-size: 13.5px; display: grid; gap: 12px; }
.brief .ib { padding: 14px; }
.ib h3 { display: flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 650; margin: 0 0 8px; color: var(--ink-2, inherit); text-transform: none; }
.ib h3 .ti { font-size: 16px; color: var(--ink-3); }
.ib ul, .ib ol { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.ib li { line-height: 1.45; overflow-wrap: anywhere; }
.ib-muted { color: var(--ink-3); margin: 0; }
.ib-top { display: flex; align-items: center; justify-content: space-between; gap: 10px 16px; flex-wrap: wrap; }
.ib-sum { margin: 0; font-size: 15px; font-weight: 600; letter-spacing: -.01em; }
.ib-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; }
.ib-tile, .ib-card { border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; background: var(--raise); min-width: 0; }
/* A name, then what is known of it. New to the case is dashed, as a proposal is everywhere. */
.ib-item { display: grid; gap: 0; padding-inline-start: 10px; border-inline-start: 2px solid var(--line-strong); color: inherit; text-decoration: none !important; }
.ib-item.fresh { border-inline-start-style: dashed; border-inline-start-color: var(--agent); }
a.ib-item:hover b { text-decoration: underline; text-underline-offset: 2px; }
/* An Arabic name keeps its own direction but lines up with the rest of the column. */
.ib-item b, .ib-item span { justify-self: start; }
.ib-item b { font-weight: 600; }
.ib-item span { color: var(--ink-3); font-size: 12.5px; }
.ib-lock { color: var(--ink-3); }
.ib-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; }
.ib-card { background: none; }
.ib-steps ol { gap: 6px; }
.ib-step { display: grid; grid-template-columns: 22px minmax(0, 1fr) 16px; gap: 10px; align-items: center; padding: 8px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: inherit; font-weight: 550; text-decoration: none !important; }
.ib-step:hover { border-color: var(--ink-3); background: var(--raise); }
.ib-steps li:first-child .ib-step { border-color: var(--ink); }
.ib-n { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; background: var(--wash); font-size: 12px; font-weight: 650; }
.ib-step .ti { color: var(--ink-3); }
.ib-foot { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; border-top: 1px solid var(--line); padding-top: 10px; }
.ib-ver { max-width: 260px; }
.ib-compact { gap: 10px; }
.ib-compact .ib-sum { font-size: 13.5px; }
.ib-compact .ib-tiles { grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }
`;

if (!document.getElementById('ib-style')) document.head.insertAdjacentHTML('beforeend', `<style id="ib-style">${STYLE}</style>`);
