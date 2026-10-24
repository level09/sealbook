import {ResearchDrawer} from '/static/js/components/ResearchDrawer.js';
import {KeepChecking} from '/static/js/components/KeepChecking.js';

export const GapFinder = {
  name: 'GapFinder',
  components: {KeepChecking, ResearchDrawer},
  props: {investigation: {type: String, default: ''}, person: {type: String, default: ''}},
  data() { return {report: null, error: '', timer: null, starting: false, research: false, researchScope: ''}; },
  computed: {
    caseReady() { return this.investigation && this.investigation !== 'all'; },
    items() { return (this.report?.items || []).filter(i => !this.person || i.person === 'person:' + this.person); },
    // Consecutive locked suggestions fold into one line per sensitive source.
    rows() {
      const rows = [];
      for (const item of this.items) {
        const last = rows[rows.length - 1];
        if (!item.locked.length) { rows.push(item); continue; }
        const block = last?.folded ? last : (rows.push({folded: []}), rows[rows.length - 1]);
        for (const s of item.locked) {
          const seen = block.folded.find(x => x.href === s.href);
          if (seen) seen.count++; else block.folded.push({...s, count: 1});
        }
      }
      return rows;
    },
    busy() { return this.starting || this.report?.pending; },
    empty() { return this.person && this.report?.items.length ? 'No new gaps were reported for this person.' : this.report?.outcome.reason; },
  },
  watch: {investigation() { this.report = null; this.load(); }},
  mounted() { this.load(); },
  beforeUnmount() { clearTimeout(this.timer); },
  methods: {
    async load() {
      clearTimeout(this.timer);
      if (!this.caseReady) return;
      const investigation = this.investigation;
      try {
        const {data} = await axios.get('/api/agents/gaps', {params: {investigation}});
        if (investigation !== this.investigation) return;
        this.report = data;
        if (data.pending) this.timer = setTimeout(() => this.load(), 4000);
      } catch (e) { this.error = 'Could not load gap suggestions.'; }
    },
    async start() {
      this.starting = true; this.error = '';
      try {
        await axios.post('/api/agents/gaps', {item: {investigation: this.investigation}});
        await this.load();
      } catch (e) {
        if (e.response?.data?.reason === 'busy') await this.load();
        else this.error = e.response?.data?.message || 'Could not start gap finder.';
      } finally { this.starting = false; }
    },
    async search(item, query) {
      this.researchScope = item.person;
      await this.$nextTick();
      const drawer = this.$refs.research;
      await drawer.start();
      drawer.queries = [{text: query, kinds: drawer.kinds.filter(k => drawer.usable(k)), why: [{label: 'Gap', value: item.window_text, cases: [this.investigation]}], people: []}];
      this.research = true;
    },
  },
  template: `<section class="pa-4 my-4" style="min-width: 0; max-width: 100%; overflow-wrap: anywhere" aria-label="Gaps">
    <div class="d-flex align-center flex-wrap ga-2 mb-2"><h2 style="font-size: 16px">Gaps</h2><v-spacer></v-spacer>
      <v-btn size="small" variant="text" :disabled="!caseReady" :loading="!!busy" @click="start">Find gaps</v-btn></div>
    <p class="text-medium-emphasis mb-2">Places to look next. These suggestions need your review.</p>
    <keep-checking kind="gaps" :investigation="investigation"></keep-checking>
    <p v-if="!caseReady">Choose a case to find gaps.</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="busy" aria-live="polite">Reading the case for gaps.</p>
    <p v-if="report?.failed" role="alert">Gap finder could not finish. Try again.</p>
    <p v-if="report && !items.length && !busy">{{ empty }}</p>
    <article v-for="(item, index) in rows" :key="index" class="mt-4">
      <template v-if="item.folded"><p v-for="source in item.folded" :key="source.href"><i class="ti ti-lock" aria-hidden="true"></i> {{ source.count === 1 ? 'One suggestion rests' : source.count + ' suggestions rest' }} on <a :href="source.href" dir="auto">{{ source.title }}</a>. Reveal it to read {{ source.count === 1 ? 'it' : 'them' }}.</p></template>
      <template v-else>
        <h3 style="font-size: 14px"><a v-if="!person" :href="'/people/' + item.person.slice(7)">{{ item.name || 'Unnamed person' }}</a><span v-if="!person">: </span>{{ item.window_text }}</h3>
        <p v-if="item.maybe" class="text-medium-emphasis">May be the same person as <a :href="'/people/' + item.maybe.person.slice(7)" dir="auto">{{ item.maybe.name || 'an unnamed person' }}</a> {{ item.maybe.where }}. A proposal links the two records and awaits review.</p>
        <div v-for="(claim, i) in item.claims" :key="i" class="mt-1"><span dir="auto">{{ claim.text }}</span><span v-if="claim.when">, {{ claim.when }}</span>. <a :href="claim.source.href" dir="auto">{{ claim.source.title }}</a></div>
        <template v-if="item.leads.length"><p class="mt-2 text-medium-emphasis">Other people were recorded at the same place in this window.</p>
          <div v-for="(claim, i) in item.leads" :key="'l' + i" class="mt-1"><span dir="auto">{{ claim.text }}</span><span v-if="claim.when">, {{ claim.when }}</span>. <a :href="claim.source.href" dir="auto">{{ claim.source.title }}</a></div></template>
        <p class="mt-2" dir="auto">{{ item.where }}</p>
        <div v-for="query in item.searches" :key="query" class="mt-1"><bdi dir="auto">{{ query }}</bdi> <a href="#research" @click.prevent="search(item, query)">Search in research</a></div>
        <p v-if="item.colleagues.length" class="mt-2">Related work by {{ item.colleagues.join(', ') }}.</p>
      </template>
    </article>
    <p v-if="report?.deferred" class="mt-3">More gaps remain for the next check.</p>
    <research-drawer v-if="researchScope" ref="research" v-model="research" :scope="researchScope" :case="investigation" :cases="[investigation]"></research-drawer>
  </section>`,
};
