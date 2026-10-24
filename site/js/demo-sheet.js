// A messy sheet: cells with every proposal pinned to its characters, and the version diff.
(() => {
  const { h } = sealbook;
  const prop = (p) => p.replace(/_/g, " ");
  const DIFF = [["new", "added"], ["changed", "changed"], ["unchanged", "unchanged"], ["missing", "missing"], ["invalid", "invalid"]];

  function cellContent(text, claims) {
    const ranged = claims.filter((c) => c.chars).sort((a, b) => a.chars[0] - b.chars[0]);
    const out = [];
    let at = 0;
    for (const c of ranged) {
      if (c.chars[0] < at) continue; // overlapping proposals are listed, not drawn twice
      out.push(text.slice(at, c.chars[0]), h("mark", { class: `tier-${c.tier}`, text: text.slice(c.chars[0], c.chars[1]) }));
      at = c.chars[1];
    }
    out.push(text.slice(at));
    return out;
  }

  sealbook.onReady((data) => {
    const root = sealbook.mount("sheet");
    const sh = data.sheet;
    if (!root || !sh) return;
    const at = (r, c) => sh.claims.filter((x) => x.row === r && x.column === c);
    const panel = h("div", { class: "sh-panel", "aria-live": "polite" },
      h("p", { class: "note", text: "Select a marked cell to see its proposals." }));

    function show(r, c, cell) {
      root.querySelectorAll(".sh-cell[aria-pressed]").forEach((b) => b.setAttribute("aria-pressed", "false"));
      cell.setAttribute("aria-pressed", "true");
      const cs = at(r, c);
      panel.replaceChildren(
        h("p", { class: "sh-where mono", text: `row ${r}, column ${c} (${sh.header[c - 1]})` }),
        h("ul", { class: "sh-claims" }, ...cs.map((x) => h("li", {},
          h("span", { class: `chip tier-${x.tier}` }, `${prop(x.prop)} `, h("bdi", { dir: "auto", text: x.text })),
          h("span", { class: "note" },
            x.kind ? ` ${x.kind}` : "",
            x.norm != null ? ` read as ${x.norm}` : "",
            x.valid[0] ? ` dated ${x.valid[0] === x.valid[1] ? x.valid[0] : `${x.valid[0]} to ${x.valid[1]}`}` : "",
            x.chars ? `, characters ${x.chars[0]} to ${x.chars[1]}` : ", the whole cell")))),
        h("p", { class: "note", text: "Each is a proposal. A person accepts or rejects it, and it keeps its pin to this cell either way." }));
    }

    const table = h("table", { class: "sh-table" },
      h("thead", {},
        h("tr", {}, ...sh.header.map((name) => h("th", { scope: "col", dir: "auto", text: name }))),
        h("tr", { class: "sh-map" }, ...sh.header.map((name) => h("td", { text: name === sh.key_column ? "row identifier" : `field: ${sh.mapping[name]}` })))),
      h("tbody", {}, ...sh.rows.map((cells, i) => {
        const r = i + 2;
        return h("tr", {}, ...cells.map((text, j) => {
          const c = j + 1, cs = at(r, c);
          if (!cs.length) return h("td", { dir: "auto", text });
          const whole = cs.some((x) => !x.chars);
          const btn = h("button", { type: "button", class: `sh-cell${whole ? " sh-whole" : ""}`, dir: "auto", "aria-pressed": "false",
            "aria-label": `${text}: ${cs.length} proposals` }, ...cellContent(text, cs));
          btn.addEventListener("click", () => show(r, c, btn));
          return h("td", {}, btn);
        }));
      })));

    const versions = h("ol", { class: "sh-versions" }, ...sh.versions.map((v) =>
      h("li", {}, h("span", { class: "mono", text: `version ${v.number}, ${v.file}` }),
        h("span", { class: "sh-diff" }, ...DIFF.filter(([k]) => v.diff[k] != null).map(([k, label]) =>
          h("span", { class: `sh-d sh-${k}`, text: `${v.diff[k]} ${label}` }))))));

    root.append(
      h("p", { class: "sh-head" }, h("span", { class: "mono", text: sh.file }), `, dataset "${sh.dataset}": ${sh.rows.length} rows, ${sh.claims.length} proposals.`),
      h("div", { class: "sh-grid" }, h("div", { class: "sh-scroll", tabindex: "0", role: "region", "aria-label": "The sheet" }, table), panel),
      versions,
      h("p", { class: "note", text: "Version 2 is compared with version 1 before anything is read; only added and changed rows go to extraction. Missing rows retract nothing." }),
      h("p", { class: "note", text: `The proposals on this page are the demo's fixed answers, written through the app's real sheet writer; no model ran. ${sh.measured}` }));
  });
})();
