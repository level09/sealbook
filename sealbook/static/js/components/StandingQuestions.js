export const StandingQuestions = {
  name: 'StandingQuestions',
  props: {investigation: {type: String, default: null}},
  data() {return {question: '', items: [], results: {}, error: '', off: false, busy: false, timer: null};},
  watch: {investigation() { this.results = {}; this.load(); }},
  mounted() { this.load(); },
  beforeUnmount() { clearTimeout(this.timer); },
  methods: {
    async load() {
      clearTimeout(this.timer);
      const investigation = this.investigation;
      try {
        const {data} = await axios.get('/api/agents/tasks', {params: {investigation: investigation || undefined}});
        if (investigation !== this.investigation) return;
        this.items = data.items; this.off = data.off;
        const results = {};
        await Promise.all(data.items.map(async item => {
          results[item.id] = (await axios.get('/api/agents/tasks/' + item.id)).data;
        }));
        if (investigation !== this.investigation) return;
        this.results = results; this.error = '';
      } catch (e) { this.error = e.response?.data?.description || e.message; }
      this.timer = setTimeout(() => this.load(), 4000);
    },
    async act(url, method, item) {
      this.busy = true;
      try { await axios({url, method, data: {item}}); await this.load(); }
      catch (e) { this.error = e.response?.data?.message || e.response?.data?.description || e.message; }
      this.busy = false;
    },
    async ask() {
      await this.act('/api/agents/tasks', 'post', {investigation: this.investigation, kind: 'standing', question: this.question});
      if (!this.error) this.question = '';
    },
    ago(iso) {
      const s = (Date.now() - new Date(iso)) / 1000;
      return s < 60 ? 'now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`;
    },
    count(n) { return n === 1 ? '1 match' : `${n} matches`; },
  },
  template: `<div aria-live="polite">
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="off" class="text-medium-emphasis">Standing questions are off until the local model is set up.</p>
    <form v-if="investigation" @submit.prevent="ask" class="d-flex ga-2 align-start mb-3">
      <v-textarea v-model="question" label="What should I watch for in this case?" rows="2" auto-grow maxlength="300" hide-details variant="outlined"></v-textarea>
      <v-btn type="submit" :disabled="question.trim().length < 3 || off" :loading="busy">Watch for this</v-btn>
    </form>
    <p v-else class="text-medium-emphasis">Choose a case to ask a standing question.</p>
    <p v-if="!items.length" class="text-medium-emphasis">Ask once. The agent checks new evidence and checks the case on a schedule. It proposes cited matches for you to review.</p>
    <section v-for="item in items" :key="item.id" class="mb-3">
      <h3 class="text-body-1" dir="auto">{{ item.question }}</h3>
      <p class="text-caption text-medium-emphasis">{{ item.state === 'checking' ? 'Checking' : item.state === 'paused' ? 'Paused' : 'Watching' }}, {{ item.last_checked ? 'checked ' + ago(item.last_checked) : 'not checked yet' }}<template v-if="item.matches !== null">. {{ count(item.matches) }} in the last check</template></p>
      <p v-if="item.reason" class="text-caption">{{ item.reason }}</p>
      <v-btn size="small" variant="text" :disabled="busy || off || item.state !== 'watching'" @click="act('/api/agents/tasks/' + item.id + '/run', 'post', {})">Check now</v-btn>
      <v-btn size="small" variant="text" :disabled="busy" @click="act('/api/agents/tasks/' + item.id, 'patch', {enabled: item.state === 'paused'})">{{ item.state === 'paused' ? 'Resume' : 'Stop' }}</v-btn>
      <template v-if="results[item.id]">
        <p v-if="results[item.id].fixed" class="text-caption text-medium-emphasis">Fixed demo output, no model ran.</p>
        <div v-if="results[item.id].matches.length">
          <template v-for="(group, i) in results[item.id].matches" :key="i">
            <p v-if="group.locked" class="mt-2"><i class="ti ti-lock" aria-hidden="true"></i> {{ group.count === 1 ? 'One match rests' : group.count + ' matches rest' }} on <a :href="group.href" dir="auto">{{ group.title }}</a>. Open it to read {{ group.count === 1 ? 'it' : 'them' }}.</p>
            <div v-else class="mt-2">
              <a v-if="group.person" :href="'/people/' + group.person" class="font-weight-medium" dir="auto">{{ group.name || 'Unnamed person' }}</a>
              <span v-else class="font-weight-medium">Not about a person</span>
              <ul class="ps-5">
                <li v-for="(match, j) in group.matches" :key="j"><template v-for="(claim, k) in match.claims" :key="k"><span dir="auto">{{ claim.field }}: {{ claim.value }}</span><span v-if="claim.when">, {{ claim.when }}</span>. <a :href="claim.href" dir="auto">{{ claim.title }}</a>. </template><span class="text-medium-emphasis" dir="auto">{{ match.why }}</span></li>
              </ul>
            </div>
          </template>
        </div>
        <p v-else-if="results[item.id].outcome" class="text-caption text-medium-emphasis">{{ results[item.id].outcome.reason }}</p>
        <p v-if="results[item.id].deferred" class="text-caption">Some claims still need checking. The agent will continue with them.</p>
        <details v-if="results[item.id].history.length" class="text-caption"><summary>Past checks</summary><p v-for="(run, i) in results[item.id].history" :key="i">{{ ago(run.at) }}: {{ run.state }}<template v-if="run.reason">. {{ run.reason }}</template></p></details>
      </template>
    </section>
  </div>`,
};
