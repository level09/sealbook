// Chapter: two real clips of one place, frame fingerprints on re-uploaded copies, and frame search.
(() => {
  const { h } = sealbook;
  const sec = (ms) => `${(ms / 1000).toFixed(1)} s`;
  const longDate = (d) => new Date(`${d}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

  // One thumbnail cut from a clip's strip: frame idx of n.
  const thumb = (sprite, n, idx, cls = "vd-thumb") => h("span", { class: cls, "aria-hidden": "true",
    style: `background-image:url(${sprite});background-size:${n * 100}% 100%;background-position:${n > 1 ? (idx / (n - 1)) * 100 : 0}% 0` });

  sealbook.onReady((data) => {
    const root = sealbook.mount("video");
    const v = data.video;
    if (!root || !v) return;
    const clips = Object.fromEntries(v.clips.map((c) => [c.id, c]));
    const players = {};

    function seek(id, ms) {
      const el = players[id];
      el.pause();
      el.currentTime = ms / 1000;
      el.scrollIntoView({ block: "nearest", behavior: sealbook.reduced ? "auto" : "smooth" });
      el.focus({ preventScroll: true });
    }

    // Place timeline and players
    const [a, b] = v.places;
    const days = Math.round((Date.parse(b.date) - Date.parse(a.date)) / 864e5);
    const line = h("ol", { class: "vd-place", "aria-label": `${a.place}: two dated recordings ${days} days apart` });
    const figs = h("div", { class: "vd-players" });
    for (const p of v.places) {
      const c = clips[p.clip];
      const video = h("video", { poster: c.poster, controls: "", preload: "metadata", playsinline: "",
        "aria-label": `${c.place}, ${longDate(c.credit.date)}` });
      video.muted = true;
      // The CDN cache ignores range requests, so a streamed clip cannot seek. Each clip
      // is small; load it whole when the chapter nears view and play it from memory.
      new IntersectionObserver(([e], io) => {
        if (!e.isIntersecting) return;
        io.disconnect();
        fetch(c.media).then((r) => r.blob()).then((b) => { video.src = URL.createObjectURL(b); });
      }, { rootMargin: "800px" }).observe(video);
      players[c.id] = video;
      figs.append(h("figure", { class: "vd-fig" }, video,
        h("figcaption", {}, h("strong", { text: longDate(c.credit.date) }), ` ${c.place}. `,
          h("span", { class: "vd-src" }, `${c.credit.author}, `, h("a", { href: c.credit.licence_url, rel: "license", text: c.credit.licence }), "."))));
      const btn = h("button", { type: "button", class: "vd-point" },
        h("img", { src: p.poster, alt: "", width: "160", height: "90", loading: "lazy" }),
        h("span", { class: "vd-date mono", text: p.date }));
      btn.setAttribute("aria-label", `Go to the ${longDate(p.date)} recording`);
      btn.addEventListener("click", () => seek(c.id, c.poster_s * 1000));
      line.append(h("li", {}, btn));
    }
    line.insertBefore(h("li", { class: "vd-gap", "aria-hidden": "true" }, h("span", { text: `${days} days` })), line.lastChild);
    root.append(h("div", { class: "vd-where" },
      h("p", { class: "vd-lede" }, `Same place, two dates. `, h("bdi", { text: a.place }), ` before and after, ${days} days apart. Nothing links the two recordings except a person's judgment that they show the same streets.`),
      line), figs);

    // Matches on the original's time axis
    const orig = clips[v.copies[0].from];
    // The axis runs one frame past the last sample so the final cell fits.
    const span = orig.duration_ms + 1000;
    const pct = (ms) => `${(ms / span) * 100}%`;
    const cellW = pct(1000);
    const axis = (sprite, frames, place) => {
      const track = h("div", { class: "vd-track" });
      frames.forEach((ms, i) => {
        const at = place(ms, i);
        if (at.ms < 0 || at.ms >= orig.duration_ms) return;
        const cell = at.match
          ? h("button", { type: "button", class: "vd-cell is-match", style: `left:${pct(at.ms)};width:${cellW}`,
              "aria-label": `Copy at ${sec(ms)} matches the original at ${sec(at.orig)}. Show it in the 2015 clip.` })
          : h("span", { class: `vd-cell${at.off ? " is-off" : ""}`, style: `left:${pct(at.ms)};width:${cellW}` });
        cell.append(thumb(sprite, frames.length, i));
        if (at.match) cell.addEventListener("click", () => seek(orig.id, at.orig));
        track.append(cell);
      });
      return h("div", { class: "vd-scroll" }, track);
    };

    const rows = h("ol", { class: "vd-copies" });
    rows.append(h("li", { class: "vd-row vd-ref" },
      h("p", { class: "vd-how" }, h("strong", { text: "Original. " }), `${longDate(orig.credit.date)}, ${orig.frames_ms.length} frames sampled one second apart.`),
      axis(orig.sprite, orig.frames_ms, (ms) => ({ ms }))));
    for (const c of v.copies) {
      const pic = c.picture;
      const pairs = new Map((pic?.sample_pairs || []).map(([q, r]) => [q, r]));
      const shift = pic ? pic.offset_ms : 0;
      const verdict = pic
        ? h("span", { class: "chip tier-unconfirmed", text: `${pic.query_coverage[0]} of ${pic.query_coverage[1]} frames matched` })
        : h("span", { class: "chip vd-miss", text: "not matched by picture" });
      const facts = [];
      if (pic) facts.push(`picture offset ${sec(shift)}, worst matched distance ${pic.max_distance} of ${v.threshold.of}`);
      facts.push(c.sound
        ? `sound offset ${sec(c.sound.offset_ms)}, ${c.sound.aligned_landmarks} of ${c.sound.shared_landmarks} landmarks agree`
        : "sound not matched");
      rows.append(h("li", { class: `vd-row${pic ? " is-match" : ""}` },
        h("p", { class: "vd-how" }, h("span", { text: c.how }), verdict),
        h("p", { class: "vd-facts mono", text: facts.join(". ") }),
        axis(c.sprite, c.frames_ms, (ms) => pairs.has(ms)
          ? { ms: ms + shift, match: true, orig: pairs.get(ms) }
          : { ms: ms + shift, off: true }),
        pic ? "" : h("p", { class: "vd-why", text: "Every block of the picture moves, so no frame lands within the threshold. Its frames are shown at their own times, unmatched." })));
    }
    root.append(h("div", { class: "vd-matches" },
      h("p", { class: "vd-lede" },
        `${v.copies.length} copies made from the 2015 clip, each fingerprinted the way the app does it: a frame each second, a picture fingerprint per frame, a match at distance ${v.threshold.distance} or below. Matched copy frames sit where they fall in the original. `,
        h("strong", { text: "Crops and mirrors are not caught by picture." })),
      rows));

    // Frame search
    const s = v.search;
    if (s) {
      const out = h("ol", { class: "vd-hits", "aria-live": "polite" });
      const qs = h("div", { class: "vd-qs", role: "group", "aria-label": "Prepared questions" });
      const run = (q, btn) => {
        qs.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === btn)));
        out.replaceChildren(...q.results.map((r, i) => {
          const c = clips[r.clip];
          const idx = c.frames_ms.reduce((best, ms, k) => Math.abs(ms - r.ms) < Math.abs(c.frames_ms[best] - r.ms) ? k : best, 0);
          const go = h("button", { type: "button", class: "vd-hit" }, thumb(c.sprite, c.frames_ms.length, idx),
            h("span", {}, h("span", { class: "vd-hit-where", text: `${c.credit.date.slice(0, 4)} clip at ${sec(r.ms)}` }),
              h("span", { class: "mono vd-score", text: `score ${r.score.toFixed(3)}${i ? "" : ", top lead"}` })));
          go.addEventListener("click", () => seek(r.clip, r.ms));
          return h("li", {}, go);
        }));
        const top = q.results[0];
        seek(top.clip, top.ms);
      };
      for (const q of s.queries) {
        const btn = h("button", { type: "button", class: "quiet", "aria-pressed": "false", lang: q.lang, dir: "auto", text: q.text });
        btn.addEventListener("click", () => run(q, btn));
        qs.append(btn);
      }
      root.append(h("div", { class: "vd-search" },
        h("p", { class: "vd-lede" }, `Ask the footage. ${s.frames} frames, one every ${s.every_s} seconds from both clips, sit in the same space as the question (${s.model}, ${s.runtime}). Choosing a question moves the clip to its top frame. Scores are similarities, not probabilities, and the top frame is a lead, not an answer.`),
        qs, out));
    }

    const credits = document.getElementById("video-credits");
    for (const c of v.clips) {
      credits.append(`“${c.credit.title}” by ${c.credit.author}, `, h("a", { href: c.credit.licence_url, rel: "license", text: c.credit.licence }),
        ", via ", h("a", { href: c.credit.source, text: "Wikimedia Commons" }), ". ");
    }
    credits.append("Shown as web copies. The re-uploaded copies above were derived from the 2015 clip for this demonstration. ", v.display_note);
  });
})();
