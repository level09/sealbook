// Speech: what local speech to text heard in the witness clip, line by line, following the player.
(() => {
  const { h } = sealbook;
  const clock = (ms) => {
    const s = ms / 1000;
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${(s % 60).toFixed(1).padStart(4, "0")}`;
  };
  const overlap = (a, b) => a[0] < b[1] && b[0] < a[1];
  // The same folding the WER uses, so a word marked here also counts against the WER.
  const fold = (s) => s.replace(/[ً-ْٰـ]/g, "").replace(/[أإآ]/g, "ا")
    .replace(/ى/g, "ي").replace(/ة/g, "ه").replace(/[^\p{L}\p{N}]/gu, "");

  // Edit distance on folded words; a script word within two edits is the word that was said.
  const dist = (a, b) => {
    let row = [...Array(b.length + 1).keys()];
    for (let i = 1; i <= a.length; i++) {
      const next = [i];
      for (let j = 1; j <= b.length; j++) next[j] = Math.min(row[j] + 1, next[j - 1] + 1, row[j - 1] + (a[i - 1] !== b[j - 1]));
      row = next;
    }
    return row[b.length];
  };
  // Ties go to the script word nearest the heard word's position in the line.
  const nearest = (heard, scriptWords, at) => {
    let best = null, bestScore = 3;
    scriptWords.forEach((s, i) => {
      const d = dist(heard, fold(s));
      const score = d + Math.abs(i / scriptWords.length - at) / 10;
      if (d <= 2 && score < bestScore) { best = s; bestScore = score; }
    });
    return best;
  };

  sealbook.onReady((data) => {
    const root = sealbook.mount("speech");
    const sp = data.speech;
    if (!root || !sp) return;
    const audio = h("audio", { controls: "", preload: "metadata", src: sp.audio });
    const lines = [], words = [];

    // The heard line that overlaps the claim's stretch most.
    const shared = (a, b) => Math.max(0, Math.min(a[1], b[1]) - Math.max(a[0], b[0]));
    const claimSeg = sp.segments.reduce((best, seg) => (shared(seg.ms, sp.seen_at.ms) > shared(best.ms, sp.seen_at.ms) ? seg : best));
    const list = h("ol", { class: "sp-lines", "aria-label": "Transcript lines, with times" });
    for (const seg of sp.segments) {
      const said = h("p", { class: "sp-said", dir: "rtl", lang: "ar" });
      const script = sp.script.filter((s) => overlap(s.ms, seg.ms));
      // Corrections come from the script sentence that starts in this line, when there is one.
      const own = script.filter((s) => s.ms[0] >= seg.ms[0] - 1500);
      const scriptWords = (own.length ? own : script).flatMap((s) => s.text.split(/\s+/)).map((w) => w.replace(/[^\p{L}\p{N}]/gu, "")).filter(Boolean);
      const expected = new Set(scriptWords.map(fold));
      for (const [k, w] of seg.words.entries()) {
        // A misheard word shows the script's spelling when one is close, marked as a correction, with the model's own text kept in the tooltip.
        const off = expected.size && fold(w.w) && !expected.has(fold(w.w));
        const fix = off && nearest(fold(w.w), scriptWords, k / seg.words.length);
        const span = h("span", !off ? { text: w.w }
          : fix ? { class: "sp-fixed", title: `Corrected against the script. The model wrote: ${w.w}`, text: fix }
          : { class: "sp-off", title: "Not in the script for this line: a transcription error", text: w.w });
        words.push([w.ms, span]);
        said.append(span, " ");
      }
      const claim = seg === claimSeg;
      const btn = h("button", { type: "button", class: "sp-time mono", "aria-label": `Play from ${clock(seg.ms[0])}` }, `${clock(seg.ms[0])} to ${clock(seg.ms[1])}`);
      btn.addEventListener("click", () => { audio.currentTime = seg.ms[0] / 1000; audio.play().catch(() => {}); });
      const li = h("li", { class: `sp-line${claim ? " sp-claim" : ""}` }, btn, said,
        h("details", { class: "sp-script" }, h("summary", { text: "Script" }),
          ...script.map((s) => h("p", { dir: "rtl", lang: "ar" }, h("span", { class: "mono", dir: "ltr", text: `${clock(s.ms[0])} ` }), s.text))),
        claim ? h("p", { class: "note" }, "The seen at claim in the case is pinned to this stretch, spoken as ",
          h("bdi", { dir: "rtl", lang: "ar", text: sp.seen_at.spoken_as }), ".") : "");
      lines.push([seg.ms, li]);
      list.append(li);
    }

    // Follow the player: the current line and word are marked; nothing moves.
    let last = null;
    audio.addEventListener("timeupdate", () => {
      const t = audio.currentTime * 1000;
      for (const [ms, li] of lines) li.classList.toggle("now", t >= ms[0] && t < ms[1]);
      const cur = words.find(([ms]) => t >= ms[0] && t < ms[1])?.[1] || null;
      if (cur !== last) { last?.classList.remove("now"); cur?.classList.add("now"); last = cur; }
    });

    const m = sp.model;
    root.append(
      h("div", { class: "sp-grid" },
        h("div", { class: "sp-main" }, h("p", { class: "sp-file mono", text: sp.file }), audio, list),
        h("aside", { class: "sp-facts" },
          h("p", { class: "sp-wer" }, h("span", { class: "mono", text: sp.wer.toFixed(2) }), " word error"),
          h("p", { class: "note", text: `About ${Math.round(sp.wer * sp.words)} of ${sp.words} words differ from the script after folding diacritics, punctuation, alef, ya and ta marbuta, the same folding the app's live test uses. Where the model misheard a word and the script has it within two letters, the script's spelling is shown with a dotted underline and the model's own text in its tooltip (the model wrote دومة for دوما). Words with no close match keep a wavy underline.` }),
          h("dl", { class: "kv" },
            h("dt", { text: "model" }), h("dd", { class: "mono", text: `${m.repo}` }),
            h("dt", { text: "revision" }), h("dd", { class: "mono", text: m.revision.slice(0, 12), title: m.revision }),
            h("dt", { text: "runtime" }), h("dd", { text: m.runtime }),
            h("dt", { text: "ran on" }), h("dd", { text: `${sp.hardware}, ${sp.device.device} ${sp.device.compute_type}` }),
            h("dt", { text: "language" }), h("dd", { text: `${sp.language.language}, ${(+sp.language.probability).toFixed(3)}` }),
            h("dt", { text: "decoding" }), h("dd", { text: `${sp.decode_s} s for ${(sp.duration_ms / 1000).toFixed(1)} s of audio, real-time factor ${sp.rtf}` })),
          h("p", { class: "note", text: "Measured during this site build on one machine; timing moves with load. The script times and the heard times differ by up to a second, and two script sentences can be heard as one line." }))));
  });
})();
