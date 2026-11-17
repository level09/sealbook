// Motion for the closing-argument home page. Every effect is an entrance or a
// scroll-scrubbed reveal; with reduced motion, or without GSAP, the page is static.
(() => {
  // The agent's film loop plays only while on screen, and not at all under reduced motion.
  const film = document.querySelector(".agent-film video");
  if (film && sealbook.reduced) film.autoplay = false;
  else if (film) new IntersectionObserver(([e]) => { e.isIntersecting ? film.play().catch(() => {}) : film.pause(); }).observe(film);
  if (!window.gsap || sealbook.reduced) return;
  gsap.registerPlugin(ScrollTrigger, SplitText);
  document.documentElement.classList.add("motion");
  const $ = (root, sel) => root.querySelector(sel);

  // The charge reads itself: each word lights up as it scrolls past.
  const charge = SplitText.create(".charge h2", { type: "words" });
  gsap.from(charge.words, {
    opacity: 0.12, stagger: 0.1, ease: "none",
    scrollTrigger: { trigger: ".charge", start: "top 75%", end: "bottom 45%", scrub: true },
  });

  // Each exhibit: its tag swings onto the picture, the old way struck through, the new way rises.
  gsap.utils.toArray(".exhibit, #proof").forEach((ex) => {
    const strike = $(ex, ".strike");
    const tl = gsap.timeline({ scrollTrigger: { trigger: ex, start: "top 70%", once: true } })
      .from($(ex, ".tag") || $(ex, ".ex-num"), { rotation: -40, y: -24, opacity: 0, duration: 0.7, ease: "back.out(2.4)" })
      .from($(ex, "h2"), { y: 26, opacity: 0, duration: 0.9, ease: "expo.out" }, "<0.1");
    if (strike) {
      tl.from($(ex, ".then"), { opacity: 0, duration: 0.4 }, "-=0.5")
        .to(strike, { "--strike": "100%", duration: 0.75, ease: "power2.inOut" })
        .from($(ex, ".now"), { y: 16, opacity: 0, duration: 0.8, ease: "expo.out" }, "-=0.25");
    }
    const link = document.querySelector(`.rail a[href="#${ex.id}"]`);
    ScrollTrigger.create({
      trigger: ex, start: "top center", end: "bottom center",
      onToggle: (self) => link.classList.toggle("on", self.isActive),
    });
  });

  // The mic drop: each letter falls into place.
  const drop = SplitText.create(".mic h2", { type: "words,chars" });
  gsap.from(drop.chars, {
    yPercent: -140, opacity: 0, stagger: 0.022, duration: 0.9, ease: "bounce.out",
    scrollTrigger: { trigger: ".mic", start: "top 65%", once: true },
  });

  // The verdict, one sentence at a time.
  gsap.from(".verdict h2 span", {
    opacity: 0.12, stagger: 0.5, ease: "none",
    scrollTrigger: { trigger: ".verdict", start: "top 75%", end: "center 50%", scrub: true },
  });

  // Unfolding a demo changes the page height; triggers below it must move with it.
  document.querySelectorAll(".unfold").forEach((b) => b.addEventListener("click", () => requestAnimationFrame(() => ScrollTrigger.refresh())));
})();
