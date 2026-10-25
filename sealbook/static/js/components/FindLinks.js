/* Find links: run the linker over this person's or source's case, focused on it,
   and say what it proposed. Every proposal waits in Review; nothing is accepted.

   import {FindLinks} from '/static/js/components/FindLinks.js';
   app.component('find-links', FindLinks);
   <find-links :scope="'person:' + id"></find-links> */
export const FindLinks = {
  name: 'FindLinks',
  props: {scope: {type: String, required: true}, btnClass: {type: String, default: ''}},
  data() {return {state: 'idle', note: '', href: null, timer: null};},
  beforeUnmount() { clearTimeout(this.timer); },
  methods: {
    async start() {
      this.state = 'running'; this.note = 'The linker is reading the case on the local model.'; this.href = null;
      try {
        const {data} = await axios.post('/api/agents/link', {item: {scope: this.scope}});
        this.poll(data.run);
      } catch (e) {
        this.state = 'idle';
        const d = e.response?.data || {};
        this.note = d.reason === 'off' ? `The linker is off until ${d.missing.join(' and ')} is set on the server.` : (d.description || e.message);
      }
    },
    async poll(id) {
      try {
        const {data} = await axios.get('/api/agents/runs/' + id);
        if (['queued', 'running'].includes(data.status)) { this.timer = setTimeout(() => this.poll(id), 4000); return; }
        this.state = 'idle';
        if (data.status === 'failed') { this.note = 'The linker could not finish: ' + data.error; return; }
        const n = data.written.length;
        this.note = n ? `${n === 1 ? 'One link' : n + ' links'} proposed for review.` : 'No new links: nothing more the evidence supports, or every link is already proposed or decided.';
        this.href = n ? '/review?box=open&statement=' + data.written[0].statement : null;
      } catch (e) { this.state = 'idle'; this.note = e.message; }
    },
  },
  template: `<span class="fl"><button v-if="btnClass" type="button" :class="btnClass" :disabled="state === 'running'" @click="start"><i class="ti ti-link" aria-hidden="true"></i> {{ state === 'running' ? 'Finding links' : 'Find links' }}</button>
    <v-btn v-else size="small" variant="text" prepend-icon="ti ti-link" :loading="state === 'running'" @click="start">Find links</v-btn>
    <span v-if="note" class="fl-note" aria-live="polite">{{ note }} <a v-if="href" :href="href">Open in Review</a></span></span>`,
};
