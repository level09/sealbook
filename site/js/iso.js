// The opening's isometric picture of the whole flow: files go into the reader, claims
// come out hatched (unconfirmed), stop at the signing ring and leave solid (accepted)
// for the sealed book. One figure[data-iso]; every shape comes from box() below.
(() => {
  const fig = document.querySelector("[data-iso]");
  if (!fig) return;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const NS = "http://www.w3.org/2000/svg";
  const S = 34, C = 0.866, H = 0.5;
  const iso = (x, y, z = 0) => [(x - y) * C * S, (x + y) * H * S - z * S];
  const P = (pts) => pts.map((p) => iso(...p).map((v) => v.toFixed(1)).join(",")).join(" ");
  const el = (tag, attrs, parent, text) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    if (parent) parent.append(n);
    return n;
  };
  // A block seen from the front corner: top, the face toward +y (left) and toward +x (right).
  const box = (p, x, y, z, w, d, h, cls = "") => {
    const g = el("g", { class: `blk ${cls}` }, p);
    el("polygon", { class: "l", points: P([[x, y + d, z + h], [x + w, y + d, z + h], [x + w, y + d, z], [x, y + d, z]]) }, g);
    el("polygon", { class: "r", points: P([[x + w, y, z + h], [x + w, y + d, z + h], [x + w, y + d, z], [x + w, y, z]]) }, g);
    el("polygon", { class: "t", points: P([[x, y, z + h], [x + w, y, z + h], [x + w, y + d, z + h], [x, y + d, z + h]]) }, g);
    return g;
  };
  // Lays flat drawing on a horizontal plane: one unit is one grid cell.
  const flat = (p, x, y, z, cls = "") => {
    const [ox, oy] = iso(x, y, z);
    return el("g", { class: cls, transform: `matrix(${C * S} ${H * S} ${-C * S} ${H * S} ${ox} ${oy})` }, p);
  };
  const move = (x) => `translate(${(x * C * S).toFixed(1)}px, ${(x * H * S).toFixed(1)}px)`;

  const svg = el("svg", { viewBox: "-214 -26 686 322", role: "img", "aria-label": "Files go into the reader. Claims come out as unconfirmed proposals, stop at the point where a person signs, and only signed claims reach the sealed package." });
  const defs = el("defs", {}, svg);
  const hatch = el("pattern", { id: "iso-hatch", width: 5, height: 5, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
  el("rect", { width: 5, height: 5, class: "hatch-bg" }, hatch);
  el("line", { x1: 0, y1: 0, x2: 0, y2: 5, class: "hatch-ln" }, hatch);

  // Belt
  box(svg, -0.6, 0.1, -0.14, 14.9, 1.4, 0.14, "belt");
  el("polyline", { class: "lane", points: P([[-0.3, 0.8, 0], [14, 0.8, 0]]) }, svg);

  // 01 files: a small stack, the top sheet lined
  box(svg, 0.1, 0.2, 0, 1.25, 1.2, 0.1, "sheet");
  box(svg, 0, 0.12, 0.1, 1.25, 1.2, 0.1, "sheet tint");
  box(svg, 0.12, 0.22, 0.2, 1.25, 1.2, 0.1, "sheet");
  const page = flat(svg, 0.12, 0.22, 0.3, "ink-ln");
  for (const [v, w] of [[0.3, 0.8], [0.55, 0.95], [0.8, 0.6]]) el("line", { x1: 0.15, y1: v, x2: 0.15 + w, y2: v }, page);

  // a sheet leaving the stack for the reader
  const sheet = box(svg, 0, 0.2, 0, 1.1, 1.2, 0.1, "sheet mover");

  // 02 reader: a block over the belt with a lens on top and a slot the claims leave by
  box(svg, 3, -0.1, 0, 1.6, 1.8, 1.3, "reader");
  el("polygon", { class: "slot", points: P([[4.6, 0.35, 0.55], [4.6, 1.25, 0.55], [4.6, 1.25, 0], [4.6, 0.35, 0]]) }, svg);
  const lens = flat(svg, 3.8, 0.8, 1.3, "lens");
  el("circle", { r: 0.5, class: "ring" }, lens);
  el("circle", { r: 0.2, class: "eye" }, lens);

  // 04 the signing ring on the belt, and the person who signs
  const ring = flat(svg, 9.8, 0.8, 0, "sign");
  el("circle", { r: 0.62 }, ring);
  const [px, py] = iso(9.8, -0.7, 0);
  const who = el("g", { class: "who" }, svg);
  el("ellipse", { cx: px, cy: py, rx: 13, ry: 6.5, class: "shade" }, who);
  el("path", { d: `M${px - 10} ${py} L${px - 8} ${py - 22} Q${px} ${py - 33} ${px + 8} ${py - 22} L${px + 10} ${py} Q${px} ${py + 6} ${px - 10} ${py}Z` }, who);
  el("circle", { cx: px, cy: py - 38, r: 7.5 }, who);

  // claims: hatched until signed, solid after
  const claims = [0, 1, 2, 3].map(() => {
    const g = el("g", { class: "claim" }, svg);
    g.draft = box(g, -0.25, 0.55, 0, 0.5, 0.5, 0.5, "draft");
    // The hatch is set here because a url() in the stylesheet would resolve against the CSS file.
    for (const f of g.draft.children) f.style.fill = "url(#iso-hatch)";
    g.fact = box(g, -0.25, 0.55, 0, 0.5, 0.5, 0.5, "fact");
    return g;
  });

  // 05 the sealed book: covers, pages, the seal on top
  box(svg, 12, -0.1, 0, 1.9, 1.8, 0.09, "cover");
  box(svg, 12.06, -0.04, 0.09, 1.78, 1.68, 0.36, "pages");
  box(svg, 12, -0.1, 0.45, 1.9, 1.8, 0.09, "cover");
  const seal = flat(svg, 12.95, 0.8, 0.54, "seal");
  for (const r of [0.58, 0.38, 0.17]) el("circle", { r }, seal);

  // Labels hang below the belt's near edge and read leftward, where the diagonal leaves room.
  const labels = [
    [0.7, "Files arrive", "photo, scan, audio"],
    [3.8, "Read", "words, speech, picture"],
    [6.9, "AI proposes", "pinned to its source"],
    [9.8, "You sign", "a passkey, and undoable"],
    [12.95, "Sealed", "anyone can check it"],
  ];
  labels.forEach(([x, title, sub], k) => {
    const [ax, ay] = iso(x, 1.5, -0.14), [bx, by] = iso(x, 2.35, -0.14);
    el("line", { class: "lead", x1: ax, y1: ay + 2, x2: bx, y2: by }, svg);
    const t = el("text", { x: bx - 6, y: by + 5, "text-anchor": "end", class: "ttl" }, svg);
    el("tspan", { class: "n" }, t, `0${k + 1} `);
    el("tspan", {}, t, title);
    el("text", { x: bx - 6, y: by + 22, "text-anchor": "end", class: "sub" }, svg, sub);
  });
  fig.prepend(svg);

  if (reduced) {
    // The end state: two proposals waiting, one signed claim on its way to the book.
    [5.6, 7.4, 11, 0].forEach((x, k) => {
      claims[k].style.transform = move(x);
      claims[k][x > 9.8 ? "draft" : "fact"].style.opacity = 0;
      if (!x) claims[k].remove();
    });
    sheet.remove();
    return;
  }
  // One claim reaches the ring every BEAT; the ring, the lens and the sheet keep that beat.
  const BEAT = 2250, TRIP = BEAT * 4, opts = { duration: TRIP, iterations: Infinity };
  claims.forEach((g, k) => {
    const delay = -k * BEAT;
    g.animate([
      { transform: move(4.7), opacity: 0, offset: 0 },
      { transform: move(5.1), opacity: 1, offset: 0.06 },
      { transform: move(9.8), opacity: 1, offset: 0.55, easing: "ease-out" },
      { transform: move(9.8), opacity: 1, offset: 0.7, easing: "ease-in" },
      { transform: move(11.6), opacity: 1, offset: 0.94 },
      { transform: move(11.95), opacity: 0, offset: 1 },
    ], { ...opts, delay });
    g.draft.animate([{ opacity: 1, offset: 0 }, { opacity: 1, offset: 0.61 }, { opacity: 0, offset: 0.64 }, { opacity: 0, offset: 1 }], { ...opts, delay });
    g.fact.animate([{ opacity: 0, offset: 0 }, { opacity: 0, offset: 0.61 }, { opacity: 1, offset: 0.64 }, { opacity: 1, offset: 1 }], { ...opts, delay });
  });
  const beat = { duration: BEAT, iterations: Infinity };
  // A claim is signed 0.61 of the way through its trip, which is 0.44 of a beat.
  ring.animate([{ opacity: 0.35, offset: 0 }, { opacity: 0.35, offset: 0.4 }, { opacity: 1, offset: 0.46 }, { opacity: 0.35, offset: 0.9 }, { opacity: 0.35, offset: 1 }], beat);
  lens.animate([{ opacity: 0.45 }, { opacity: 1, offset: 0.85 }, { opacity: 0.45 }], beat);
  sheet.animate([
    { transform: move(0.1), opacity: 0, offset: 0 },
    { transform: move(0.5), opacity: 1, offset: 0.15 },
    { transform: move(1.8), opacity: 1, offset: 0.8 },
    { transform: move(2), opacity: 0, offset: 1 },
  ], beat);
})();
