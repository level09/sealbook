// The case film: one drawn play control on the poster, then the browser's own.
// The storyboard's stills seek the film to their moment, and the still for the moment
// playing is marked. A start comes from the figure's data-start or, on the home page,
// from a #t=<seconds> link (a case page's "Watch this case in the film"). Playing waits
// for a tap, so phones play inline and with sound.
(() => {
  const fig = document.querySelector(".walkthrough");
  if (!fig) return;
  const video = fig.querySelector("video");
  const play = fig.querySelector(".wt-play");
  const stills = [...document.querySelectorAll(".board button")];
  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let start = Number(fig.dataset.start || 0);
  const at = (t) => {
    fig.classList.add("playing");
    video.controls = true;
    // Phones play only inside the tap itself, so play() is called now and the seek
    // waits for the film's length to be known.
    if (video.readyState >= 1) video.currentTime = t;
    else if (t) video.addEventListener("loadedmetadata", () => { video.currentTime = t; }, { once: true });
    video.play().catch((e) => { if (e.name !== "AbortError") failed(`play refused, ${e.name}: ${e.message}`); });
    fig.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
  };
  const fromHash = () => {
    const m = /^#t=(\d+(?:\.\d+)?)$/.exec(location.hash);
    if (!m) return;
    start = Number(m[1]);
    fig.scrollIntoView({ block: "center" });
  };
  fromHash();
  addEventListener("hashchange", fromHash);
  if (start) play.querySelector(".wt-len").textContent = `from ${clock(start)}`;
  play.addEventListener("click", () => at(start));
  // If the page cannot play it, say why and offer the film on its own, which plays
  // in the browser's own player.
  const failed = (why) => {
    const cap = fig.querySelector("figcaption");
    if (cap.querySelector(".wt-fail")) return;
    const p = document.createElement("p");
    p.className = "wt-fail";
    p.append(`The film could not play on this page (${why}). `);
    const a = document.createElement("a");
    a.href = video.currentSrc || video.src;
    a.textContent = "Open the film on its own";
    p.append(a, ".");
    cap.prepend(p);
  };
  video.addEventListener("error", () => failed(`error ${video.error?.code}: ${video.error?.message || "no message"}, network state ${video.networkState}`));
  // Captions are drawn over the film from its WebVTT file, not with a <track>: an
  // iPhone refuses to play this film inline at all when it has one (docs/lessons.md).
  const cc = fig.querySelector(".wt-cc"), ccBtn = fig.querySelector(".wt-cc-btn");
  let cues = null, ccOn = true;
  const stamp = (v) => v.split(":").reduce((a, n) => a * 60 + Number(n), 0);
  const showCue = () => {
    const now = video.currentTime;
    const cue = ccOn && cues?.find((c) => c.start <= now && now < c.end);
    cc.hidden = !cue;
    if (cue && cc.textContent !== cue.text) cc.textContent = cue.text;
  };
  video.addEventListener("play", () => {
    ccBtn.hidden = false;
    if (cues) return;
    cues = [];
    fetch(video.dataset.captions).then((r) => r.text()).then((text) => {
      for (const block of text.split(/\r?\n\r?\n/)) {
        const m = /([\d:.]+) --> ([\d:.]+)[^\n]*\n([\s\S]+)/.exec(block);
        if (m) cues.push({ start: stamp(m[1]), end: stamp(m[2]), text: m[3].trim() });
      }
      showCue();
    }).catch(() => {});
  });
  video.addEventListener("timeupdate", showCue);
  ccBtn.addEventListener("click", () => {
    ccOn = !ccOn;
    ccBtn.setAttribute("aria-pressed", String(ccOn));
    ccBtn.textContent = ccOn ? "Captions on" : "Captions off";
    showCue();
  });
  stills.forEach((b) => b.addEventListener("click", () => at(Number(b.dataset.seek))));
  video.addEventListener("timeupdate", () => {
    const now = video.currentTime;
    let on = -1;
    stills.forEach((b, i) => { if (Number(b.dataset.seek) <= now) on = i; });
    stills.forEach((b, i) => b.parentElement.classList.toggle("on", i === on));
  });
})();
