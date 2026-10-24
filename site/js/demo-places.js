// Places and incidents: Branch 999 and the incident accepted there, as the app's
// place and incident endpoints returned them.
(() => {
  const { h } = sealbook;
  const SOURCES = { geonames: "the GeoNames gazetteer", "cod-ab": "OCHA COD-AB boundaries", analyst: "an analyst", exif: "photo EXIF", c2pa: "content credentials" };
  const within = (m) => (m >= 1000 ? `${m / 1000} km` : `${m} m`);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const date = (s) => { const [y, m, d] = s.split("-"); return d ? `${+d} ${MONTHS[m - 1]} ${y}` : m ? `${MONTHS[m - 1]} ${y}` : y; };
  // A whole month reads as the month; a single day as the day.
  const range = (a, b) => (a === b ? date(a) : a.slice(0, 7) === b.slice(0, 7) && a.endsWith("-01") ? date(a.slice(0, 7)) : `${date(a)} to ${date(b)}`);
  const SHOW = 6;

  function people(rows) {
    const item = (p) => h("li", {}, h("bdi", { dir: "auto", class: p.named ? "" : "pl-unnamed", text: p.label }),
      h("span", { class: "pl-when mono", text: range(p.from, p.to) }), h("span", { class: `chip tier-${p.tier} pl-tier`, text: p.tier }));
    const ul = h("ul", { class: "pl-people" }, ...rows.slice(0, SHOW).map(item));
    const out = [ul];
    if (rows.length > SHOW) {
      const more = h("button", { type: "button", class: "quiet", text: `Show all ${rows.length}` });
      more.addEventListener("click", () => { ul.replaceChildren(...rows.map(item)); more.remove(); });
      out.push(more);
    }
    return out;
  }

  sealbook.onReady((data) => {
    const root = sealbook.mount("places");
    const e = data.entities;
    if (!root || !e) return;
    const p = e.place, inc = e.incident;

    const place = h("article", { class: "pl-page" },
      h("p", { class: "pl-type", text: p.kind }),
      h("h3", { class: "pl-name" }, h("bdi", { text: p.en }), " ", h("bdi", { lang: "ar", dir: "rtl", class: "pl-ar", text: p.ar })),
      h("p", { class: "pl-facts", text: `Known to within ${within(p.precision_m)}, position set by ${SOURCES[p.geo_source] || p.geo_source}. ${p.claims} claims from ${p.sources} sources.` }),
      h("h4", { text: "Spellings in the evidence" }),
      h("ul", { class: "pl-spell" }, ...p.spellings.map((s) => h("li", {}, h("bdi", { dir: "auto", text: s.name }), h("span", { class: "note", text: ` ${s.claims} claims` })))),
      h("h4", { text: `People placed here, ${p.people.length}` }),
      ...people(p.people),
      h("h4", { text: "Incidents here" }),
      h("ul", { class: "pl-list" }, ...p.incidents.map((i) => h("li", {}, h("bdi", { text: i.title }), h("span", { class: "note", text: `, ${i.people} people, ${i.tier}` })))),
      h("h4", { text: "Open findings that touch it" }),
      h("ul", { class: "pl-list" }, ...p.findings.map((f) => h("li", {}, `${f.label}: ${f.reason}`, h("span", { class: "note" }, ", ", h("span", { class: `cn-band band-${f.band}`, text: `${f.band} band` }), ` ${f.why}`)))));

    const incident = h("article", { class: "pl-page" },
      h("p", { class: "pl-type", text: "Incident" }),
      h("h3", { class: "pl-name" }, h("bdi", { text: inc.title })),
      h("p", { class: "pl-facts", text: `${range(inc.window[0], inc.window[1])}, at ${inc.places.join(", ")}. Accepted by ${inc.signed_by} with one passkey gesture.` }),
      h("h4", { text: "People, by role" }),
      h("ul", { class: "pl-people" }, ...inc.people.map((x) => h("li", {}, h("bdi", { dir: "auto", class: x.named ? "" : "pl-unnamed", text: x.label }),
        h("span", { class: "pl-when", text: x.roles.join(", ") }), h("span", { class: `chip tier-${x.tier} pl-tier`, text: x.tier })))),
      h("h4", { text: "Built from" }),
      h("ul", { class: "pl-list" }, ...inc.findings.map((f) => h("li", {}, `${f.label}, ${f.state}: ${f.reason}`, h("span", { class: "note", text: `, ${f.why}` })))),
      h("p", { class: "note", text: "Its signed history sits beside it, grouped by passkey gesture, with undo for gestures the reader signed." }));

    root.append(h("div", { class: "pl-grid" }, place, incident),
      h("p", { class: "note", text: `Read from the app's place and incident endpoints for the demo case. Place data: ${p.attribution}. "Unnamed person" is how the app labels someone whose name the reader may not see.` }));
  });
})();
