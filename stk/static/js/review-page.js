/* Review: the decision workspace. Left, each pending claim as a sentence with its
   source, proposer, requester and due date; right, the evidence at the exact span
   beside the claim, its checks, provenance, rival claims and thread, with Accept,
   Reject, Correct and Ask. Every decision is staged on the server, shown as the rows
   the passkey will sign, then signed (monitor/passkey.js); undo is a signed retraction.
   Values stay masked by the server's rule until the source is revealed, which the
   evidence viewer logs. */
import {prepare, sign} from '/static/js/monitor/passkey.js';
import {EvidencePair} from '/static/js/evidence-media.js';
import {registerMediaViewers} from '/static/js/components/media.js';
import {TIMEOUT, failure} from '/static/js/components/EvidenceViewer.js';

const api = axios.create({timeout: TIMEOUT});

const PROP = {name: 'name', father_name: "father's name", mother_name: "mother's name", birth_date: 'born', place_of_birth: 'born in', arrested_at: 'arrested at', last_seen: 'last seen', detention_branch: 'held by branch', transferred_to: 'transferred to', seen_at: 'seen at', detained_at: 'detained at', sex: 'sex', age: 'age', occupation: 'occupation', event: 'event', document_date: 'document date', document_type: 'document type', same_as: 'same person', not_same: 'different people', media_match: 'same recording', located_at: 'place confirmed', photographed_at: 'photographed at', filmed_at: 'filmed at', finding: 'finding', relation: 'relation', involved: 'involved in'};
// A proposed relation reads as the question the reviewer answers.
const RELATION_ASK = {family: 'Family?', cellmate: 'Cellmates?', command: 'Command relation?', contains: 'Contains the other document?', contained_in: 'Excerpt of the other document?', related: 'Related documents?'};
const TIER = {unconfirmed: 'Unconfirmed', corroborated: 'Corroborated', accepted: 'Accepted', rejected: 'Rejected'};
const BOXES = [{v: 'mine', t: 'Asked of me'}, {v: 'sent', t: 'Sent by me'}, {v: 'open', t: 'All open'}, {v: 'findings', t: 'Findings'}];
const RELATIONSHIPS = [{title: 'Same recording', value: 'same_recording'}, {title: 'Contains the other recording', value: 'contains'}, {title: 'Excerpt of the other recording', value: 'contained_in'}, {title: 'Related event, different recording', value: 'related'}];
const CORRECTABLE = ['name', 'birth_date', 'place_of_birth', 'father_name', 'mother_name', 'arrested_at', 'detained_at', 'detention_branch', 'transferred_to', 'seen_at', 'last_seen', 'document_type', 'document_date', 'occupation', 'sex', 'age', 'event'];
const KINDS = {shared_presence: 'Shared presence', candidate_incident: 'Candidate incident'};
const ICON = {image: 'ti ti-photo', pdf: 'ti ti-file-type-pdf', audio: 'ti ti-volume', video: 'ti ti-video', web: 'ti ti-world', sheet: 'ti ti-table', text: 'ti ti-file-text'};

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const ISO = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/;
export function fmtDate(s) {
  const m = ISO.exec(s || '');
  if (!m) return s || '';
  if (!m[2]) return m[1];
  if (!m[3]) return `${MONTHS[+m[2] - 1]} ${m[1]}`;
  return `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}`;
}
const span = (a, b) => !a ? '' : !b || a === b ? fmtDate(a) : a.slice(0, 7) === b.slice(0, 7) && a.endsWith('-01') ? fmtDate(a.slice(0, 7)) : `${fmtDate(a)} to ${fmtDate(b)}`;
const ago = iso => {
  const d = (Date.now() - new Date(iso)) / 864e5;
  return d < 1 / 24 ? 'just now' : d < 1 ? `${Math.round(d * 24)} h ago` : d < 30 ? `${Math.round(d)} d ago` : fmtDate(iso.slice(0, 10));
};
// The viewer's own calendar day: due dates are days, not instants (en-CA prints YYYY-MM-DD).
const today = () => new Date().toLocaleDateString('en-CA');
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;
const label = p => PROP[p] || (p || '').replaceAll('_', ' ');
const sec = ms => {const s = Math.round((ms || 0) / 1000); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;};

