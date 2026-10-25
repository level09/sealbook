/**
 * stk Components Registration
 * Registers all custom components globally on a Vue app
 */

function registerStkComponents(app) {
  return app;
}

/* A finding takes the hue of what it proposes: people seen together, or an incident
   (docs/design.md, Colour roles). `cls` for role utilities, `color` for Vuetify props. */
const FINDING_HUES = {
  shared_presence: {cls: 'is-person', color: 'person', icon: 'ti ti-users-group'},
  candidate_incident: {cls: 'is-incident', color: 'incident', icon: 'ti ti-alert-triangle'},
};
function findingHue(kind) {
  return FINDING_HUES[kind] || {cls: 'is-finding', color: 'agent', icon: 'ti ti-robot'};
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
