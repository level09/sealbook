// One switch per person, case and agent: when on, the agent checks again as evidence arrives.
export const KeepChecking = {
  name: 'KeepChecking',
  props: {kind: {type: String, required: true}, investigation: {type: String, default: ''}},
  data() { return {task: null, saving: false, note: ''}; },
  computed: {
    ready() { return this.investigation && this.investigation !== 'all'; },
    on() { return !!this.task && this.task.state !== 'paused'; },
  },
  watch: {investigation: {immediate: true, handler() { this.task = null; this.note = ''; this.load(); }}},
  methods: {
    async load() {
      if (!this.ready) return;
      try {
        const {data} = await axios.get('/api/agents/tasks', {params: {investigation: this.investigation, kind: this.kind}});
        this.task = data.items[0] || null;
      } catch { this.note = 'Could not read whether this keeps checking.'; }
    },
    async toggle(value) {
      this.saving = true; this.note = '';
      try {
        if (value) await axios.post('/api/agents/tasks', {item: {investigation: this.investigation, kind: this.kind}});
        else await axios.patch('/api/agents/tasks/' + this.task.id, {item: {enabled: false}});
        await this.load();
      } catch (e) { this.note = e.response?.data?.message || 'Could not change this. Try again.'; }
      finally { this.saving = false; }
    },
  },
  template: `<div v-if="ready" class="mb-2">
    <v-switch :model-value="on" :loading="saving" :disabled="saving" density="compact" hide-details color="primary"
      label="Check again when evidence arrives" @update:model-value="toggle"></v-switch>
    <p v-if="task && task.state === 'paused' && task.reason" class="text-medium-emphasis">{{ task.reason }}</p>
    <p v-if="note" role="alert">{{ note }}</p>
  </div>`,
};
