// Who was where, and when: the app's case map for the demo case. A timeline of people
// over places and a map of the same places share one slider; a band or a place lists
// who was there with sources; the path box shows the app's own path answers.
(() => {
  const { h } = sealbook;
  const NS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs = {}, ...kids) => {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) k === "text" ? (el.textContent = v) : el.setAttribute(k, v);
    el.append(...kids);
    return el;
  };
  const DAY = 864e5;
  const day = (iso) => Date.parse(`${iso}T00:00:00Z`) / DAY;
  const date = (d, opts) => new Date(d * DAY).toLocaleDateString("en-GB", { timeZone: "UTC", ...opts });
  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  const PROP = { detained_at: "held at", detention_branch: "held by", transferred_to: "transferred to", arrested_at: "arrested at", seen_at: "seen at", last_seen: "last seen at" };
  const verb = (p) => PROP[p] || p.replace(/_/g, " ");
  const when = (a, b) => {
    if (a === b) return date(day(a), { day: "numeric", month: "long", year: "numeric" });
    const [y, m, d] = a.split("-").map(Number), [y2, m2, d2] = b.split("-").map(Number);
    if (y === y2 && m === m2 && d === 1 && d2 === new Date(Date.UTC(y, m, 0)).getUTCDate()) return date(day(a), { month: "long", year: "numeric" });
    if (y === y2 && m === 1 && d === 1 && m2 === 12 && d2 === 31) return String(y);
    return `${date(day(a), { day: "numeric", month: "short", year: "numeric" })} to ${date(day(b), { day: "numeric", month: "short", year: "numeric" })}`;
  };
  // The calendar month, quarter or year holding day d, as [first, last] day.
  function unit(d, span) {
    const t = new Date(d * DAY), y = t.getUTCFullYear(), m = t.getUTCMonth();
    if (span === "year") return [Date.UTC(y, 0, 1) / DAY, Date.UTC(y, 11, 31) / DAY];
    const m0 = span === "quarter" ? m - (m % 3) : m, n = span === "quarter" ? 3 : 1;
    return [Date.UTC(y, m0, 1) / DAY, Date.UTC(y, m0 + n, 0) / DAY];
  }
  function unitLabel(w, span) {
    if (span === "year") return date(w[0], { year: "numeric" });
    if (span === "quarter") return `${date(w[0], { month: "short" })} to ${date(w[1], { month: "short", year: "numeric" })}`;
    return date(w[0], { month: "long", year: "numeric" });
  }
  const press = (el, onClick) => {
    el.addEventListener("click", onClick);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } });
  };

  sealbook.onReady((data) => {
    const root = sealbook.mount("casemap");
    const c = data.casemap;
    if (!root || !c) return;
    const placeBy = Object.fromEntries(c.places.map((p) => [p.id, p]));
    const rowBy = Object.fromEntries(c.rows.map((r) => [r.id, r]));
    const points = [...c.points].sort((a, b) => a.from.localeCompare(b.from) || a.id.localeCompare(b.id));
    const byRow = {};
    for (const p of points) (byRow[p.row] ||= []).push(p);
    const ends = Object.fromEntries(c.open_ends.map((o) => [o.row, o]));
    const partner = {};
    for (const [a, b] of c.pairs) { (partner[a] ||= []).push(b); (partner[b] ||= []).push(a); }
    // Rows by first record; two records an open proposal may join stay together.
    const order = [];
    for (const r of [...c.rows].sort((a, b) => (byRow[a.id]?.[0]?.from || "~").localeCompare(byRow[b.id]?.[0]?.from || "~"))) {
      if (order.includes(r.id)) continue;
      order.push(r.id, ...(partner[r.id] || []).filter((o) => !order.includes(o)));
    }
    const placeName = (p) => p.en || p.ar;
    const at = (p) => (p.place ? placeName(placeBy[p.place]) : p.written);

    const t0 = Math.min(...points.map((p) => day(p.from))) - 31, t1 = Math.max(...points.map((p) => day(p.to))) + 31;
    let cur = t1, span = "all", selected = null, answer = null;
    const win = () => (span === "all" ? null : unit(cur, span));
    const inWin = (p, w = win()) => !w || (day(p.from) <= w[1] && day(p.to) >= w[0]);

    const tl = h("div", { class: "cm-tl" });
    const map = h("div", { class: "cm-map" });
    const panel = h("div", { class: "cm-panel", "aria-live": "polite" });
    const summary = h("p", { class: "cm-summary", "aria-live": "polite" });

    function mark() {
      if (!answer?.found) return null;
      return { rows: new Set(answer.steps.flatMap((x) => x.rows)), points: new Set(answer.steps.flatMap((x) => x.points)), places: new Set(answer.steps.flatMap((x) => x.places)) };
    }

    // As the app does: a few far records should not squeeze the rest, so the timeline
    // starts on the 5th to 95th percentile of dates, padded, when that is under 60%.
    const dense = (() => {
      const ts = points.map((p) => day(p.from)).sort((a, b) => a - b);
      if (ts.length < 8) return null;
      const q = (f) => ts[Math.min(ts.length - 1, Math.floor(f * ts.length))];
      const a = Math.max(t0, q(0.05) - 31), b = Math.min(t1, q(0.95) + 62);
      return b - a < 0.6 * (t1 - t0) ? [a, b] : null;
    })();
    let whole = false;
    const fit = h("button", { type: "button", class: "quiet cm-fit" });
    fit.addEventListener("click", () => { whole = !whole; timeline(); });

    function timeline() {
      const W = Math.max(300, tl.clientWidth || 700), L = W < 560 ? 104 : 168, R = 10, step = 24, top = 30;
      const H = top + order.length * step + 6;
      const [v0, v1] = dense && !whole ? dense : [t0, t1];
      const x = (d) => L + ((d - v0) / (v1 - v0)) * (W - L - R);
      const y = (i) => top + i * step + step / 2;
      const m = mark(), w = win();
      const svg = s("svg", { width: W, height: H, role: "img", "aria-label": `Timeline of ${order.length} people over ${c.places.length} places` });
      svg.append(s("defs", {},
        s("pattern", { id: "cm-q", width: 5, height: 5, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, s("rect", { width: 1.2, height: 5, class: "cm-hq" })),
        s("pattern", { id: "cm-e", width: 5, height: 5, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, s("rect", { width: 1.4, height: 5, class: "cm-he" })),
        s("pattern", { id: "cm-u", width: 4, height: 4, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, s("rect", { width: 1.6, height: 4, class: "cm-hu" })),
        s("clipPath", { id: "cm-tlclip" }, s("rect", { x: L - 4, y: 0, width: W - L + 4, height: H }))));
      // Everything on the time axis is clipped to it; names stay outside.
      const plot = s("g", { "clip-path": "url(#cm-tlclip)" });
      svg.append(plot);
      // Year ticks, and quarters when there is room.
      const years = (v1 - v0) / 365.25;
      const every = (W - L) / Math.max(1, years) > 220 ? 3 : 12;
      for (let t = new Date(Date.UTC(new Date(v0 * DAY).getUTCFullYear(), 0, 1)); t.getTime() / DAY <= v1; t.setUTCMonth(t.getUTCMonth() + every)) {
        const d = t.getTime() / DAY;
        if (d < v0) continue;
        const jan = t.getUTCMonth() === 0;
        plot.append(s("line", { class: jan ? "cm-tick" : "cm-tick cm-minor", x1: x(d), x2: x(d), y1: top - 6, y2: H }),
          s("text", { class: "cm-tick-t", x: x(d) + 3, y: top - 12, text: jan ? String(t.getUTCFullYear()) : date(d, { month: "short" }) }));
      }
      if (w) plot.append(s("rect", { class: "cm-win", x: x(w[0]), y: top - 6, width: Math.max(2, x(w[1] + 1) - x(w[0])), height: H - top + 6 }));
      // Bands under the rows they join.
      for (const b of c.bands) {
        const ys = b.people.map((k) => order.indexOf(k)).filter((i) => i >= 0);
        const x0 = x(day(b.from)) - 3, x1 = x(day(b.to) + 1) + 3;
        const sel = selected?.kind === "band" && selected.id === b;
        const names = b.people.map((k) => rowBy[k].label).join(", ");
        const g = s("g", { class: `cm-band${sel ? " sel" : ""}${inWin(b, w) ? "" : " out"}`, tabindex: "0", role: "button", "aria-label": `${b.label}: ${names}. Show who was there` },
          s("title", { text: `${b.label}: ${names}` }),
          s("rect", { class: "cm-band-span", x: x0, y: y(Math.min(...ys)) - 10, width: x1 - x0, height: y(Math.max(...ys)) - y(Math.min(...ys)) + 20, rx: 4 }),
          ...ys.map((i) => s("rect", { class: "cm-band-row", x: x0, y: y(i) - 9, width: x1 - x0, height: 18, rx: 3 })));
        press(g, () => pick({ kind: "band", id: b }));
        plot.append(g);
      }
      const chars = Math.floor((L - 14) / 6.6);
      order.forEach((k, i) => {
        const r = rowBy[k], dim = m && !m.rows.has(k);
        const g = s("g", { class: `cm-row${dim ? " dim" : ""}` });
        const lane = s("g", { class: `cm-row${dim ? " dim" : ""}` });
        const label = r.label.length > chars ? `${r.label.slice(0, chars - 1)}…` : r.label;
        g.append(s("text", { class: `cm-name${r.named ? "" : " cm-unnamed"}`, x: 0, y: y(i) + 4 }, s("title", { text: r.named ? r.label : `${r.label}: the name is from a sensitive source` }), label),
          s("line", { class: "cm-lane", x1: L, x2: W - R, y1: y(i), y2: y(i) }));
        svg.append(g);
        plot.append(lane);
        if (partner[k] && order.indexOf(partner[k][0]) === i + 1) {
          g.append(s("path", { class: "cm-pair", d: `M${L - 8} ${y(i) - 6} h-4 v${step + 12} h4` },
            s("title", { text: "Possibly two people: an open same-person proposal joins these records. They stay two rows until someone reviews it." })));
        }
        for (const gap of r.gaps) lane.append(s("rect", { class: "cm-gap", x: x(day(gap.from) + 1), y: y(i) - 3, width: Math.max(0, x(day(gap.to)) - x(day(gap.from) + 1)), height: 6, fill: "url(#cm-q)" }, s("title", { text: `${gap.days} days, no evidence` })));
        const end = ends[k];
        if (end) lane.append(s("rect", { class: "cm-end", x: x(day(end.after) + 1), y: y(i) - 3, width: Math.min(40, W - R - x(day(end.after) + 1)), height: 6, fill: "url(#cm-e)" }, s("title", { text: end.label })));
        for (const p of byRow[k] || []) {
          const a = x(day(p.from)), wd = Math.max(5, x(day(p.to) + 1) - a);
          const seg = s("rect", { class: `cm-pt tier-${p.tier}${inWin(p, w) ? "" : " out"}${m?.points.has(p.id) ? " on" : ""}`, x: a, y: y(i) - 5, width: wd, height: 10, rx: 2, fill: p.tier === "unconfirmed" ? "url(#cm-u)" : null });
          if (p.tier !== "unconfirmed") seg.removeAttribute("fill");
          seg.append(s("title", { text: `${r.label}: ${verb(p.prop)} ${at(p)}, ${when(p.from, p.to)}, ${p.tier}. Source: ${p.file}` }));
          lane.append(seg);
        }
      });
      if (w) plot.append(s("line", { class: "cm-cur", x1: x(cur), x2: x(cur), y1: top - 6, y2: H }));
      const outside = dense && !whole ? points.filter((p) => day(p.to) < v0 || day(p.from) > v1).length : 0;
      fit.hidden = !dense;
      fit.textContent = whole ? "Fit the busy years" : `Whole range, ${plural(outside, "claim")} outside`;
      tl.replaceChildren(svg);
    }

    // The map: places from their coordinates, no tiles. "Around" zooms to the busiest
    // place and the places near it.
    const busiest = c.places.map((p) => [p, points.filter((q) => q.place === p.id).length]).sort((a, b) => b[1] - a[1])[0][0];
    const near = c.places.filter((p) => Math.hypot(p.lonlat[0] - busiest.lonlat[0], p.lonlat[1] - busiest.lonlat[1]) < 0.3);
    let extent = "all";
    function moves(w) {
      const out = {};
      for (const k of order) {
        let prev = null;
        for (const p of (byRow[k] || []).filter((q) => q.place)) {
          if (prev && prev.place !== p.place && (!w || (day(p.from) >= w[0] && day(prev.to) <= w[1]))) {
            const key = `${prev.place}>${p.place}`;
            (out[key] ||= { a: prev.place, b: p.place, n: 0 }).n++;
          }
          if (!prev || prev.place !== p.place || day(p.to) > day(prev.to)) prev = p;
        }
      }
      return Object.values(out);
    }
    function drawMap() {
      const W = Math.max(280, map.clientWidth || 420);
      const shown = extent === "all" ? c.places : near;
      const ll = shown.map((p) => p.lonlat);
      const pad = extent === "all" ? 0.25 : 0.04;
      let [x0, y0, x1, y1] = [Math.min(...ll.map((p) => p[0])) - pad, Math.min(...ll.map((p) => p[1])) - pad, Math.max(...ll.map((p) => p[0])) + pad, Math.max(...ll.map((p) => p[1])) + pad];
      const k = Math.cos((((y0 + y1) / 2) * Math.PI) / 180), aspect = 0.8;
      if (((x1 - x0) * k) / (y1 - y0) < 1 / aspect) { const m = (x0 + x1) / 2, half = (y1 - y0) / aspect / k / 2; x0 = m - half; x1 = m + half; }
      else { const m = (y0 + y1) / 2, half = ((x1 - x0) * k * aspect) / 2; y0 = m - half; y1 = m + half; }
      const H = W * aspect;
      const px = ([lon, lat]) => [((lon - x0) / (x1 - x0)) * W, (1 - (lat - y0) / (y1 - y0)) * H];
      const mPerPx = ((y1 - y0) * 111320) / H;
      const w = win(), m = mark();
      const svg = s("svg", { width: W, height: H, role: "img", "aria-label": `Map of ${c.places.length} places in the case` });
      svg.append(s("defs", {},
        s("clipPath", { id: "cm-clip" }, s("rect", { width: W, height: H })),
        s("marker", { id: "cm-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, s("path", { class: "cm-tip", d: "M0 0L10 5L0 10z" }))));
      const g = s("g", { "clip-path": "url(#cm-clip)" });
      const here = {};
      for (const p of points) if (p.place && inWin(p, w)) (here[p.place] ||= new Set()).add(p.row);
      const radius = (id) => 4 + 3.2 * Math.sqrt(here[id]?.size || 0);
      for (const mv of moves(w)) {
        const [ax, ay] = px(placeBy[mv.a].lonlat), [bx, by] = px(placeBy[mv.b].lonlat);
        const len = Math.hypot(bx - ax, by - ay);
        if (len < 12) continue;
        const ra = radius(mv.a) + 3, rb = radius(mv.b) + 4;
        const ux = (bx - ax) / len, uy = (by - ay) / len;
        const sx = ax + ux * ra, sy = ay + uy * ra, ex = bx - ux * rb, ey = by - uy * rb;
        const cx = (sx + ex) / 2 - uy * len * 0.15, cy = (sy + ey) / 2 + ux * len * 0.15;
        g.append(s("path", { class: "cm-move", d: `M${sx} ${sy}Q${cx} ${cy} ${ex} ${ey}`, "stroke-width": 1.2 + Math.min(4, mv.n * 0.8), "marker-end": "url(#cm-arrow)" },
          s("title", { text: `${plural(mv.n, "move")} from ${placeName(placeBy[mv.a])} to ${placeName(placeBy[mv.b])}` })));
      }
      // Labels go right, left or below their dot, busiest place first; one that
      // would overlap another is left to the dot's name for screen readers.
      const taken = shown.map((p) => { const [cx, cy] = px(p.lonlat), r = radius(p.id); return { x: cx - r, y: cy - r, w: 2 * r, h: 2 * r }; });
      const hits = (b) => taken.some((t) => b.x < t.x + t.w && t.x < b.x + b.w && b.y < t.y + t.h && t.y < b.y + b.h);
      const labels = new Map();
      for (const p of [...shown].sort((a, b) => (here[b.id]?.size || 0) - (here[a.id]?.size || 0))) {
        const [cx, cy] = px(p.lonlat), r = radius(p.id), n = here[p.id]?.size || 0;
        const text = n ? `${placeName(p)} · ${n}` : placeName(p), tw = text.length * 6.6;
        const spot = [[cx + r + 4, cy + 4, "start", cx + r + 2], [cx - r - 4, cy + 4, "end", cx - r - 6 - tw], [cx, cy + r + 14, "middle", cx - tw / 2]]
          .map(([x, y, anchor, left]) => ({ x, y, anchor, box: { x: left, y: y - 12, w: tw + 4, h: 15 } }))
          .find((o) => o.box.x >= 0 && o.box.x + o.box.w <= W && !hits(o.box));
        if (spot) { taken.push(spot.box); labels.set(p.id, { ...spot, text }); }
      }
      for (const p of shown) {
        const [cx, cy] = px(p.lonlat), n = here[p.id]?.size || 0, r = radius(p.id);
        const sel = selected?.kind === "place" && selected.id === p;
        const node = s("g", { class: `cm-place${p.approximate ? " approx" : ""}${n ? "" : " empty"}${sel ? " sel" : ""}${m?.places.has(p.id) ? " on" : ""}`, tabindex: "0", role: "button",
          "aria-label": `${placeName(p)}: ${plural(n, "person", "people")} ${w ? "in this window" : "over the whole case"}${p.approximate ? `, known to within ${p.precision_m / 1000} km` : ""}` });
        if (p.approximate) node.append(s("circle", { class: "cm-area", cx, cy, r: Math.max(r + 6, p.precision_m / mPerPx) }));
        const l = labels.get(p.id);
        node.append(s("circle", { class: "cm-dot", cx, cy, r }, s("title", { text: placeName(p) })),
          l ? s("text", { class: "cm-place-t", x: l.x, y: l.y, "text-anchor": l.anchor, text: l.text }) : "");
        press(node, () => pick({ kind: "place", id: p }));
        g.append(node);
      }
      svg.append(g);
      map.replaceChildren(svg);
    }

    // Who was at a band or a place, each with what the sources say and which sources.
    function who(list) {
      const by = {};
      for (const p of list) (by[p.row] ||= []).push(p);
      return h("ul", { class: "cm-who" }, ...order.filter((k) => by[k]).map((k) => {
        const ps = by[k], files = [...new Set(ps.map((p) => p.file))];
        const end = ends[k];
        return h("li", {},
          h("p", { class: "cm-who-n" }, h("bdi", { dir: "auto", text: rowBy[k].label }), partner[k] ? h("span", { class: "note", text: " (possibly two people)" }) : ""),
          h("p", {}, ps.map((p) => `${verb(p.prop)} ${at(p)}, ${when(p.from, p.to)}${p.tier === "accepted" ? "" : ` (${p.tier})`}`).join("; ")),
          h("p", { class: "note" }, `Source${files.length > 1 ? "s" : ""}: `, ...files.flatMap((f, i) => [i ? "; " : "", h("bdi", { text: f })])),
          end && day(end.after) <= Math.max(...ps.map((p) => day(p.to))) ? h("p", { class: "cm-none", text: end.label }) : "");
      }));
    }
    function paintPanel() {
      if (answer) return paintAnswer();
      if (!selected) {
        panel.replaceChildren(h("p", { class: "note", text: "Pick a band on the timeline or a place on the map to see who was there." }));
        return;
      }
      if (selected.kind === "band") {
        const b = selected.id, list = points.filter((p) => b.members.includes(p.id));
        panel.replaceChildren(
          h("h4", { text: b.label[0].toUpperCase() + b.label.slice(1) }),
          h("p", { class: "note", text: `${b.places.map((id) => placeName(placeBy[id])).join(" and ")}, ${plural(b.people.length, "person", "people")} from ${plural(new Set(list.map((p) => p.file)).size, "source")}` }),
          who(list),
          b.proposed ? h("p", { class: "note", text: "Discovery has proposed this as a finding, waiting for review." }) : "");
        return;
      }
      const pl = selected.id, w = win(), list = points.filter((p) => p.place === pl.id && inWin(p, w));
      panel.replaceChildren(
        h("h4", {}, h("bdi", { text: placeName(pl) }), " ", h("bdi", { dir: "rtl", lang: "ar", class: "note", text: pl.ar })),
        h("p", { class: "note", text: `${w ? unitLabel(w, span) : "The whole case"}: ${list.length ? `${plural(new Set(list.map((p) => p.row)).size, "person", "people")} from ${plural(new Set(list.map((p) => p.file)).size, "source")}` : "nobody on record here"}${pl.approximate ? `. Known only to within ${pl.precision_m / 1000} km, so drawn as a faint area` : ""}.` }),
        list.length ? who(list) : "");
    }
    function pick(sel) { selected = sel; answer = null; redraw(); }

    // The path question: the app's answers between people whose names are unique here.
    const label = (k) => rowBy[k].label;
    const option = (k) => h("option", { value: k, text: label(k) });
    const ask = {
      from: h("select", { id: "cm-from" }, ...c.people.map(option)),
      to: h("select", { id: "cm-to" }, ...c.people.map(option)),
    };
    [ask.from.value, ask.to.value] = c.preset;
    const go = h("button", { type: "button", class: "quiet", text: "Show the path" });
    const clear = h("button", { type: "button", class: "quiet", text: "Clear" });
    function paintAnswer() {
      const a = answer;
      const head = h("h4", {}, "How is ", h("bdi", { text: label(a.from) }), " connected to ", h("bdi", { text: label(a.to) }), "?");
      if (!a.found) { panel.replaceChildren(head, h("p", { text: `No chain of links within ${c.depth} steps.` })); return; }
      panel.replaceChildren(head,
        h("p", { class: "note", text: `${plural(a.steps.length, "step")}, the shortest the app found, at most ${c.depth}.` }),
        h("ol", { class: "cm-steps" }, ...a.steps.map((x) => h("li", { class: x.weak ? "weak" : "" },
          h("p", {}, h("bdi", { dir: "ltr", text: x.text }), x.unconfirmed ? h("span", { class: "cm-tag", text: "unconfirmed" }) : ""),
          x.weak ? h("p", { class: "cm-weak", text: x.weak }) : "",
          h("p", { class: "note" }, `Source${x.sources.length > 1 ? "s" : ""}: `, ...x.sources.flatMap((f, i) => [i ? "; " : "", h("bdi", { text: f })]))))));
    }
    go.addEventListener("click", () => {
      if (ask.from.value === ask.to.value) { answer = null; selected = null; redraw(); panel.replaceChildren(h("p", { class: "note", text: "Pick two different people." })); return; }
      answer = c.paths.find((p) => p.from === ask.from.value && p.to === ask.to.value);
      selected = null;
      redraw();
    });
    clear.addEventListener("click", () => { answer = null; selected = null; redraw(); });

    // One slider and window for both views.
    const slider = h("input", { type: "range", min: String(t0), max: String(t1), step: "1", value: String(t1), "aria-label": "Time", "aria-valuetext": "The whole case" });
    const shown = h("output", { class: "cm-date", for: "cm-slider" });
    slider.id = "cm-slider";
    const spans = [["month", "Month"], ["quarter", "Quarter"], ["year", "Year"], ["all", "All"]];
    const windows = h("div", { class: "cm-spans", role: "group", "aria-label": "Window" }, ...spans.map(([v, t]) => {
      const b = h("button", { type: "button", class: "quiet", "aria-pressed": String(v === span), text: t });
      b.addEventListener("click", () => { span = v; redraw(); });
      b.dataset.span = v;
      return b;
    }));
    slider.addEventListener("input", () => { cur = +slider.value; if (span === "all") span = "month"; redraw(); });
    let timer = null;
    const play = h("button", { type: "button", class: "quiet cm-play", text: "Play" });
    const stop = () => { clearInterval(timer); timer = null; play.textContent = "Play"; };
    play.addEventListener("click", () => {
      if (timer) return stop();
      if (span === "all") span = "month";
      if (cur >= t1) cur = t0;
      play.textContent = "Pause";
      // Reduced motion: a month at a time, slowly.
      timer = setInterval(() => {
        cur = Math.min(t1, cur + (sealbook.reduced ? 30 : 6));
        redraw();
        if (cur >= t1) stop();
      }, sealbook.reduced ? 900 : 80);
    });
    const extents = h("div", { class: "cm-spans", role: "group", "aria-label": "Map extent" }, ...[["all", "All places"], ["near", `Around ${placeName(busiest)}`]].map(([v, t]) => {
      const b = h("button", { type: "button", class: "quiet", "aria-pressed": String(v === extent), text: t });
      b.addEventListener("click", () => { extent = v; extents.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); drawMap(); });
      return b;
    }));

    function redraw() {
      const w = win();
      slider.value = String(cur);
      const text = w ? unitLabel(w, span) : "The whole case";
      shown.textContent = text;
      slider.setAttribute("aria-valuetext", text);
      windows.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.span === span)));
      const live = points.filter((p) => inWin(p, w));
      const together = c.bands.filter((b) => inWin(b, w));
      summary.textContent = `${text}: ${plural(new Set(live.map((p) => p.row)).size, "person", "people")} at ${plural(new Set(live.filter((p) => p.place).map((p) => p.place)).size, "place")}${together.length ? `, ${plural(together.length, "band")} of people held or seen together` : ""}.`;
      timeline();
      drawMap();
      paintPanel();
    }

    const sources = new Set(points.map((p) => p.file)).size;
    root.append(
      h("p", { class: "cm-head" }, `${plural(c.rows.length, "person", "people")}, ${plural(c.places.length, "place")}, ${plural(sources, "source")}, ${when(points[0].from, points[points.length - 1].to)}.`),
      h("div", { class: "cm-controls" }, play, slider, shown, windows),
      h("div", { class: "cm-sumrow" }, summary, fit),
      tl,
      h("ul", { class: "cm-key", "aria-label": "Key" },
        h("li", {}, h("span", { class: "cm-k cm-k-acc", "aria-hidden": "true" }), "accepted"),
        h("li", {}, h("span", { class: "cm-k cm-k-cor", "aria-hidden": "true" }), "corroborated"),
        h("li", {}, h("span", { class: "cm-k cm-k-unc", "aria-hidden": "true" }), "unconfirmed"),
        h("li", {}, h("span", { class: "cm-k cm-k-band", "aria-hidden": "true" }), "together at one place"),
        h("li", {}, h("span", { class: "cm-k cm-k-gap", "aria-hidden": "true" }), "no evidence"),
        h("li", {}, h("span", { class: "cm-k cm-k-end", "aria-hidden": "true" }), "no record after"),
        h("li", {}, h("span", { class: "cm-k cm-k-pair", "aria-hidden": "true" }), "possibly two people")),
      h("div", { class: "cm-grid" },
        h("div", {}, extents, map,
          h("p", { class: "note", text: `Places: ${c.attribution}. Drawn from their coordinates, sized by the people there in the window. Arrows join known places in time order; they are not routes. Faint circles are places known only roughly.${c.unresolved ? ` ${plural(c.unresolved, "place name")} not yet matched to a place ${c.unresolved === 1 ? "is" : "are"} left off.` : ""}` })),
        h("div", { class: "cm-side" },
          h("div", { class: "cm-ask" },
            h("p", { class: "cm-ask-t", text: "How is one person connected to another?" }),
            h("div", { class: "cm-pick" }, h("label", { for: "cm-from", text: "From" }), ask.from, h("label", { for: "cm-to", text: "To" }), ask.to),
            h("div", { class: "cm-ask-b" }, go, clear)),
          panel)),
      h("p", { class: "note", text: "Read from the app's case map and path endpoints for the demo case on a throwaway database. The path box offers the people whose names are unique on this map; every answer is the app's own." }));
    redraw();
    go.click();
    let width = root.clientWidth;
    new ResizeObserver(() => { if (root.clientWidth !== width) { width = root.clientWidth; timeline(); drawMap(); } }).observe(root);
  });
})();
