/* Who did something: an avatar and a name, with the role or the run behind it.
   People get their initials on a stable muted colour (never violet, which is for
   agents); agents and extractor runs get a violet robot; imports an import icon.

   import {ActorChip} from '/static/js/components/ActorChip.js';  (registerMediaViewers registers it)
   <actor-chip :actor="{kind: 'human', name: 'analyst-2', role: 'analyst', key: 'user:3'}"></actor-chip>
   <actor-chip :actor="{kind: 'agent', name: 'extract run', detail: 'extract · qwen3-vl'}" size="sm" :you="false"></actor-chip>

   actor: {kind: human | agent | extractor | import, name, role?, detail?, key?}
   size: 'sm' (avatar only beside text, 18px) or 'md' (22px). you: name it "you".
   bare: the avatar alone, with the name as its tooltip and label. */
const HUES = ['#5d6b78', '#6b7552', '#7a5d4f', '#4f7069', '#7a6a45', '#5e6680', '#73565f', '#4d6a55'];
const DARK = ['#9fb0bf', '#b2bd92', '#c9a593', '#93bab2', '#c8b689', '#a6aecb', '#c49aa5', '#93b39c'];

const hash = s => [...(s || '')].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
export const initials = name => {
  const parts = (name || '?').replace(/[^\p{L}\p{N}\s-]/gu, ' ').split(/[\s-]+/).filter(Boolean);
  return ((parts[0]?.[0] || '?') + (parts.length > 1 ? parts[parts.length - 1][0] : parts[0]?.[1] || '')).toUpperCase();
};

export const ActorChip = {
  name: 'ActorChip',
  props: {
    actor: {type: Object, default: null},
    size: {type: String, default: 'md'},
    you: {type: Boolean, default: false},
    bare: {type: Boolean, default: false},
  },
  computed: {
    a() {return this.actor || {kind: 'human', name: 'unknown'};},
    machine() {return this.a.kind === 'agent' || this.a.kind === 'extractor';},
    icon() {return this.machine ? 'ti ti-robot' : this.a.kind === 'import' ? 'ti ti-file-import' : null;},
    px() {return this.size === 'sm' ? 18 : 22;},
    style() {
      if (this.icon) return {width: this.px + 'px', height: this.px + 'px'};
      const i = hash(this.a.key || this.a.name) % HUES.length;
      return {width: this.px + 'px', height: this.px + 'px', '--ac-l': HUES[i], '--ac-d': DARK[i]};
    },
    initials() {return initials(this.a.name);},
    label() {return this.you ? 'you' : this.a.name;},
    sub() {return this.you ? '' : this.a.role || this.a.detail || '';},
    title() {return [this.a.name, this.a.role, this.a.detail].filter(Boolean).join(' · ');},
  },
  template: `<span class="ac" :class="['ac-' + a.kind, {'ac-machine': machine}]" :title="title">
    <span class="ac-av" :style="style" :data-i="icon ? null : initials" :aria-label="bare ? title : null" :role="bare ? 'img' : null" :aria-hidden="bare ? null : 'true'"><i v-if="icon" :class="icon" aria-hidden="true"></i></span>
    <template v-if="!bare"><span class="ac-n"><bdi>{{ label }}</bdi></span><span v-if="sub && size !== 'sm'" class="ac-r">{{ sub }}</span></template>
  </span>`,
  beforeCreate() {
    if (document.getElementById('actor-chip-css')) return;
    document.head.append(Object.assign(document.createElement('style'), {id: 'actor-chip-css', textContent: `
.ac { display: inline-flex; align-items: center; gap: 6px; min-width: 0; vertical-align: middle; line-height: 1.3; }
.ac-av { flex: none; display: inline-grid; place-items: center; border-radius: 50%; font-size: 9.5px; font-weight: 650; letter-spacing: .02em; color: #fff; background: var(--ac-l); }
.ac-av[data-i]::before { content: attr(data-i); }
.dark-mode .ac-av { color: #141518; background: var(--ac-d); }
.ac-machine .ac-av { background: var(--agent-wash, rgba(90,70,200,.09)); color: var(--agent, #5a46c8); box-shadow: inset 0 0 0 1px var(--agent-solid, rgba(90,70,200,.18)); font-size: 12px; }
.ac-import .ac-av { background: var(--wash, rgba(24,25,27,.06)); color: var(--ink-2, #44464a); box-shadow: inset 0 0 0 1px var(--line-strong, #c9c9c3); font-size: 12px; }
.ac-n { font-weight: 550; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ac-machine .ac-n { color: var(--agent, #5a46c8); }
.ac-r { color: var(--ink-3, #62656a); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ac-r::before { content: '·'; margin-inline-end: 6px; }
`}));
  },
};
