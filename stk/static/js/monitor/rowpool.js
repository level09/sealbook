/**
 * A fixed pool of DOM rows rebound on scroll. The pool holds enough rows to fill the
 * viewport plus a margin; scrolling changes which items they show, never how many
 * rows exist. Rows are keyed by pool slot, so Vue patches them in place.
 */
export const RowPool = {
  name: 'RowPool',
  props: {
    items: {type: Array, required: true},
    rowHeight: {type: Number, required: true},
    selected: {type: Number, default: -1},
    label: {type: String, required: true},
  },
  emits: ['pick'],
  data() {
    return {start: 0, size: 0};
  },
  computed: {
    slots() {
      return Array.from({length: this.size}, (_, i) => i);
    },
  },
  watch: {
    selected(index) {
      this.reveal(index);
    },
    items() {
      this.onScroll();
    },
  },
  methods: {
    measure() {
      const height = this.$refs.viewport.clientHeight || 600;
      this.size = Math.ceil(height / this.rowHeight) + 4;
      this.onScroll();
    },
    onScroll() {
      const top = this.$refs.viewport.scrollTop;
      const max = Math.max(0, this.items.length - this.size);
      this.start = Math.min(max, Math.max(0, Math.floor(top / this.rowHeight) - 2));
    },
    reveal(index) {
      if (index < 0) return;
      const vp = this.$refs.viewport;
      const top = index * this.rowHeight;
      if (top < vp.scrollTop) vp.scrollTop = top;
      else if (top + this.rowHeight > vp.scrollTop + vp.clientHeight) {
        vp.scrollTop = top + this.rowHeight - vp.clientHeight;
      }
    },
  },
  mounted() {
    this.measure();
    this._resize = new ResizeObserver(() => this.measure());
    this._resize.observe(this.$refs.viewport);
  },
  beforeUnmount() {
    this._resize.disconnect();
  },
  template: `
    <div class="pool" ref="viewport" @scroll.passive="onScroll" role="listbox" :aria-label="label">
      <div class="pool-spacer" :style="{height: items.length * rowHeight + 'px'}">
        <div v-for="i in slots" :key="i" class="pool-row" role="option"
             v-show="start + i < items.length"
             :aria-selected="start + i === selected"
             :class="{selected: start + i === selected}"
             :style="{height: rowHeight + 'px', transform: 'translateY(' + (start + i) * rowHeight + 'px)'}"
             @click="$emit('pick', start + i)">
          <slot v-if="start + i < items.length" :item="items[start + i]" :index="start + i"></slot>
        </div>
      </div>
    </div>`,
};
