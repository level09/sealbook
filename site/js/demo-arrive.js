// Chapter 1: a file arrives. Tray of case files, the specimen, and a record that fills in order.
sealbook.onReady((data) => {
  const root = sealbook.mount("arrive");
  const h = sealbook.h;
  const files = data.files.filter((f) => !f.derived);
  const KIND = { image: "image", pdf: "PDF document", audio: "audio recording", html: "captured web page" };
  const STEP_MS = sealbook.reduced ? 0 : 380;
  const accepted = new Set();
  const label = (p) => p.replace(/_/g, " ");
  const fmtBytes = (n) => `${n.toLocaleString("en-US")} bytes`;
  const fmtMs = (ms) => {
    const s = ms / 1000;
    return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
  };
  const whereText = (w) =>
    w.chars ? `${w.page ? `page ${w.page}, ` : ""}characters ${w.chars[0]} to ${w.chars[1]}` :
    w.bbox ? `page ${w.page ?? 1}, region ${w.bbox.join(", ")}` :
    w.ms ? `${fmtMs(w.ms[0])} to ${fmtMs(w.ms[1])}` : "";

  const tray = h("ul", { class: "tray", "aria-label": "Case files" });
  const specimen = h("div", { class: "specimen", "aria-live": "polite" },
    h("p", { class: "cap", text: "Pick a file from the tray to put it on the bench." }));
  const record = h("div", { class: "record", "aria-live": "polite" });
  root.append(h("div", { class: "bench-grid" }, tray, specimen, record));

  for (const f of files) {
    const b = h("button", { type: "button", "aria-pressed": "false" },
      h("span", { class: "t-name", text: f.name }), h("span", { class: "t-kind", text: KIND[f.kind] || f.kind }));
    b.addEventListener("click", () => open(f, b));
    tray.append(h("li", {}, b));
  }

  // The bench never opens empty: the first time the chapter scrolls into view, the register
  // scan goes through the pipeline once, unless the reader has already picked a file.
  const first = files.find((f) => f.name === "register_page.png");
  const io = new IntersectionObserver(([e]) => {
    if (!e.isIntersecting) return;
    io.disconnect();
    if (!run) open(first, tray.querySelectorAll("button")[files.indexOf(first)]);
  }, { rootMargin: "0px 0px -25% 0px" });
  io.observe(root);

  let run = 0;
  async function open(f, btn) {
    const me = ++run;
    tray.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    showSpecimen(f);
    record.replaceChildren();
    const cards = buildCards(f);
    for (const c of cards) {
      if (me !== run) return;
      record.append(c);
      c.classList.add("in");
      if (STEP_MS) await new Promise((r) => setTimeout(r, STEP_MS));
    }
  }

  let hls = [];
  let audioEl = null;
  function showSpecimen(f) {
    hls = [];
    audioEl = null;
    const frame = h("div", { class: "frame" });
    const img = f.media.endsWith(".html")
      // The capture is shown as stored; the padding is on a white box around it, never in its bytes.
      ? h("div", { class: "capture" }, h("iframe", { src: f.media, sandbox: "", title: `${f.name}, rendered without scripts`, loading: "lazy" }))
      : h("img", { src: f.media, alt: f.kind === "audio" ? `Waveform of ${f.name}` : `${f.name}, as shown to the reviewer` });
    frame.append(img);
    const parts = [frame];
    if (f.audio) {
      audioEl = h("audio", { controls: "", preload: "metadata", src: f.audio });
      parts.push(audioEl);
    }
    const placed = (f.claims || []).map((c, i) => ({ c, i })).filter(({ c }) => c.where.bbox || c.where.ms);
    const place = () => {
      for (const { c, i } of placed) {
        const el = h("span", { class: `hl ${tierClass(f, i)}`, "data-claim": String(i), "aria-hidden": "true" });
        if (c.where.bbox) {
          const [x0, y0, x1, y1] = c.where.bbox;
          // bbox is in the original file's pixels; the media may be downscaled.
          const [W, H] = f.size || [img.naturalWidth, img.naturalHeight];
          Object.assign(el.style, { left: `${(x0 / W) * 100}%`, top: `${(y0 / H) * 100}%`, width: `${((x1 - x0) / W) * 100}%`, height: `${((y1 - y0) / H) * 100}%` });
        } else {
          const dur = f.duration_ms || (audioEl && isFinite(audioEl.duration) ? audioEl.duration * 1000 : 0);
          if (!dur) continue;
          const [a, b] = c.where.ms;
          Object.assign(el.style, { left: `${(a / dur) * 100}%`, width: `${((b - a) / dur) * 100}%`, top: "4%", height: "92%" });
        }
        frame.append(el);
        hls[i] = el;
      }
    };
    const needsAudio = placed.some(({ c }) => c.where.ms) && !f.duration_ms;
    if (needsAudio && audioEl) audioEl.addEventListener("loadedmetadata", place, { once: true });
    else if (img.tagName === "IMG") img.complete ? place() : img.addEventListener("load", place, { once: true });
    const cap = f.kind === "audio"
      ? "Waveform with the claimed stretches marked. The text is the script the clip was spoken from; the speech chapter shows what local speech to text heard."
      : f.kind === "pdf" ? "Page 1, rendered for review. Claims cite characters in the page's extracted text."
      : f.kind === "html" ? "The captured page, shown with scripts disabled. Claims cite characters in its visible text."
      : (f.claims || []).length ? "Claim regions are marked where they were found." : "No claims were proposed for this file.";
    const capEl = h("p", { class: "cap", text: cap });
    if (f.credit) {
      const c = f.credit;
      capEl.append(` A real photograph, not part of the synthetic case: “${c.title}” (${c.date.slice(0, 10)}) by ${c.author}, `,
        h("a", { href: c.licence_url, rel: "license", text: c.licence }), ", via ", h("a", { href: c.source, text: "Wikimedia Commons" }),
        `. ${c.description} Downscaled here; the content credentials are a test manifest added for this demonstration.`);
    }
    specimen.replaceChildren(...parts, capEl);
  }

  const tierClass = (f, i) => (accepted.has(`${f.id}:${i}`) ? "tier-accepted" : "tier-unconfirmed");

  function card(title, ...body) {
    return h("section", { class: "card" }, h("h4", { text: title }), ...body);
  }
  function kv(pairs) {
    const dl = h("dl", { class: "kv" });
    for (const [k, v, mono] of pairs) {
      if (v == null || v === "") continue;
      dl.append(h("dt", { text: k }), h("dd", mono ? { class: "mono" } : {}, h("bdi", { text: String(v) })));
    }
    return dl;
  }

  function buildCards(f) {
    const cards = [];
    cards.push(card("Fixity", kv([["SHA-256", f.sha256, 1], ["MD5", f.md5, 1], ["size", f.bytes != null ? fmtBytes(f.bytes) : null, 1]])));
    if (f.acquired) cards.push(card("Acquisition", kv([["by", f.acquired.by], ["at", f.acquired.at, 1], ["how", f.acquired.how], ["origin", f.acquired.origin, 1]])));
    if (f.c2pa) {
      const states = { valid: "Valid credentials", untrusted: "Credentials present, signer not trusted", absent: "No content credentials", not_applicable: "Not applicable to this format" };
      cards.push(card("Content credentials (C2PA)",
        h("p", { class: `verdict-${f.c2pa.state}`, text: states[f.c2pa.state] || f.c2pa.state, style: "margin:0 0 4px;font-weight:500" }),
        f.c2pa.note ? h("p", { class: "note", text: f.c2pa.note }) : ""));
    }
    cards.push(textCard(f));
    const dup = (data.duplicates || []).filter((d) => d.a === f.id || d.b === f.id);
    const name = (id) => data.files.find((x) => x.id === id)?.name || id;
    cards.push(card("Near duplicates", dup.length
      ? h("ul", { class: "claims" }, ...dup.map((d) => h("li", {},
          h("span", { class: "mono", style: "font-size:13px", text: name(d.a === f.id ? d.b : d.a) }),
          h("span", { class: "where mono", text: `${d.method === "pdq" ? "picture" : "sound"} distance ${d.distance} of ${d.of}${d.offset_ms != null ? `, offset ${fmtMs(Math.abs(d.offset_ms))}` : ""}, ${d.verdict}` }))))
      : h("p", { class: "note", text: "No near duplicate among the case files." })));
    cards.push(chainCard(f));
    return cards;
  }

  function textCard(f) {
    const parts = [];
    if (f.text?.content) {
      const t = f.text.content;
      const body = h("div", { class: "text-body", dir: f.text.dir || "auto", lang: f.text.lang || "" });
      const spans = (f.claims || []).map((c, i) => ({ c, i })).filter(({ c }) => c.where.chars).sort((a, b) => a.c.where.chars[0] - b.c.where.chars[0]);
      let pos = 0;
      for (const { c, i } of spans) {
        const [a, b] = c.where.chars;
        if (a < pos) continue;
        body.append(t.slice(pos, a));
        const s = h("mark", { class: `span-claim ${tierClass(f, i)}`, "data-claim": String(i), text: t.slice(a, b) });
        body.append(s);
        hls[i] = s;
        pos = b;
      }
      body.append(t.slice(pos));
      parts.push(body);
      if (f.text.source) parts.push(h("p", { class: "note", text: `Text source: ${f.text.source}.` }));
    } else {
      parts.push(h("p", { class: "note", text: "No text was extracted from this file." }));
    }
    const list = h("ul", { class: "claims", style: "margin-top:12px" });
    (f.claims || []).forEach((c, i) => {
      const key = `${f.id}:${i}`;
      const chip = h("span", { class: `chip ${tierClass(f, i)}` }, h("bdi", { text: `${label(c.prop)}: ` }), h("bdi", { dir: "auto", text: c.value }));
      const btn = h("button", { type: "button", class: "quiet", text: accepted.has(key) ? "Accepted" : "Accept" });
      if (accepted.has(key)) btn.disabled = true;
      const where = h("span", { class: "where mono", text: `${c.person || ""} ${whereText(c.where)}`.trim() });
      const left = h("span", { style: "display:grid;gap:2px" }, chip, where);
      btn.addEventListener("click", () => accept(f, i, c, chip, btn));
      const li = h("li", {}, left, btn);
      li.addEventListener("mouseenter", () => hls[i]?.classList.add("focus"));
      li.addEventListener("mouseleave", () => hls[i]?.classList.remove("focus"));
      btn.addEventListener("focus", () => hls[i]?.classList.add("focus"));
      btn.addEventListener("blur", () => hls[i]?.classList.remove("focus"));
      if (c.where.ms) where.addEventListener("click", () => { if (audioEl) { audioEl.currentTime = c.where.ms[0] / 1000; audioEl.play(); } });
      list.append(li);
    });
    const title = f.kind === "audio" ? "Transcript and proposed claims" : "Extracted text and proposed claims";
    return card(title, ...parts,
      h("p", { class: "note ai", text: (f.claims || []).length ? "Violet is the AI's first pass. Nothing here is accepted yet." : "No claims were proposed for this file." }),
      list);
  }

  let chainList;
  function chainCard(f) {
    chainList = h("ol", { class: "claims", style: "padding-left:1.1em;list-style:decimal" },
      h("li", {}, h("span", { style: "font-size:14px", text: "Arrival recorded with its SHA-256." })));
    for (const key of accepted) {
      if (!key.startsWith(`${f.id}:`)) continue;
      const c = f.claims[+key.split(":")[1]];
      chainList.append(h("li", {}, h("span", { style: "font-size:14px" }, "Accepted ", h("bdi", { text: label(c.prop) }), " by a reviewer.")));
    }
    return card("Custody chain for this file", chainList);
  }

  async function accept(f, i, c, chip, btn) {
    const key = `${f.id}:${i}`;
    if (accepted.has(key)) return;
    accepted.add(key);
    btn.disabled = true;
    btn.textContent = "Accepted";
    chip.className = "chip tier-accepted";
    hls[i]?.classList.replace("tier-unconfirmed", "tier-accepted");
    specimen.querySelector(".stamp")?.remove();
    specimen.append(h("div", { class: "stamp", "aria-hidden": "true" }, h("span", {}, "accepted", h("br"), "signed")));
    chainList?.append(h("li", {}, h("span", { style: "font-size:14px" }, "Accepted ", h("bdi", { text: label(c.prop) }), " by a reviewer.")));
    await sealbook.chain.append(root, {
      title: "A person accepted a claim",
      signer: "reviewer (you)",
      text: `Accepted ${label(c.prop)} "${c.value}" for ${c.person || "the record"} from ${f.name} at ${whereText(c.where)}. Every link below was re-hashed and re-signed.`,
    });
  }
});
