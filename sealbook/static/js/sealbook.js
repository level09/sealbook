/* Sealbook's changes to stk's Vuetify config, and the page helpers every page shares.
   Loaded from _head.html; stk's config.js calls configureApp before Vuetify starts. */

function configureApp(config) {
  const v = config.vuetifyConfig;
  // v-hotkey renders a blank key without these
  Object.assign(v.icons.aliases, {
    command: 'command', ctrl: 'chevron-up', shift: 'arrow-big-up', alt: 'alt', enter: 'corner-down-left',
    arrowup: 'arrow-up', arrowdown: 'arrow-down', arrowleft: 'arrow-left', arrowright: 'arrow-right',
    backspace: 'backspace', space: 'space',
  });
  Object.assign(v.defaults, {
    VCard: {elevation: 0, rounded: 'sm'},
    VBtn: {variant: 'flat', rounded: 'sm'},
    VDialog: {rounded: 'sm'},
  });
  // Paper and ink with quiet semantic roles; the colour roles mirror shell.css (docs/design.md).
  v.theme.themes.light.colors = {
    primary: '#1f2124', 'on-primary': '#ffffff', secondary: '#555555', accent: '#333333', agent: '#6f5bb0',
    accepted: '#00775c', corroborated: '#00775c', conflict: '#724800', negative: '#ac4a41',
    sensitive: '#9c5076', due: '#724800', overdue: '#ac4a41', new: '#006cac',
    person: '#007289', place: '#45751c', source: '#404e60', incident: '#b2471c', story: '#b2471c',
    error: '#ac4a41', info: '#1f2124', success: '#00775c', warning: '#724800',
    background: '#ecebe6', surface: '#fbfaf7', 'surface-light': '#f5f4f0', 'on-surface': '#1f2124',
  };
  v.theme.themes.dark.colors = {
    primary: '#d9dad6', 'on-primary': '#1c1d21', secondary: '#a3a3a3', accent: '#d4d4d4', agent: '#b3a1fd',
    accepted: '#6cd5b3', corroborated: '#6cd5b3', conflict: '#eaa950', negative: '#f2897c',
    sensitive: '#e694bb', due: '#eaa950', overdue: '#f2897c', new: '#3ba6f5',
    person: '#50c2df', place: '#d3ef8b', source: '#ccd5e2', incident: '#f0834e', story: '#f0834e',
    error: '#f2897c', info: '#d9dad6', success: '#6cd5b3', warning: '#eaa950',
    background: '#151619', surface: '#1c1d21', 'surface-light': '#222428', 'on-surface': '#d9dad6',
  };
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
