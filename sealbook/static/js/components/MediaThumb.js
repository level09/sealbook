/* A small tile for lists from GET /api/evidence/<sha>/thumb. 403 with reason
   reveal_required means a sensitive source not revealed in this session: a hatched
   tile with a lock. 204 means no thumbnail (yet): a plain type icon. Text, web
   and sheet sources never get one, so they are not requested. Opens the evidence
   page. */
const ICON = {image: 'ti ti-photo', pdf: 'ti ti-file-type-pdf', audio: 'ti ti-volume', video: 'ti ti-video', web: 'ti ti-world', sheet: 'ti ti-table', text: 'ti ti-file-text'};

export const kindOf = mime => !mime ? 'text' : ['text/csv', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'].includes(mime) ? 'sheet' : mime.startsWith('image/') ? 'image' : mime === 'application/pdf' ? 'pdf' : mime.startsWith('audio/') ? 'audio' : mime.startsWith('video/') ? 'video' : mime === 'text/html' ? 'web' : 'text';

const NEVER = new Set(['text', 'web', 'sheet']);

export const MediaThumb = {
  name: 'MediaThumb',
  props: {
    sha: {type: String, required: true},
    mime: {type: String, default: ''},
    kind: {type: String, default: null},
    file: {type: String, default: ''},
    width: {type: Number, default: 116},
    height: {type: Number, default: 64},
    href: {type: String, default: null},
    caption: {type: Boolean, default: true},
    link: {type: Boolean, default: true},  // false inside an element that is already a link
    // The API said this source is sensitive and not revealed: show the lock without asking, so no 403 is logged.
    locked: {type: Boolean, default: false},
  },
  data: () => ({url: null, state: 'loading'}),  // loading | ok | sensitive | none
  computed: {
    icon() {return ICON[this.kind || kindOf(this.mime)] || 'ti ti-file';},
    src() {return `/api/evidence/${this.sha}/thumb?size=${this.width > 160 ? 480 : 160}`;},
  },
  watch: {src: {immediate: true, handler: 'load'}, locked: 'load'},
  beforeUnmount() {if (this.url) URL.revokeObjectURL(this.url);},
  methods: {
    async load() {
      if (NEVER.has(this.kind || (this.mime && kindOf(this.mime)))) {this.state = 'none'; return;}
      if (this.locked) {this.state = 'sensitive'; return;}
      try {
        const r = await fetch(this.src, {credentials: 'same-origin'});
        if (r.status === 204) {this.state = 'none'; return;}
        if (r.ok) {this.url = URL.createObjectURL(await r.blob()); this.state = 'ok'; return;}
        const body = r.status === 403 ? await r.json().catch(() => ({})) : {};
        this.state = body.reason === 'reveal_required' ? 'sensitive' : 'none';
      } catch (err) {this.state = 'none';}
    },
  },
  template: `<component :is="link ? 'a' : 'div'" class="mv-thumb" :href="link ? href || '/evidence/' + sha : undefined" :style="{width: width + 'px'}" :title="file">
    <div class="im" :class="{sensitive: state === 'sensitive'}" :style="{height: height + 'px'}">
      <img v-if="state === 'ok'" :src="url" alt="">
      <template v-else><i :class="icon" style="font-size: 22px" aria-hidden="true"></i><i v-if="state === 'sensitive'" class="ti ti-eye-off lock" aria-hidden="true" title="Sensitive: shown after the logged reveal"></i></template>
    </div>
    <div v-if="caption" class="n">{{ file || sha.slice(0, 8) }}</div>
  </component>`,
};
