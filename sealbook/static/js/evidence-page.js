/* Evidence page: one source with its preview, claims and intelligence panel.
   Every media view goes through the logged reveal (POST /api/evidence/<sha>/reveal);
   text read from the source comes from /read, which the server gates on that reveal. */
import {attest} from '/static/js/monitor/passkey.js';
import {registerMediaViewers} from '/static/js/components/media.js';
import {EvidenceLocation} from '/static/js/components/EvidenceLocation.js';
import {ResearchDrawer} from '/static/js/components/ResearchDrawer.js';
import {FindLinks} from '/static/js/components/FindLinks.js';
import {NoticerPanel} from '/static/js/components/NoticerPanel.js';
import {SourceSteps, LIVE} from '/static/js/components/SourceSteps.js';
import {TIMEOUT, failure} from '/static/js/components/EvidenceViewer.js';

const api = axios.create({timeout: TIMEOUT});
const PASS = ['triage', 'title', 'description', 'label'];

const PROP = {name: 'Name', father_name: "Father's name", mother_name: "Mother's name", birth_date: 'Born', place_of_birth: 'Born in', arrested_at: 'Arrested at', last_seen: 'Last seen', detention_branch: 'Held by branch', transferred_to: 'Transferred to', seen_at: 'Seen at', detained_at: 'Detained at', sex: 'Sex', age: 'Age', occupation: 'Occupation', event: 'Event', document_date: 'Document date', reports: 'Reports', document_type: 'Document type', same_as: 'Same person', media_match: 'Media match', review: 'Review', retraction: 'Undo', located_at: 'Place confirmed', access: 'Access', sha256: 'SHA-256', md5: 'MD5', c2pa_verify: 'C2PA check', fingerprint: 'Fingerprint', url_liveness: 'Link check', photographed_at: 'Photographed at', filmed_at: 'Filmed at', triage: 'Triage', title: 'Title', description: 'Description', label: 'Alleged label'};
const DOC = {detention_register: 'Detention register', transfer_order: 'Transfer order', witness_statement: 'Witness statement', photo: 'Photo', web_post: 'Web post', letter: 'Letter', voice_note: 'Voice note', video: 'Video', news_article: 'News article', report: 'Report', intake_sheet: 'Intake sheet'};
const ICON = {image: 'ti ti-photo', pdf: 'ti ti-file-type-pdf', audio: 'ti ti-volume', video: 'ti ti-video', web: 'ti ti-world', sheet: 'ti ti-table', text: 'ti ti-file-text'};
const PENDING = ['unconfirmed', 'corroborated'];
const ms = v => {const s = Math.max(0, Math.round((v || 0) / 1000)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;};
const prop = p => PROP[p] || p.replace(/_/g, ' ');
const short = h => h ? h.slice(0, 8) : '';
const message = failure;
// Unicode isolate: an Arabic name ending in digits keeps its own order inside English copy.
const iso = t => `\u2068${t}\u2069`;
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
// A place more sources than this name folds behind one summary line.
const FOLD_PLACE = 3;
const spanLabel = s => !s ? '' : s.bbox ? `region, p. ${s.page || 1}` : s.ms ? `${ms(s.ms[0])} to ${ms(s.ms[1])}` : s.row ? `row ${s.row}, column ${s.column}` : s.start != null ? `characters ${s.start} to ${s.end}` : '';

export function mount(boot) {
  // Outside data: Vue's proxy breaks EventSource methods.
  let stream = null, nudge = null;
  const app = Vue.createApp({
    mixins: [layoutMixin], delimiters: config.delimiters,
    data() {
      return {boot, it: null, other: null, read: null, otherRead: null, error: '', readError: '', otherReadError: '', tab: 'read', drawer: false, sel: null,
        cursor: {list: 'claims', i: -1}, excerpt: null, threadId: null, thread: null, threadBusy: false, comment: '',
        ask: {open: false, statement: null, reviewer: null, reason: '', busy: false}, analysts: [], relate: {open: false, row: null, value: 'same_recording'},
        snack: {show: false, text: ''}, unfolded: {}, busy: false, research: false, pdfPage: boot.segment?.location?.page || boot.page || null, focusMs: boot.segment?.location?.ms?.[0] ?? null};
    },
    computed: {
      claims() {return this.it ? this.it.claims.map(c => ({...c, label: prop(c.prop), value: this.read?.values?.[c.id] ?? c.display})) : [];},
      otherClaims() {return this.other ? this.other.claims.map(c => ({...c, label: prop(c.prop), value: this.otherRead?.values?.[c.id] ?? c.display})) : [];},
      // The first pass of the analyst's method (sealbook/method), shown on its own.
      pass() {
        const all = this.claims.filter(c => PASS.includes(c.prop) && c.tier !== 'rejected');
        const by = p => all.filter(c => c.prop === p);
        return {all, open: all.filter(c => c.tier === 'unconfirmed'), triage: by('triage')[0], title: by('title')[0], lines: by('description'), labels: by('label')};
      },
      groups() {
        const g = {};
        this.claims.filter(c => !PASS.includes(c.prop)).forEach(c => (g[c.subject] ||= []).push(c));
        return Object.entries(g).map(([subject, claims]) => ({subject, claims, name: this.nameOf(subject)}));
      },
      ordered() {return this.groups.flatMap(g => g.claims);},
      pins() {return this.claims.filter(c => c.tier !== 'rejected' && c.value);},
      marks() {return ['audio', 'video'].includes(this.it?.kind) ? this.it.copies.map(c => [c.here[0], Math.max(c.here[1], c.here[0] + 1000)]) : [];},
      otherCopy() {return this.other && this.it.copies.find(c => c.other === this.other.sha256);},
      otherAt() {return this.boot.at ?? this.otherCopy?.there?.[0] ?? null;},
      otherMarks() {const c = this.otherCopy; return c ? [[c.there[0], Math.max(c.there[1], c.there[0] + 1000)]] : this.otherAt != null ? [[this.otherAt, this.otherAt + 2000]] : [];},
      relations() {
        if (!this.it) return [];
        const out = [], folded = new Set();
        for (const r of this.it.relations) {
          if (r.kind !== 'Same place' || r.sources <= FOLD_PLACE) {out.push(r); continue;}
          if (!folded.has(r.place)) {folded.add(r.place); out.push({id: `fold:${r.place}`, fold: true, place: r.place, at_place: r.at_place, sources: r.sources});}
          if (this.unfolded[r.place]) out.push({...r, nested: true});
        }
        return out;
      },
      connections() {
        if (!this.it) return [];
        const seen = new Set();
        return [...this.it.copies, ...this.it.relations].filter(r => r.other && r.other !== this.it.sha256 && !seen.has(r.other + r.kind) && seen.add(r.other + r.kind))
          .map(r => ({key: r.other + r.kind, kind: r.kind === 'Nearest by meaning' ? 'Similar meaning' : r.kind, o: this.it.docs[r.other], href: this.side(r)}));
      },
      threadsByAbout() {const m = {}; (this.it?.threads || []).forEach(t => (m[t.about] ||= []).push(t)); return m;},
      c2pa() {const v = this.it?.c2pa; return v && (v.status || v.result);},
      custody() {
        if (!this.it) return [];
        const acq = this.it.acquisitions.map(a => ({id: a.id, title: 'acquired', detail: a.origin, who: a.collector?.name || 'unknown', agent: a.collector?.kind === 'agent', at: a.at}));
        const st = this.it.custody.map(s => ({id: s.id, title: prop(s.prop), detail: s.prop === 'access' ? s.display : '', who: s.actor.label, agent: s.actor.agent, at: s.recorded_at}));
        return [...acq, ...st].sort((a, b) => b.at.localeCompare(a.at));
      },
      lists() {return {claims: this.other ? [] : this.ordered, copies: this.it?.copies || [], rel: this.relations};},
      activeList() {return this.cursor.list === 'claims' && this.lists.claims.length ? 'claims' : this.tab === 'dups' ? 'copies' : this.tab === 'rel' ? 'rel' : this.lists.claims.length ? 'claims' : 'copies';},
      current() {const l = this.lists[this.cursor.list]; return l && this.cursor.i >= 0 ? l[this.cursor.i] : null;},
      // Something is queued or running on this source: the run stream stays open.
      live() {return (this.it?.steps || []).some(s => LIVE.includes(s.state));},
      // With claims shown, the steps panel stays while anything is not settled.
      // A sheet's columns wait for a person: the claims panel takes the full width.
      mapping() {return (this.it?.steps || []).some(s => s.action === 'map');},
      stepsOpen() {return (this.it?.steps || []).some(s => !['done', 'skipped'].includes(s.state));},
      hl() {const s = this.boot.segment; return s?.span && ['text', 'web'].includes(this.it?.kind) && s.kind === 'text' ? s.span : null;},
    },
    methods: {
      ms, prop, short, spanLabel, plural,
      say(text) {this.snack = {show: true, text};},
      // Where a claim came from in plain words; the model or importer is the tooltip.
      byLabel(c) {
        if (c.actor.kind === 'import') return `from the imported ${(this.it.doc_type && this.it.doc_type !== 'unknown' ? this.it.doc_type : 'source').replaceAll('_', ' ')}`;
        return c.actor.label;
      },
      // The name this source gives, else the best one the reader may see elsewhere,
      // else a number on this page: never an id.
      nameOf(subject) {
        if (!subject.startsWith('person:')) return 'This document';
        const n = this.claims.find(c => c.subject === subject && c.prop === 'name' && c.value);
        if (n) return n.value;
        if (this.it?.people?.[subject.slice(7)]) return this.it.people[subject.slice(7)];
        const unnamed = [...new Set(this.claims.map(c => c.subject).filter(s => s.startsWith('person:') && !this.claims.some(c => c.subject === s && c.prop === 'name' && c.value) && !this.it?.people?.[s.slice(7)]))];
        return `Unnamed person ${unnamed.indexOf(subject) + 1}`;
      },
      // One sentence per relation, with the same prop words as the claims list.
      relWhy(r) {
        const words = ps => ps.map(p => prop(p).toLowerCase()).join(', ');
        if (r.kind === 'Same place') return `Both name ${iso(r.at_place)}. There: ${words(r.props_there)} (${plural(r.count, 'claim')}). Here: ${words(r.props_here)}.`;
        if (r.kind === 'Same person?') {
          const here = this.claims.find(c => c.subject === r.here && c.prop === 'name' && c.value)?.value || r.here_name;
          return `${here ? iso(here) : 'An unnamed person'} here and ${r.their_name ? iso(r.their_name) : 'an unnamed person'} there, ${r.why}`;
        }
        if (r.here_text || r.there_text) return r.why.replace(r.here_text || '\0', iso(r.here_text)).replace(r.there_text || '\0', iso(r.there_text));
        return r.why;
      },
      fold(r) {this.unfolded = {...this.unfolded, [r.place]: !this.unfolded[r.place]};},
      side(r) {return r.other ? `/evidence/${this.it.sha256}?with=${r.other}${r.there?.[0] != null ? '&at=' + r.there[0] : ''}` : null;},
      doc(sha) {return this.it.docs[sha] || {file: short(sha), title: short(sha)};},
      tierOf(r) {return r.tier;},
      async load() {
        this.error = '';
        try {
          this.it = (await api.get(`/api/evidence/${boot.sha}/item`)).data;
          document.title = `${this.it.title}, ${DOC[this.it.doc_type] || 'Evidence'}`;
          if (boot.with) this.other = (await api.get(`/api/evidence/${boot.with}/item`)).data;
          if (!boot.segment && !this.it.claims.length) this.tab = this.it.copies.length ? 'dups' : this.relations.length ? 'rel' : 'read';
        } catch (err) {this.error = message(err); return;}
        if (this.it.revealed) await this.loadRead();
        if (this.other?.revealed) await this.loadOtherRead();
      },
      async loadRead() {
        this.readError = '';
        try {
          this.read = (await api.get(`/api/evidence/${this.it.sha256}/read`)).data;
          const s = boot.segment;
          if (s?.span && !this.hl) this.excerpt = (await api.get(`/api/evidence/segments/${s.id}/excerpt`, {params: s.span})).data;
        } catch (err) {this.readError = message(err);}
      },
      async loadOtherRead() {
        this.otherReadError = '';
        try {this.otherRead = (await api.get(`/api/evidence/${this.other.sha256}/read`)).data;} catch (err) {this.otherReadError = message(err);}
      },
      async revealed(item) {
        this.say(`Showing ${item.title}. Your access is logged to the custody chain`);
        if (item.sha256 === this.it.sha256) {this.it.revealed = true; await this.loadRead(); await this.refresh();}
        else {this.other.revealed = true; await this.loadOtherRead();}
      },
      async refresh() {try {this.it = (await api.get(`/api/evidence/${boot.sha}/item`)).data;} catch (err) {this.say(message(err));}},
      // A claim from a transcript points at characters of the joined transcript; the read timeline maps them to the line's second.
      msAt(span) {
        if (span?.start == null || !this.read?.timeline) return null;
        const line = this.read.timeline.findLast(l => l[0] <= span.start);
        return line ? line[1] : null;
      },
      // Selecting a claim shows it in the source; Show in source (Enter) also plays media.
      pick(id, play = false) {
        const c = this.claims.find(x => x.id === id);
        this.sel = id; const i = this.ordered.findIndex(x => x.id === id); if (i >= 0) this.cursor = {list: 'claims', i};
        if (c) this.$refs.viewer?.show(c, {play, ms: this.msAt(c.span) ?? c.span?.ms?.[0] ?? null});
      },
      openSpan(c) {
        this.pick(c.id, true);
        const panel = this.$refs.viewer?.$el.closest('.ev-panel'), r = panel?.getBoundingClientRect();
        if (r && (r.top < 0 || r.top > innerHeight * 0.5)) panel.scrollIntoView({block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
      },
      move(d) {
        const list = this.activeList, rows = this.lists[list];
        if (!rows.length) return;
        const i = this.cursor.list === list ? Math.max(0, Math.min(rows.length - 1, this.cursor.i + d)) : 0;
        this.cursor = {list, i};
        if (list === 'claims') this.pick(rows[i].id);
        this.$nextTick(() => {const el = document.querySelector(`[data-row="${list}:${i}"]`); el?.focus(); el?.scrollIntoView({block: 'nearest'});});
      },
      focusRow(list, i) {this.cursor = {list, i}; if (list === 'claims') this.pick(this.lists.claims[i].id);},
      enter() {const r = this.current; if (!r) return; if (this.cursor.list === 'claims') this.openSpan(r); else if (r.fold) this.fold(r); else if (r.other) location.href = `/evidence/${r.other}`;},
      // A media time when the line rests on a transcript; text needs no position.
      at(c) {const t = this.msAt(c.span); return t != null ? ms(t) : '';},
      async acceptPass() {
        this.busy = true;
        try {
          await attest({sha: this.it.sha256}, '/api/review/firstpass/options');
          this.say('First pass accepted and signed with your passkey');
          await this.refresh();
        } catch (err) {this.say(err.message);} finally {this.busy = false;}
      },
      canDecide(r) {return r && r.can_review && PENDING.includes(r.tier);},
      async decide(r, verdict, relationship) {
        if (!this.canDecide(r)) {this.say(r?.statement || r?.prop ? 'The review flow does not let you decide this one' : 'A lead, not a proposal. Nothing to accept'); return;}
        const statement = r.statement || r.id;
        if (r.kind && r.kind !== 'Same person?' && verdict === 'accept' && !relationship) {this.relate = {open: true, row: r, value: 'same_recording'}; return;}
        this.busy = true;
        try {
          const done = await attest({statement, verdict, reason: '', ...(relationship ? {relationship} : {})});
          this.say(`${verdict === 'accept' ? 'Accepted' : 'Rejected'} and signed with your passkey. Now ${done.tier}`);
          await this.refresh();
        } catch (err) {this.say(err.message);} finally {this.busy = false; this.relate.open = false;}
      },
      threadFor(r) {return (this.threadsByAbout[r.statement || r.id] || [])[0];},
      async openThread(t) {
        this.drawer = true; this.threadId = t?.id || null; this.thread = null;
        if (t?.visible) {this.threadBusy = true; try {this.thread = (await api.get(`/api/collaboration/requests/${t.id}`)).data;} catch (err) {this.say(message(err));} finally {this.threadBusy = false;}}
      },
      async askAbout(r) {
        const t = r && this.threadFor(r);
        if (t) {await this.openThread(t); this.$nextTick(() => document.getElementById('ev-comment')?.focus()); return;}
        if (!r || !(r.statement || r.prop)) {this.say('Pick a claim or a proposal to ask about'); return;}
        if (r.tier !== 'unconfirmed') {this.say('Only pending proposals can be sent for review'); return;}
        if (!this.analysts.length) this.analysts = (await api.get('/api/collaboration/analysts')).data.items;
        this.ask = {open: true, statement: r.statement || r.id, reviewer: null, reason: '', busy: false};
      },
      async sendAsk() {
        this.ask.busy = true;
        try {await api.post('/api/collaboration/requests', {item: {statement: this.ask.statement, reviewer: this.ask.reviewer, reason: this.ask.reason}}); this.ask.open = false; this.say('Review requested. Your colleague is notified'); await this.refresh();}
        catch (err) {this.say(message(err));} finally {this.ask.busy = false;}
      },
      async sendComment() {
        this.threadBusy = true;
        try {await api.post(`/api/collaboration/requests/${this.thread.id}/comments`, {item: {text: this.comment}}); this.comment = ''; await this.openThread({id: this.thread.id, visible: true}); await this.refresh();}
        catch (err) {this.say(message(err));} finally {this.threadBusy = false;}
      },
      // While anything on this source is queued or running, live run events nudge a
      // reload, so the steps and claims change as runs finish; the stream closes once
      // nothing is queued.
      follow() {
        if (!this.live) {stream?.close(); stream = null; return;}
        if (stream) return;
        stream = new EventSource('/monitor/stream');
        stream.addEventListener('run_event', e => {
          if (JSON.parse(e.data).doc?.sha256 !== this.it.sha256) return;
          clearTimeout(nudge);
          nudge = setTimeout(async () => {await this.refresh(); this.follow();}, 400);
        });
      },
      // After an action in the steps panel the panel itself shows the new state.
      async changed() {await this.refresh(); this.follow();},
      aboutLabel(id) {const c = this.claims.find(x => x.id === id); return c ? `${prop(c.prop)}${c.value ? ': ' + c.value : ''}` : `proposal ${short(id)}`;},
      key(e) {
        const tag = e.target.tagName;
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || e.target.isContentEditable || this.ask.open || this.relate.open) return;
        const act = {j: () => this.move(1), k: () => this.move(-1), Enter: () => this.enter(), t: () => {this.drawer = !this.drawer; if (this.drawer && !this.threadId && this.it.threads[0]) this.openThread(this.it.threads[0]);},
          a: () => this.decide(this.current, 'accept'), r: () => this.decide(this.current, 'reject'), c: () => this.askAbout(this.current),
          s: () => {const r = this.current?.other ? this.current : this.it.copies[0] || this.relations.find(x => x.other); if (r && this.side(r)) location.href = this.side(r); else this.say('Nothing to open side by side');}}[e.key];
        if (!act || (e.key === 'Enter' && ['A', 'BUTTON'].includes(tag))) return;
        if (!this.it) return;
        e.preventDefault(); act();
      },
    },
    async mounted() {
      if (location.pathname.startsWith('/evidence/segments/')) history.replaceState(null, '', `/evidence/${boot.sha}?segment=${boot.segment.id}${boot.segment.span ? `&start=${boot.segment.span.start}&end=${boot.segment.span.end}` : ''}`);
      document.addEventListener('keydown', this.key);
      await this.load();
      if (this.it) this.follow();
    },
  });
  Object.assign(app.config.globalProperties, {ICON, DOC});
  registerMediaViewers(app);
  app.component('evidence-location', EvidenceLocation);
  app.component('research-drawer', ResearchDrawer);
  app.component('find-links', FindLinks);
  app.component('noticer-panel', NoticerPanel);
  app.component('source-steps', SourceSteps);
  registerStkComponents(app);
  app.use(Vuetify.createVuetify(config.vuetifyConfig)).mount('#app');
  return app;
}
