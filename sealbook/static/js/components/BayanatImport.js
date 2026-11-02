/* A Bayanat export on Add evidence, once stored: what it holds, then one import
   that makes an item of each bulletin and media file, then one passkey gesture that
   accepts what Bayanat's analysts decided (analyst titles and labels). Nothing is
   accepted by the import itself. */
import {attest} from '/static/js/monitor/passkey.js';

const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
const err = e => e.response?.data?.message || e.response?.data?.description || e.message;

export const BayanatImport = {
  props: {it: {type: Object, required: true}},
  data() { return {p: null, done: null, error: '', busy: false, needReveal: false, accepted: false}; },
  computed: {
    statuses() { return Object.entries(this.p?.statuses || {}).map(([s, n]) => `${n} ${s}`).join(', '); },
  },
  mounted() { if (this.it.sens) this.needReveal = true; else this.load(false); },
  methods: {
    plural,
    async load(reveal) {
      this.error = '';
      try {
        if (reveal) await axios.post(`/api/evidence/${this.it.sha}/reveal`, {item: {}});
        this.p = (await axios.get(`/api/bayanat/${this.it.acquisition}/preview`)).data;
        this.done = this.p.imported?.counts ? this.p.imported : null;
        this.needReveal = false;
      } catch (e) {
        if (e.response?.data?.reason === 'reveal_required') this.needReveal = true;
        else this.error = err(e);
      }
    },
    async run() {
      this.busy = true; this.error = '';
      try {
        const {data} = await axios.post(`/api/bayanat/${this.it.acquisition}/confirm`, {item: {}});
        this.done = {id: data.run, counts: data.counts};
      } catch (e) { this.error = err(e); }
      this.busy = false;
    },
    async accept() {
      this.busy = true; this.error = '';
      try {
        await attest({run: this.done.id}, '/api/review/firstpass/imported/options');
        this.accepted = true;
      } catch (e) { this.error = e.message; }
      this.busy = false;
    },
  },
  template: `<div class="sheet sheet-cols" :aria-label="'Bayanat export ' + it.name">
    <header>
      <template v-if="!p && needReveal"><span class="muted">This export is sensitive. Reading it records an access entry on its custody chain.</span><v-btn size="small" variant="outlined" prepend-icon="ti ti-eye" @click="load(true)">Read the export</v-btn></template>
      <span v-else-if="!p" class="muted">{{ error || 'Reading the export' }}</span>
      <template v-else>
        <span class="r-chip is-source"><i class="ti ti-database-import" aria-hidden="true"></i>Bayanat export</span>
        <span>{{ plural(p.bulletins, 'bulletin') }}, {{ plural(p.media, 'media file') }}<template v-if="p.media"> ({{ p.media_in_file }} in this file)</template></span>
      </template>
    </header>
    <template v-if="p">
      <p class="says" v-if="!done">Each bulletin becomes an item of this case with its text, links, dates, places and labels; each media file in the zip becomes an item of its own. What its analysts decided (the analyst title, labels, verified labels) is proposed as the item's first pass, marked as from Bayanat, for you to accept. Bulletins nobody worked get Sealbook's own first pass.</p>
      <p class="says" v-if="!done">{{ statuses }}. {{ plural(p.worked, 'bulletin') }} worked by an analyst, {{ plural(p.analyst_titles, 'analyst title') }}, {{ plural(p.labels, 'label') }}.<template v-if="p.already_here"> {{ plural(p.already_here, 'bulletin') }} already here, kept once.</template></p>
      <p class="says" v-if="!done && p.labels_unknown.length"><i class="ti ti-info-circle" aria-hidden="true"></i> {{ plural(p.labels_unknown.length, 'label') }} not on the method's list, kept as written:</p>
      <ul v-if="!done && p.labels_unknown.length" class="says"><li v-for="l in p.labels_unknown.slice(0, 8)" :key="l"><bdi>{{ l }}</bdi></li><li v-if="p.labels_unknown.length > 8">and {{ p.labels_unknown.length - 8 }} more</li></ul>
      <p class="says" v-if="done">Imported {{ plural(done.counts.new, 'new bulletin') }}<template v-if="done.counts.bulletins > done.counts.new"> ({{ done.counts.bulletins - done.counts.new }} already here)</template>, {{ plural(done.counts.media, 'media file') }}<template v-if="done.counts.media_missing">, {{ done.counts.media_missing }} named but not in the file</template>. {{ plural(done.counts.proposals, 'decision') }} from Bayanat wait for you.</p>
      <p v-if="error" class="says"><i class="ti ti-alert-circle" aria-hidden="true"></i> {{ error }}</p>
      <footer>
        <v-btn v-if="!done" size="small" color="primary" prepend-icon="ti ti-database-import" :loading="busy" :disabled="p.too_many" @click="run">Import {{ plural(p.bulletins, 'bulletin') }}</v-btn>
        <template v-else>
          <v-btn v-if="done.counts.proposals && !accepted" size="small" color="primary" prepend-icon="ti ti-fingerprint" :loading="busy" @click="accept">Accept what Bayanat's analysts decided</v-btn>
          <span v-if="accepted" class="text-caption"><i class="ti ti-check" aria-hidden="true"></i> Accepted and signed with your passkey.</span>
          <a class="text-caption" :href="'/sources?investigation=' + encodeURIComponent(p.case)">Open the case's sources</a>
        </template>
        <span v-if="!done" class="text-caption muted">{{ p.too_many ? 'Too many bulletins for one import; export them in smaller parts.' : 'Nothing is accepted by the import itself.' }}</span>
      </footer>
    </template>
  </div>`,
};
