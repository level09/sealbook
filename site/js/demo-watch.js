// Agents that keep watching: the standing question, contradiction hunter, gap finder
// and Noticer as their owner reads them after one run each on the demo case, and the
// link one resemblance sent to Review.
(() => {
  const { h } = sealbook;
  const NS = "http://www.w3.org/2000/svg";
  const prop = (p) => ({ same_as: "same person", detained_at: "detained at", seen_at: "seen at", last_seen: "last seen", birth_date: "born", father_name: "father's name", mother_name: "mother's name", arrested_at: "arrested at" }[p] || p.replace(/_/g, " "));
  const plural = (n, one, many = `${one}s`) => `${n === 1 ? "One" : n} ${n === 1 ? one : many}`;
  const SHOW = 4;

  const lock = () => {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "wt-lock");
    svg.innerHTML = '<rect x="3" y="7" width="10" height="7" rx="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/>';
    return svg;
  };
  const locked = (source, count) => h("li", { class: "wt-locked" }, lock(),
    h("span", {}, count > 1 ? `${count} items rest on ` : "Locked: rests on ", h("bdi", { text: source }), ", a sensitive source not revealed in this session."));

  // A claim as one line: field and value, when, and the source it was read from.
  const claim = (c) => h("p", { class: "wt-claim" },
    h("span", { class: "wt-field", text: `${c.field}: ` }), h("bdi", { dir: "auto", text: c.value }),
    c.when ? h("span", { class: "note", text: `, ${c.when}` }) : "",
    h("span", { class: "wt-src" }, h("bdi", { text: c.source })));

  // A gap's line, already worded by the app as "Field: value".
  const said = (c) => h("p", { class: "wt-claim" }, h("bdi", { dir: "auto", text: c.text }),
    c.when ? h("span", { class: "note", text: `, ${c.when}` }) : "",
    h("span", { class: "wt-src" }, h("bdi", { text: c.source })));

  // The first few of a long list, and a button for the rest.
  function clipped(cls, items, render, label) {
    const ul = h("ul", { class: cls, "aria-label": label }, ...items.slice(0, SHOW).map(render));
    if (items.length <= SHOW + 1) { ul.replaceChildren(...items.map(render)); return [ul]; }
    const more = h("button", { type: "button", class: "quiet wt-more", text: `Show all ${items.length}` });
    more.addEventListener("click", () => { ul.replaceChildren(...items.map(render)); more.remove(); });
    return [ul, more];
  }

  function standing(q) {
    const people = q.people.filter((p) => !p.locked);
    const whys = new Set(people.flatMap((p) => p.matches.map((m) => m.why)));
    // Templated reasons repeat; a few distinct ones are listed once above the matches.
    const one = whys.size <= 3 ? [...whys] : null;
    const person = (p) => p.locked ? locked(p.source, p.count) : h("li", { class: "wt-item" },
      h("p", { class: "wt-who" }, h("bdi", { dir: "auto", text: p.name }), h("span", { class: "note", text: ` ${plural(p.matches.length, "match", "matches").toLowerCase()}` })),
      ...p.matches.map((m) => h("div", { class: "wt-match" }, one ? "" : h("p", { class: "wt-why", text: m.why }), ...m.claims.map(claim))));
    return [
      h("p", { class: "wt-q" }, h("span", { class: "note", text: "Question asked once: " }), h("bdi", { text: q.text })),
      h("p", { class: "wt-count", text: `${plural(q.outcome.proposed, "proposed match", "proposed matches")}, by person.` }),
      one ? h("div", { class: "wt-reasons" }, h("p", { class: "note", text: one.length > 1 ? "Reasons the matches give:" : "Reason each match gives:" }),
        h("ul", {}, ...one.map((t) => h("li", { text: t })))) : "",
      ...clipped("wt-list", q.people, person, "Matches"),
    ];
  }

  function contradictions(r) {
    const item = (i) => i.locked ? locked(i.locked.join(", "), 1) : h("li", { class: "wt-item" },
      h("p", {}, h("span", { class: "wt-tag", text: i.kind })),
      h("p", { class: "wt-why", text: i.why }),
      h("ol", { class: "wt-sides" }, ...i.sides.map((c) => h("li", {},
        h("p", { class: "wt-who" }, h("bdi", { dir: "auto", text: c.name }), c.decided ? h("span", { class: "wt-tag wt-solid", text: "accepted" }) : ""),
        claim(c)))),
      h("div", { class: "wt-ask" }, h("p", { class: "note", text: "Drafted question for a colleague" }), h("p", { text: i.question })));
    return [h("p", { class: "wt-count", text: `${plural(r.outcome.proposed, "disagreement")} reported.` }), ...clipped("wt-list", r.items, item, "Disagreements")];
  }

  function gaps(r) {
    const item = (i) => i.locked ? locked(i.locked.join(", "), 1) : h("li", { class: "wt-item" },
      h("p", { class: "wt-who" }, h("bdi", { dir: "auto", text: i.name }), h("span", { class: "wt-tag", text: i.window })),
      ...i.claims.map((c) => said(c)),
      i.maybe ? h("p", { class: "note", text: `An open proposal says this may be the same person as the record ${i.maybe}.` }) : "",
      i.leads.length ? h("div", { class: "wt-leads" }, h("p", { class: "note", text: "Other people placed at the same place in this window:" }),
        ...i.leads.map((c) => said(c))) : "",
      h("p", { class: "wt-why", text: i.where }),
      i.searches.length ? h("p", { class: "wt-searches" }, h("span", { class: "note", text: "Suggested searches: " }),
        ...i.searches.map((s) => h("bdi", { dir: "auto", class: "wt-search", text: s }))) : "");
    return [
      h("p", { class: "wt-count", text: `${plural(r.outcome.proposed, "place to look", "places to look")}. A suggested search only fills in the research drawer; a person sends it.` }),
      ...clipped("wt-list", r.items, item, "Silences"),
    ];
  }

  function noticer(r, proposal, review) {
    const item = (i) => {
      if (i.locked) return locked(i.source, i.count);
      const li = h("li", { class: "wt-item" },
        h("p", {}, h("span", { class: "wt-tag", text: i.kind })),
        h("p", { class: "wt-pair" }, h("bdi", { text: i.a }), h("span", { class: "wt-and", text: " and " }), h("bdi", { text: i.b }),
          i.other_case ? h("span", { class: "note", text: ", in another case" }) : ""),
        h("ul", { class: "wt-signals", "aria-label": "What they share" }, ...i.signals.map((s) => h("li", {}, h("bdi", { text: s })))),
        h("p", { class: "wt-why", text: i.why }),
        h("p", { class: "note", text: `Next: ${i.next}` }),
        i.claims.length ? h("details", { class: "wt-cites" }, h("summary", { text: `${plural(i.claims.length, "cited claim")}` }), h("div", {}, ...i.claims.map(claim))) : "");
      if (i.proposed && proposal) {
        const btn = h("button", { type: "button", class: "quiet wt-propose", text: "Propose a link" });
        btn.addEventListener("click", () => {
          btn.disabled = true;
          btn.textContent = "Proposed, waiting in Review";
          review.replaceChildren(...question(proposal));
          review.hidden = false;
          review.focus({ preventScroll: true });
          if (!sealbook.reduced) review.scrollIntoView({ block: "nearest", behavior: "smooth" });
        });
        li.append(btn);
      }
      return li;
    };
    return [h("p", { class: "wt-count", text: `${plural(r.outcome.proposed, "resemblance")} found.${r.deferred ? ` ${r.deferred} more wait for the next check.` : ""}` }),
      ...clipped("wt-list", r.items, item, "Resemblances")];
  }

  // The proposed link as Review shows it, from the app's review endpoint.
  function question(p) {
    const basis = (b) => h("li", { class: "wt-basis" },
      h("p", {}, h("span", { class: `chip tier-${b.tier}` }, `${prop(b.prop)} `, b.value == null ? "hidden" : h("bdi", { dir: "auto", text: b.value })),
        b.person && b.prop !== "name" ? h("bdi", { dir: "auto", class: "note", text: ` ${b.person}` }) : ""),
      h("p", { class: "note" }, h("bdi", { text: b.source }), `, ${b.where}${b.sensitive ? ", sensitive" : ""}`));
    return [
      h("h4", { text: "In Review, as a question" }),
      h("p", { class: "wt-dhead" }, h("span", { class: "wt-ask-q", text: p.ask }), " ", h("bdi", { text: p.joins[0] }), h("span", { class: "wt-and", text: " and " }), h("bdi", { text: p.joins[1] })),
      h("p", { class: "wt-why" }, h("span", { class: "note", text: `${p.proposer}'s reason: ` }), p.reason),
      h("p", { class: "note", text: `It rests on ${plural(p.basis.length, "claim").toLowerCase()}, each with its source:` }),
      ...clipped("wt-bases", p.basis, basis, "What it rests on"),
      h("p", { class: "note", text: `Tier: ${p.tier}. Nothing is linked until a person accepts it with a passkey.` }),
    ];
  }

  sealbook.onReady((data) => {
    const root = sealbook.mount("watch");
    const w = data.watch;
    if (!root || !w) return;
    const review = h("div", { class: "wt-review", tabindex: "-1", "aria-live": "polite", hidden: "" });
    const body = { standing: () => standing(w.question), contradictions: () => contradictions(w.contradictions), gaps: () => gaps(w.gaps), noticer: () => noticer(w.noticer, w.proposal, review) };
    // The demo switch only flips what each card says; the reads below were made with it on.
    const keep = Object.fromEntries(w.agents.map((a) => [a.key, true]));

    const tabs = h("div", { class: "wt-tabs", role: "group", "aria-label": "Agents" });
    const pane = h("div", { class: "wt-pane", "aria-live": "polite" });
    const buttons = w.agents.map((a) => {
      const b = h("button", { type: "button", class: "quiet", "aria-pressed": "false", text: a.name });
      b.addEventListener("click", () => { buttons.forEach((x) => x.setAttribute("aria-pressed", String(x === b))); show(a); });
      tabs.append(b);
      return b;
    });

    function show(a) {
      const state = h("p", { class: "wt-state" });
      const sw = h("button", { type: "button", role: "switch", class: "wt-switch", "aria-describedby": `wt-demo-${a.key}` },
        h("span", { class: "wt-knob", "aria-hidden": "true" }), h("span", { text: "Keep checking" }));
      const paint = () => {
        sw.setAttribute("aria-checked", String(keep[a.key]));
        state.textContent = keep[a.key] ? a.on : a.off;
        state.classList.toggle("wt-off", !keep[a.key]);
      };
      sw.addEventListener("click", () => { keep[a.key] = !keep[a.key]; paint(); });
      paint();
      review.hidden = true;
      pane.replaceChildren(
        h("div", { class: "wt-head" },
          h("div", {}, h("h3", { text: a.name }), h("p", { class: "wt-does", text: a.does }), h("p", { class: "note", text: `Runs: ${a.when}` })),
          h("div", { class: "wt-control" }, sw, state,
            h("p", { class: "note", id: `wt-demo-${a.key}`, text: "Demo control: it changes what this card says. Nothing runs on this page." }))),
        h("p", { class: "wt-last" }, h("span", { class: "note", text: "Last check: " }), a.last),
        ...body[a.key](),
        a.key === "noticer" ? review : "");
    }

    root.append(tabs, pane,
      h("p", { class: "note", text: "Shown here: the app's own agent code, run once each on the demo case with the demo's fixed answer, so no model ran and the wording is templated. Read back through the app's endpoints as the person who asked reads them, after the same reveals as the brief above." }));
    buttons[0].click();
  });
})();
