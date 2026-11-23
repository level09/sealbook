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
// What a masked first-pass line says until the sensitive item is shown (and logged).
const MASKED = 'Masked: show the sensitive item to read it';

const DOC = {detention_register: 'Detention register', transfer_order: 'Transfer order', witness_statement: 'Witness statement', photo: 'Photo', web_post: 'Web post', letter: 'Letter', voice_note: 'Voice note', video: 'Video', news_article: 'News article', report: 'Report', intake_sheet: 'Intake sheet'};
const ICON = {image: 'ti ti-photo', pdf: 'ti ti-file-type-pdf', audio: 'ti ti-volume', video: 'ti ti-video', web: 'ti ti-world', sheet: 'ti ti-table', text: 'ti ti-file-text'};
const PENDING = ['unconfirmed', 'corroborated'];
const ms = v => {const s = Math.max(0, Math.round((v || 0) / 1000)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;};
const prop = propTitle;
const short = h => h ? h.slice(0, 8) : '';
const message = failure;
// Unicode isolate: an Arabic name ending in digits keeps its own order inside English copy.
const iso = t => `\u2068${t}\u2069`;
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
// A place more sources than this name folds behind one summary line.
const FOLD_PLACE = 3;
// The words the Guide uses for each witness and receipt state.
const WITNESS = {ots: 'OpenTimestamps', tsa: 'Time-stamp authority', partner: 'Partner copy'};
const RECEIPT = {held: 'Receipt held', pending: 'Receipt held, waiting for Bitcoin', failed: 'Not sent yet, retried next run', unsent: 'Not sent yet'};
const CREDENTIALS = {valid: {text: 'verified', color: 'success', icon: 'ti ti-shield-check'}, absent: {text: 'none in this file', color: undefined, icon: 'ti ti-shield-off'}, invalid: {text: 'invalid', color: 'warning', icon: 'ti ti-shield-x'}};
const when = at => `${at.slice(0, 16).replace('T', ' ')} UTC`;
const spanLabel = s => !s ? '' : s.bbox ? `region, p. ${s.page || 1}` : s.ms ? `${ms(s.ms[0])} to ${ms(s.ms[1])}` : s.row ? `row ${s.row}, column ${s.column}` : s.start != null ? 'quoted text' : '';

export function mount(boot) {
  // Outside data: Vue's proxy breaks EventSource methods.
  let stream = null, nudge = null;
  const app = Vue.createApp({
    mixins: [layoutMixin], delimiters: config.delimiters,
    data() {
      return {boot, masked: MASKED, it: null, other: null, read: null, otherRead: null, error: '', readError: '', otherReadError: '', tab: 'read', drawer: false, sel: null,
        cursor: {list: 'claims', i: -1}, excerpt: null, threadId: null, thread: null, threadBusy: false, comment: '',
        ask: {open: false, statement: null, reviewer: null, reason: '', busy: false}, analysts: [], relate: {open: false, row: null, value: 'same_recording'},
        snack: {show: false, text: ''}, hov: null, proof: {open: false, claim: null, data: null, error: '', busy: false}, locations: [], people: [], viewers: {}, working: false, unfolded: {}, busy: false, research: false, pdfPage: boot.segment?.location?.page || boot.page || null, focusMs: boot.segment?.location?.ms?.[0] ?? null};
    },
    computed: {
      claims() {return this.it ? this.it.claims.map(c => ({...c, label: prop(c.prop), value: this.read?.values?.[c.id] ?? c.display})) : [];},
      otherClaims() {return this.other ? this.other.claims.map(c => ({...c, label: prop(c.prop), value: this.otherRead?.values?.[c.id] ?? c.display})) : [];},
      // A recording's transcript lines, and what speech to text made of it in words.
      spoken() {return this.read?.passages.filter(p => p.location.ms) || [];},
      speechSaid() {
        const s = this.read?.speech;
        if (!s) return 'Speech to text has not run on it.';
        const by = s.route === 'local' ? 'on this server' : `by ${s.model}, a hosted model${s.route === 'cloud-zdr' ? ' that keeps no data' : ''}`;
        return {
          reading: 'Speech to text is reading it.',
          failed: 'Speech to text did not finish. The steps say why and offer a retry.',
          no_audio: 'It has no sound track, so there is no speech to read.',
          none: `No speech found: speech to text heard none, read ${by}.`,
          done: `Transcript read ${by}. Each line opens at its time.`,
        }[s.state];
      },
      // The first pass of the analyst's method (sealbook/method), shown on its own.
      pass() {
        const all = this.claims.filter(c => PASS.includes(c.prop) && c.tier !== 'rejected');
        const by = p => all.filter(c => c.prop === p);
        const labels = by('label');
        return {all, open: all.filter(c => c.tier === 'unconfirmed'), triage: by('triage')[0], title: by('title')[0], lines: by('description'), seen: labels.filter(c => c.value.startsWith('Seen: ')), alleged: labels.filter(c => !c.value.startsWith('Seen: '))};
      },
      // The triage reads "type, reliability, priority": the priority is its own chip.
      triage() {
        const parts = (this.pass.triage?.value || '').split(', ');
        return {priority: (parts.pop() || '').replace(/ priority$/, ''), kind: parts};
      },
      // Who signed the first pass, from the review history of its lines.
      passBy() {
        const ids = new Set(this.pass.all.map(c => `statement:${c.id}`));
        const h = (this.it?.history || []).filter(h => h.prop === 'review' && h.display === 'accept' && ids.has(h.subject)).sort((a, b) => b.recorded_at.localeCompare(a.recorded_at))[0];
        return h && {by: h.actor.label, at: when(h.recorded_at)};
      },
      credentials() {return CREDENTIALS[this.c2pa];},
      located() {
        const l = this.locations[0];
        if (!l) return '';
        return `Location ${['exif', 'c2pa'].includes(l.method) ? 'read from the file' : 'set by an analyst'}, within ${l.precision_m} m`;
      },
      // The steps of one decision, from the run that read it to the witnesses that hold its batch.
      proofSteps() {
        const p = this.proof.data, c = this.proof.claim;
        if (!p) return [];
        const b = p.batch, r = p.review, steps = [];
        const ran = p.read && ['model', 'tool'].includes(p.read.how);
        steps.push(p.read ? {title: ran ? `Read by ${p.read.tool}` : p.read.tool, detail: `${ran ? `Run on ${p.read.where}` : p.read.where[0].toUpperCase() + p.read.where.slice(1)}.${p.run_signature ? ` The run signed the claim with its own key${p.run_signature.verifies ? ', and the signature checks' : ', but the signature does not check'}.` : ''}`, state: 'ai'}
          : {title: 'Entered by a person', detail: 'No tool read this value.', state: 'hand'});
        const by = {planted: 'Proposed from the case file', fixed: 'Proposed by the demo seed'}[c.actor.how];
        steps.push({title: c.actor.agent ? by || 'Proposed by AI' : 'Proposed', detail: `${when(p.proposed_at)}${c.span?.bbox ? `, pinned to a region on page ${c.span.page || 1}` : c.span ? `, pinned to ${spanLabel(c.span)}` : ''}.`, state: 'ai waiting'});
        steps.push(r ? {title: `Accepted by ${r.by}`, detail: `Signed ${r.passkey ? (r.demo ? 'with a demo passkey' : 'with a passkey') : 'without a passkey'} at ${when(r.at)}${r.verifies ? '' : ', and the signature does not check'}.`, state: 'held'}
          : {title: 'Accepted', detail: 'No review record names who accepted it.', state: 'waiting'});
        steps.push({title: `Sealed as entry ${p.entry.seq.toLocaleString('en')}`, detail: 'Each entry carries the hash of the one before it.', state: 'held'});
        steps.push(b.closed ? {title: `Anchored at ${when(p.anchored_at)}`, detail: `Batch root ${short(b.root)}, entries ${b.first_seq.toLocaleString('en')} to ${b.last_seq.toLocaleString('en')}.`, state: 'held'}
          : {title: 'Waiting for the next anchor', detail: `Its batch is open, entries ${b.first_seq.toLocaleString('en')} to ${b.last_seq.toLocaleString('en')} so far. The next anchor run closes it.`, state: 'waiting'});
        steps.push(p.witnesses.length ? {title: 'Witnesses', detail: p.witnesses.map(w => `${WITNESS[w.kind] || w.kind}: ${RECEIPT[w.state] || w.state}`).join('. ') + '.', state: p.witnesses.every(w => w.state === 'held') ? 'held' : 'waiting'}
          : {title: 'Witnesses', detail: 'Not sent yet. They receive the batch root once the batch closes.', state: 'waiting'});
        return steps;
      },
      groups() {
        const g = {};
        this.claims.filter(c => !PASS.includes(c.prop)).forEach(c => (g[c.subject] ||= []).push(c));
        return Object.entries(g).map(([subject, claims]) => ({subject, claims, name: this.nameOf(subject)}));
      },
      ordered() {return this.groups.flatMap(g => g.claims);},
      pins() {return this.claims.filter(c => c.tier !== 'rejected' && c.value);},
      // One chip per thing read, with how often: a gate log names one gate a hundred times.
      pinGroups() {
        const out = new Map();
        for (const c of this.pins) {
          const k = c.prop + '\u0000' + c.value;
          if (out.has(k)) out.get(k).n++; else out.set(k, {c, n: 1});
        }
        return [...out.values()];
      },
      marks() {return ['audio', 'video'].includes(this.it?.kind) ? this.it.copies.map(c => [c.here[0], Math.max(c.here[1], c.here[0] + 1000)]) : [];},
      otherCopy() {return this.other && this.it.copies.find(c => c.other === this.other.sha256);},
      // Why these two are beside each other, said once above both.
      besideWhy() {
        const c = this.otherCopy;
        if (c) return `${c.kind}: ${c.why}.` + (this.it.kind !== 'image' ? ` A at ${ms(c.here[0])}, B at ${ms(c.there[0])}.` : '');
        const r = this.relations.find(x => x.other === this.other.sha256);
        if (!r) return 'Opened together. Each side marks its own claims.';
        return `${r.kind}${/[?.]$/.test(r.kind) ? '' : '.'} ${this.relWhy(r, 'in A', 'in B')}`;
      },
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
      work() {return this.it?.work.assignment;},
      viewing() {return Object.values(this.viewers).map(v => v.name).join(', ');},
      mine() {return this.work?.assignee.id === this.it?.work.me;},
      workText() {
        const w = this.work, due = w?.due ? `, due ${w.due}` : '';
        if (!w) return 'Not assigned yet';
        const who = this.mine ? 'you' : w.assignee.name;
        return {open: `Assigned to ${who}${due}`, done: `${this.mine ? 'You' : w.assignee.name} marked it updated`, returned: `Returned to ${who} for changes`}[w.status];
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
      ms, prop, short, spanLabel, plural, evidenceWords,
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
      // `here` and `there` name the two sources: side by side they are A and B.
      relWhy(r, here = 'here', there = 'there') {
        const words = ps => ps.map(p => prop(p).toLowerCase()).join(', ');
        const cap = s => s[0].toUpperCase() + s.slice(1);
        if (r.kind === 'Same place') return `Both name ${iso(r.at_place)}. ${cap(there)}: ${words(r.props_there)} (${plural(r.count, 'claim')}). ${cap(here)}: ${words(r.props_here)}.`;
        if (r.kind === 'Same person?') {
          const name = this.claims.find(c => c.subject === r.here && c.prop === 'name' && c.value)?.value || r.here_name;
          return `${name ? iso(name) : 'An unnamed person'} ${here} and ${r.their_name ? iso(r.their_name) : 'an unnamed person'} ${there}, ${r.why}`;
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
          // The item's case is the page's case: the rail, its counts and the next pages follow it.
          if (this.it.investigation && this.caseSel !== this.it.investigation) this.switchCase(this.it.investigation);
          document.title = `${this.it.title}, ${DOC[this.it.doc_type] || 'Evidence'}`;
          if (boot.with) this.other = (await api.get(`/api/evidence/${boot.with}/item`)).data;
          api.get(`/api/map/evidence/${boot.sha}/locations`).then(r => {this.locations = r.data.items;}, () => {});
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
        if (span?.ms) return span.ms[0];
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
      async loadPeople() {
        if (!this.people.length) this.people = (await api.get('/api/analysts', {params: {investigation: this.it.investigation}})).data.items;
      },
      async assignTo(id) {
        this.working = true;
        try {
          await api.post('/api/assignments/bulk', {item: {investigation: this.it.investigation, subjects: ['blob:' + this.it.sha256], assignee: id, priority: this.work?.priority || 'normal'}});
          this.say(id === this.it.work.me ? 'Assigned to you' : 'Assigned');
          await this.refresh();
        } catch (err) {this.say(err.response?.data?.error || message(err));} finally {this.working = false;}
      },
      async setWork(status) {
        this.working = true;
        try {
          await api.patch(`/api/assignments/${this.work.id}`, {item: {status}});
          this.say(status === 'done' ? 'Marked updated' : 'Returned for changes');
          await this.refresh();
        } catch (err) {this.say(err.response?.data?.error || message(err));} finally {this.working = false;}
      },
      async peerReview() {
        await this.askAbout(this.pass.triage);
        if (this.ask.open && !this.ask.reason) this.ask.reason = 'Peer review of the first pass: triage, title, description and labels.';
      },
      // The method's review notes: +label to add, -label to remove, *note.
      noteClass(line) {return {'+': 'ev-n-add', '-': 'ev-n-del', '*': 'ev-n-note'}[line.trim()[0]] || '';},
      async acceptPass() {
        this.busy = true;
        try {
          await attest({sha: this.it.sha256}, '/api/review/firstpass/options');
          this.say('First pass accepted and signed with your passkey');
          await this.refresh();
        } catch (err) {this.say(err.message);} finally {this.busy = false;}
      },
      async openProof(c) {
        this.proof = {open: true, claim: c, data: null, error: '', busy: true};
        try {this.proof.data = (await api.get(`/api/evidence/statement/${c.id}/proof`)).data;}
        catch (err) {this.proof.error = message(err);} finally {this.proof.busy = false;}
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
      // What a decision was about, in words: a claim by its field and value, a link by
      // the records it joins, a copy by the other source; the id stays on hover.
      aboutLabel(id) {
        const c = this.claims.find(x => x.id === id);
        if (c) return `${prop(c.prop)}${c.value ? ': ' + c.value : ''}`;
        const r = (this.it?.relations || []).find(x => x.statement === id);
        if (r && r.kind === 'Same person?') return `same-person link between ${r.here_name || 'an unnamed record'} and ${r.their_name || 'an unnamed record'}`;
        const m = (this.it?.copies || []).find(x => x.statement === id);
        if (m) return `${(m.kind || 'copy').toLowerCase()} match with ${this.doc(m.other).title}`;
        return 'a proposal on this source';
      },
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
      // A colleague's change to this item reloads it; their tab open on it shows here.
      // A viewer not heard from for two heartbeats has gone.
      this.followWork(() => this.refresh(), boot.sha, m => {
        const {[m.user]: _, ...rest} = this.viewers;
        this.viewers = m.state === 'left' ? rest : {...rest, [m.user]: {name: m.name, at: Date.now()}};
      });
      setInterval(() => {
        const fresh = Object.entries(this.viewers).filter(([, v]) => Date.now() - v.at < 35000);
        if (fresh.length !== Object.keys(this.viewers).length) this.viewers = Object.fromEntries(fresh);
      }, 5000);
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
