/* One source in the viewer for its type. A sensitive source waits for the logged
   reveal; any other is shown at once and the server logs the view. Every request
   times out; a dropped request or a media load error shows a plain message with
   Retry instead of an endless spinner. */
import {centre, pulse} from './focus.js';

export const TIMEOUT = 15000;
// Three different troubles, three different answers: no answer (Retry helps), no file
// stored (Retry never helps), not allowed (reveal first).
export const failure = err => {
  const r = err.response;
  if (!r) return 'The server did not answer. Retry.';
  if (r.data?.reason === 'no_file') return 'No file is stored for this source.';
  if (r.data?.reason === 'reveal_required') return 'Reveal the source to see it; the reveal is logged.';
  return r.data?.message || r.data?.error || `Request failed (${r.status}). Retry.`;
};
const MEDIA_TROUBLE = {
  network: {title: 'The media did not load', text: 'The server did not answer. Retry.', retry: true},
  missing: {title: 'No file stored', text: 'The bytes for this source are not in the store.', retry: false},
  denied: {title: 'Not shown', text: 'Reveal the source to see it; the reveal is logged.', retry: false},
  broken: {title: 'The media could not be played', text: 'The file is stored but this browser could not decode it.', retry: true},
};

// A captured web page reads as its text: tags, scripts and comments are dropped
// from the shown string only, so claim spans keep their offsets into the source.
const BLOCK = /^(p|div|h[1-6]|li|br|tr|section|article|header|footer|ul|ol|table|blockquote|figure|figcaption)$/i;
const ENTITY = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', '#39': "'"};
export function markup(text) {
  const re = /<(script|style)\b[\s\S]*?<\/\1\s*>|<!--[\s\S]*?-->|<\/?([a-zA-Z][\w-]*)[^>]*>/g;
  return [...text.matchAll(re)].map(m => ({start: m.index, end: m.index + m[0].length, put: BLOCK.test(m[2] || '') ? '\n' : ''}));
}
export function readable(text, start, end, tags) {
  let out = '', i = start;
  for (const t of tags) {
    if (t.end <= start || t.start >= end) continue;
    if (t.start > i) out += text.slice(i, t.start);
    out += t.put;
    i = Math.max(i, t.end);
  }
  if (i < end) out += text.slice(i, end);
  return out.replace(/&(#39|[a-z]+);/g, (m, k) => ENTITY[k] ?? m);
}

export const EvidenceViewer = {
  name: 'EvidenceViewer',
  props: {
    // {sha256, kind, mime, file, sensitive, revealed, origins, segments}
    item: {type: Object, required: true},
    claims: {type: Array, default: () => []},
    marks: {type: Array, default: () => []},
    sel: {type: String, default: null},
    focus: {type: Number, default: null},
    page: {type: Number, default: null},
    read: {type: Object, default: null},        // /read payload, for text and transcripts
    readError: {type: String, default: ''},
    hl: {type: Object, default: null},          // {start, end} in the source text
    highlight: {type: String, default: ''},     // passage to find in a PDF
  },
  emits: ['select', 'revealed', 'retry-read'],
  data() {return {raw: false, url: this.item.revealed ? `/api/evidence/${this.item.sha256}/content` : null, busy: false, error: '', mediaError: null, nonce: 0, strip: null, table: null, tableError: '', where: ''};},
  computed: {
    kind() {return this.item.kind;},
    trouble() {return MEDIA_TROUBLE[this.mediaError] || MEDIA_TROUBLE.network;},
    base() {return `/api/evidence/${this.item.sha256}`;},
    boxes() {return this.claims.filter(c => c.span?.bbox).map(c => ({id: c.id, bbox: c.span.bbox, tier: c.tier, agent: c.actor?.agent, short: c.label || c.prop, label: `${c.label || c.prop}: ${c.value || 'masked'}, ${c.tier}`}));},
    spans() {return this.claims.filter(c => c.span?.ms).map(c => ({id: c.id, ms: c.span.ms, label: c.label || c.prop}));},
    frames() {
      const st = this.strip;
      if (st?.frames?.length) {const total = st.cell_width * st.frames.length; return st.frames.map(f => ({ms: f.ms, sprite: {url: `${this.base}/strip`, x: f.x, w: st.cell_width, h: st.cell_height, total}}));}
      return (this.item.segments || []).filter(s => s.kind === 'frame' && s.image).map(s => ({ms: s.location.ms[0], src: `${this.base}/segments/${s.id}/image`}));
    },
    transcript() {return (this.read?.passages || []).filter(p => p.location.ms).map(p => ({start: p.location.ms[0], end: p.location.ms[1], text: p.text}));},
    pieces() {
      const text = this.read?.text;
      if (text == null) return [];
      const spans = this.claims.filter(c => c.span?.start != null && c.span.end <= text.length).map(c => ({start: c.span.start, end: c.span.end, claim: c}));
      if (this.hl) spans.push({...this.hl, hit: true});
      spans.sort((a, b) => a.start - b.start);
      const out = []; let i = 0;
      for (const s of spans) {if (s.start < i) continue; if (s.start > i) out.push({text: text.slice(i, s.start)}); out.push({text: text.slice(s.start, s.end), claim: s.claim, hit: s.hit}); i = s.end;}
      if (i < text.length) out.push({text: text.slice(i)});
      if (this.kind !== 'web' || this.raw) return out;
      const tags = markup(text);
      let at = 0;
      return out.map(p => {
        const start = at; at += p.text.length;
        const shown = readable(text, start, at, tags);
        return {...p, text: p.claim || p.hit ? shown : shown.replace(/[ \t]*\n[\s]*\n\s*/g, '\n\n')};
      }).filter((p, i) => p.text || p.claim || p.hit || i === 0);
    },
    origin() {return (this.item.origins || []).find(o => /^(url:)?https?:/.test(o))?.replace(/^url:/, '');},
  },
  watch: {url: {immediate: true, handler() {this.loadStrip(); this.loadTable();}}},
  methods: {
    async reveal() {
      this.busy = true; this.error = '';
      try {this.url = (await axios.post(`${this.base}/reveal`, {item: {}}, {timeout: TIMEOUT})).data.url; this.$emit('revealed', this.item);}
      catch (err) {this.error = failure(err);} finally {this.busy = false;}
    },
    // The thumbnail tool's frame strip, when it has run; indexed frames otherwise.
    async loadStrip() {
      if (!this.url || this.kind !== 'video' || this.item.stored === false) return;
      try {this.strip = (await axios.get(`${this.base}/thumbs`, {timeout: TIMEOUT})).data.strip || null;} catch (err) {this.strip = null;}
    },
    // A sheet reads as its cells (GET /sheet, under the same reveal rule as the bytes).
    async loadTable() {
      if (!this.url || this.kind !== 'sheet' || this.item.stored === false) return;
      this.tableError = '';
      try {this.table = (await axios.get(`${this.base}/sheet`, {timeout: TIMEOUT})).data;} catch (err) {this.tableError = failure(err);}
    },
    /* Show a claim in the source: the obvious result for each kind. `ms` is the
       claim's time when the caller knows it (a transcript span); `play` starts there. */
    show(c, {play = false, ms = null} = {}) {
      this.where = '';
      this.$nextTick(() => {
        const m = this.$refs.media;
        if (this.kind === 'image' && c.span?.bbox) m?.zoomTo(c.span.bbox);
        else if (this.kind === 'pdf') {
          // Search for the characters the span covers in the page text it was read from.
          const p = (this.read?.passages || []).find(x => x.text_blob === c.span_blob);
          m?.find_(p && c.span?.start != null ? p.text.slice(c.span.start, c.span.end) : c.value, p?.location.page || c.span?.page);
        }
        else if (this.kind === 'sheet') m?.reveal(c.id);
        else if (['audio', 'video'].includes(this.kind)) {
          const at = ms ?? c.span?.ms?.[0];
          if (at != null && m) {m.seek(at); if (play) m.$refs.m.play();}
          if (this.kind === 'audio' && at != null) this.$nextTick(() => pulse(this.$el.querySelector('.mv-line.now')));
        } else this.showText(c);
      });
    },
    showText(c) {
      const box = this.$el.querySelector('.ev-txt'), mark = box?.querySelector(`mark[data-claim="${c.id}"]`);
      if (!mark) return;
      centre(box, mark); pulse(mark);
      const before = this.read.text.slice(0, c.span.start), line = before.split('\n').length;
      this.where = `${c.label || c.prop}: line ${line}, characters ${c.span.start} to ${c.span.end}`;
    },
    retryMedia() {this.mediaError = null; this.nonce++;},
    // A media element's error says nothing about why; ask the server for one byte.
    async onMediaError() {
      try {
        const r = await fetch(this.src(), {headers: {Range: 'bytes=0-0'}, credentials: 'same-origin'});
        const body = r.ok ? {} : await r.json().catch(() => ({}));
        this.mediaError = body.reason === 'no_file' ? 'missing' : r.status === 403 ? 'denied' : r.ok ? 'broken' : 'network';
      } catch (err) {this.mediaError = 'network';}
    },
    src(extra = '') {return this.url + (extra || this.nonce ? `?${extra}${extra && this.nonce ? '&' : ''}${this.nonce ? 'r=' + this.nonce : ''}` : '');},
  },
  template: `<div class="ev-viewer">
    <div v-if="item.stored === false" class="ev-gate"><v-empty-state icon="ti ti-file-off" title="No file stored" :text="item.synthetic ? 'This is a synthetic filler record for testing at scale.' : 'The bytes for this source are not in the store.'"></v-empty-state></div>
    <div v-else-if="!url" class="ev-gate">
      <v-empty-state icon="ti ti-eye-off" title="This item is marked sensitive" text="It may be graphic or identify victims or witnesses. Showing it writes your name and the time to the custody chain.">
        <template #actions><v-btn color="primary" variant="flat" :prepend-icon="error ? 'ti ti-refresh' : 'ti ti-eye'" :loading="busy" @click="reveal">{{ error ? 'Retry' : 'Show sensitive item (access is logged)' }}</v-btn></template>
      </v-empty-state>
      <p v-if="error" role="alert" class="text-error px-4 pb-4">{{ error }}</p>
    </div>
    <div v-else-if="mediaError" class="ev-gate"><v-empty-state :icon="mediaError === 'missing' ? 'ti ti-file-off' : 'ti ti-alert-triangle'" :title="trouble.title" :text="trouble.text">
      <template v-if="trouble.retry" #actions><v-btn variant="outlined" prepend-icon="ti ti-refresh" @click="retryMedia">Retry</v-btn></template></v-empty-state></div>
    <image-viewer v-else-if="kind === 'image'" ref="media" :key="nonce" :src="src()" :alt="item.file" :boxes="boxes" :sel="sel" @select="id => $emit('select', id)" @error="onMediaError"></image-viewer>
    <pdf-viewer v-else-if="kind === 'pdf'" ref="media" :key="nonce" :src="src('raw=1')" :label="item.file" :page="page || 1" :highlight="highlight" @error="onMediaError"></pdf-viewer>
    <video-player v-else-if="kind === 'video'" ref="media" :key="nonce" :src="src()" :label="item.file" :marks="marks" :spans="spans" :frames="frames" :focus="focus" @error="onMediaError"></video-player>
    <audio-player v-else-if="kind === 'audio'" ref="media" :key="nonce" :src="src()" :label="item.file" :marks="marks" :spans="spans" :transcript="transcript" :focus="focus" :peaks-url="base + '/peaks'" @error="onMediaError"></audio-player>
    <template v-else-if="kind === 'sheet'">
      <div v-if="tableError" class="ev-gate"><v-empty-state icon="ti ti-alert-triangle" title="The sheet did not load" :text="tableError"><template #actions><v-btn variant="outlined" prepend-icon="ti ti-refresh" @click="loadTable">Retry</v-btn></template></v-empty-state></div>
      <sheet-viewer v-else-if="table" ref="media" :table="table" :claims="claims" :sel="sel" @select="id => $emit('select', id)"></sheet-viewer>
      <v-skeleton-loader v-else type="table"></v-skeleton-loader>
    </template>
    <template v-else>
      <div v-if="kind === 'web'" class="mv-tools"><v-btn v-if="origin" size="small" variant="text" prepend-icon="ti ti-external-link" :href="origin" target="_blank" rel="noopener noreferrer">Original link</v-btn><span class="muted">{{ origin ? 'captured snapshot below; the link leaves Sealbook' : 'captured snapshot below' }}</span><v-btn size="small" variant="text" :prepend-icon="raw ? 'ti ti-file-text' : 'ti ti-code'" class="ms-auto" @click="raw = !raw">{{ raw ? 'Show the text' : 'Show the HTML' }}</v-btn></div>
      <div v-if="readError" class="ev-gate"><v-empty-state icon="ti ti-alert-triangle" title="The text did not load" :text="readError"><template #actions><v-btn variant="outlined" prepend-icon="ti ti-refresh" @click="$emit('retry-read')">Retry</v-btn></template></v-empty-state></div>
      <div v-if="read && where" class="mv-tools ev-where" role="status"><i class="ti ti-focus-2" aria-hidden="true"></i><span>{{ where }}</span></div>
      <div v-if="!readError && read" class="ev-txt" dir="auto"><template v-for="(p, i) in pieces" :key="i"><mark v-if="p.claim" class="span ev-span" :data-claim="p.claim.id" :class="['tier-' + p.claim.tier, {on: sel === p.claim.id}]" :title="(p.claim.label || p.claim.prop) + ', ' + p.claim.tier" @click="$emit('select', p.claim.id)">{{ p.text }}</mark><mark v-else-if="p.hit" class="hit">{{ p.text }}</mark><template v-else>{{ p.text }}</template></template></div>
      <v-skeleton-loader v-if="!readError && !read" type="paragraph, paragraph"></v-skeleton-loader>
    </template>
  </div>`,
};