// Invariant copy in plain words. Same checks and outcomes as data.invariants.
export function checkText(c, claim) {
  const n = /(\d+) basis/.exec(c.note || '')?.[1];
  if (c.name === 'basis stands') return c.ok ? `Based on ${plural(+n, 'statement')}, all still standing` : c.note === 'no basis statements named' ? 'Names no statements it is based on' : `Its basis no longer stands: ${c.note.replace('a basis statement', 'one statement')}`;
  if (c.name === 'span exists') return c.ok ? (c.note === 'no span needed' ? 'Needs no place in a source' : 'Points at a real place in the source') : `The source location does not check out: ${c.note.replaceAll('_', ' ')}`;
  if (c.name === 'sources available') return c.ok ? 'Both recordings are still held' : 'A recording is missing or erased';
  if (c.name === 'date in range') return c.ok === null ? 'The document date is unknown, so the date cannot be checked' : c.note === 'no date claimed' ? (claim?.inherited && claim.from ? `Takes its date from the document, ${fmtDate(claim.from)}` : 'No date claimed') : c.ok ? `Dated within the document date, ${fmtDate(c.note.split(' ').pop())}` : `Dated after the document date, ${fmtDate(c.note.split(' ').pop())}`;
  if (c.name === 'subject not stale') return c.ok ? 'Nothing changed since it was proposed' : 'Something about this person changed since it was proposed';
  return c.note;
}

// The claimed region of an image, cropped with context and the box drawn on it.
const ClaimCrop = {
  props: {src: {type: String, required: true}, bbox: {type: Array, required: true}, tier: String, agent: Boolean, alt: String},
  data: () => ({nat: null}),
  computed: {
    box() {
      if (!this.nat) return null;
      const [W, H] = this.nat, [x0, y0, x1, y1] = this.bbox;
      const pad = Math.max(24, (x1 - x0) * 0.25), cx0 = Math.max(0, x0 - pad), cy0 = Math.max(0, y0 - pad * 0.6);
      const cw = Math.min(W, x1 + pad) - cx0, ch = Math.min(H, y1 + pad * 0.6) - cy0;
      const pct = (v, of) => v / of * 100 + '%';
      return {frame: {aspectRatio: `${cw} / ${ch}`}, img: {width: pct(W, cw), left: '-' + pct(cx0, cw), top: '-' + pct(cy0, ch)}, mark: {left: pct(x0 - cx0, cw), top: pct(y0 - cy0, ch), width: pct(x1 - x0, cw), height: pct(y1 - y0, ch)}};
    },
  },
  template: `<figure class="rv-crop" :class="['tier-' + tier, agent ? 'by-agent' : 'by-person']">
    <div class="rv-crop-f" :style="box ? box.frame : {aspectRatio: '4 / 1'}"><img :src="src" :alt="alt" :style="box ? box.img : {opacity: 0}" @load="e => nat = [e.target.naturalWidth, e.target.naturalHeight]"><span v-if="box" class="rv-crop-m" :style="box.mark"></span></div>
  </figure>`,
};

