/**
 * Sealbook rail, in the order of the approved shell. `badge` names a key of
 * /api/nav/counts. `match` is the path that marks the item current; an item
 * standing in for a page that does not exist yet has none, so it never steals
 * the highlight from the page it borrows.
 */

const stkNavigation = [
  {title: 'Desk', icon: 'ti ti-layout-dashboard', to: '/desk', match: '/desk'},
  {title: 'Inbox', icon: 'ti ti-inbox', to: '/inbox', match: '/inbox', badge: 'notifications'},
  {title: 'Discover', icon: 'ti ti-robot', to: '/discover', match: '/discover'},
  {title: 'Review', icon: 'ti ti-checklist', to: '/review', match: '/review', badge: 'review'},
  {title: 'Sources', icon: 'ti ti-files', to: '/sources', match: '/sources'},
  {title: 'People', icon: 'ti ti-users', to: '/people', match: '/people'},
  {title: 'Watching', icon: 'ti ti-eye', to: '/watch', match: '/watch'},
  {title: 'Places', icon: 'ti ti-map-pin', to: '/places', match: '/places'},
  {title: 'Stories', icon: 'ti ti-timeline', to: '/stories', match: '/stories'},
  {title: 'Packages', icon: 'ti ti-package', to: '/packages', match: '/packages'},
  {title: 'Map and live feed', icon: 'ti ti-map-2', to: '/monitor', match: '/monitor'},
];
