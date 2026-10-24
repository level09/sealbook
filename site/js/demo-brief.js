// A batch, read as a whole: the intake brief for both demo batches and every linker
// proposal, as the app's brief and review endpoints returned them.
(() => {
  const { h } = sealbook;
  const NS = "http://www.w3.org/2000/svg";
  const prop = (p) => ({ same_as: "same person", detained_at: "detained at", seen_at: "seen at", last_seen: "last seen", birth_date: "born", father_name: "father's name", mother_name: "mother's name", arrested_at: "arrested at" }[p] || p.replace(/_/g, " "));
  const SECTIONS = [["arrived", "What arrived"], ["who", "Who it names"], ["where", "Where"], ["when", "When"], ["connects", "What it connects to"], ["disagrees", "What disagrees"], ["next", "Next steps"]];
  const BATCHES = ["A roster and a released detainee's statement", "Two versions of a family intake sheet"];
  const SHOW = 5;

  const lock = () => {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "bf-lock");
    svg.innerHTML = '<rect x="3" y="7" width="10" height="7" rx="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/>';
    return svg;
  };

  function line(l) {
    if (!l.text) {
      return h("li", { class: "bf-line bf-locked" }, lock(),
        h("span", {}, "Locked: rests on ", h("bdi", { text: l.locked.join(", ") }), ", a sensitive source not revealed in this session."));
    }
    const li = h("li", { class: "bf-line" }, h("bdi", { dir: "ltr", class: "bf-text", text: l.text }),
      l.tag ? h("span", { class: `bf-tag bf-${l.tag}`, text: l.tag === "open" ? "to confirm" : l.tag }) : "");
    if (l.cites.length) {
      const sources = [...new Set(l.cites.map((c) => c.source))];
      li.append(h("details", { class: "bf-cites" },
        h("summary", { text: `${l.cites.length} cited claim${l.cites.length > 1 ? "s" : ""}` }),
        h("ul", {}, ...sources.map((s) => h("li", {},
          h("bdi", { text: s }), h("span", { class: "note", text: `: ${[...new Set(l.cites.filter((c) => c.source === s).map((c) => prop(c.prop)))].join(", ")}` }))))));
    }
    return li;
  }

  function brief(b) {
    return SECTIONS.filter(([k]) => b[k].length || k === "disagrees").map(([k, label]) => {
      const items = b[k];
      const ul = h("ul", { class: "bf-lines" }, ...items.slice(0, SHOW).map(line));
      const sec = h("section", { class: "bf-sec" }, h("h4", { text: label }));
      if (!items.length) sec.append(h("p", { class: "note", text: "Nothing in this batch disagrees with an accepted claim." }));
      else sec.append(ul);
      if (items.length > SHOW) {
        const more = h("button", { type: "button", class: "quiet bf-more", text: `Show all ${items.length}` });
        more.addEventListener("click", () => { ul.replaceChildren(...items.map(line)); more.remove(); });
        sec.append(more);
      }
      return sec;
    });
  }

  // Two records under one name read as such, not as "X and X".
  function joins(p) {
    const [a, b] = p.joins;
    if (a === b) return ["two records named ", h("bdi", { dir: "auto", text: a })];
    return [h("bdi", { dir: "auto", text: a }), h("span", { class: "bf-and", text: p.kind === "involved" ? " in " : " and " }), h("bdi", { dir: "auto", text: b })];
  }

  function basis(r) {
    return h("li", { class: "bf-basis" },
      h("p", {}, h("span", { class: `chip tier-${r.tier}` }, `${prop(r.prop)} `,
        r.value == null ? h("span", { class: "bf-hidden" }, lock(), "hidden") : h("bdi", { dir: "auto", text: r.value })),
        r.person && r.prop !== "name" ? h("bdi", { dir: "auto", class: "bf-who", text: ` ${r.person}` }) : ""),
      h("p", { class: "note" }, h("bdi", { text: r.source }), `, ${r.where}${r.sensitive ? ", sensitive" : ""}`));
  }

  sealbook.onReady((data) => {
    const root = sealbook.mount("brief");
    const a = data.agents;
    if (!root || !a) return;

    const tabs = h("div", { class: "bf-tabs", role: "group", "aria-label": "Intake batches" });
    const pane = h("div", { class: "bf-brief", "aria-live": "polite" });
    const buttons = a.briefs.map((b, i) => {
      const btn = h("button", { type: "button", class: "quiet", "aria-pressed": "false", text: BATCHES[i] || `Batch ${i + 1}` });
      btn.addEventListener("click", () => {
        buttons.forEach((x) => x.setAttribute("aria-pressed", String(x === btn)));
        pane.replaceChildren(...brief(b));
      });
      tabs.append(btn);
      return btn;
    });

    const list = h("ul", { class: "bf-qs", "aria-label": "Linker proposals" });
    const detail = h("div", { class: "bf-detail", "aria-live": "polite" });
    const qs = a.proposals.map((p) => {
      const b = h("button", { type: "button", class: "bf-q", "aria-pressed": "false" },
        h("span", { class: "bf-ask", text: p.ask }),
        h("span", { class: "bf-joins" }, ...joins(p)));
      b.addEventListener("click", () => open(p, b));
      list.append(h("li", {}, b));
      return b;
    });
    function open(p, btn) {
      qs.forEach((x) => x.setAttribute("aria-pressed", String(x === btn)));
      detail.replaceChildren(
        h("p", { class: "bf-dhead" }, h("span", { class: "bf-ask", text: p.ask }), " ", ...joins(p)),
        p.reason ? h("p", { class: "bf-reason" }, h("span", { class: "note", text: `${p.proposer}'s reason: ` }), p.reason)
          : h("p", { class: "bf-reason note", text: "The reason stays hidden until the sensitive source it rests on is revealed, because it could repeat a hidden value." }),
        h("h4", { text: "What it rests on" }),
        h("ul", { class: "bf-bases" }, ...p.basis.map(basis)),
        h("p", { class: "note", text: "A question for a reviewer. Nothing is linked until a person accepts it with a passkey." }));
    }

    root.append(
      h("div", { class: "bf-grid" },
        h("div", {}, h("h3", { text: "Intake brief" }), tabs, pane),
        h("div", {}, h("h3", { text: `Linker: ${a.proposals.length} proposals` }), list, detail,
          h("p", { class: "note", text: `From ${a.candidates} rule-built candidates. ${a.already_known === 1 ? "One more was" : `${a.already_known} more were`} already proposed elsewhere, so the linker did not ask again.` }))),
      h("p", { class: "note", text: `Shown here: the app's own brief and linker code on the demo case with the demo's fixed answer, so no model ran and the wording is templated. Measured once with a real model, ${a.measured} One model, one case, no quality evaluation.` }));
    buttons[0].click();
    open(a.proposals[0], qs[0]);
  });
})();
