/* Shared by the case Overview, the conclusion form and a conclusion's page: the
   signing step (rows shown, then one passkey assertion) and the words for states. */
import {prepare, sign} from '/static/js/monitor/passkey.js';

export const plural = (n, one, many) => `${(n ?? 0).toLocaleString()} ${n === 1 ? one : many || one + 's'}`;
export const day = iso => iso ? new Date(iso).toLocaleDateString(undefined, {day: 'numeric', month: 'long', year: 'numeric'}) : '';

export const STATUS = {
  open: {label: 'Open', icon: 'ti ti-folder-open', cls: 'cc-open'},
  concluded: {label: 'Concluded', icon: 'ti ti-circle-check', cls: 'cc-done'},
  closed: {label: 'Closed without a conclusion', short: 'Closed', icon: 'ti ti-folder-x', cls: 'cc-closed'},
};

/* "Concluded by Mira on 3 May 2024", "Reopened by ...: reason". */
export function statusLine(s) {
  if (!s || !s.at) return 'Open. No conclusion has been signed.';
  const verb = {open: 'Reopened', concluded: 'Concluded', closed: 'Closed'}[s.status];
  return `${verb} by ${s.by} on ${day(s.at)}${s.reason ? ': ' + s.reason : '.'}`;
}

/* Why a conclusion no longer stands, in one sentence. */
export function brokenLine(c) {
  const n = c.broken.length, total = c.rests_on.length + (c.contradicts || []).length;
  const words = [...new Set(c.broken.map(b => b.words))].join(' or ');
  return `No longer stands on what it cited: ${n} of ${plural(total, 'item')} ${n === 1 ? 'was' : 'were'} ${words}. Revise or withdraw it.`;
}

export function peerLine(p) {
  if (!p || p.state === 'none') return 'Not peer reviewed yet.';
  if (p.state === 'asked') return `Peer review asked of ${p.reviewer} on ${day(p.asked_at)}.`;
  return `Peer reviewed: ${p.by} agreed on ${day(p.at)}${p.note && p.note !== 'agree' ? ', "' + p.note + '"' : '.'}`;
}

/* The evidence page's own logged reveal, once per locked source, then reload. */
export async function reveal(locked, then) {
  for (const l of locked) await axios.post(`/api/evidence/${l.sha256}/reveal`, {item: {}});
  await then();
}

export const signing = {
  data: () => ({staged: null, signBusy: false, snack: {show: false, text: ''}}),
  methods: {
    plural,
    human: s => (s || '').replaceAll('_', ' '),
    say(text) { this.snack = {show: true, text}; },
    fail(err) { return apiError(err); },
    async stage(item, start, title, after) {
      this.signBusy = true;
      try { this.staged = {data: await prepare(item, start), title, after}; }
      catch (err) { this.say(err.message); }
      finally { this.signBusy = false; }
    },
    async confirmSign() {
      const {data, after} = this.staged;
      this.signBusy = true;
      try { await after(await sign(data)); } catch (err) { this.say(err.message); }
      finally { this.signBusy = false; this.staged = null; }
    },
  },
};
