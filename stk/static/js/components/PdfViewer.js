/* PDF on pdf.js 4.10.38 (vendored in /static/pdfjs): page thumbnails, page
   navigation, zoom, a selectable text layer and search within the document.
   `highlight` marks a passage (a search hit) on load. Keys while focused:
   PageUp/PageDown or arrows change page, + and - zoom, 0 fits width, f full screen. */
import * as pdfjs from '/static/pdfjs/pdf.min.mjs';
import {centre, pulse} from './focus.js';

pdfjs.GlobalWorkerOptions.workerSrc = '/static/pdfjs/pdf.worker.min.mjs';
// ponytail: thumbnails render eagerly for the first pages only; lazy-render on
// scroll if documents with hundreds of pages become common.
const EAGER_THUMBS = 60;

export const PdfViewer = {
  name: 'PdfViewer',
  props: {
    src: {type: String, required: true},       // raw PDF bytes, already revealed
    label: {type: String, default: ''},
    page: {type: Number, default: 1},
    highlight: {type: String, default: ''},
  },
  emits: ['error', 'pages'],
  data() {return {n: 0, no: this.page || 1, zoom: 1, query: this.highlight || '', hits: [], hitAt: -1, busy: true, failed: false, lastQuery: (this.highlight || '').trim().toLowerCase()};},
  created() {this.doc = null; this.texts = {}; this.task = null;},  // pdf.js objects stay outside Vue reactivity
  async mounted() {
    try {
      this.doc = await pdfjs.getDocument({url: this.src, isEvalSupported: false, enableXfa: false, withCredentials: true}).promise;
      this.n = this.doc.numPages; this.$emit('pages', this.n);
      this.no = Math.min(Math.max(1, this.no), this.n);
      await this.render();
      if (this.query) await this.find();
      this.$nextTick(() => this.thumbs());
    } catch (err) {this.failed = true; this.$emit('error', err);} finally {this.busy = false;}
  },
  beforeUnmount() {this.doc?.destroy();},
  watch: {page(v) {if (v) this.go(v);}},
  methods: {
    async render() {
      const page = await this.doc.getPage(this.no);
      const wrap = this.$refs.wrap, base = page.getViewport({scale: 1});
      const fit = Math.max(0.2, (wrap.clientWidth - 24) / base.width);
      const viewport = page.getViewport({scale: fit * this.zoom});
      const ratio = window.devicePixelRatio || 1, canvas = this.$refs.canvas, box = this.$refs.page;
      canvas.width = Math.floor(viewport.width * ratio); canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = viewport.width + 'px'; canvas.style.height = viewport.height + 'px';
      box.style.width = viewport.width + 'px'; box.style.height = viewport.height + 'px';
      box.style.setProperty('--scale-factor', viewport.scale);
      this.task?.cancel();
      this.task = page.render({canvasContext: canvas.getContext('2d'), viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null});
      try {await this.task.promise;} catch (err) {if (err?.name !== 'RenderingCancelledException') throw err; return;}
      const layer = this.$refs.text; layer.replaceChildren();
      await new pdfjs.TextLayer({textContentSource: page.streamTextContent(), container: layer, viewport}).render();
      this.mark();
    },
    mark() {
      const q = this.query.trim().toLowerCase();
      this.$refs.text?.querySelectorAll('span').forEach(s => s.classList.toggle('mv-hit', !!q && s.textContent.toLowerCase().includes(q)));
    },
    async text(i) {
      if (!(i in this.texts)) this.texts[i] = (await (await this.doc.getPage(i)).getTextContent()).items.map(x => x.str).join(' ').toLowerCase();
      return this.texts[i];
    },
    async find() {
      const q = this.query.trim().toLowerCase();
      this.hits = [];
      if (!q) {this.mark(); return;}
      for (let i = 1; i <= this.n; i++) if ((await this.text(i)).includes(q)) this.hits.push(i);
      this.hitAt = this.hits.indexOf(this.no);
      if (this.hitAt < 0 && this.hits.length) {this.hitAt = 0; await this.go(this.hits[0]);} else this.mark();
    },
    // Show a passage: go to its page when known, find it, centre and pulse the hit.
    async find_(text, page) {
      if (!this.doc) return;
      if (page) await this.go(page);
      this.query = (text || '').trim(); this.lastQuery = this.query.toLowerCase();
      await this.find();
      const hit = this.$refs.text?.querySelector('span.mv-hit');
      centre(this.$refs.wrap, hit); pulse(hit);
    },
    async nextHit(d) {if (!this.hits.length) return; this.hitAt = (this.hitAt + d + this.hits.length) % this.hits.length; await this.go(this.hits[this.hitAt]);},
    async go(i) {i = Math.min(Math.max(1, +i || 1), this.n || 1); if (i === this.no && this.$refs.text?.childElementCount) return; this.no = i; if (this.doc) await this.render(); this.$el.querySelector(`.mv-thumbs [data-page="${i}"]`)?.scrollIntoView({block: 'nearest'});},
    async setZoom(z) {this.zoom = Math.min(4, Math.max(0.5, z)); await this.render();},
    async thumbs() {
      for (let i = 1; i <= Math.min(this.n, EAGER_THUMBS); i++) {
        const canvas = this.$el.querySelector(`.mv-thumbs [data-page="${i}"] canvas`);
        if (!canvas) continue;
        const page = await this.doc.getPage(i), viewport = page.getViewport({scale: 116 / page.getViewport({scale: 1}).width});
        canvas.width = viewport.width; canvas.height = viewport.height;
        await page.render({canvasContext: canvas.getContext('2d'), viewport}).promise;
      }
    },
    full() {document.fullscreenElement ? document.exitFullscreen() : this.$el.requestFullscreen?.().then(() => this.render());},
    key(e) {
      if (e.metaKey || e.ctrlKey || e.altKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      const act = {PageDown: () => this.go(this.no + 1), ArrowRight: () => this.go(this.no + 1), PageUp: () => this.go(this.no - 1), ArrowLeft: () => this.go(this.no - 1),
        '+': () => this.setZoom(this.zoom * 1.25), '=': () => this.setZoom(this.zoom * 1.25), '-': () => this.setZoom(this.zoom / 1.25), '0': () => this.setZoom(1), f: () => this.full()}[e.key];
      if (act) {e.preventDefault(); act();}
    },
  },
  template: `<div class="mv" tabindex="0" role="group" :aria-label="'PDF viewer, ' + label" @keydown="key">
    <div class="mv-tools">
      <v-btn icon="ti ti-chevron-left" size="small" variant="text" aria-label="Previous page" :disabled="no <= 1" @click="go(no - 1)"></v-btn>
      <v-text-field :model-value="no" type="number" density="compact" variant="outlined" hide-details class="mv-page-no" aria-label="Page" @change="e => go(e.target.value)"></v-text-field>
      <span class="muted">of {{ n || '...' }}</span>
      <v-btn icon="ti ti-chevron-right" size="small" variant="text" aria-label="Next page" :disabled="no >= n" @click="go(no + 1)"></v-btn>
      <v-btn icon="ti ti-zoom-out" size="small" variant="text" aria-label="Zoom out" @click="setZoom(zoom / 1.25)"></v-btn>
      <v-btn size="small" variant="text" @click="setZoom(1)">{{ Math.round(zoom * 100) }}%</v-btn>
      <v-btn icon="ti ti-zoom-in" size="small" variant="text" aria-label="Zoom in" @click="setZoom(zoom * 1.25)"></v-btn>
      <v-spacer></v-spacer>
      <v-text-field v-model="query" density="compact" variant="outlined" hide-details prepend-inner-icon="ti ti-search" placeholder="Find in document" class="mv-find" aria-label="Find in document" @keydown.enter.prevent="hits.length && query.trim().toLowerCase() === lastQuery ? nextHit(1) : (lastQuery = query.trim().toLowerCase(), find())"></v-text-field>
      <span v-if="query.trim() && lastQuery" class="muted">{{ hits.length ? (hitAt + 1) + ' of ' + hits.length + ' pages' : 'not found' }}</span>
      <v-tooltip text="Full screen (f)"><template #activator="{props}"><v-btn v-bind="props" icon="ti ti-maximize" size="small" variant="text" aria-label="Full screen" @click="full"></v-btn></template></v-tooltip>
    </div>
    <p v-if="failed" class="pa-4 muted" role="alert">This PDF could not be opened.</p>
    <div v-show="!failed" class="mv-pdf">
      <div class="mv-thumbs" aria-label="Pages"><button v-for="i in n" :key="i" :data-page="i" :aria-label="'Page ' + i" :aria-current="i === no" :class="{hit: hits.includes(i)}" @click="go(i)"><canvas></canvas><span>{{ i }}</span></button></div>
      <div ref="wrap" class="mv-page-wrap"><v-progress-linear v-if="busy" indeterminate></v-progress-linear><div ref="page" class="mv-page"><canvas ref="canvas"></canvas><div ref="text" class="textLayer"></div></div></div>
    </div>
  </div>`,
};
