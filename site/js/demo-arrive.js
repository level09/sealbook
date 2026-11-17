// Exhibit 1: a file arrives and Sealbook reads it in front of you. The file stays large; a
// violet reader moves to each place a claim came from and the claim lands in the list beside it.
sealbook.onReady((data) => {
  const root = sealbook.mount("arrive");
  const h = sealbook.h;
  const files = data.files.filter((f) => !f.derived);
  const DOC = {
    detention_register: ["Detention register", "Scanned page"],
    transfer_order: ["Transfer order", "PDF"],
    witness_statement: ["Witness voice note", "Audio"],
    photo: ["Damaged building", "Photo"],
    web_post: ["Missing person post", "Web capture"],
  };
  const PROP = {
    name: "Name", father_name: "Father", mother_name: "Mother", birth_date: "Born", arrested_at: "Arrested in",
    detention_branch: "Held at", detained_at: "Detained at", transferred_to: "Transferred to", seen_at: "Seen at", last_seen: "Last seen",
  };
  const STEP_MS = sealbook.reduced ? 0 : 620;
  const AR = /[؀-ۿ]/;
  const accepted = new Set();
  const label = (p) => PROP[p] || p.replace(/_/g, " ");
  const person = (c) => (c.person ? `Person ${c.person.replace(/\D/g, "")}` : "");
  const fmtMs = (ms) => {
    const s = ms / 1000;
    return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
  };
  const whereText = (w) =>
    w.chars ? `${w.page ? `page ${w.page}, ` : ""}characters ${w.chars[0]} to ${w.chars[1]}` :
    w.bbox ? `page ${w.page ?? 1}, region ${w.bbox.join(", ")}` :
    w.ms ? `${fmtMs(w.ms[0])} to ${fmtMs(w.ms[1])}` :
    w.page ? `page ${w.page}` : "";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const tabs = h("div", { class: "rd-tabs", role: "group", "aria-label": "Case files" });
  const page = h("div", { class: "rd-page" });
  const status = h("p", { class: "rd-status", "aria-live": "polite" });
  const list = h("ol", { class: "rd-list" });
  const recBody = h("div", { class: "rd-rec" });
  const record = h("details", { class: "rd-record" }, h("summary", { text: "The record behind this file: fingerprints, acquisition, credentials, custody" }), recBody);
  root.append(tabs, h("div", { class: "rd-grid" }, page, h("div", { class: "rd-side" }, status, list)), record);

  for (const f of files) {
    const [title, kind] = DOC[f.doc_type] || [f.name, f.kind];
    const thumb = f.media.endsWith(".html")
      ? h("span", { class: "rd-thumb web", "aria-hidden": "true", text: "www" })
      : h("img", { class: "rd-thumb", src: f.media, alt: "", loading: "lazy" });
    const b = h("button", { type: "button", "aria-pressed": "false" }, thumb,
      h("span", {}, h("b", { text: title }), h("small", { text: kind })));
    b.addEventListener("click", () => open(f, b));
    tabs.append(b);
  }

  // The reader never opens empty: the first time the exhibit scrolls into view, the register
  // scan is read once, unless the visitor has already picked a file.
  const first = files.find((f) => f.doc_type === "detention_register") || files[0];
  const io = new IntersectionObserver(([e]) => {
    if (!e.isIntersecting) return;
    io.disconnect();
    if (!run) open(first, tabs.children[files.indexOf(first)]);
  }, { rootMargin: "0px 0px -25% 0px" });
  io.observe(root);

  let run = 0;
  let marks = [];
  let audioEl = null;
  let chainList = null;

  async function open(f, btn) {
    const me = ++run;
    [...tabs.children].forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    const [title] = DOC[f.doc_type] || [f.name];
    list.replaceChildren();
    fillRecord(f);
    const eye = await showFile(f);
    if (me !== run) return;
    const claims = (f.claims || []).map((c, i) => ({ c, i }));
    // Reading order: down the page, and right to left along a line, as the Arabic is written.
    const placed = claims.filter(({ i }) => marks[i]).sort((a, b) => order(a.c) - order(b.c));
    const rest = claims.filter(({ i }) => !marks[i]);
    if (!claims.length) {
      status.className = "rd-status done";
      status.textContent = f.kind === "image"
        ? "No writing in this photo, so nothing was proposed. It is still fingerprinted and its credentials checked, below."
        : "Nothing was proposed from this file.";
      return;
    }
    status.className = "rd-status busy";
    page.classList.add("reading");
    let n = 0;
    for (const { c, i } of [...placed, ...rest]) {
      if (me !== run) return;
      status.textContent = `Reading the ${title.toLowerCase()}: ${++n} of ${claims.length}`;
      if (marks[i]) {
        page.querySelector(".now")?.classList.remove("now");
        moveEye(eye, marks[i]);
        if (STEP_MS) await sleep(STEP_MS * 0.55);
        if (me !== run) return;
        marks[i].classList.add("seen", "now");
      }
      list.append(item(f, c, i));
      if (STEP_MS) await sleep(STEP_MS * 0.45);
    }
    if (me !== run) return;
    page.classList.remove("reading");
    page.querySelector(".now")?.classList.remove("now");
    eye.classList.add("gone");
    status.className = "rd-status done";
    status.textContent = `${claims.length} ${claims.length === 1 ? "claim" : "claims"} proposed, each pinned to where it was read. Violet is the AI's first pass: none counts until a person accepts it.`;
  }

  const order = (c) => (c.where.bbox ? c.where.bbox[1] * 10 - c.where.bbox[2] / 1000 : c.where.chars ? c.where.chars[0] : c.where.ms ? c.where.ms[0] : 1e9);

  // Puts the file on the page with every claim's region marked but not yet lit. Resolves with
  // the reader once the marks have their positions.
  function showFile(f) {
    marks = [];
    audioEl = null;
    page.classList.remove("reading");
    const frame = h("div", { class: "rd-frame" });
    const eye = h("span", { class: "rd-eye", "aria-hidden": "true" });
    const isWeb = f.media.endsWith(".html");
    const media = isWeb
      // The capture is shown as stored; the padding is on a white box around it, never in its bytes.
      ? h("div", { class: "rd-capture" }, h("iframe", { src: f.media, sandbox: "", title: `${f.name}, rendered without scripts`, loading: "lazy" }))
      : h("img", { src: f.media, alt: f.kind === "audio" ? `Waveform of ${f.name}` : `${f.name}, as shown to the reviewer` });
    frame.append(media, h("span", { class: "rd-sweep", "aria-hidden": "true" }));
    const parts = [frame];
    if (f.audio) {
      audioEl = h("audio", { controls: "", preload: "metadata", src: f.audio });
      parts.push(audioEl);
    }
    const spans = (f.claims || []).map((c, i) => ({ c, i })).filter(({ c }) => c.where.chars);
    const withText = spans.length && f.text?.content;
    page.classList.toggle("with-text", !!withText);
    if (withText) parts.push(textPanel(f, spans));
    parts.push(caption(f));
    page.replaceChildren(...parts, eye);

    const boxes = (f.claims || []).map((c, i) => ({ c, i })).filter(({ c }) => c.where.bbox || c.where.ms);
    const place = () => {
      for (const { c, i } of boxes) {
        const el = h("span", { class: `hl ${tierClass(f, i)}`, "aria-hidden": "true" },
          h("span", { class: "rd-pin", dir: "auto", text: c.value }));
        if (c.where.bbox) {
          const [x0, y0, x1, y1] = c.where.bbox;
          // bbox is in the original file's pixels; the media may be downscaled.
          const [W, H] = f.size || [media.naturalWidth, media.naturalHeight];
          Object.assign(el.style, { left: `${(x0 / W) * 100}%`, top: `${(y0 / H) * 100}%`, width: `${((x1 - x0) / W) * 100}%`, height: `${((y1 - y0) / H) * 100}%` });
        } else {
          const dur = f.duration_ms || (audioEl && isFinite(audioEl.duration) ? audioEl.duration * 1000 : 0);
          if (!dur) continue;
          const [a, b] = c.where.ms;
          Object.assign(el.style, { left: `${(a / dur) * 100}%`, width: `${((b - a) / dur) * 100}%`, top: "4%", height: "92%" });
        }
        frame.append(el);
        marks[i] = el;
      }
    };
    return new Promise((resolve) => {
      const done = () => { place(); resolve(eye); };
      if (boxes.some(({ c }) => c.where.ms) && !f.duration_ms && audioEl) audioEl.addEventListener("loadedmetadata", done, { once: true });
      else if (media.tagName === "IMG" && !media.complete) media.addEventListener("load", done, { once: true });
      else done();
    });
  }

  function textPanel(f, spans) {
    const t = f.text.content;
    const body = h("div", { class: "text-body rd-text", dir: f.text.dir || "auto", lang: f.text.lang || "" });
    let pos = 0;
    for (const { c, i } of spans.sort((a, b) => a.c.where.chars[0] - b.c.where.chars[0])) {
      const [a, b] = c.where.chars;
      if (a < pos) continue;
      body.append(t.slice(pos, a));
      marks[i] = h("mark", { class: `span-claim ${tierClass(f, i)}`, text: t.slice(a, b) });
      body.append(marks[i]);
      pos = b;
    }
    body.append(t.slice(pos));
    return h("div", {}, h("p", { class: "rd-label", text: f.kind === "pdf" ? "Text read from the page" : "Text read from the capture" }), body);
  }

  function caption(f) {
    const cap = h("p", { class: "cap" });
    if (f.kind === "audio") cap.append("The marked stretch is where the claim was heard. Press play, or click the claim's time.");
    if (f.credit) {
      const c = f.credit;
      cap.append(`A real photograph, not part of the synthetic case: “${c.title}” (${c.date.slice(0, 10)}) by ${c.author}, `,
        h("a", { href: c.licence_url, rel: "license", text: c.licence }), ", via ", h("a", { href: c.source, text: "Wikimedia Commons" }),
        `. ${c.description} Downscaled here; the content credentials are a test manifest added for this demonstration.`);
    }
    return cap;
  }

  // The reader glides to a mark, measured against the page, so it works for page regions,
  // stretches of audio and words in the text alike.
  function moveEye(eye, el) {
    const tb = el.closest(".rd-text");
    if (tb) tb.scrollTop = el.offsetTop - tb.clientHeight / 2;
    const p = page.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    eye.classList.remove("gone");
    Object.assign(eye.style, { left: `${r.left - p.left - 4}px`, top: `${r.top - p.top - 4}px`, width: `${r.width + 8}px`, height: `${r.height + 8}px` });
  }

  const tierClass = (f, i) => (accepted.has(`${f.id}:${i}`) ? "tier-accepted" : "tier-unconfirmed");

  function item(f, c, i) {
    const key = `${f.id}:${i}`;
    const val = h("bdi", { class: "rd-val", dir: "auto", text: c.value });
    if (AR.test(c.value)) val.lang = "ar";
    const where = h("span", { class: "where mono", text: whereText(c.where) });
    const btn = h("button", { type: "button", class: "quiet", text: accepted.has(key) ? "Accepted" : "Accept" });
    btn.disabled = accepted.has(key);
    const li = h("li", { class: accepted.has(key) ? "ok" : "" },
      h("span", { class: "rd-what" }, h("small", { text: [label(c.prop), person(c)].filter(Boolean).join(" · ") }), val, where), btn);
    btn.addEventListener("click", () => accept(f, i, c, li, btn));
    const focus = (on) => marks[i]?.classList.toggle("focus", on);
    li.addEventListener("mouseenter", () => focus(true));
    li.addEventListener("mouseleave", () => focus(false));
    btn.addEventListener("focus", () => focus(true));
    btn.addEventListener("blur", () => focus(false));
    if (c.where.ms) where.addEventListener("click", () => { if (audioEl) { audioEl.currentTime = c.where.ms[0] / 1000; audioEl.play(); } });
    return li;
  }

  function fillRecord(f) {
    const kv = (pairs) => {
      const dl = h("dl", { class: "kv" });
      for (const [k, v, mono] of pairs) {
        if (v == null || v === "") continue;
        dl.append(h("dt", { text: k }), h("dd", mono ? { class: "mono" } : {}, h("bdi", { text: String(v) })));
      }
      return dl;
    };
    const block = (title, ...body) => h("section", {}, h("h4", { text: title }), ...body);
    const states = { valid: "Valid credentials", untrusted: "Credentials present, signer not trusted", absent: "No content credentials", not_applicable: "Not applicable to this format" };
    const name = (id) => data.files.find((x) => x.id === id)?.name || id;
    const dup = (data.duplicates || []).filter((d) => d.a === f.id || d.b === f.id);
    chainList = h("ol", { class: "rd-chain" }, h("li", { text: "Arrival recorded with its SHA-256." }));
    for (const key of accepted) {
      if (key.startsWith(`${f.id}:`)) chainList.append(h("li", { text: `Accepted ${label(f.claims[+key.split(":")[1]].prop).toLowerCase()} by a reviewer.` }));
    }
    const blocks = [
      block("Fixity", kv([["file", f.name, 1], ["SHA-256", f.sha256, 1], ["MD5", f.md5, 1], ["size", f.bytes != null ? `${f.bytes.toLocaleString("en-US")} bytes` : null, 1]])),
      f.acquired && block("Acquisition", kv([["by", f.acquired.by], ["at", f.acquired.at, 1], ["how", f.acquired.how], ["origin", f.acquired.origin, 1]])),
      f.c2pa && block("Content credentials", h("p", { class: `verdict-${f.c2pa.state}`, text: states[f.c2pa.state] || f.c2pa.state }), f.c2pa.note ? h("p", { class: "note", text: f.c2pa.note }) : ""),
      block("Near duplicates", dup.length
        ? h("ul", { class: "rd-chain" }, ...dup.map((d) => h("li", {}, `${name(d.a === f.id ? d.b : d.a)}: ${d.method === "pdq" ? "picture" : "sound"} distance ${d.distance} of ${d.of}${d.offset_ms != null ? `, offset ${fmtMs(Math.abs(d.offset_ms))}` : ""}, ${d.verdict}`)))
        : h("p", { class: "note", text: "No near duplicate among the case files." })),
      block("Custody chain", chainList),
    ];
    if (f.text?.content && !(f.claims || []).some((c) => c.where.chars)) {
      blocks.push(block("Text", h("div", { class: "text-body", dir: f.text.dir || "auto", lang: f.text.lang || "", text: f.text.content }),
        f.text.source ? h("p", { class: "note", text: `Text source: ${f.text.source}.` }) : ""));
    }
    recBody.replaceChildren(...blocks.filter(Boolean));
  }

  async function accept(f, i, c, li, btn) {
    const key = `${f.id}:${i}`;
    if (accepted.has(key)) return;
    accepted.add(key);
    btn.disabled = true;
    btn.textContent = "Accepted";
    li.classList.add("ok");
    marks[i]?.classList.replace("tier-unconfirmed", "tier-accepted");
    page.querySelector(".stamp")?.remove();
    page.append(h("div", { class: "stamp", "aria-hidden": "true" }, h("span", {}, "accepted", h("br"), "signed")));
    chainList?.append(h("li", { text: `Accepted ${label(c.prop).toLowerCase()} by a reviewer.` }));
    await sealbook.chain.append(root, {
      title: "A person accepted a claim",
      signer: "reviewer (you)",
      text: `Accepted ${c.prop.replace(/_/g, " ")} "${c.value}" for ${c.person || "the record"} from ${f.name} at ${whereText(c.where)}. Every link below was re-hashed and re-signed.`,
    });
  }
});
