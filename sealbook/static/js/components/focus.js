/* Bring a mark into view inside its own scroll box (never the page) and pulse it.
   Motion follows the reader's reduced-motion setting. */
const still = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

export function centre(box, el) {
  if (!box || !el) return;
  const b = box.getBoundingClientRect(), r = el.getBoundingClientRect();
  box.scrollTo({
    top: box.scrollTop + (r.top - b.top) - (box.clientHeight - r.height) / 2,
    left: box.scrollLeft + (r.left - b.left) - (box.clientWidth - r.width) / 2,
    behavior: still() ? 'auto' : 'smooth',
  });
}

export function pulse(el) {
  if (!el) return;
  el.classList.remove('ev-pulse');
  void el.offsetWidth;  // restart the animation on a repeat
  el.classList.add('ev-pulse');
  setTimeout(() => el.classList.remove('ev-pulse'), 1400);
}