export function mount(boot) {
  const app = Vue.createApp({
    mixins: [layoutMixin], delimiters: config.delimiters,
    data() {
      const p = new URLSearchParams(location.search);
      return {
        BOXES, RELATIONSHIPS, box: p.get('box') || 'mine', items: [], counts: {}, findings: [], loaded: false, error: '',
        selId: p.get('statement'), deep: {statement: p.get('statement'), request: p.get('request')},
        cur: null, curBusy: false, read: null, readError: '', finding: null, picked: [], seq: 0,
        mode: null, reason: '', relationship: null, fix: {text: '', lang: 'ar', reviewer: null, note: ''}, ask: {reviewer: null, text: ''}, title: '',
        reply: '', analysts: [], assignee: null, staged: null, busy: false, done: null, needPasskey: null, snack: {show: false, text: ''},
        shellHints: [['j', 'next'], ['k', 'previous'], ['enter', 'evidence'], ['a', 'accept'], ['r', 'reject'], ['e', 'correct'], ['s', 'ask'], ['x', 'select']],
      };
    },
    computed: {
      list() {
        if (this.box === 'findings') return this.findings.map(f => ({...f, kind: 'finding'}));
        // Due first, earliest first; undated keep the server's order (newest request, or by use).
        return this.items.map((it, i) => ({it, i})).sort((a, b) => (a.it.due || '9999').localeCompare(b.it.due || '9999') || a.i - b.i).map(x => x.it);
      },
      selIndex() {return this.list.findIndex(x => x.id === this.selId);},
      sel() {return this.list[this.selIndex] || null;},
      claim() {return this.cur && this.cur.id === this.selId ? this.cur : null;},
      values() {return this.read?.values || {};},
      viewerItem() {
        const s = this.claim?.source;
        return s && {sha256: s.sha256, kind: s.kind, mime: s.mime, file: s.file, sensitive: s.sensitive, revealed: s.revealed, origins: s.origins, segments: []};
      },
      // The claim drawn on its source: a direct span, or the second it was heard at.
      viewerClaims() {
        const c = this.claim;
        if (!c?.span) return [];
        const direct = c.span_blob === c.source?.sha256;
        const sp = direct ? c.span : c.where.ms ? {ms: c.where.ms} : null;
        return sp ? [{id: c.id, prop: c.prop, label: label(c.prop), value: this.valueText(c), tier: c.tier, actor: {agent: c.agent}, span: sp}] : [];
      },
      pdfHighlight() {
        const c = this.claim;
        if (c?.source?.kind !== 'pdf' || c.span?.start == null) return '';
        const passage = (this.read?.passages || []).find(p => p.text_blob === c.span_blob);
        return passage ? passage.text.slice(c.span.start, c.span.end).trim() : c.excerpt?.span.trim() || '';
      },
      checks() {return (this.claim?.checks || []).map(c => ({...c, text: checkText(c, this.claim)}));},
      stale() {return this.checks.find(c => c.ok === false && 'acknowledge' in c) || null;},
      blocked() {return this.checks.some(c => c.ok === false && !('acknowledge' in c));},
      blind() {return (this.claim?.hidden_sources || []).length > 0;},
      decidable() {const c = this.claim; return !!c && c.can_review && ['unconfirmed', 'corroborated'].includes(c.tier) && c.prop !== 'finding';},
      correctable() {const c = this.claim; return !!c && CORRECTABLE.includes(c.prop) && c.span && ['unconfirmed', 'corroborated'].includes(c.tier) && !c.value.erased;},
      summary() {
        if (!this.loaded) return 'Loading';
        const mine = this.counts.mine ?? 0, due = this.items.filter(i => i.due && i.due <= today()).length;
        return mine ? `${plural(mine, 'claim')} waiting for your decision${this.box === 'mine' && due ? `, ${due} due today or overdue` : ''}` : 'Nothing is waiting for your decision';
      },
      emptyText() {
        return {mine: ['Nothing is waiting for you', 'When a colleague asks you to check a claim, or work is assigned to you, it appears here with its source.'],
          sent: ['Nothing you sent is open', 'Ask a colleague from any claim with s, and follow it here until it is decided.'],
          open: ['No unconfirmed claims', 'Every claim in this investigation is decided. New sources bring new ones.'],
          findings: ['No findings waiting', 'Discovery proposes people held at the same place and time, and candidate incidents.']}[this.box];
      },
    },
    methods: {
      fmtDate, span, ago, plural, label, sec,
      say(text) {this.snack = {show: true, text};},
      fail(err) {
        if (err?.noPasskey) {this.needPasskey = err.setupUrl; return;}
        this.error = err?.response ? failure(err) : err?.message || String(err);
      },
      due(it) {
        if (!it.due) return null;
        const d = it.due, t = today();
        return {text: d < t ? `overdue since ${fmtDate(d)}` : d === t ? 'due today' : `due ${fmtDate(d)}`, late: d <= t};
      },
      personName(p) {return !p ? '' : p.masked ? 'Name from a sensitive source' : p.name || 'Unnamed person';},
      valueText(c) {return this.values[c.id] ?? c.value.text;},
      // The claim as one sentence: who, what, which value, when.
      parts(c) {
        if (!c) return [];
        if (c.kind === 'finding') return [{t: c.reason}];
        const who = s => s ? (s.masked ? {masked: true, t: 'Name from a sensitive source'} : {t: s.name || 'Unnamed person', strong: true}) : null;
        if (c.prop === 'same_as' || c.prop === 'not_same') return [{t: c.prop === 'same_as' ? 'Same person?' : 'Different people?'}, who(c.person), {t: 'and', quiet: true}, who(c.identity)];
        if (c.prop === 'media_match') return [{t: 'Same recording?'}, {t: c.source?.file, strong: true}, {t: 'and', quiet: true}, {t: c.media?.other?.file, strong: true}];
        if (c.prop === 'relation' && c.document) return [{t: RELATION_ASK[c.relation] || 'Related?'}, {t: c.source?.file, strong: true}, {t: 'and', quiet: true}, {t: c.document.file, strong: true}];
        if (c.prop === 'relation') return [{t: RELATION_ASK[c.relation] || 'Related?'}, who(c.person), {t: 'and', quiet: true}, who(c.other)];
        if (c.prop === 'involved') return [{t: 'In this incident?'}, who(c.other), {t: 'in', quiet: true}, {t: c.incident?.title || 'an incident', strong: true}];
        const subject = c.person ? who(c.person) : {t: c.source?.file || 'This source', strong: true};
        const v = this.valueText(c);
        const value = c.value.erased ? {t: 'value erased', quiet: true} : c.value.masked && v == null ? {masked: true, t: "value from a sensitive source"} : c.fields.length && !v ? {t: c.fields.map(f => `${f[0]} ${f[1]}`).join(', ')} : {t: /^\d{4}(-\d\d){0,2}$/.test(v || '') ? fmtDate(v) : v, value: true};
        // A correction reads as the change it makes: "Name: correct X to Y". A name
        // correction drops the subject, whose label is the old name.
        const old = c.corrects?.prop === c.prop && !c.corrects.value.erased && c.corrects.value;
        if (old && value.value) {
          const was = old.masked ? {masked: true, t: 'value from a sensitive source'} : {t: old.text, value: true};
          const fix = [{t: label(c.prop).replace(/^./, x => x.toUpperCase()) + ': correct', quiet: true}, {...was, tight: true}, {t: 'to', quiet: true, tight: true}, {...value, tight: true}];
          return c.prop === 'name' ? fix : [subject, ...fix];
        }
        const out = [subject, {t: label(c.prop), quiet: true}, value];
        if (c.from && !['birth_date', 'document_date'].includes(c.prop)) out.push({t: span(c.from, c.to) + (c.inherited ? ' (document date)' : ''), quiet: c.inherited});
        return out;
      },
      sentence(c) {return this.parts(c).filter(Boolean).map((p, i) => (i ? (p.tight ? ' ' : ' · ') : '') + p.t).join('');},
      pick(id) {
        if (id === this.selId) return;
        this.selId = id; this.mode = null; this.done = null; this.needPasskey = null; this.reason = ''; this.relationship = null;
        const u = new URL(location); u.searchParams.delete('request'); u.searchParams.delete('statement'); u.searchParams.delete('finding');
        if (id) u.searchParams.set(this.box === 'findings' ? 'finding' : 'statement', id);
        u.searchParams.set('box', this.box);
        history.replaceState(null, '', u);
        this.openSel();
      },
      async setBox(v) {
        if (v === this.box) return;
        this.box = v; this.selId = null; this.cur = null; this.finding = null; this.picked = [];
        await this.load();
        if (this.list[0]) this.pick(this.list[0].id);
      },
      async load() {
        this.error = '';
        try {
          const investigation = this.caseSel || undefined;
          if (this.box === 'findings') {
            const [f, q] = await Promise.all([api.get('/api/discover/findings', {params: {investigation}}), api.get('/api/review/queue', {params: {box: 'mine', investigation}})]);
            this.findings = f.data.items; this.counts = {...q.data.counts, findings: f.data.items.length};
          } else {
            const {data} = await api.get('/api/review/queue', {params: {box: this.box, investigation}});
            this.items = data.items; this.counts = {...this.counts, ...data.counts};
          }
        } catch (err) {this.fail(err);} finally {this.loaded = true;}
      },
      async openSel() {
        const id = this.selId, seq = ++this.seq;
        this.read = null; this.readError = '';
        if (!id) {this.cur = null; this.finding = null; return;}
        this.curBusy = true;
        try {
          if (this.box === 'findings') {
            const {data} = await api.get(`/api/discover/findings/${id}`);
            if (seq !== this.seq) return;
            const place = data.places[0] || {};
            this.finding = data; this.title = `${place.en || place.ar || 'Unnamed place'}, ${fmtDate(data.month)}`;
          } else {
            const {data} = await api.get(`/api/review/claim/${id}`);
            if (seq !== this.seq) return;
            this.cur = data;
            this.patchRow(data);
            if (data.source?.revealed) await this.loadRead();
          }
        } catch (err) {if (seq === this.seq) this.fail(err);} finally {if (seq === this.seq) this.curBusy = false;}
      },
      patchRow(c) {const i = this.items.findIndex(x => x.id === c.id); if (i >= 0) this.items.splice(i, 1, {...this.items[i], ...c});},
      async loadRead() {
        const sha = this.cur?.source?.sha256;
        if (!sha || !['pdf', 'audio', 'video', 'text', 'web'].includes(this.cur.source.kind) && !this.cur.value.masked) return;
        try {this.read = (await api.get(`/api/evidence/${sha}/read`)).data;} catch (err) {this.readError = failure(err);}
      },
      async revealed() {
        this.say('Showing the source. Your access is logged to the custody chain');
        await this.openSel();
        await this.load();
      },
      // One logged reveal per hidden source the decision rests on, then the claim in full.
      async revealAll() {
        const n = this.claim.hidden_sources.length;
        this.busy = true;
        try {
          this.cur = (await api.post(`/api/review/claim/${this.claim.id}/reveal`, {item: {}})).data;
          this.patchRow(this.cur);
          const left = this.cur.hidden_sources.length;
          this.say(left ? `${plural(left, 'source')} could not stay shown: a session holds 8 reveals at a time` : `Showing ${plural(n, 'source')}. Each reveal is logged to the custody chain under your name`);
          if (this.cur.source?.revealed) await this.loadRead();
          await this.load();
        } catch (err) {this.fail(err);} finally {this.busy = false;}
      },
      move(d) {
        const n = this.list.length;
        if (!n) return;
        const i = this.selIndex < 0 ? 0 : Math.max(0, Math.min(n - 1, this.selIndex + d));
        this.pick(this.list[i].id);
        this.$nextTick(() => document.querySelector(`.rv-row[data-id="${this.list[i].id}"]`)?.scrollIntoView({block: 'nearest'}));
      },
      togglePick(id) {this.picked = this.picked.includes(id) ? this.picked.filter(x => x !== id) : [...this.picked, id];},
      focusPane() {const el = document.getElementById('rv-evidence'); el?.scrollIntoView({block: 'start', behavior: 'smooth'}); el?.querySelector('[tabindex="0"], button, a')?.focus({preventScroll: true});},
      async needAnalysts() {if (!this.analysts.length) this.analysts = (await api.get('/api/collaboration/analysts')).data.items;},
      async start(mode) {
        if (this.box === 'findings') {
          if (!this.finding) return;
          if (mode === 'accept') return this.acceptFinding();
          if (mode === 'reject') {this.mode = 'reject'; this.focusField('rv-reason');}
          return;
        }
        const c = this.claim;
        if (!c) return;
        if (mode === 'accept' || mode === 'reject') {
          if (!this.decidable) {this.say(c.can_review ? `This claim is ${c.tier}; there is nothing to decide` : 'Another analyst was asked to decide this one'); return;}
          if (this.blind) {this.say('Show the values first. A decision is signed on what you could read'); return;}
          if (mode === 'accept' && this.blocked) {this.say('A check fails, so it cannot be accepted. Reject it or correct it'); return;}
          if (mode === 'accept' && c.prop !== 'media_match') return this.decide('accept');
          this.mode = mode; this.focusField(mode === 'reject' ? 'rv-reason' : 'rv-rel');
        } else if (mode === 'correct') {
          if (!this.correctable) {this.say('Only a pending claim read from a source can be corrected'); return;}
          await this.needAnalysts();
          const v = this.valueText(c);
          this.fix = {text: v || '', lang: /[؀-ۿ]/.test(v || '') ? 'ar' : 'en', reviewer: c.request?.by?.user_id && c.request.by.user_id !== boot.me ? c.request.by.user_id : null, note: ''};
          this.mode = 'correct'; this.focusField('rv-fix');
        } else if (mode === 'ask') {
          if (c.request) {this.focusField('rv-reply'); return;}
          if (c.tier !== 'unconfirmed') {this.say('Only unconfirmed claims can be sent for review'); return;}
          await this.needAnalysts();
          this.ask = {reviewer: null, text: ''}; this.mode = 'ask'; this.focusField('rv-ask');
        }
      },
      focusField(id) {this.$nextTick(() => document.getElementById(id)?.focus());},
      // Stage on the server, show the rows the passkey will sign, sign on confirm.
      async stage(item, start, title, after) {
        this.busy = true; this.needPasskey = null; this.error = '';
        try {this.staged = {data: await prepare(item, start, {inline: false}), title, after};}
        catch (err) {this.fail(err);} finally {this.busy = false;}
      },
      async confirm() {
        const {data, after} = this.staged;
        this.busy = true;
        try {await after(await sign(data));} catch (err) {this.fail(err);} finally {this.busy = false; this.staged = null;}
      },
      decide(verdict) {
        const c = this.claim, item = {statement: c.id, verdict, reason: verdict === 'reject' ? this.reason.trim() : ''};
        if (verdict === 'accept' && this.stale) item.acknowledged_head = this.stale.acknowledge;
        if (verdict === 'accept' && c.prop === 'media_match') item.relationship = this.relationship;
        this.stage(item, '/api/review/options', `${verdict === 'accept' ? 'Accept' : 'Reject'}: ${this.sentence(c)}`, async out => {
          this.mode = null; this.reason = '';
          this.done = {id: c.id, text: `${verdict === 'accept' ? 'Accepted' : 'Rejected'} and signed with your passkey. Now ${out.tier}.`, review: out.review};
          await this.openSel(); await this.load();
        });
      },
      undo() {
        const review = this.done?.review || this.claim?.my_review;
        if (!review) return;
        this.stage({undo: review}, '/api/review/options', `Undo your decision on: ${this.sentence(this.claim)}`, async () => {
          this.done = {id: this.selId, text: 'Undone. The claim is open again.'};
          await this.openSel(); await this.load();
        });
      },
      async sendCorrection() {
        const c = this.claim;
        this.busy = true;
        try {
          await api.post('/api/collaboration/suggestions', {item: {statement: c.id, reviewer: this.fix.reviewer, reason: this.fix.note.trim() || 'Corrected value', value: {text: this.fix.text.trim(), lang: this.fix.lang.trim()}, span: c.span}});
          this.mode = null;
          this.say('Correction sent. It stays pending until your colleague signs it; the original is kept');
          await this.openSel(); await this.load();
        } catch (err) {this.fail(err);} finally {this.busy = false;}
      },
      async sendAsk() {
        this.busy = true;
        try {
          await api.post('/api/collaboration/requests', {item: {statement: this.claim.id, reviewer: this.ask.reviewer, reason: this.ask.text.trim()}});
          this.mode = null; this.say('Review requested. Your colleague is notified');
          await this.openSel(); await this.load();
        } catch (err) {this.fail(err);} finally {this.busy = false;}
      },
      async sendReply() {
        this.busy = true;
        try {
          await api.post(`/api/collaboration/requests/${this.claim.request.id}/comments`, {item: {text: this.reply.trim()}});
          this.reply = ''; await this.openSel();
        } catch (err) {this.fail(err);} finally {this.busy = false;}
      },
      acceptFinding() {
        const f = this.finding;
        if (!this.title.trim()) {this.say('Name the incident first'); return;}
        const kept = f.members.map(m => m.statement), roles = Object.fromEntries(f.people.map(p => [p, 'unspecified']));
        this.stage({finding: f.id, kept, roles, title: this.title.trim(), incident: null}, '/api/review/findings/options', `Accept: ${f.reason}`, async out => {
          const incident = out.statements.find(s => s.subject.startsWith('incident:'))?.subject.slice(9);
          this.done = {id: f.id, text: `Signed once: ${plural(out.statements.length, 'statement')} accepted, incident “${this.title.trim()}” created.`, gesture: out.gesture, statements: out.statements.map(s => s.id), incident};
          await this.load();
        });
      },
      rejectFinding() {
        const f = this.finding;
        this.stage({finding: f.id, reason: this.reason.trim()}, '/api/review/findings/reject', `Reject: ${f.reason}`, async out => {
          this.mode = null; this.reason = '';
          this.done = {id: f.id, text: 'Rejected and signed. This set will not be proposed again.', gesture: out.gesture, statements: out.statements.map(s => s.id)};
          await this.load();
        });
      },
      undoGesture() {
        const d = this.done;
        this.stage({gesture: d.gesture}, '/api/review/undo', 'Undo the whole gesture', async () => {
          this.done = {id: d.id, text: 'Undone. The finding is back in the queue.'};
          await this.load();
        });
      },
      async assign() {
        this.busy = true;
        try {
          const by = {};
          for (const it of this.items.filter(i => this.picked.includes(i.id))) (by[it.source?.investigation || this.caseSel] ||= []).push('statement:' + it.id);
          let n = 0;
          for (const [investigation, subjects] of Object.entries(by)) n += (await api.post('/api/assignments/bulk', {item: {investigation, assignee: this.assignee, subjects}})).data.assigned;
          this.picked = []; this.assignee = null; this.say(`${plural(n, 'claim')} assigned`);
          await this.load();
        } catch (err) {this.fail(err);} finally {this.busy = false;}
      },
      useCase() {this.selId = null; this.cur = null; this.load().then(() => this.list[0] && this.pick(this.list[0].id));},
      key(e) {
        const t = e.target, typing = t.isContentEditable || ['TEXTAREA', 'SELECT'].includes(t.tagName) || (t.tagName === 'INPUT' && !['checkbox', 'radio', 'button'].includes(t.type));
        if (e.key === 'Escape' && this.mode && !this.staged) {this.mode = null; return;}
        if (typing || t.closest('.v-overlay') || e.metaKey || e.ctrlKey || e.altKey || this.staged) return;
        const act = {j: () => this.move(1), k: () => this.move(-1), Enter: () => this.focusPane(), x: () => this.sel && this.box !== 'findings' && this.togglePick(this.sel.id),
          a: () => this.start('accept'), r: () => this.start('reject'), e: () => this.start('correct'), s: () => this.start('ask')}[e.key];
        if (!act || (e.key === 'Enter' && ['A', 'BUTTON'].includes(t.tagName))) return;
        e.preventDefault(); act();
      },
      _onWsMessage(msg) {
        layoutMixin.methods._onWsMessage.call(this, msg);
        // Request links come as /review?request=<id> (old) or /inbox?item=request:<id>.
        const rid = /request[=:]([0-9a-f-]{36})/.exec(msg.to || '')?.[1];
        if (msg.type === 'notification' && rid) {
          this.load();
          if (this.claim?.request?.id === rid) this.openSel();
        }
      },
    },
    async mounted() {
      document.addEventListener('keydown', this.key);
      try {
        if (this.deep.request) {
          const {data} = await api.get(`/api/review/request/${this.deep.request}`);
          this.box = data.request?.mine ? 'mine' : data.request?.sent ? 'sent' : 'open';
          this.selId = data.id;
        } else if (this.deep.statement) {
          const {data} = await api.get(`/api/review/claim/${this.deep.statement}`);
          if (!data.request?.mine && this.box === 'mine') this.box = data.request?.sent ? 'sent' : 'open';
          this.selId = data.id;
        } else if (new URLSearchParams(location.search).get('finding')) {
          this.box = 'findings'; this.selId = new URLSearchParams(location.search).get('finding');
        }
      } catch (err) {this.fail(err);}
      await this.load();
      // A deep-linked claim outside the box's first page still opens, and joins the list.
      if (this.selId && this.box !== 'findings' && !this.items.some(i => i.id === this.selId)) {
        try {const {data} = await api.get(`/api/review/claim/${this.selId}`); this.items.unshift(data);} catch (err) {this.fail(err);}
      }
      if (!this.selId && this.list[0]) this.selId = this.list[0].id;
      await this.openSel();
    },
  });
  Object.assign(app.config.globalProperties, {ICON, KINDS, TIER});
  app.component('evidence-pair', EvidencePair).component('claim-crop', ClaimCrop);
  registerMediaViewers(app);
  registerStkComponents(app);
  app.use(Vuetify.createVuetify(config.vuetifyConfig)).mount('#app');
  return app;
}
