// Ask the case a question now. The answer is sentences, each with the claims it
// rests on; a reading to check, never a finding.
export const AskCase = {
  name: 'AskCase',
  props: {investigation: {type: String, required: true}},
  data() { return {question: '', items: [], busy: false, note: '', timer: null, generation: 0}; },
  watch: {investigation: {immediate: true, handler() {
    clearTimeout(this.timer); this.generation++; this.items = []; this.note = ''; this.load(this.generation);
  }}},
  beforeUnmount() { this.generation++; clearTimeout(this.timer); },
  computed: {
    ready() { const n = this.question.trim().length; return n >= 3 && n <= 500 && !this.busy; },
  },
  methods: {
    async load(generation) {
      if (!this.investigation) return;
      try {
        const {data} = await axios.get('/api/agents/ask', {params: {investigation: this.investigation}});
        if (generation !== this.generation) return;
        this.items = data.items; this.busy = data.busy;
        if (data.busy) this.timer = setTimeout(() => this.load(generation), 3000);
      } catch { if (generation === this.generation) this.note = 'Earlier answers could not be loaded. Try again.'; }
    },
    async send() {
      if (!this.ready) return;
      const generation = this.generation;
      this.busy = true; this.note = '';
      try {
        await axios.post('/api/agents/ask', {item: {investigation: this.investigation, question: this.question.trim()}});
        this.question = '';
      } catch (e) {
        const reason = e.response?.data?.reason;
        this.note = reason === 'off' ? 'Questions need the agents model. Ask an administrator to set it up.' :
          reason === 'busy' ? 'Your last question is still being answered.' :
          apiError(e, 'The question could not be sent. Try again.');
      }
      if (generation === this.generation) this.load(generation);
    },
    // One chip per source, however many of its claims a sentence cites.
    bySource(claims) {
      const m = new Map();
      for (const c of claims) { const g = m.get(c.href) || {href: c.href, title: c.source, n: 0}; g.n++; m.set(c.href, g); }
      return [...m.values()];
    },
    lockedOf(q) { return [...new Map(q.sentences.flatMap(s => s.locked).map(l => [l.href, l])).values()]; },
    when(iso) { return new Date(iso).toLocaleString(undefined, {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}); },
  },
  template: `<section class="ov-panel ask-panel" aria-labelledby="ask-h">
    <header>
      <h2 id="ask-h" class="text-title-large">Ask this case</h2>
      <p class="ov-muted">An agent reads every claim in the case and answers in a few sentences, each with its sources. Check them; nothing here is accepted.</p>
    </header>
    <form class="ask-form" @submit.prevent="send">
      <v-text-field v-model="question" label="Your question" placeholder="Where was Kassar held in 2013?" density="comfortable" hide-details="auto" maxlength="500" :disabled="busy"></v-text-field>
      <v-btn type="submit" color="agent" variant="flat" prepend-icon="ti ti-sparkles" :disabled="!ready" :loading="busy">Ask</v-btn>
    </form>
    <p v-if="note" class="ov-muted mt-2" role="status">{{ note }}</p>
    <article v-for="q in items" :key="q.id" class="ask-q">
      <div class="ask-head"><span class="ask-question" dir="auto">{{ q.question }}</span><span class="ov-muted text-body-small">{{ when(q.at) }}</span></div>
      <p v-if="q.status === 'queued' || q.status === 'running'" class="ov-muted" role="status">Reading the case to answer.</p>
      <p v-else-if="q.status === 'failed'" class="ov-muted">The answer could not be finished. Ask again.</p>
      <template v-else>
        <p v-if="q.sentences.length && q.sentences.every(s => !s.text)" class="ov-muted"><i class="ti ti-lock" aria-hidden="true"></i> The answer has {{ q.sentences.length }} sentence{{ q.sentences.length > 1 ? 's' : '' }} resting on <template v-for="(l, j) in lockedOf(q)" :key="l.href">{{ j ? ', ' : '' }}<a :href="l.href" dir="auto">{{ l.title }}</a></template>, marked sensitive. Open a source to read them; the look is logged.</p>
        <ol v-else-if="q.sentences.length" class="ask-lines">
          <li v-for="(s, i) in q.sentences" :key="i">
            <template v-if="s.text"><span dir="auto">{{ s.text }}</span>
              <span class="ask-cites"><a v-for="g in bySource(s.claims)" :key="g.href" :href="g.href" class="ask-cite" :title="g.title"><i class="ti ti-file-text" aria-hidden="true"></i><span class="ask-src" dir="auto">{{ g.title }}</span><span v-if="g.n > 1" class="ask-n">{{ g.n }}</span></a></span>
              <details class="ask-claims"><summary>{{ s.claims.length }} claim{{ s.claims.length > 1 ? 's' : '' }} cited</summary>
                <ul><li v-for="c in s.claims" :key="c.id"><i class="ti" :class="c.decided ? 'ti-circle-check' : 'ti-circle-dashed'" :title="c.decided ? 'accepted' : 'not yet decided'" aria-hidden="true"></i> {{ c.field }}: <span dir="auto">{{ c.value }}</span><template v-if="c.when">, {{ c.when }}</template></li></ul>
              </details>
            </template>
            <span v-else class="ov-muted"><i class="ti ti-lock" aria-hidden="true"></i> Rests on <template v-for="(l, j) in s.locked" :key="l.href">{{ j ? ', ' : '' }}<a :href="l.href" dir="auto">{{ l.title }}</a></template>, marked sensitive. Open it to read this sentence; the look is logged.</span>
          </li>
        </ol>
        <p v-else class="ov-muted">{{ q.outcome?.reason || 'No answer.' }}</p>
        <p v-if="q.unread || q.over" class="ov-muted text-body-small">{{ q.unread + q.over }} claims {{ q.unread ? 'were not read in time' : 'were past what one answer reads' }}; the answer may miss them.</p>
        <p v-if="q.demo" class="ov-muted text-body-small">Demo answer from a fixed rule, no model ran.</p>
      </template>
    </article>
  </section>`,
};
