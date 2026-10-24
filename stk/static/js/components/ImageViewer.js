/* Image with zoom, pan, full screen and claim boxes. Keys work only while the
   viewer has focus: + and - zoom, 0 fits, f full screen, arrows pan. Ctrl or
   pinch wheel zooms; once zoomed, the plain wheel pans. */
import {pulse} from './focus.js';

export const ImageViewer = {
  name: 'ImageViewer',
  props: {
    src: {type: String, required: true},
    alt: {type: String, default: ''},
    // [{id, bbox: [x0, y0, x1, y1] in image pixels, label, short, tier, agent}]
    boxes: {type: Array, default: () => []},
    sel: {type: String, default: null},
  },
  emits: ['select', 'error'],
  data: () => ({z: 1, x: 0, y: 0, nat: [0, 0], w: 0, drag: null}),
  computed: {
    placed() {
      const [W, H] = this.nat;
      if (!W) return [];
      // Labels sit above their box; a label that would cover an earlier one moves up a lane.
      // Widths are estimates in screen pixels at the fitted size, so lanes hold at any zoom.
      const k = this.w / W, rects = [];
      return this.boxes.map(b => {
        const [x0, y0, x1, y1] = b.bbox;
        const l = x0 * k, t = y0 * k, wd = String(b.short).length * 6.5 + 10;
        let lane = 0;
        while (rects.some(r => r.lane === lane && l < r.l + r.wd && r.l < l + wd && Math.abs(t - r.t) < 14)) lane++;
        rects.push({l, t, wd, lane});
        return {...b, lane, style: {left: x0 / W * 100 + '%', top: y0 / H * 100 + '%', width: (x1 - x0) / W * 100 + '%', height: (y1 - y0) / H * 100 + '%'}};
      });
    },
  },
  mounted() {
    // The stage width changes with the window; lanes are computed against it.
    this._ro = new ResizeObserver(() => {this.w = this.$refs.inner.offsetWidth;});
    this._ro.observe(this.$refs.inner);
  },
  beforeUnmount() {this._ro?.disconnect();},
  methods: {
    // Layout offsets ignore the transform (and its transition), unlike client rects.
    zoom(f, cx, cy) {
      const st = this.$refs.stage, inner = this.$refs.inner;
      cx ??= st.clientWidth / 2; cy ??= st.clientHeight / 2;
      const z = Math.min(8, Math.max(1, this.z * f)), k = z / this.z;
      const ox = inner.offsetLeft, oy = inner.offsetTop;
      this.x = (cx - ox) - (cx - ox - this.x) * k; this.y = (cy - oy) - (cy - oy - this.y) * k; this.z = z;
      if (z === 1) this.reset();
    },
    reset() {this.z = 1; this.x = 0; this.y = 0;},
    // Zoom so the region fills about 60% of the stage, centred, then pulse its box.
    zoomTo(bbox) {
      const W = this.nat[0], st = this.$refs.stage, inner = this.$refs.inner;
      if (!W) return;
      const s = inner.offsetWidth / W, [x0, y0, x1, y1] = bbox;
      const z = Math.min(6, Math.max(1, Math.min(st.clientWidth * 0.6 / ((x1 - x0) * s), st.clientHeight * 0.6 / ((y1 - y0) * s))));
      this.z = z;
      this.x = st.clientWidth / 2 - inner.offsetLeft - z * (x0 + x1) / 2 * s;
      this.y = st.clientHeight / 2 - inner.offsetTop - z * (y0 + y1) / 2 * s;
      this.$nextTick(() => pulse(this.$el.querySelector('.mv-box.on')));
    },
    wheel(e) {
      if (e.ctrlKey || e.metaKey) {e.preventDefault(); const r = this.$refs.stage.getBoundingClientRect(); this.zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top);}
      else if (this.z > 1) {e.preventDefault(); this.x -= e.deltaX; this.y -= e.deltaY;}
    },
    down(e) {if (this.z > 1 && !e.target.closest('.mv-box')) {this.drag = {px: e.clientX, py: e.clientY, x: this.x, y: this.y}; e.currentTarget.setPointerCapture(e.pointerId);}},
    move(e) {if (this.drag) {this.x = this.drag.x + e.clientX - this.drag.px; this.y = this.drag.y + e.clientY - this.drag.py;}},
    full() {document.fullscreenElement ? document.exitFullscreen() : this.$el.requestFullscreen?.();},
    key(e) {
      const act = {'+': () => this.zoom(1.25), '=': () => this.zoom(1.25), '-': () => this.zoom(1 / 1.25), '0': () => this.reset(), f: () => this.full(),
        ArrowLeft: () => {this.x += 40;}, ArrowRight: () => {this.x -= 40;}, ArrowUp: () => {this.y += 40;}, ArrowDown: () => {this.y -= 40;}}[e.key];
      if (!act || e.metaKey || e.ctrlKey || e.altKey || (e.key.startsWith('Arrow') && this.z === 1)) return;
      e.preventDefault(); act();
    },
  },
  template: `<div class="mv" tabindex="0" role="group" :aria-label="'Image viewer, ' + alt" @keydown="key">
    <div class="mv-tools">
      <v-tooltip text="Zoom in (+)"><template #activator="{props}"><v-btn v-bind="props" icon="ti ti-zoom-in" size="small" variant="text" aria-label="Zoom in" @click="zoom(1.25)"></v-btn></template></v-tooltip>
      <v-tooltip text="Zoom out (-)"><template #activator="{props}"><v-btn v-bind="props" icon="ti ti-zoom-out" size="small" variant="text" aria-label="Zoom out" :disabled="z === 1" @click="zoom(1 / 1.25)"></v-btn></template></v-tooltip>
      <v-btn size="small" variant="text" @click="reset">Fit</v-btn>
      <span class="muted">{{ Math.round(z * 100) }}%<template v-if="boxes.length">, {{ boxes.length }} claim boxes</template></span>
      <v-spacer></v-spacer>
      <v-tooltip text="Full screen (f)"><template #activator="{props}"><v-btn v-bind="props" icon="ti ti-maximize" size="small" variant="text" aria-label="Full screen" @click="full"></v-btn></template></v-tooltip>
    </div>
    <div ref="stage" class="mv-stage" :class="{zoomed: z > 1, dragging: drag}" @wheel="wheel" @pointerdown="down" @pointermove="move" @pointerup="drag = null" @pointercancel="drag = null" @dblclick="z > 1 ? reset() : zoom(2)">
      <div ref="inner" class="mv-inner" :style="{transform: 'translate(' + x + 'px,' + y + 'px) scale(' + z + ')'}">
        <img :src="src" :alt="alt" draggable="false" @load="e => nat = [e.target.naturalWidth, e.target.naturalHeight]" @error="$emit('error')">
        <button v-for="b in placed" :key="b.id" class="mv-box" :class="['tier-' + b.tier, b.agent ? 'by-agent' : 'by-person', {on: sel === b.id}]" :style="b.style" :aria-label="b.label" @click.stop="$emit('select', b.id)"><span :style="{'--lane': b.lane}">{{ b.short }}<em v-if="b.label !== b.short">{{ b.label.slice(String(b.short).length) }}</em></span></button>
      </div>
    </div>
  </div>`,
};
