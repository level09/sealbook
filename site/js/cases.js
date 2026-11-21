// The case pages and the cases section of the home page.
// The case film slot stays hidden until its file exists, so a missing film never shows
// a broken player. On a case page (no site.js) this also runs the theme toggle.
(() => {
  for (const fig of document.querySelectorAll(".case-film[hidden]")) {
    const video = fig.querySelector("video");
    fetch(video.dataset.src, { method: "HEAD" })
      .then((r) => {
        if (!r.ok) return;
        video.poster = video.dataset.poster;
        video.src = video.dataset.src;
        fig.hidden = false;
      })
      .catch(() => {});
  }

  if (window.sealbook) return;
  const head = document.querySelector(".top");
  const stuck = () => head.classList.toggle("stuck", scrollY > 4);
  addEventListener("scroll", stuck, { passive: true });
  stuck();
  const root = document.documentElement;
  const toggle = document.getElementById("theme");
  if (!toggle) return;
  const label = () => {
    const dark = root.dataset.theme === "dark";
    toggle.setAttribute("aria-pressed", String(dark));
    toggle.querySelector("span").textContent = dark ? "Paper" : "Dark";
  };
  toggle.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("sealbook-theme", root.dataset.theme); } catch {}
    label();
  });
  label();
})();
