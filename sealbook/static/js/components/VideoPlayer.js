/* Video with custom controls: scrubber with matched moments and claim spans, frame
   strip, speed, frame step (, and . while paused), full screen. The browser streams
   with Range requests from the content endpoint. */
import {controls, scrubber, timeMedia} from './timeMedia.js';

const FRAME_MS = 1000 / 30;  // the container does not expose its frame rate

export const VideoPlayer = {
  name: 'VideoPlayer',
  mixins: [timeMedia],
  // [{ms, src}] for single images, or [{ms, sprite: {url, x, w, h, total}}] for strip sprite cells
  props: {frames: {type: Array, default: () => []}},
  emits: ['error'],
  computed: {
    strip() {return this.frames.map(f => ({...f, match: this.marks.some(([a, b]) => f.ms >= a - 1500 && f.ms <= b + 1500)}));},
  },
  methods: {
    step(d) {const m = this.$refs.m; m.pause(); this.seek(this.t + d * FRAME_MS);},
    // One cell of a horizontal sprite: scale the sprite so a cell fills the button.
    cell(c) {return {backgroundPosition: (c.total > c.w ? c.x / (c.total - c.w) * 100 : 0) + '% 0', backgroundSize: (c.total / c.w * 100) + '% 100%', '--ar': c.w + ' / ' + c.h};},
    extraKey(e) {return {',': () => this.step(-1), '.': () => this.step(1)}[e.key];},
  },
  template: `<div class="mv" tabindex="0" role="group" :aria-label="'Video player, ' + label" @keydown="key">
    <video ref="m" :src="src" preload="metadata" playsinline :aria-label="label" @click="toggle" @loadedmetadata="loaded" @timeupdate="tick" @play="playing = true" @pause="playing = false" @error="$emit('error')"></video>
    ${controls(`<v-tooltip text="Previous frame (,)"><template #activator="{props}"><v-btn v-bind="props" icon="ti ti-chevron-left" size="small" variant="text" aria-label="Previous frame" @click="step(-1)"></v-btn></template></v-tooltip>
      <v-tooltip text="Next frame (.)"><template #activator="{props}"><v-btn v-bind="props" icon="ti ti-chevron-right" size="small" variant="text" aria-label="Next frame" @click="step(1)"></v-btn></template></v-tooltip>
      <span v-if="marks.length" class="muted">matched moments hatched violet</span>`)}
    ${scrubber}
    <div v-if="strip.length" class="mv-strip" aria-label="Indexed frames"><button v-for="f in strip" :key="f.ms" :class="{match: f.match, now: Math.abs(f.ms - t) < 1500}" :aria-label="'Frame at ' + ms(f.ms) + (f.match ? ', matched' : '')" @click="seek(f.ms)"><div v-if="f.sprite" class="cell" :style="{backgroundImage: 'url(' + f.sprite.url + ')', ...cell(f.sprite)}"></div><img v-else :src="f.src" alt="" loading="lazy"><span>{{ ms(f.ms) }}</span></button></div>
  </div>`,
};
