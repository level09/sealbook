// Chapter 4: two people with one name. Similar names are proposed, never merged.
(() => {
  const { h } = sealbook;
  const LABEL = {
    name: "name", father_name: "father's name", mother_name: "mother's name", birth_date: "born",
    arrested_at: "arrested at", detention_branch: "detention branch", transferred_to: "transferred to",
    detained_at: "detained at", seen_at: "seen at", last_seen: "last seen",
  };
  const val = (v) => h("bdi", { dir: "auto", text: v });

  sealbook.onReady((data) => {
    const root = sealbook.mount("people");
    if (!root) return;
    const [p1, p2] = ["P1", "P2"].map((id) => data.people.find((p) => p.id === id));
    const claims = (pid) => data.files.flatMap((f) =>
      (f.claims || []).filter((c) => c.person === pid).map((c) => ({ ...c, file: f.name })));
    const first = (pid, prop) => claims(pid).find((c) => c.prop === prop)?.value;

    const card = (p) => h("article", { class: "pp-card" },
      h("h3", { class: "pp-name", dir: "auto", text: p.label }),
      h("p", { class: "pp-id mono", text: p.id }),
      p.spellings.length > 1
        ? h("div", {}, h("p", { class: "pp-sub", text: `Spelled ${p.spellings.length} ways in the sources` }),
            h("ul", { class: "pp-spell" }, ...p.spellings.map((s) => h("li", { dir: "auto" }, h("bdi", { text: s })))))
        : h("p", { class: "pp-sub", text: "One spelling in the sources" }),
      h("ul", { class: "pp-facts" }, ...claims(p.id).filter((c) => c.prop !== "name").map((c) =>
        h("li", {},
          h("span", { class: "pp-prop", text: LABEL[c.prop] || c.prop.replace(/_/g, " ") }),
          h("span", { class: "chip tier-unconfirmed" }, val(c.value), c.valid ? h("bdi", { class: "mono pp-when", dir: "ltr", text: c.valid }) : ""),
          h("span", { class: "pp-src mono", text: c.file })))));

    const row = (label, a, b) => {
      const same = a === b;
      return h("tr", { class: same ? "same" : "differs" },
        h("th", { scope: "row", text: label }), h("td", {}, val(a ?? "none")), h("td", {}, val(b ?? "none")),
        h("td", { text: same ? "matches" : "differs" }));
    };
    const state = h("p", { class: "pp-state", "aria-live": "polite",
      text: "Proposed. Both records stay separate until a person decides." });
    const prop = h("section", { class: "pp-prop-card tier-unconfirmed", "aria-label": "Identity proposal" },
      h("h3", { text: "These may be the same person" }),
      h("table", { class: "pp-cmp" },
        h("thead", {}, h("tr", {}, h("td", {}), h("th", { scope: "col", text: p1.id }), h("th", { scope: "col", text: p2.id }), h("td", {}))),
        h("tbody", {},
          row("name", first("P1", "name"), first("P2", "name")),
          row("father's name", p1.father_name, p2.father_name),
          row("mother's name", first("P1", "mother_name"), first("P2", "mother_name")),
          row("born", first("P1", "birth_date"), first("P2", "birth_date")))),
      h("p", { class: "pp-sub", text: "The two names in the register are identical. The mothers' names and birth years in the same register differ." }));
    const btns = h("div", { class: "pp-btns" });
    const pick = (link) => {
      prop.dataset.state = link ? "linked" : "separate";
      prop.classList.toggle("tier-unconfirmed", false);
      prop.classList.toggle("tier-accepted", false);
      state.textContent = link
        ? "Linked by you. In the app this is a signed decision that names you, and it can be reversed."
        : "Kept separate by you. In the app this is signed too, so the pair is not proposed again without further evidence.";
      [...btns.children].forEach((b, i) => b.setAttribute("aria-pressed", String(i === 0 ? link : !link)));
    };
    for (const [t, link] of [["Link", true], ["Keep separate", false]]) {
      const b = h("button", { class: "quiet", type: "button", "aria-pressed": "false", text: t });
      b.addEventListener("click", () => pick(link));
      btns.append(b);
    }
    prop.append(btns, state);

    root.append(
      h("div", { class: "pp-pair" }, card(p1), card(p2)),
      prop,
      h("p", { class: "pp-rule" }, "Similar names are never merged automatically. A match on name proposes a candidate; linking two records is a signed human decision."),
      h("p", { class: "pp-note", text: "Demonstration: your choice changes this page only." }));

    const d = data.disagree;
    if (!d) return;
    const f = d.fork;
    root.append(h("div", { class: "pp-more" },
      h("section", { class: "pp-fork" },
        h("h3", { text: "Sources disagree" }),
        h("p", { class: "pp-sub" }, `${(LABEL[f.prop] || f.prop).replace(/^./, (c) => c.toUpperCase())} of `, h("bdi", { dir: "auto", text: f.person }), ", ", h("span", { class: "mono", text: f.from })),
        h("ul", { class: "pp-sides" }, ...f.sides.map((s) => h("li", {},
          h("span", { class: "chip tier-unconfirmed" }, val(s.value)), h("span", { class: "pp-src mono", text: s.file })))),
        h("p", { class: "pp-note", text: `Two readings of one line differ by a letter. Both stay listed with their source, and neither wins until a reviewer decides. Across the ${d.people} people in the demo case, ${d.forks} disagreements are open.` })),
      h("section", { class: "pp-count" },
        h("h3", { text: "One count on every screen" }),
        h("p", { class: "pp-sub" }, h("bdi", { dir: "auto", text: d.counted.person })),
        h("table", { class: "pp-cmp" }, h("tbody", {}, ...d.counted.screens.map(([screen, facts, docs]) =>
          h("tr", {}, h("th", { scope: "row", text: screen }), h("td", { text: `${facts} facts from ${docs} documents` }))))),
        h("p", { class: "pp-note", text: "Read from the People list, the person page and a search for the name on the demo case." }))));
  });
})();
