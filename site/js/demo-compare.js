// Side by side: two sources from one open finding of the demo case, as A and B, with
// the reason they were opened together. Pairs and passages come from the discovery run.
(() => {
  const { h } = sealbook;
  const prop = (p) => p.replace(/_/g, " ");

  // One pair per two different files a finding joins, the first finding that joins them.
  function pairs(findings) {
    const seen = new Set(), out = [];
    for (const f of findings) {
      const ms = f.members.filter((m) => m.excerpt);
      for (let i = 0; i < ms.length; i++) for (let j = i + 1; j < ms.length; j++) {
        const [a, b] = [ms[i], ms[j]], key = [a.file, b.file].sort().join("|");
        if (a.file === b.file || seen.has(key)) continue;
        seen.add(key);
        out.push({ f, a, b });
      }
    }
    return out;
  }

  function side(letter, m) {
    const shut = m.sensitive;
    const text = h("p", { class: "cp-ex", dir: "auto" }, m.excerpt.before, h("mark", { class: "tier-accepted", text: m.excerpt.span }), m.excerpt.after);
    const body = h("div", { class: "cp-body" },
      h("p", { class: "cp-who" }, h("bdi", { dir: "auto", text: m.name })),
      h("p", { class: "cp-what" }, h("span", { class: `chip tier-${m.tier}` }, `${prop(m.prop)} `, h("bdi", { dir: "auto", text: m.value })),
        h("span", { class: "mono cp-when", text: m.when })));
    if (shut) {
      const cover = h("div", { class: "cp-cover" },
        h("p", { text: "Sensitive source. Covered until someone chooses to read it." }),
        h("button", { class: "quiet", type: "button", text: "Reveal, logged" }));
      cover.querySelector("button").addEventListener("click", () => cover.replaceWith(text, h("p", { class: "note", text: "In the app this reveal is written to the access log with your name." })));
      body.append(cover);
    } else body.append(text);
    return h("article", { class: "cp-side" },
      h("header", {}, h("b", { "aria-hidden": "true", text: letter }),
        h("span", { class: "cp-file", text: m.derived ? `text read from ${m.file}` : m.file })),
      body);
  }

  sealbook.onReady((data) => {
    const root = sealbook.mount("compare");
    const all = pairs(data.connections?.findings || []);
    if (!root || !all.length) return;
    let at = 0, swapped = false;
    const picks = h("div", { class: "cp-picks", role: "group", "aria-label": "Pairs to compare" });
    const view = h("div", { "aria-live": "polite" });

    const draw = () => {
      const p = all[at], [a, b] = swapped ? [p.b, p.a] : [p.a, p.b];
      const swap = h("button", { class: "quiet", type: "button", text: "Swap sides" });
      swap.addEventListener("click", () => { swapped = !swapped; draw(); });
      view.replaceChildren(
        h("div", { class: "cp-bar" }, h("strong", { text: "Side by side" }),
          h("span", { class: "cp-why", text: `${p.f.reason}. The marked passage on each side is what that rests on.` }),
          swap),
        h("div", { class: "cp-pair" }, side("A", a), side("B", b)));
      picks.querySelectorAll("button").forEach((x, i) => x.setAttribute("aria-pressed", String(i === at)));
    };
    all.forEach((p, i) => {
      const b = h("button", { class: "quiet", type: "button", text: `${p.a.file} and ${p.b.file}` });
      b.addEventListener("click", () => { at = i; swapped = false; draw(); });
      picks.append(b);
    });
    root.append(picks, view,
      h("p", { class: "note", text: "Pairs and passages from the demo case's discovery run. Each side keeps its own marks; the match is a proposal until a person signs it." }));
    draw();
  });
})();
