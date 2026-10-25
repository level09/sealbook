/**
 * stk Components Registration
 * Registers all custom components globally on a Vue app
 */

function registerStkComponents(app) {
  return app;
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
