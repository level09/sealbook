// Opening: the reference photo becomes its picture fingerprint and is sealed.
sealbook.onReady((data) => {
  const fig = document.getElementById("hero");
  const photo = data.photos?.street;
  if (!fig || !photo) return;
  const h = sealbook.h;
  // ponytail: PDQ of media/street_photo.jpg, computed once with the pdqhash package; move into case.json if the photo changes
  const PDQ = "6d37458259c3b489ac3c4f4d13639b6c48b74ddcb63a01e36279b36ed981dc91";
  const bits = [...PDQ].flatMap((x) => [...parseInt(x, 16).toString(2).padStart(4, "0")].map(Number));
  const reduced = sealbook.reduced;
  const wait = (ms) => new Promise((r) => setTimeout(r, reduced ? 0 : ms));

  const img = h("img", { src: photo.media, alt: "" });
  const grid = h("div", { class: "hero-grid" });
  const cells = bits.map(() => grid.appendChild(h("i")));
  const stage = h("div", { class: "hero-stage", role: "img",
    "aria-label": `${photo.credit.title}, a photograph from the case, turning into its 256-bit picture fingerprint.` }, img, grid);
  const sha = h("dd", { class: "mono" }), pdq = h("dd", { class: "mono v" });
  const seal = h("p", { class: "hero-seal mono" }, h("b"), "fingerprinted on arrival, never overwritten");
  const c = photo.credit;
  fig.append(stage,
    h("dl", { class: "hero-read", "aria-hidden": "true" }, h("dt", { text: "sha-256" }), sha, h("dt", { text: "picture" }), pdq),
    seal,
    h("figcaption", {}, h("a", { href: c.source, text: c.title }), `, ${c.author}, `, h("a", { href: c.licence_url, text: c.licence }), "."));

  const order = [...cells.keys()].sort(() => Math.random() - 0.5);
  const ink = () => getComputedStyle(document.documentElement).getPropertyValue("--ink").trim();
  const type = async (el, s, ms) => { for (let i = 2; i <= s.length; i += 2) { el.textContent = s.slice(0, i); await wait(ms); } };
  let greys;
  const sample = () => {
    // Mean of each 16 x 16 block: the downsampled picture a fingerprint starts from.
    const cv = h("canvas", { width: 16, height: 16 }), cx = cv.getContext("2d");
    cx.drawImage(img, 0, 0, 16, 16);
    const d = cx.getImageData(0, 0, 16, 16).data;
    greys = cells.map((_, k) => Math.round(d[k * 4] * 0.3 + d[k * 4 + 1] * 0.59 + d[k * 4 + 2] * 0.11));
  };
  const digest = fetch(photo.media).then((r) => r.arrayBuffer()).then((b) => crypto.subtle.digest("SHA-256", b))
    .then((b) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join(""));

  let visible = false, wake = null;
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible && wake) { wake(); wake = null; } }).observe(fig);
  const run = async () => {
    sample();
    const hex = await digest;
    do {
      if (!visible && !reduced) await new Promise((r) => (wake = r));
      stage.className = "hero-stage"; seal.classList.remove("in"); sha.textContent = ""; pdq.textContent = "";
      cells.forEach((cell, k) => (cell.style.backgroundColor = `rgb(${greys[k]},${greys[k]},${greys[k]})`));
      await wait(1800);
      await type(sha, hex, 12);
      await wait(400);
      stage.classList.add("mosaic");
      await wait(1100);
      stage.classList.add("bits");
      for (const [n, k] of order.entries()) {
        cells[k].style.backgroundColor = bits[k] ? ink() : "transparent";
        if (n % 8 === 7) await wait(16);
      }
      await type(pdq, PDQ, 18);
      await wait(400);
      seal.classList.add("in");
      await wait(5000);
    } while (!reduced);
  };
  img.complete ? run() : img.addEventListener("load", run, { once: true });
});
