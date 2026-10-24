// A person over time: the app's story for one person, a timeline and a map driven by one scrubber.
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
  const iso = (d) => new Date(d * DAY).toISOString().slice(0, 10);
  const prop = (p) => p.replace(/_/g, " ");
  const SOURCES = { geonames: "GeoNames gazetteer", "cod-ab": "OCHA COD-AB", geoboundaries: "geoBoundaries", analyst: "an analyst", exif: "photo EXIF", c2pa: "content credentials", derived: "derived" };
  const within = (m) => (m >= 1000 ? `${m / 1000} km` : `${m} m`);

  sealbook.onReady((data) => {
    const root = sealbook.mount("story");
    const st = data.story;
    if (!root || !st) return;
    const row = st.rows[0];
    const points = [...st.points].sort((a, b) => a.from.localeCompare(b.from));
    // Places numbered in the order they first appear.
    const order = [];
    for (const p of points) if (!order.includes(p.place_id)) order.push(p.place_id);
    const num = (id) => order.indexOf(id) + 1;

    const dates = [...points.flatMap((p) => [p.from, p.to]), ...st.pending.flatMap((f) => f.window)].map(day);
    const t0 = Math.min(...dates) - 12, t1 = Math.max(...dates) + 12;
    let now = t1;

    const tl = h("div", { class: "st-tl" });
    const map = h("div", { class: "st-map" });
    const info = h("div", { class: "st-info", "aria-live": "polite" },
      h("p", { class: "note", text: "Select a point on the timeline or a place on the map." }));

    function select(p) {
      const place = st.places[p.place_id];
      info.replaceChildren(
        h("p", { class: "st-claim" }, h("span", { class: `chip tier-${p.tier}` }, `${prop(p.prop)} `, h("bdi", { dir: "auto", text: p.place })),
          h("span", { class: "mono", text: ` ${p.from === p.to ? p.from : `${p.from} to ${p.to}`}` })),
        h("dl", { class: "kv" },
          h("dt", { text: "source" }), h("dd", { class: "mono", text: p.file }),
          h("dt", { text: "tier" }), h("dd", { text: p.tier }),
          h("dt", { text: "place" }), h("dd", {}, `${num(p.place_id)}. `, h("bdi", { text: place.en || "" }), ", ", place.kind),
          h("dt", { text: "position" }), h("dd", { text: `within ${within(place.precision_m)}, from ${SOURCES[place.geo_source] || place.geo_source}` })),
        p.sensitive ? h("p", { class: "note", text: "From a sensitive source: in the app its excerpt shows only after a logged reveal." }) : "");
    }

    function timeline() {
      const w = Math.max(300, tl.clientWidth || 700), left = 44, right = 16;
      const x = (d) => left + ((d - t0) / (t1 - t0)) * (w - left - right);
      const lanes = [];
      const bands = st.pending.map((f) => {
        const a = day(f.window[0]), b = day(f.window[1]);
        let lane = lanes.findIndex((end) => end < a);
        if (lane < 0) { lane = lanes.length; lanes.push(b); } else lanes[lane] = b;
        return { f, a, b, lane };
      });
      const top = 8 + lanes.length * 24, rowY = top + 46;
      const svg = s("svg", { width: w, height: rowY + 64, role: "img", "aria-label": `Timeline of ${st.title}` });
      for (const { f, a, b, lane } of bands) {
        const y = 8 + lane * 24;
        const g = s("g", { class: `st-band ${a > now ? "future" : ""}` },
          s("rect", { x: x(a), y, width: Math.max(4, x(b) - x(a)), height: 18, rx: 3 }),
          s("title", { text: `${f.label}, ${f.band} band, waiting for review: ${f.reason}` }));
        svg.append(g);
      }
      for (const gap of row.gaps) {
        const a = x(day(gap.from)), b = x(day(gap.to));
        svg.append(s("line", { class: "st-gap", x1: a, x2: b, y1: rowY, y2: rowY }),
          s("text", { class: "st-gap-t", x: (a + b) / 2, y: rowY - 10, "text-anchor": "middle", text: b - a - 28 > 7 * `${gap.days} days, no evidence`.length ? `${gap.days} days, no evidence` : `${gap.days} days` }));
      }
      // Month ticks
      for (let d = new Date(t0 * DAY); d.getTime() / DAY < t1; d.setUTCMonth(d.getUTCMonth() + 1, 1)) {
        const dd = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / DAY;
        if (dd < t0) continue;
        svg.append(s("line", { class: "st-tick", x1: x(dd), x2: x(dd), y1: rowY + 20, y2: rowY + 26 }),
          s("text", { class: "st-tick-t", x: x(dd), y: rowY + 40, "text-anchor": "middle", text: new Date(dd * DAY).toLocaleString("en", { month: "short", year: "2-digit", timeZone: "UTC" }) }));
      }
      const byDay = {};
      for (const p of points) (byDay[p.from] ||= []).push(p);
      for (const [d, ps] of Object.entries(byDay)) {
        const places = new Set(ps.map((p) => p.place_id));
        const fork = row.steps.find((x) => x.date === d)?.fork;
        ps.forEach((p, i) => {
          const spread = places.size > 1 ? [...places].indexOf(p.place_id) : 0;
          const y = rowY + (places.size > 1 ? (spread - (places.size - 1) / 2) * 22 : 0);
          const a = x(day(p.from)), b = x(day(p.to));
          const g = s("g", { class: `st-pt tier-${p.tier} ${day(p.from) > now ? "future" : ""}`, tabindex: "0", role: "button", "aria-label": `${prop(p.prop)} ${p.en || p.place}, ${p.from}, from ${p.file}` },
            b - a > 2 ? s("rect", { x: a, y: y - 6, width: b - a, height: 12, rx: 6 }) : s("circle", { cx: a + i * 0, cy: y, r: 7 }),
            s("text", { class: "st-num", x: a + (b - a > 2 ? 0 : 0) + (b - a > 2 ? 10 : 0), y: y + 4, "text-anchor": b - a > 2 ? "start" : "middle", text: String(num(p.place_id)) }));
          const go = () => select(p);
          g.addEventListener("click", go);
          g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
          svg.append(g);
        });
        if (fork) {
          const a = x(day(d));
          svg.append(s("path", { class: "st-fork", d: `M${a - 12},${rowY - 18} h-4 v36 h4` }),
            s("text", { class: "st-fork-t", x: a - 20, y: rowY + 4, "text-anchor": "end", text: "fork" }));
        }
      }
      svg.append(s("line", { class: "st-now", x1: x(now), x2: x(now), y1: 0, y2: rowY + 28 }));
      tl.replaceChildren(svg);
    }

    let view = "all";
    function drawMap() {
      const w = Math.max(280, map.clientWidth || 400);
      const ids = view === "all" ? order : order.filter((id) => st.places[id].lonlat[1] < 34);
      const ll = ids.map((id) => st.places[id].lonlat);
      const pad = view === "all" ? 0.12 : 0.05;
      let [x0, y0, x1, y1] = [Math.min(...ll.map((p) => p[0])) - pad, Math.min(...ll.map((p) => p[1])) - pad, Math.max(...ll.map((p) => p[0])) + pad, Math.max(...ll.map((p) => p[1])) + pad];
      const k = Math.cos(((y0 + y1) / 2) * Math.PI / 180);
      // Keep a pleasant aspect: widen the narrow side.
      const aspect = 0.9, spanX = (x1 - x0) * k, spanY = y1 - y0;
      if (spanX / spanY < 1 / aspect) { const c = (x0 + x1) / 2, half = (spanY / aspect) / k / 2; x0 = c - half; x1 = c + half; }
      else { const c = (y0 + y1) / 2, half = (spanX * aspect) / 2; y0 = c - half; y1 = c + half; }
      const hgt = w * aspect;
      const px = ([lon, lat]) => [((lon - x0) * k) / ((x1 - x0) * k) * w, (1 - (lat - y0) / (y1 - y0)) * hgt];
      const mPerPx = ((y1 - y0) * 111320) / hgt;
      const svg = s("svg", { width: w, height: hgt, role: "img", "aria-label": `Map of the places in ${st.title}'s story` });
      svg.append(s("defs", {}, s("clipPath", { id: "st-clip" }, s("rect", { width: w, height: hgt }))));
      const g = s("g", { "clip-path": "url(#st-clip)" });
      for (const a of st.map.areas) {
        const d = a.rings.map((r) => "M" + r.map((p) => px(p).map((v) => v.toFixed(1)).join(",")).join("L") + "Z").join("");
        g.append(s("path", { class: "st-area", d }, s("title", { text: a.name })));
      }
      for (const a of st.map.areas) {
        const xs = a.rings[0].map(px), cx = xs.reduce((t, p) => t + p[0], 0) / xs.length, cy = xs.reduce((t, p) => t + p[1], 0) / xs.length;
        if (cx > 20 && cx < w - 20 && cy > 12 && cy < hgt - 8) g.append(s("text", { class: "st-area-t", x: cx, y: cy, "text-anchor": "middle", text: a.name }));
      }
      const seq = points.filter((p) => ids.includes(p.place_id));
      const path = [];
      for (const p of seq) if (path.at(-1) !== p.place_id) path.push(p.place_id);
      const visible = path.filter((id) => seq.some((p) => p.place_id === id && day(p.from) <= now));
      if (visible.length > 1) g.append(s("polyline", { class: "st-seq", points: visible.map((id) => px(st.places[id].lonlat).join(",")).join(" ") }));
      for (const id of ids) {
        const pl = st.places[id], [cx, cy] = px(pl.lonlat);
        const first = seq.find((p) => p.place_id === id);
        const live = day(first.from) <= now;
        const r = Math.max(2, pl.precision_m / mPerPx);
        const dot = s("g", { class: `st-place ${live ? "" : "future"}`, tabindex: "0", role: "button", "aria-label": `${num(id)}. ${pl.en}, within ${within(pl.precision_m)}` },
          s("circle", { class: "st-prec", cx, cy, r }),
          s("circle", { class: "st-dot", cx, cy, r: 9 }),
          s("text", { class: "st-num", x: cx, y: cy + 4, "text-anchor": "middle", text: String(num(id)) }));
        const go = () => select(first);
        dot.addEventListener("click", go);
        dot.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
        g.append(dot);
      }
      svg.append(g);
      map.replaceChildren(svg);
    }

    const scrub = h("input", { type: "range", min: String(t0), max: String(t1), step: "1", value: String(t1), "aria-label": "Time" });
    const shown = h("output", { class: "mono st-date", text: iso(t1) });
    const redraw = () => { shown.textContent = iso(now); timeline(); drawMap(); };
    scrub.addEventListener("input", () => { now = +scrub.value; redraw(); });
    let timer = null;
    const play = h("button", { type: "button", class: "quiet", "aria-label": "Play the timeline and map", text: "Play" });
    play.addEventListener("click", () => {
      if (timer) { clearInterval(timer); timer = null; play.textContent = "Play"; return; }
      now = t0; play.textContent = "Pause";
      timer = setInterval(() => {
        now = Math.min(t1, now + (sealbook.reduced ? 30 : 3));
        scrub.value = String(now); redraw();
        if (now >= t1) { clearInterval(timer); timer = null; play.textContent = "Play"; }
      }, sealbook.reduced ? 600 : 60);
    });
    const views = h("div", { class: "st-views", role: "group", "aria-label": "Map extent" },
      ...[["all", "All places"], ["near", "Damascus area"]].map(([v, label]) => {
        const b = h("button", { type: "button", class: "quiet", "aria-pressed": String(v === view), text: label });
        b.addEventListener("click", () => { view = v; views.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); drawMap(); });
        return b;
      }));

    const legend = h("ol", { class: "st-legend" }, ...order.map((id) => {
      const p = st.places[id];
      return h("li", {}, h("bdi", { text: p.en }), " (", h("bdi", { dir: "rtl", lang: "ar", text: p.ar }), ")",
        h("span", { class: "note", text: `, ${p.kind}, within ${within(p.precision_m)}, from ${SOURCES[p.geo_source] || p.geo_source}` }));
    }));

    root.append(
      h("p", { class: "st-head" }, h("bdi", { text: st.title }), `: ${points.length} accepted claims, ${row.gaps.length} gaps, ${st.pending.length} findings waiting for review.`),
      h("div", { class: "st-controls" }, play, scrub, shown),
      tl,
      h("ul", { class: "st-pending", "aria-label": "Findings waiting for review" }, ...st.pending.map((f) =>
        h("li", {}, h("span", { class: "st-key-band", "aria-hidden": "true" }), ` ${f.label}, ${f.band} band: ${f.reason}. Waiting for review.`))),
      h("div", { class: "st-grid" }, h("div", {}, views, map, legend,
        h("p", { class: "note", text: `Places: ${st.attribution}. Boundaries: ${st.map.boundaries}. Numbers are the order places first appear; lines show sequence, not routes. Circles show each place's stated precision.` })), info),
      h("p", { class: "note", text: `Read from the app's story endpoint for the demo case on a throwaway database; that read wrote ${st.rows.length === 1 ? "one access record" : `${st.rows.length} access records`}, one per person row.` }));
    redraw();
    select(points[0]);
    new ResizeObserver(() => { timeline(); drawMap(); }).observe(root);
  });
})();
