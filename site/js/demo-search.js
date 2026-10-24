/* Chapter 2: search by meaning, replaying the measured retrieval run from case.json. */
(() => {
  const { h } = sealbook;
  const root = sealbook.mount("search");
  if (!root) return;

  const GROUPS = [
    ["msa", "Modern Standard Arabic"],
    ["dialect", "Syrian dialect"],
    ["cross", "Cross-lingual"],
    ["text_to_image", "Text to image"],
    ["text_to_scan", "Text to scan"],
    ["text_to_audio", "Text to audio"],
    ["negative", "No answer in the set"],
  ];
  const pct = (x) => `${Math.round(x * 100)}%`;
  const sec = (ms) => `${(ms / 1000).toFixed(1)} s`;
  const where = (loc) => {
    if (!loc) return "no located span";
    if (loc.chars) return `characters ${loc.chars[0]} to ${loc.chars[1]}`;
    if (loc.ms) return `${sec(loc.ms[0])} to ${sec(loc.ms[1])} of the audio`;
    if (loc.bbox) return "a region of the page";
    if (loc.whole) return "the whole file";
    return "unknown location";
  };
  const row = (k, v) => [h("dt", { text: k }), h("dd", {}, v)];

  sealbook.onReady((data) => {
    const s = data.search;
    if (!s?.queries?.length) return root.append(h("p", { class: "demo-error", text: "Search data is missing." }));
    const sum = s.summary;
    const neg = sum.negative;

    const picker = h("div", { class: "sq-picker", role: "group", "aria-label": "Prepared queries" });
    const out = h("div", { class: "sq-out", "aria-live": "polite" });
    const buttons = [];

    for (const [type, label] of GROUPS) {
      const qs = s.queries.filter((q) => q.type === type);
      if (!qs.length) continue;
      const list = h("ul", { class: "sq-list" });
      for (const q of qs) {
        const b = h("button", { type: "button", class: "sq-q", "aria-pressed": "false", "data-id": q.id },
          h("bdi", { dir: "auto", text: q.text }));
        b.addEventListener("click", () => select(q, b));
        buttons.push(b);
        list.append(h("li", {}, b));
      }
      picker.append(h("details", type === "msa" ? { class: "sq-group", open: "" } : { class: "sq-group" },
        h("summary", {}, label, h("span", { class: "sq-n", text: ` ${qs.length}` })), list));
    }

    function select(q, btn) {
      for (const b of buttons) b.setAttribute("aria-pressed", String(b === btn));
      btn.closest("details").open = true;
      const top = q.results[0];
      const card = h("article", { class: "sq-card" });
      card.append(
        h("p", { class: "sq-asked" }, "Query ", h("bdi", { dir: "auto", class: "sq-text", text: q.text })),
        h("div", { class: "sq-head" },
          h("span", { class: "chip tier-unconfirmed", text: "lead" }),
          h("span", { class: "sq-file mono", text: top.file }),
          h("span", { class: "sq-score mono", text: `score ${top.score.toFixed(3)}` })),
      );

      const dl = h("dl", { class: "kv sq-kv" });
      if (q.type === "negative") {
        dl.append(...row("Expected", "nothing; no file in the set answers this"),
          ...row("Top score", `${top.score.toFixed(3)}, real hits average ${neg.positive_top_score_mean.toFixed(3)}`));
        card.append(dl, h("p", { class: "sq-point",
          text: `A question with no answer still returns a confident looking file. Across the ${neg.n} no-answer queries the top score reached ${neg.top_score_max.toFixed(3)}, not far below real hits. A score is a ranking, not a verdict, so every result stays a lead for a person to check.` }));
      } else {
        const exp = q.expected.map((e) => e.file).join(" or ");
        dl.append(
          ...row("Top result", top.hit ? h("span", { class: "sq-ok", text: "the expected file" }) : h("span", { class: "sq-miss", text: `not the expected file (${exp})` })),
          ...row("Expected at", `rank ${q.relevant_rank}`),
          ...row("Located", q.location_hit ? `yes, at ${where(top.location)}` : "no"),
        );
        card.append(dl);
        const media = top.hit ? top.media : q.expected[0].media;
        if (media) {
          const isWave = media.includes("_wave");
          card.append(h("figure", { class: `sq-media${isWave ? " wave" : ""}` },
            h("img", { src: media, alt: `${isWave ? "Waveform of" : "Picture of"} ${top.hit ? top.file : q.expected[0].file}`, loading: "lazy" }),
            h("figcaption", { text: top.hit ? `Opens at ${where(top.location)}.` : `The expected file, which ranked ${q.relevant_rank}.` })));
        } else if (top.hit && q.location_hit) {
          card.append(h("p", { class: "sq-span", dir: "auto" }, h("mark", { class: "span-claim tier-unconfirmed", text: q.expected[0].text })),
            h("p", { class: "note", text: `The located span. The rest of ${top.file} is not published on this page.` }));
        }
      }
      out.replaceChildren(card);
    }

    const types = GROUPS.filter(([t]) => sum.by_type[t]);
    const table = h("table", { class: "sq-table" },
      h("caption", { text: `Measured on ${sum.query_latency.n} synthetic queries, ${s.model.replace("embeddinggemma-2", "EmbeddingGemma 2")} on local Ollama (${s.runtime}).` }),
      h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Query type" }), h("th", { scope: "col", text: "Queries" }),
        h("th", { scope: "col", text: "Right file first" }), h("th", { scope: "col", text: "In top five" }))),
      h("tbody", {}, ...types.map(([t, label]) => {
        const b = sum.by_type[t];
        return h("tr", {}, h("th", { scope: "row", text: label }), h("td", { text: String(b.n) }), h("td", { text: pct(b.recall_at_1) }), h("td", { text: pct(b.recall_at_5) }));
      })));
    const note = h("p", { class: "sq-note",
      text: `Across ${sum.n} answerable queries the right file came first ${pct(sum.recall_at_1)} of the time and within the top five ${pct(sum.recall_at_5)}. ${s.note}` });

    const credits = h("p", { class: "sq-note sq-credit" }, "Photographs in the set, real and openly licensed, not part of the synthetic case: ");
    for (const c of s.credits) {
      credits.append(`“${c.title}” (${c.date.slice(0, 10)}) by ${c.author}, `,
        h("a", { href: c.licence_url, rel: "license", text: c.licence }), ", via ", h("a", { href: c.source, text: "Wikimedia Commons" }), ". ");
    }

    root.append(h("div", { class: "sq-grid" }, picker, out), h("div", { class: "sq-measured" }, note, table, credits));
    const lead = s.queries.find((q) => q.results[0]?.hit && q.location_hit) || s.queries[0];
    select(lead, buttons.find((b) => b.dataset.id === String(lead.id)));
  });
})();
