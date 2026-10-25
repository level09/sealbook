import {KeepChecking} from '/static/js/components/KeepChecking.js';

export const ContradictionHunter = {
  name: 'ContradictionHunter',
  components: {KeepChecking},
  props: {investigation: {type: String, required: true}},
  data() { return {items: [], outcome: null, running: false, busy: false, note: '', timer: null, generation: 0}; },
  watch: {investigation: {immediate: true, handler() {
    clearTimeout(this.timer); this.generation++; this.items = []; this.outcome = null;
    this.running = false; this.busy = false; this.note = ''; this.load(this.generation);
  }}},
  beforeUnmount() { this.generation++; clearTimeout(this.timer); },
  methods: {
    async load(generation) {
      if (!this.investigation) return;
      try {
        const {data} = await axios.get('/api/agents/contradictions', {params: {investigation: this.investigation}});
        if (generation !== this.generation) return;
        this.items = data.items; this.outcome = data.outcome; this.busy = data.busy;
        if (data.pending) { this.running = true; this.poll(data.pending, generation); }
        else if (data.busy) {
          this.note = 'A colleague is checking this case.';
          this.timer = setTimeout(() => this.load(generation), 4000);
        }
      } catch { if (generation === this.generation) this.note = 'The report could not be loaded. Try again.'; }
    },
    async start() {
      const generation = this.generation;
      this.running = true; this.note = 'Reading the case for disagreements.';
      try {
        const {data} = await axios.post('/api/agents/contradictions', {item: {investigation: this.investigation}});
        if (generation === this.generation) this.poll(data.run, generation);
      } catch (e) {
        if (generation !== this.generation) return;
        this.running = false;
        const reason = e.response?.data?.reason;
        this.note = reason === 'off' ? 'The contradiction hunter is not set up. Ask an administrator to enable it.' :
          reason === 'busy' ? 'A colleague is already checking this case.' : 'The check could not start. Try again.';
        if (reason === 'busy') this.load(generation);
      }
    },
    async poll(id, generation) {
      try {
        const {data} = await axios.get('/api/agents/contradictions/runs/' + id);
        if (generation !== this.generation) return;
        if (['queued', 'running'].includes(data.status)) {
          this.timer = setTimeout(() => this.poll(id, generation), 4000); return;
        }
        this.running = false; this.busy = false;
        this.note = data.status === 'failed' ? 'The check could not finish. Try again.' : '';
        await this.load(generation);
      } catch {
        if (generation === this.generation) { this.running = false; this.note = 'Progress could not be read. Refresh to check again.'; }
      }
    },
    async copy(question) {
      try { await navigator.clipboard.writeText(question); this.note = 'Question copied.'; }
      catch { this.note = 'Copy failed. Select the question and copy it.'; }
    },
  },
  template: `<section aria-label="Contradiction hunter" class="mt-4">
    <h2>Contradiction hunter</h2>
    <p class="muted mb-2">Checks the selected case and drafts questions for a colleague. You decide what to do.</p>
    <keep-checking kind="contradictions" :investigation="investigation"></keep-checking>
    <v-btn size="small" variant="tonal" :loading="running" :disabled="!investigation || busy" @click="start">Look for contradictions</v-btn>
    <p v-if="!investigation" class="muted mt-2">Choose a case to check.</p>
    <p v-if="running || note" class="muted mt-2" role="status">{{ running ? 'Reading the case for disagreements.' : note }}</p>
    <p v-if="outcome && !items.length" class="muted mt-2">{{ outcome.reason }}</p>
    <article v-for="(item, i) in items" :key="i" class="mt-3">
      <template v-if="item.locked.length">
        <p class="muted">Open the sensitive source to reveal this question:</p>
        <a v-for="source in item.locked" :key="source.href" :href="source.href" class="me-2" dir="auto">{{ source.title }}</a>
        <v-btn size="small" variant="text" @click="load(generation)">Refresh after reveal</v-btn>
      </template>
      <template v-else>
        <div v-for="(claim, n) in item.claims" :key="n" class="mb-1">
          <span dir="auto">{{ claim.field }}: {{ claim.value }}</span>
          <span v-if="claim.person">, for <a :href="'/people/' + claim.person" dir="auto">{{ claim.name || 'an unnamed person' }}</a></span>
          <span v-if="claim.when" class="muted">, {{ claim.when }}</span>
          <a :href="claim.href" class="ms-2" dir="auto">{{ claim.source }}</a>
          <span v-if="claim.source_date" class="muted">, {{ claim.source_date }}</span>
          <span v-if="claim.decided" class="ppl-tag ms-2">Accepted by a reviewer</span>
        </div>
        <p class="mt-2" dir="auto">{{ item.why }}</p>
        <p class="mt-2" dir="auto">{{ item.question }}</p>
        <v-btn size="small" variant="text" prepend-icon="ti ti-copy" @click="copy(item.question)">Copy question</v-btn>
      </template>
    </article>
  </section>`,
};
