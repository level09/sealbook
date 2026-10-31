/**
 * stk Components Registration
 * Registers all custom components globally on a Vue app
 */

function registerStkComponents(app) {
  return app;
}

/* A finding takes the hue of what it proposes: people seen together, or an incident,
   whether from positions or from posts linked as one event (docs/design.md, Colour
   roles). `cls` for role utilities, `color` for Vuetify props. */
const FINDING_HUES = {
  shared_presence: {cls: 'is-person', color: 'person', icon: 'ti ti-users-group'},
  candidate_incident: {cls: 'is-incident', color: 'incident', icon: 'ti ti-alert-triangle'},
  event_group: {cls: 'is-incident', color: 'incident', icon: 'ti ti-stack-2'},
};
function findingHue(kind) {
  return FINDING_HUES[kind] || {cls: 'is-finding', color: 'agent', icon: 'ti ti-robot'};
}

/* A statement's prop in readable words, the one map every page uses. Lowercase,
   to sit inside a sentence; propTitle starts a line or a field label with it. */
const PROP_WORDS = {
  name: 'name', father_name: "father's name", mother_name: "mother's name", birth_date: 'born', place_of_birth: 'born in',
  arrested_at: 'arrested at', detained_at: 'held at', detention_branch: 'held by branch', transferred_to: 'transferred to',
  seen_at: 'seen at', last_seen: 'last seen at', sex: 'sex', age: 'age', occupation: 'occupation', event: 'event',
  document_date: 'document date', reports: 'reported event', document_type: 'document type', same_as: 'same person',
  not_same: 'different people', media_match: 'media match', located_at: 'place confirmed', photographed_at: 'photographed at',
  filmed_at: 'filmed at', finding: 'finding', relation: 'relation', involved: 'involved in', review: 'review', retraction: 'undo',
  access: 'access', sha256: 'SHA-256', md5: 'MD5', c2pa_verify: 'C2PA check', fingerprint: 'fingerprint', url_liveness: 'link check',
  triage: 'triage', title: 'title', description: 'description line', label: 'label',
};
function propWord(p) {
  return PROP_WORDS[p] || (p || '').replaceAll('_', ' ');
}
function propTitle(p) {
  const w = propWord(p);
  return w.charAt(0).toUpperCase() + w.slice(1);
}

/**
 * Filter navigation items by user role
 * @param {Array} items - Navigation items
 * @param {Array} userRoles - User's roles (e.g., ['admin', 'user'])
 * @returns {Array} Filtered navigation items
 */
function filterNavByRole(items, userRoles = []) {
  // Pure on purpose: this runs inside a computed, so mutating a nav item would
  // re-trigger that computed forever (Vue 3.5 prod loops instead of warning).
  return items
    .filter(item => !item.role || userRoles.includes(item.role))
    .map(item =>
      item.children
        ? { ...item, children: filterNavByRole(item.children, userRoles) }
        : item
    );
}
