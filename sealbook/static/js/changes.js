// How a row of "since you left" reads: who did what, to which sources. Shared by
// the Desk and the To do page.
const VERBS = {
  acquisition: n => `added ${n.items === 1 ? 'a source' : n.items + ' sources'}`,
  review: n => `decided ${plural(n.n, 'claim')}`, review_accept: n => `accepted ${plural(n.n, 'claim')}`, review_reject: n => `rejected ${plural(n.n, 'claim')}`,
  access: n => `viewed ${plural(n.items, 'source')}`, located_at: n => `confirmed ${plural(n.n, 'place')}`, retraction: n => `retracted ${plural(n.n, 'decision')}`,
  review_request: n => `asked for ${plural(n.n, 'review')}`, review_comment: n => `commented ${n.n === 1 ? 'once' : n.n + ' times'}`,
  media_match: n => `proposed ${plural(n.n, 'picture or sound match', 'picture or sound matches')}`,
  same_as: n => `proposed ${plural(n.n, 'same-person link')}`, not_same: n => `separated ${plural(n.n, 'person record')}`,
  c2pa_verify: n => `checked content credentials on ${plural(n.items, 'source')}`, url_liveness: n => `rechecked ${plural(n.items, 'link')}`,
  finding: n => `proposed ${plural(n.n, 'finding')}`, package: n => `built ${plural(n.n, 'package')}`, attest: n => `signed ${plural(n.n, 'package')}`,
};
const REVIEWS = ['review', 'review_accept', 'review_reject', 'retraction', 'review_request', 'review_comment'];
// A claim kind as a countable noun, for "AI proposed 17 ages".
const NOUN = {name: ['name'], father_name: ["father's name", "fathers' names"], mother_name: ["mother's name", "mothers' names"], birth_date: ['birth date'], place_of_birth: ['birthplace'],
  arrested_at: ['arrest place'], detained_at: ['detention place'], detention_branch: ['detention branch', 'detention branches'], transferred_to: ['transfer'], seen_at: ['sighting'],
  last_seen: ['last-seen place'], sex: ['sex entry', 'sex entries'], age: ['age'], occupation: ['occupation'], event: ['event'], document_date: ['document date'], reports: ['reported event'],
  document_type: ['document type'], photographed_at: ['photo place'], filmed_at: ['filming place'], located_at: ['place'],
  triage: ['triage', 'triages'], title: ['title'], description: ['description line'], label: ['label']};
function plural(n, one, many) { return `${(n ?? 0).toLocaleString()} ${n === 1 ? one : many || one + 's'}`; }
// Field names as the bell and Inbox write them.
const FIELD = {father_name: "Father's name", mother_name: "Mother's name", birth_date: 'Birth date', place_of_birth: 'Birthplace', detention_branch: 'Held by branch'};
const human = s => FIELD[s] || (s || '').replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());

// Agents propose claims and people add them; neither "reads" a value.
export function verb(r) {
  if (VERBS[r.what] && !(r.who.agent && r.what === 'located_at')) return VERBS[r.what](r);
  const [one, many] = NOUN[r.what] || [human(r.what).toLowerCase() + ' claim'];
  return `${r.who.agent ? 'AI proposed' : 'added'} ${plural(r.n, one, many)}`;
}
// Work on sources lists them; work on claims says where the claims are.
export function prep(r) { return ['acquisition', 'c2pa_verify', 'url_liveness'].includes(r.what) ? ': ' : r.who.agent && (!VERBS[r.what] || r.what === 'located_at') ? 'from ' : 'in '; }
// Titles carry commas of their own, so a list of them is split by semicolons.
export function where(r) { const t = [...new Set(r.sources.map(s => s.title))]; return t.join('; ') + (r.items > r.sources.length ? ` and ${(r.items - r.sources.length).toLocaleString()} more` : ''); }
export function changeHref(r) { return REVIEWS.includes(r.what) ? '/review' : r.sources[0] ? '/evidence/' + r.sources[0].sha256 : '/sources'; }
export {plural, human};
