/* Audio with waveform, scrubber marks, speed and a transcript that follows playback.
   Peaks come from `peaksUrl` ({duration_ms, peaks}) when the thumbnail tool made them, else from decoding the file
   once in the browser. */
import {controls, scrubber, timeMedia} from './timeMedia.js';

const BARS = 240;

async function decodePeaks(src) {
  // ponytail: decodes the whole file; fine for clips, use server peaks for hours.
  const buf = await (await fetch(src)).arrayBuffer();
  const audio = await new (window.AudioContext || window.webkitAudioContext)().decodeAudioData(buf);
  const data = audio.getChannelData(0), step = Math.max(1, Math.floor(data.length / BARS)), out = [];
  for (let i = 0; i < BARS; i++) {let m = 0; for (let j = i * step; j < (i + 1) * step && j < data.length; j += 16) m = Math.max(m, Math.abs(data[j])); out.push(m);}
  return out;
}

export const AudioPlayer = {
  name: 'AudioPlayer',
  mixins: [timeMedia],
  props: {
    peaksUrl: {type: String, default: null},
    transcript: {type: Array, default: () => []},  // [{start, end, text}] in ms
  },
  emits: ['error'],
  data: () => ({peaks: []}),
  async mounted() {
    let peaks = null;
    if (this.peaksUrl) {
      try {const r = await fetch(this.peaksUrl, {credentials: 'same-origin'}); if (r.ok) {const d = await r.json(); peaks = Array.isArray(d) ? d : d.peaks;}} catch (err) {peaks = null;}
    }
    try {peaks ||= await decodePeaks(this.src);} catch (err) {peaks = [];}
    const top = Math.max(...peaks, 0) || 1;
    this.peaks = peaks.map(v => v / top);
  },
  watch: {t() {this.$nextTick(() => this.$el.querySelector('.mv-line.now')?.scrollIntoView({block: 'nearest'}));}},
  template: `<div class="mv" tabindex="0" role="group" :aria-label="'Audio player, ' + label" @keydown="key">
    <audio ref="m" :src="src" preload="metadata" @loadedmetadata="loaded" @timeupdate="tick" @play="playing = true" @pause="playing = false" @error="$emit('error')"></audio>
    ${controls(`<span class="muted">{{ transcript.length ? 'transcript follows playback' : marks.length ? 'matched moments hatched violet' : '' }}</span>`)}
    <svg class="mv-wave" viewBox="0 0 960 110" preserveAspectRatio="none" aria-hidden="true" @click="clickSeek">
      <rect v-for="m in marks" :key="'m' + m[0]" class="mk" :x="m[0] / dur * 960" y="2" :width="Math.max(8, (m[1] - m[0]) / dur * 960)" height="106"></rect>
      <rect v-for="(p, i) in peaks" :key="i" :class="{done: i / peaks.length < t / dur}" :x="i * 960 / peaks.length" :y="55 - Math.max(1, p * 52)" :width="Math.max(0.5, 960 / peaks.length * 0.75)" :height="Math.max(2, p * 104)"></rect>
      <line v-if="!peaks.length" x1="0" y1="55" x2="960" y2="55" stroke="currentColor" stroke-opacity=".3"></line>
    </svg>
    ${scrubber}
    <div v-if="transcript.length" style="max-height: 40vh; overflow: auto">
      <div v-for="l in transcript" :key="l.start" class="mv-line" :class="{now: t >= l.start && t < l.end}" @click="seek(l.start)"><span class="mono muted">{{ ms(l.start) }}</span><span dir="auto">{{ l.text }}</span></div>
    </div>
  </div>`,
};
