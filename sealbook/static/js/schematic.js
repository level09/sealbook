/**
 * Maps without a real world under them. A place a case marks invented
 * (`place.fictional`) is drawn as a schematic: no coastline, no tiles, no region
 * outline, only the places at their relative positions with a scale, labels pushed
 * apart so none covers another, and a note that says what the drawing is. With no
 * located place a map shows words instead of an empty region.
 */
import * as maplibregl from '/static/maplibre/maplibre-gl.mjs';

export const NOTE = 'Invented places, drawn to relative position, not a real map';
export const NO_PLACES = 'No place here has a location yet, so there is nothing to draw on a map.';

/* The note over the map, and a scale: distances between invented places are as given. */
export function mark(map) {
  const el = document.createElement('div');
  el.className = 'schematic-note';
  el.textContent = NOTE;
  map.getContainer().appendChild(el);
  // Labels wrap rather than run off the edge (shell.css).
  map.getContainer().classList.add('schematic');
  map.addControl(new maplibregl.ScaleControl({unit: 'metric'}), 'bottom-left');
}

/* Fit invented places close: their spacing is the point, not a region around them.
   Room on the right for labels, which sit right of their place, and on top for the note. */
export function fit(map, coords) {
  if (!coords.length) return;
  const b = new maplibregl.LngLatBounds();
  coords.forEach(c => b.extend(c));
  // One place has no spread to fit: it sits left so its label has the width.
  const w = map.getContainer().clientWidth, one = b.getWest() === b.getEast() && b.getSouth() === b.getNorth();
  const right = one ? Math.round(w * 0.6) : Math.min(200, Math.round(w * 0.4));
  map.fitBounds(b, {padding: {top: 64, bottom: 40, left: 40, right}, maxZoom: 15, duration: 0});
}

const overlaps = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

/* Moves labels apart, top to bottom, each by whole label heights down or up from
   where it was drawn, so no two overlap at this zoom. `labels()` gives the markers;
   with `part` (a selector inside each marker) only that label moves and the marker,
   a place's dot, stays on its place. Reruns on zoom and resize; call the returned
   function after redrawing. */
export function spread(map, labels, part = null) {
  // A moved label keeps a thin leader line back to its place.
  const lead = (el, dx, dy) => {
    el.classList.toggle('schematic-lead', !!dy);
    el.style.setProperty('--lead-len', `${Math.hypot(dx, dy)}px`);
    el.style.setProperty('--lead-angle', `${Math.atan2(-dy, -dx)}rad`);
  };
  const shift = (m, dy) => {
    if (part) {
      const el = m.getElement().querySelector(part);
      if (!el) return;
      el.style.transform = dy ? `translateY(${dy}px)` : '';
      lead(el, 12, dy);
    } else {
      m._base ??= m.getOffset();
      m.setOffset([m._base.x, m._base.y + dy]);
      lead(m.getElement(), Math.abs(m._base.x), dy);
    }
  };
  const run = () => {
    const placed = [];
    const items = (labels() || []).map(m => {
      shift(m, 0);
      const el = part ? m.getElement().querySelector(part) : m.getElement();
      return {m, r: (el || m.getElement()).getBoundingClientRect()};
    }).sort((a, b) => a.r.top - b.r.top || a.r.left - b.r.left);
    for (const {m, r} of items) {
      const step = r.height + 2;
      let dy = 0;
      for (let i = 1; i < 2 * items.length + 2; i++) {
        const box = {left: r.left, right: r.right, top: r.top + dy, bottom: r.bottom + dy};
        if (!placed.some(p => overlaps(p, box))) {
          placed.push(box);
          break;
        }
        dy = (i % 2 ? 1 : -1) * Math.ceil(i / 2) * step;
      }
      shift(m, dy);
    }
  };
  map.on('zoomend', run);
  map.on('resize', run);
  return run;
}
