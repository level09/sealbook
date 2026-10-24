// Chapter 7: a real Merkle root over this page's links, and the tamper moment.
sealbook.onReady(() => {
  const root = sealbook.mount("proof");
  const h = sealbook.h;
  const chain = sealbook.chain;
  const word = document.querySelector("#opening .tamper-word");
  const FROM = word.textContent, TO = "Machines";

  const rootEl = h("p", { class: "root mono" });
  const leaves = h("ul", { class: "leaves", "aria-hidden": "true" });
  const count = h("p", { class: "note" });
  const live = h("p", { class: "live", "aria-live": "polite" });
  const edit = h("button", { type: "button", class: "quiet solid", text: `Change "${FROM}" to "${TO}" in link 0` });
  const restore = h("button", { type: "button", class: "quiet", text: "Restore" });
  restore.disabled = true;

  root.append(h("div", { class: "proof-grid" },
    h("div", { class: "panel" }, h("h3", { text: "Merkle root of this page" }), rootEl, count, leaves,
      h("p", { class: "note", text: "Each square is one link's hash. Pairs are hashed together, level by level, until one root is left." })),
    h("div", { class: "panel" }, h("h3", { text: "Change one word" }),
      h("p", { class: "note", style: "font-size:14px", text: "Edit the opening link without re-signing it, the way a quiet change to a record would happen. Then scroll back up." }),
      h("div", { class: "actions" }, edit, restore), live)));

  async function paint() {
    const r = await chain.merkleRoot();
    rootEl.textContent = r;
    count.textContent = `${chain.links.length} links. ${chain.links.filter((l) => !l.ok).length} do not match their signed value.`;
    leaves.replaceChildren(...chain.links.map((l) => h("li", { class: l.ok ? "" : "bad" })));
  }
  chain.onChange(paint);
  chain.ready.then(paint);

  edit.addEventListener("click", async () => {
    word.textContent = TO;
    await chain.tamper(0, FROM, TO);
    const bad = chain.links.filter((l) => !l.ok).length;
    live.className = "live bad";
    live.textContent = `Link 0 changed. Its hash no longer matches what was signed, and because every later link includes the one before it, all ${bad} links from there down fail. The root changed too.`;
    edit.disabled = true;
    restore.disabled = false;
    restore.focus();
  });
  restore.addEventListener("click", async () => {
    word.textContent = FROM;
    await chain.restore();
    live.className = "live";
    live.textContent = "Restored. Every link matches its signed value again, and the root is back.";
    edit.disabled = false;
    restore.disabled = true;
    edit.focus();
  });
});
