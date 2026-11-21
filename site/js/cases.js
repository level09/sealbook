// The case pages (no site.js): the sticky header's rule and the theme toggle.
(() => {
  const head = document.querySelector(".top");
  const stuck = () => head.classList.toggle("stuck", scrollY > 4);
  addEventListener("scroll", stuck, { passive: true });
  stuck();
  const root = document.documentElement;
  const toggle = document.getElementById("theme");
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
