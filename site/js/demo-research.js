// Web research: the queries the app proposed for one person, what it left out and
// why, and the refusal for a case marked sensitive. Nothing was sent anywhere.
(() => {
  const { h } = sealbook;
  const prop = (p) => ({ birth_date: "birth date", arrested_at: "arrest place", father_name: "father's name", detention_branch: "detention branch", last_seen: "last seen", seen_at: "seen at", transferred_to: "transferred to" }[p] || p.replace(/_/g, " "));
  const KIND = { web: "web", images: "images", videos: "videos" };

  sealbook.onReady((data) => {
    const root = sealbook.mount("research");
    const r = data.research;
    if (!root || !r) return;

    const queries = h("ol", { class: "rs-queries" }, ...r.queries.map((q) => {
      const box = h("input", { type: "checkbox", checked: "", "aria-label": `Send ${q.text}` });
      const li = h("li", { class: "rs-q" },
        h("label", { class: "rs-row" }, box, h("bdi", { dir: "auto", class: "rs-text", text: q.text })),
        h("p", { class: "note" }, `${q.kinds.map((k) => KIND[k]).join(", ")}. Built from `,
          ...q.why.flatMap((w, i) => [i ? ", " : "", `${w.label.toLowerCase()} `, h("bdi", { dir: "auto", text: w.value })]), "."));
      box.addEventListener("change", () => li.classList.toggle("off", !box.checked));
      return li;
    }));

    const counts = {};
    for (const x of r.left_out) (counts[x.why] ||= []).push(prop(x.prop));
    const left = h("ul", { class: "rs-left" }, ...Object.entries(counts).map(([why, props]) =>
      h("li", {}, h("b", { text: `${props.length} ${why}: ` }), props.join(", "))));

    root.append(h("div", { class: "rs-grid" },
      h("div", { class: "rs-drawer" },
        h("h3", {}, "Proposed queries for ", h("bdi", { dir: "auto", text: r.person })),
        queries,
        h("p", { class: "note", text: "The analyst edits or unticks any query. What is listed is exactly what would be sent." })),
      h("div", {},
        h("h3", { text: `Left out of the queries, ${r.left_out.length} of ${r.facts} facts` }), left,
        h("h3", { text: "The same case, marked sensitive" }),
        h("p", { class: "rs-refused" }, h("bdi", { text: r.refused })),
        h("p", { class: "note", text: `The query proposal for the same person then returns ${r.refused_queries === 0 ? "nothing" : r.refused_queries}.` }))),
      h("p", { class: "note", text: "Proposed by the app's research code for the demo case; no query was sent and no search results are shown, since this page makes no third-party requests. Ticking boxes here changes this page only." }));
  });
})();
