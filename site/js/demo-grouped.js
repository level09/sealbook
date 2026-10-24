// Grouped search: answers replayed from the app's /api/search on a throwaway copy of the demo case.
(() => {
  const { h } = sealbook;
  const GROUPS = { people: "People", places: "Places", sources: "Sources", passages: "Passages", media: "Media", claims: "Claims", findings: "Findings", incidents: "Incidents" };
  const SIGNALS = { literal: "exact text", words: "all words", name: "name", name_variant: "recorded spelling", place_name: "gazetteer name", hash: "hash", prefix: "hash prefix", file: "file name", origin: "origin" };
  const pct = (x) => `${Math.round(x * 100)}%`;

  function snippet(s) {
    if (!s?.text) return null;
    const p = h("p", { class: "gs-snip", dir: "auto" });
    let at = 0;
    for (const [a, b] of s.highlights || []) {
      p.append(s.text.slice(at, a), h("mark", { text: s.text.slice(a, b) }));
      at = b;
    }
    p.append(s.text.slice(at));
    return p;
  }

  function item(i) {
    const tags = h("span", { class: "gs-tags" },
      ...i.signals.map((sg) => h("span", { class: "gs-tag", text: SIGNALS[sg] || sg.replace(/_/g, " ") })));
    return h("li", { class: "gs-item" },
      h("p", { class: "gs-title" }, h("bdi", { dir: "auto", text: i.masked ? "Masked until a logged reveal" : i.title }),
        i.alt?.length ? h("span", { class: "gs-alt" }, " also ", ...i.alt.flatMap((a, k) => [k ? ", " : "", h("bdi", { dir: "auto", text: a })])) : "",
        i.subtitle ? h("span", { class: "gs-sub", text: ` ${i.subtitle.replace(/_/g, " ")}` }) : ""),
      tags,
      i.matched && !/^[a-z]+:[0-9a-f-]{8,}/.test(i.matched) ? h("p", { class: "note" }, "Matched through ", h("bdi", { dir: "auto", text: i.matched })) : "",
      snippet(i.snippet) || "");
  }

  sealbook.onReady((data) => {
    const root = sealbook.mount("grouped");
    const g = data.grouped_search;
    if (!root || !g) return;
    const b = g.battery, gold = g.gold;
    root.append(h("dl", { class: "gs-measured" },
      h("div", {}, h("dt", { text: `Analyst battery, ${b.scored} queries on the demo case` }),
        h("dd", { text: `Expected source first for ${pct(b.hit_at_1)}, in the top three for ${pct(b.hit_at_3)}. Median ${b.p50_ms} ms, p95 ${b.p95_ms} ms.` })),
      h("div", {}, h("dt", { text: `Retrieval gold set, ${gold.queries} queries` }),
        h("dd", { text: `Right file first for ${pct(gold.recall_at_1)}, in the top five for ${pct(gold.recall_at_5)}.` }))),
      h("p", { class: "note", text: `${b.setup}, ${b.date}. Synthetic data only; these numbers say nothing about archive scale, handwriting or field audio.` }));

    const picker = h("div", { class: "gs-picker", role: "group", "aria-label": "Grouped search queries" });
    const out = h("div", { class: "gs-out", "aria-live": "polite" });
    const buttons = g.queries.map((q) => {
      const btn = h("button", { type: "button", class: "quiet", "aria-pressed": "false" }, h("bdi", { dir: "auto", text: q.query }));
      btn.addEventListener("click", () => show(q, btn));
      picker.append(btn);
      return btn;
    });

    function show(q, btn) {
      buttons.forEach((x) => x.setAttribute("aria-pressed", String(x === btn)));
      const cols = Object.entries(q.groups).map(([name, grp]) =>
        h("section", { class: "gs-group" },
          h("h4", {}, GROUPS[name] || name, h("span", { class: "gs-n", text: ` ${grp.exact} exact of ${grp.total}` })),
          h("ol", { class: "gs-list" }, ...grp.items.map(item))));
      out.replaceChildren(
        h("p", { class: "gs-head" }, h("bdi", { dir: "auto", text: q.query }), ` answered in ${q.ms} ms`),
        h("div", { class: "gs-groups" }, ...cols));
    }
    root.append(picker, out,
      h("p", { class: "note", text: "Answers from the app's search endpoint on a throwaway copy of the demo case, after the logged reveals in the connections chapter. The local model was off for this build, so only exact signals answer here; the measured runs above used it." }));
    show(g.queries[0], buttons[0]);
  });
})();
