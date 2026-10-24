// What the person's Noticer found: one source resembles another, why, and what to do next.
// With `sha`, only resemblances of that source, the other source named first.
export const NoticerPanel = {
  name: 'NoticerPanel',
  props: {investigation: {type: String, default: ''}, sha: {type: String, default: ''}, revealed: {type: Boolean, default: false}, limit: {type: Number, default: 0}},
  emits: ['changed'],
  data() { return {report: null, error: '', note: '', starting: false, timer: null, sending: '', gen: 0}; },
  computed: {
    ready() { return this.investigation && this.investigation !== 'all'; },
    items() { const all = this.report?.items || []; return this.limit ? all.slice(0, this.limit) : all; },
    more() { return this.limit && (this.report?.items.length || 0) > this.limit; },
    busy() { return this.starting || this.report?.pending; },
  },
  watch: {
    investigation() { this.report = null; this.load(); },
    revealed() { this.load(); },
  },
  mounted() { this.load(); },
  beforeUnmount() { this.gen++; clearTimeout(this.timer); },
  methods: {
    async load() {
      clearTimeout(this.timer);
      if (!this.ready) return;
      const gen = ++this.gen;
      try {
        const {data} = await axios.get('/api/agents/notice', {params: {investigation: this.investigation, sha: this.sha || undefined}});
        if (gen !== this.gen) return;
        const was = this.report?.pending;
        this.report = data; this.error = '';
        if (data.pending) this.timer = setTimeout(() => this.load(), 4000);
        else if (was) this.$emit('changed');
      } catch { if (gen === this.gen) this.error = 'Could not load what the noticer found.'; }
    },
    async check() {
      this.starting = true; this.note = '';
      try {
        await axios.post('/api/agents/notice', {item: {investigation: this.investigation}});
        this.$emit('changed');
        await this.load();
      } catch (e) {
        if (e.response?.data?.reason === 'busy') await this.load();
        else this.note = e.response?.data?.message || 'Could not start the noticer.';
      } finally { this.starting = false; }
    },
    other(item) { return this.sha && item.there.href === '/evidence/' + this.sha ? item.here : item.there; },
    self(item) { return this.sha && item.there.href === '/evidence/' + this.sha ? item.there : item.here; },
    async propose(item) {
      this.sending = item.key; this.note = '';
      try {
        await axios.post('/api/agents/notice/link', {item: {run: item.run, key: item.key}});
        item.link = 'proposed';
        this.note = 'Link proposed. It waits for review.';
      } catch (e) { this.note = e.response?.data?.message || 'Could not propose this link. Try again.'; }
      finally { this.sending = ''; }
    },
  },
  template: `<section aria-label="Noticer" style="min-width: 0; max-width: 100%; overflow-wrap: anywhere">
    <div class="d-flex align-center flex-wrap ga-2"><h3 style="font-size: 15px">This resembles</h3><v-spacer></v-spacer>
      <v-btn v-if="!sha" size="small" variant="outlined" :disabled="!ready" :loading="!!busy" @click="check">Check now</v-btn></div>
    <p class="text-medium-emphasis mb-2">Resemblances the noticer proposes. Nothing here is a match until a person decides.</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="note" role="status">{{ note }}</p>
    <p v-if="busy" aria-live="polite">Reading the case for resemblances.</p>
    <p v-if="report?.failed && !busy" role="alert">The last check could not finish. Try again.</p>
    <p v-if="report && !report.items.length && !busy">{{ report.outcome?.reason || (sha ? 'Nothing found that resembles this source yet.' : 'No check yet. Press Check now to compare the whole case.') }}</p>
    <article v-for="item in items" :key="item.key || item.href" class="mt-3">
      <p v-if="item.locked"><i class="ti ti-lock" aria-hidden="true"></i> {{ item.count === 1 ? 'One resemblance rests' : item.count + ' resemblances rest' }} on <a :href="item.href" dir="auto">{{ item.title }}</a>. Reveal it to read {{ item.count === 1 ? 'it' : 'them' }}.</p>
      <template v-else>
        <div><v-chip size="x-small" color="agent" variant="tonal" class="me-1">{{ item.kind }}</v-chip>
          <template v-if="!sha"><a :href="item.here.href" dir="auto">{{ item.here.title }}</a> and </template><a :href="other(item).href" dir="auto">{{ other(item).title }}</a><span v-if="other(item).case" class="text-medium-emphasis"> in {{ other(item).case }}</span></div>
        <p class="mt-1" dir="auto">{{ item.why }}</p>
        <p class="mt-1 text-medium-emphasis" dir="auto">Next: {{ item.next }}</p>
        <details class="mt-1"><summary>What it rests on</summary>
          <div v-for="(s, i) in item.signals" :key="'s' + i" dir="auto">{{ s }}</div>
          <div v-for="(c, i) in item.claims" :key="'c' + i"><span dir="auto">{{ c.field }}: {{ c.value }}</span><span v-if="c.when">, {{ c.when }}</span>. <a :href="c.href" dir="auto">{{ c.title }}</a></div>
        </details>
        <div class="d-flex ga-2 mt-1 flex-wrap align-center">
          <v-btn v-if="item.link === 'can'" size="x-small" variant="outlined" prepend-icon="ti ti-link" :loading="sending === item.key" @click="propose(item)">Propose a link</v-btn>
          <span v-else-if="item.link === 'proposed'" class="text-medium-emphasis">A link is proposed and waits for <a href="/review">review</a>.</span>
          <v-btn size="x-small" variant="text" prepend-icon="ti ti-layout-columns" :href="self(item).href + '?with=' + other(item).href.slice(10)">Side by side</v-btn>
        </div>
      </template>
    </article>
    <p v-if="more" class="mt-2">{{ report.items.length - limit }} more on the evidence pages they are about.</p>
    <p v-if="report?.deferred" class="mt-2">More resemblances remain for the next check.</p>
  </section>`,
};
