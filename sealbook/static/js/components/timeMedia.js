/* Shared state and keys for VideoPlayer and AudioPlayer. The element is $refs.m.
   Keys while focused: space play/pause, arrows seek 5 s (shift 1 s), [ and ]
   change speed, f full screen. */
export const ms = v => {const s = Math.max(0, Math.floor((v || 0) / 1000)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;};
export const RATES = [0.5, 0.75, 1, 1.25, 1.5, 2];

export const timeMedia = {
  props: {
    src: {type: String, required: true},
    label: {type: String, default: ''},
    marks: {type: Array, default: () => []},   // matched moments [[startMs, endMs]]
    spans: {type: Array, default: () => []},   // claim spans [{id, ms: [a, b], label}]
    focus: {type: Number, default: null},      // seek here once loaded and on change
  },
  emits: ['time'],
  data: () => ({t: 0, dur: 1, playing: false, rate: 1, RATES}),
  watch: {focus(v) {if (v != null) this.seek(v);}, rate(v) {if (this.$refs.m) this.$refs.m.playbackRate = v;}},
  methods: {
    ms,
    loaded() {this.dur = (this.$refs.m.duration || 1) * 1000; if (this.focus != null) this.seek(this.focus);},
    tick() {this.t = this.$refs.m.currentTime * 1000; this.$emit('time', this.t);},
    toggle() {const m = this.$refs.m; m.paused ? m.play() : m.pause();},
    seek(v) {const m = this.$refs.m; if (!m) return; v = Math.max(0, Math.min(v, this.dur)); m.currentTime = v / 1000; this.t = v;},
    pct(v) {return Math.max(0, Math.min(100, v / this.dur * 100)) + '%';},
    clickSeek(e) {const r = e.currentTarget.getBoundingClientRect(); let x = (e.clientX - r.left) / r.width; if (getComputedStyle(e.currentTarget).direction === 'rtl') x = 1 - x; this.seek(x * this.dur);},
    full() {document.fullscreenElement ? document.exitFullscreen() : this.$el.requestFullscreen?.();},
    speed(d) {const i = RATES.indexOf(this.rate) + d; if (RATES[i]) this.rate = RATES[i];},
    baseKey(e) {
      const step = e.shiftKey ? 1000 : 5000;
      return {' ': () => this.toggle(), k: () => this.toggle(), ArrowRight: () => this.seek(this.t + step), ArrowLeft: () => this.seek(this.t - step),
        '[': () => this.speed(-1), ']': () => this.speed(1), f: () => this.full(), Home: () => this.seek(0), End: () => this.seek(this.dur)}[e.key];
    },
    key(e) {
      if (e.metaKey || e.ctrlKey || e.altKey || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
      const act = this.extraKey?.(e) || this.baseKey(e);
      if (act) {e.preventDefault(); act();}
    },
  },
};

// The scrubber both players share: matched moments hatched violet, claim spans in ink.
export const scrubber = `<div class="mv-scrub" role="slider" tabindex="-1" aria-label="Position" aria-valuemin="0" :aria-valuemax="Math.round(dur / 1000)" :aria-valuenow="Math.round(t / 1000)" :aria-valuetext="ms(t)" @click="clickSeek">
  <div class="track"></div>
  <div v-for="m in marks" :key="'m' + m[0]" class="mk" :style="{insetInlineStart: pct(m[0]), width: pct(m[1] - m[0])}" :title="'matched moment ' + ms(m[0])"></div>
  <div v-for="s in spans" :key="'s' + s.id" class="sp" :style="{insetInlineStart: pct(s.ms[0]), width: pct(s.ms[1] - s.ms[0])}" :title="s.label"></div>
  <div class="fill" :style="{width: pct(t)}"></div><div class="head" :style="{insetInlineStart: pct(t)}"></div></div>`;

export const controls = extra => `<div class="mv-tools">
  <v-btn size="small" variant="text" :icon="playing ? 'ti ti-player-pause' : 'ti ti-player-play'" :aria-label="playing ? 'Pause' : 'Play'" @click="toggle"></v-btn>
  <span class="mono">{{ ms(t) }} / {{ ms(dur) }}</span>${extra}
  <v-spacer></v-spacer>
  <v-select v-model="rate" :items="RATES.map(r => ({title: r + 'x', value: r}))" density="compact" variant="plain" hide-details class="mv-speed" aria-label="Playback speed"></v-select>
  <v-tooltip text="Full screen (f)"><template #activator="{props}"><v-btn v-bind="props" icon="ti ti-maximize" size="small" variant="text" aria-label="Full screen" @click="full"></v-btn></template></v-tooltip>
</div>`;
