/*
  Sealbook showcase: page shell, data loading and the demonstration chain.

  API for demo scripts (window.sealbook):
    sealbook.data            case.json once loaded, else null
    sealbook.onReady(fn)     fn(data) runs once data is loaded (immediately if it already is)
    sealbook.mount(name)     the <div class="demo" data-demo="name"> element, or null
    sealbook.reduced         true when the reader prefers reduced motion
    sealbook.chain
      .links                 [{el, signer, text, prev, hash, signed, ok}] in page order
      .ready                 Promise resolved after the first hashing pass
      .append(afterEl, {title, signer, text})  insert a link after a section, re-sign downstream
      .tamper(i, from, to)   change one word in link i's content without re-signing
      .restore()             undo tampering
      .merkleRoot()          Promise of hex root over current link hashes
      .onChange(fn)          fn(chain) after every recompute
      .sha256(str)           Promise of hex digest

  The chain hashes SHA-256(previous hash + "\n" + signer + "\n" + link text). It shows
  the same idea as the app's custody chain. It is not the app's statement format, and
  signers are labels, not keys.
*/
(() => {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const GENESIS = "0".repeat(64);
  const enc = new TextEncoder();
  const sha256 = async (s) =>
    [...new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(s)))]
      .map((b) => b.toString(16).padStart(2, "0")).join("");
  const short = (h) => `${h.slice(0, 10)}…${h.slice(-6)}`;
  const norm = (s) => s.replace(/\s+/g, " ").trim();

  // Theme: paper by default, toggle stored per viewer.
  const root = document.documentElement;
  const toggle = document.getElementById("theme");
  // Paper is the default; the night archive is opt-in.
  const current = () => root.dataset.theme || "light";
  const label = () => {
    const dark = current() === "dark";
    toggle.setAttribute("aria-pressed", String(dark));
    toggle.querySelector("span").textContent = dark ? "Paper" : "Night archive";
  };
  toggle.addEventListener("click", () => {
    root.dataset.theme = current() === "dark" ? "light" : "dark";
    try { localStorage.setItem("sealbook-theme", root.dataset.theme); } catch {}
    label();
  });
  label();

  // Navigation: the chapter and contents entry of the section under the reading line.
  const head = document.querySelector(".top");
  const toc = document.getElementById("toc");
  const chapters = [...document.querySelectorAll(".chapters a")];
  const entries = [...toc.querySelectorAll("ol ol a")].map((a) => ({
    a,
    section: document.querySelector(a.hash),
    chapter: chapters[[...toc.querySelectorAll(":scope > ol > li")].indexOf(a.closest("ol").parentElement)],
  }));
  let frame = 0;
  const spy = () => {
    frame = 0;
    head.classList.toggle("stuck", scrollY > 4);
    const line = innerHeight * 0.35;
    const now = entries.findLast((e) => e.section.getBoundingClientRect().top <= line);
    for (const e of entries) e.a.toggleAttribute("aria-current", e === now);
    for (const c of chapters) c.toggleAttribute("aria-current", c === now?.chapter);
  };
  addEventListener("scroll", () => { frame ||= requestAnimationFrame(spy); }, { passive: true });
  // Demos grow as a smooth scroll passes them and push its target away, so jump.
  head.addEventListener("click", (e) => {
    const a = e.target.closest("a[href^='#']");
    if (!a) return;
    e.preventDefault();
    toc.hidePopover();
    document.querySelector(a.hash).scrollIntoView({ behavior: "instant" });
    history.replaceState(null, "", a.hash);
  });
  spy();

  // The seal settles once on load.
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    document.querySelector(".mark animate")?.beginElement();
  }

  // Chain
  const listeners = [];
  const links = [...document.querySelectorAll("section.link")].map((el) => ({
    el,
    signer: el.dataset.signer,
    text: norm(el.querySelector(".copy").textContent),
    seen: false,
  }));
  let tampered = null;

  function sealFor(link) {
    let dl = link.el.querySelector(".seal");
    if (!dl) {
      dl = document.createElement("dl");
      dl.className = "seal mono";
      dl.innerHTML = `<div><dt>link</dt><dd class="s-n"></dd></div><div><dt>hash</dt><dd class="s-h"></dd></div>
        <div><dt>previous</dt><dd class="s-p"></dd></div><div><dt>signer</dt><dd class="s-s"></dd></div>
        <div><dt>check</dt><dd class="s-c" aria-live="polite"></dd></div>`;
      link.el.prepend(dl);
    }
    return dl;
  }

  function paint(link, i) {
    const dl = sealFor(link);
    dl.querySelector(".s-n").textContent = String(i);
    const h = dl.querySelector(".s-h");
    h.textContent = short(link.hash);
    h.title = link.hash;
    dl.querySelector(".s-p").textContent = short(link.prev);
    dl.querySelector(".s-s").textContent = link.signer;
    const c = dl.querySelector(".s-c");
    link.el.classList.remove("pending", "verified", "broken");
    if (!link.seen) { link.el.classList.add("pending"); c.textContent = "not yet checked"; return; }
    link.el.classList.add(link.ok ? "verified" : "broken");
    c.textContent = link.ok ? "matches the signed value" : "hash does not match the signed value";
  }

  async function recompute({ resign = false } = {}) {
    let prev = GENESIS;
    for (const [i, l] of links.entries()) {
      const text = tampered && tampered.i === i ? l.text.replace(tampered.from, tampered.to) : l.text;
      l.prev = prev;
      l.hash = await sha256(`${prev}\n${l.signer}\n${text}`);
      if (resign || !l.signed) l.signed = l.hash;
      l.ok = l.hash === l.signed;
      prev = l.hash;
    }
    links.forEach(paint);
    document.getElementById("chain").classList.toggle("tampered", links.some((l) => !l.ok));
    listeners.forEach((fn) => fn(chain));
  }

  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const i = links.findIndex((l) => l.el === e.target);
      if (i < 0) continue;
      links[i].seen = true;
      paint(links[i], i);
      io.unobserve(e.target);
    }
  }, { rootMargin: "0px 0px -20% 0px" });

  const chain = {
    links,
    sha256,
    ready: null,
    onChange(fn) { listeners.push(fn); },
    async append(afterEl, { title, signer, text }) {
      const i = links.findIndex((l) => l.el === afterEl || l.el.contains(afterEl));
      const el = document.createElement("section");
      el.className = "link added";
      el.dataset.signer = signer;
      el.innerHTML = `<div class="copy"><p class="added-title"></p><p class="added-text"></p></div>`;
      el.querySelector(".added-title").textContent = title;
      el.querySelector(".added-text").textContent = text;
      links[i].el.after(el);
      const link = { el, signer, text: norm(el.querySelector(".copy").textContent), seen: true };
      links.splice(i + 1, 0, link);
      // Downstream links are re-signed: the page owns every signer label.
      await recompute({ resign: true });
      links.slice(i + 2).forEach((l) => {
        l.el.classList.remove("resigned");
        void l.el.offsetWidth;
        l.el.classList.add("resigned");
      });
      return link;
    },
    async tamper(i, from, to) {
      tampered = { i, from, to };
      links.forEach((l) => (l.seen = true));
      await recompute();
    },
    async restore() { tampered = null; await recompute(); },
    get tampered() { return tampered; },
    async merkleRoot() {
      let level = links.map((l) => l.hash);
      if (!level.length) return GENESIS;
      while (level.length > 1) {
        const next = [];
        for (let k = 0; k < level.length; k += 2) next.push(await sha256(level[k] + (level[k + 1] ?? level[k])));
        level = next;
      }
      return level[0];
    },
  };
  chain.ready = recompute().then(() => links.forEach((l) => io.observe(l.el)));

  // Data
  const queue = [];
  const sealbook = {
    data: null,
    chain,
    reduced,
    mount: (name) => document.querySelector(`[data-demo="${name}"]`),
    onReady(fn) { sealbook.data ? fn(sealbook.data) : queue.push(fn); },
  };
  window.sealbook = sealbook;

  fetch("data/case.json")
    .then((r) => { if (!r.ok) throw new Error(`data/case.json: HTTP ${r.status}`); return r.json(); })
    .then((data) => {
      sealbook.data = data;
      fillOpening(data);
      fillStatus(data);
      document.querySelectorAll("#archive [data-clamp], #status [data-clamp]").forEach(clamp);
      queue.splice(0).forEach((fn) => {
        try { fn(data); } catch (err) { console.error(err); }
      });
    })
    .catch((err) => {
      console.error(err);
      document.querySelectorAll(".demo").forEach((d) => {
        d.innerHTML = `<p class="demo-error">The case data did not load (${err.message}). Reload the page to try again.</p>`;
      });
    });

  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) k === "text" ? (el.textContent = v) : el.setAttribute(k, v);
    el.append(...kids);
    return el;
  };
  sealbook.h = h;

  // Small screens: a long secondary list shows its first few items and a button for the rest.
  // The CSS hides .extra items only under 600px, so wider screens always show everything.
  function clamp(list) {
    const items = [...list.children];
    const n = +list.dataset.clamp;
    if (items.length <= n + 1) return;
    items.slice(n).forEach((li) => li.classList.add("extra"));
    list.classList.add("clamped");
    const more = `Show all ${items.length}`;
    const btn = h("button", { type: "button", class: "quiet more", "aria-expanded": "false", text: more });
    btn.addEventListener("click", () => {
      const open = !list.classList.toggle("clamped");
      btn.setAttribute("aria-expanded", String(open));
      btn.textContent = open ? "Show fewer" : more;
    });
    list.after(btn);
  }
  chain.ready.then(() => clamp(document.querySelector(".lessons")));

  function fillOpening(data) {
    const panel = document.getElementById("archive");
    const person = data.people?.find((p) => (p.spellings || []).length > 1) || data.people?.[0];
    const spell = panel.querySelector(".spell");
    if (person) {
      for (const s of person.spellings) spell.append(h("li", { dir: "auto" }, h("bdi", { text: s })));
      panel.querySelector(".spell-note").textContent =
        `${person.spellings.length} spellings of ${person.label} across the case files. Matched as candidates, never merged on their own.`;
    }
    // Picture copies of one image, with the app's match threshold.
    const PDQ_DISTANCE = 31;
    const dupes = (data.duplicates || []).filter((d) => d.method === "pdq");
    const ups = panel.querySelector(".uploads");
    const name = (id) => data.photos?.[id]?.name || data.files.find((f) => f.id === id)?.name || id;
    const how = (id) => data.derived?.find((x) => x.id === id)?.how || id;
    if (dupes.length) {
      const base = dupes[0].a;
      const group = dupes.filter((d) => d.a === base).sort((x, y) => x.distance - y.distance);
      ups.append(h("li", {}, h("span", { class: "u-name" }, "original ", h("span", { class: "mono", text: name(base) })), h("span", { class: "u-d mono", text: "reference" })));
      for (const d of group) {
        const hit = d.distance <= PDQ_DISTANCE;
        ups.append(h("li", {}, h("span", { class: "u-name", text: how(d.b) }),
          h("span", { class: `u-d mono${hit ? "" : " u-off"}`, text: `${d.distance} of ${d.of}, ${hit ? "matched" : "not matched"}` })));
      }
      const hits = group.filter((d) => d.distance <= PDQ_DISTANCE).length;
      panel.querySelector(".uploads-note").textContent =
        `${hits} of ${group.length} copies fall within the picture fingerprint threshold of ${PDQ_DISTANCE}. Cropped copies do not. Matches stay separate sources until a person links them.`;
    } else {
      panel.querySelector(".uploads-block").hidden = true;
    }
    const file = data.files.find((f) => f.claims?.length);
    if (file) {
      const c = file.claims.find((x) => x.prop !== "name") || file.claims[0];
      const claim = panel.querySelector(".pending-claim");
      claim.querySelector(".tier-unconfirmed").replaceChildren(h("bdi", { text: `${c.prop.replace(/_/g, " ")}: ` }), h("bdi", { dir: "auto", text: c.value }));
      claim.querySelector(".pc-src").textContent = file.name;
    }
  }

  // Doc text with `code` spans, as docs/status.md writes it.
  const prose = (text) => text.split("`").map((t, i) => (i % 2 ? h("code", { text: t }) : t));
  sealbook.prose = prose;

  function fillStatus(data) {
    const box = document.getElementById("status");
    const st = data.status || {};
    const areas = box.querySelector('[data-status="areas"]');
    for (const a of st.areas || []) {
      areas.append(h("details", { class: "area" },
        h("summary", { text: a.area }),
        h("p", {}, h("b", { text: "Does: " }), ...prose(a.does)),
        h("p", {}, h("b", { text: "Limits: " }), ...prose(a.limits))));
    }
    for (const key of ["measured", "planned"]) {
      const ul = box.querySelector(`[data-status="${key}"]`);
      for (const s of st[key] || []) ul.append(h("li", {}, ...prose(s)));
    }
    const gen = document.getElementById("generated");
    if (data.generated_at) gen.textContent = `From docs/status.md, updated ${st.updated}. Data generated ${data.generated_at}.`;
  }
})();
