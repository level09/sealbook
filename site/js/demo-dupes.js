// Chapter 3: picture fingerprints and audio landmarks, every match proposed until a person decides.
(() => {
  const THRESHOLD = 31; // the app's PDQ match threshold
  const { h } = sealbook;

  function decision(subject) {
    const state = h("p", { class: "dd-state", "aria-live": "polite", text: "Proposed. Nothing is linked until a person confirms." });
    const wrap = h("div", { class: "dd-decide" });
    const pick = (linked) => {
      wrap.dataset.state = linked ? "confirmed" : "rejected";
      state.textContent = linked
        ? `Confirmed by you: ${subject} linked as the same media.`
        : `Rejected by you: ${subject} stays separate.`;
      yes.setAttribute("aria-pressed", String(linked));
      no.setAttribute("aria-pressed", String(!linked));
    };
    const yes = h("button", { class: "quiet", type: "button", "aria-pressed": "false", text: "Confirm" });
    const no = h("button", { class: "quiet", type: "button", "aria-pressed": "false", text: "Not the same" });
    yes.addEventListener("click", () => pick(true));
    no.addEventListener("click", () => pick(false));
    wrap.append(h("div", { class: "dd-btns" }, yes, no), state);
    return wrap;
  }

  sealbook.onReady((data) => {
    const root = sealbook.mount("dupes");
    if (!root) return;
    const files = Object.fromEntries(data.files.map((f) => [f.id, f]));
    const derived = Object.fromEntries((data.derived || []).map((d) => [d.id, d]));
    const pics = data.duplicates.filter((d) => d.method === "pdq").sort((a, b) => a.distance - b.distance);
    const audio = data.duplicates.find((d) => d.method === "audio");
    const orig = data.photos[pics[0]?.a];
    const cr = orig.credit;

    // Pictures
    const list = h("ol", { class: "dd-list" });
    for (const p of pics) {
      const c = derived[p.b];
      const matched = p.distance <= THRESHOLD;
      const bar = h("div", { class: "dd-scale", role: "img",
        "aria-label": `distance ${p.distance} of ${p.of}, match threshold ${THRESHOLD}` },
        h("span", { class: "dd-thr", style: `left:${(THRESHOLD / p.of) * 100}%` }),
        h("span", { class: `dd-dot ${matched ? "tier-unconfirmed" : "off"}`, style: `left:${(p.distance / p.of) * 100}%` }));
      list.append(h("li", { class: `dd-copy${matched ? " is-match" : ""}` },
        h("img", { src: c.media, alt: `Copy: ${c.how}`, loading: "lazy", width: "160", height: "107" }),
        h("div", { class: "dd-body" },
          h("p", { class: "dd-how", text: c.how }),
          h("p", { class: "dd-num" }, h("span", { class: "mono", text: `${p.distance} / ${p.of}` }),
            h("span", { class: matched ? "chip tier-unconfirmed" : "chip dd-miss", text: matched ? "proposed match" : "not matched" })),
          bar,
          matched ? decision("this copy and the original") : h("p", { class: "dd-why", text: "Above the threshold. The crop shifts every block of the picture, so the fingerprint changes." }))));
    }

    const pictures = h("div", { class: "dd-pics" },
      h("figure", { class: "dd-orig" },
        h("img", { src: orig.media, alt: "Photograph of a damaged building facade in Aleppo, the original used for the copies", width: "1024", height: "768" }),
        h("figcaption", {}, h("strong", { text: "Original. " }), h("span", { class: "mono", text: orig.name }),
          h("span", { class: "dd-credit" }, `A real photograph of Aleppo, ${cr.date.slice(0, 10)}. Commons description: “${cr.description}”. ${cr.author}, `,
            h("a", { href: cr.licence_url, rel: "license", text: cr.licence }), ", via ", h("a", { href: cr.source, text: "Wikimedia Commons" }),
            ". It is not part of the synthetic case. The copies below were made for this demonstration."))),
      h("div", {},
        h("p", { class: "dd-lede" },
          `PDQ distance between each copy and the original, on a 0 to 256 scale. The app proposes a match at ${THRESHOLD} or below. `,
          h("strong", { text: "Picture fingerprints do not catch crops." })),
        list));

    root.append(pictures);

    // Audio
    if (audio) {
      const full = files[audio.a], ex = derived[audio.b];
      const len = (+ex.how.match(/(\d+)-(\d+) ms/)[2]) - audio.cut_at_ms;
      const pct = (ms) => `${(ms / full.duration_ms) * 100}%`;
      const slot = h("div", { class: "dd-ex", style: `left:${pct(audio.offset_ms)};width:${pct(len)}` },
        h("img", { src: ex.wave, alt: "" }));
      const track = h("div", { class: "dd-track", role: "img",
        "aria-label": `Excerpt aligned at ${audio.offset_ms} ms on the ${(full.duration_ms / 1000).toFixed(1)} second clip` },
        h("img", { src: full.media, alt: "" }), slot);
      const player = (src, label) => {
        const a = new Audio(src); a.preload = "none";
        const b = h("button", { class: "quiet", type: "button", "aria-pressed": "false", text: `Play ${label}` });
        b.addEventListener("click", () => (a.paused ? a.play() : a.pause()));
        a.addEventListener("play", () => { b.textContent = `Pause ${label}`; b.setAttribute("aria-pressed", "true"); });
        a.addEventListener("pause", () => { b.textContent = `Play ${label}`; b.setAttribute("aria-pressed", "false"); });
        return b;
      };
      root.append(h("div", { class: "dd-audio" },
        h("p", { class: "dd-lede" },
          `An ${(len / 1000).toFixed(1)} second excerpt, re-encoded at a lower bitrate, found inside the witness clip by sound landmarks. `,
          `${audio.aligned_landmarks} of ${audio.shared_landmarks} shared landmarks line up at one offset.`),
        track,
        h("dl", { class: "dd-facts" },
          h("div", {}, h("dt", { text: "recovered offset" }), h("dd", { class: "mono", text: `${audio.offset_ms} ms` })),
          h("div", {}, h("dt", { text: "where it was cut" }), h("dd", { class: "mono", text: `${audio.cut_at_ms} ms` })),
          h("div", {}, h("dt", { text: "aligned landmarks" }), h("dd", { class: "mono", text: `${audio.aligned_landmarks}` }))),
        h("div", { class: "dd-btns" }, player(full.audio, "witness clip"), player(ex.media, "excerpt")),
        decision("the excerpt and the witness clip")));
    }

    root.append(h("p", { class: "dd-note", text: "Demonstration: your choice changes this page only. In the app, the decision is signed and logged." }));
  });
})();
