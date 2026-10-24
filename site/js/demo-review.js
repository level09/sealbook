// Chapter 6: a scripted review of one real correction from case.json, ending in a signed accept and a signed undo.
(() => {
  const { h } = sealbook;
  const ANALYST = "analyst-1", COLLEAGUE = "analyst-2";
  const HOLD_MS = 1200;

  sealbook.onReady((data) => {
    const root = sealbook.mount("review");
    if (!root) return;
    const section = root.closest("section");
    const file = data.files.find((f) => f.id === "transfer");
    const claim = file.claims.find((c) => c.prop === "birth_date");
    const [a, b] = claim.where.chars;
    const text = file.text.content;
    const before = claim.extracted_as, after = claim.value;
    const line = text.slice(text.lastIndexOf("\n", a) + 1, text.indexOf("\n", b));

    const value = h("bdi", { class: "mono", text: before });
    const chip = h("span", { class: "chip tier-unconfirmed rv-chip" }, h("span", { text: "birth date: " }), value);
    const status = h("p", { class: "rv-status", role: "status", text: `Current value as extracted. Unconfirmed.` });
    const log = h("ol", { class: "rv-log" });
    const steps = h("div", { class: "rv-steps" });
    const note = (who, t) => log.append(h("li", {}, h("span", { class: "mono", text: who }), " ", t));

    const ask = h("button", { class: "quiet", type: "button", text: `Ask ${COLLEAGUE} to review` });
    root.append(
      h("p", { class: "rv-label mono", text: "Demonstration with case data" }),
      h("div", { class: "card rv-claim" },
        h("p", { class: "rv-file" }, h("span", { class: "mono", text: file.name }), `, characters ${a} to ${b}`),
        chip,
        h("dl", { class: "kv rv-diff" },
          h("dt", { text: "current" }), h("dd", {}, h("bdi", { class: "mono", text: before }), " as extracted, day first"),
          h("dt", { text: "proposed" }), h("dd", {}, h("bdi", { class: "mono", text: after }), ` by ${ANALYST}, same span`)),
        h("p", { class: "rv-span", dir: "rtl", lang: "ar" },
          line.slice(0, a - (text.lastIndexOf("\n", a) + 1)), h("mark", { class: "tier-unconfirmed", dir: "auto", text: text.slice(a, b) }),
          line.slice(b - (text.lastIndexOf("\n", a) + 1))),
        status),
      steps, h("h3", { class: "rv-h", text: "Signed statements" }), log);
    steps.append(ask);

    ask.addEventListener("click", () => {
      ask.remove();
      note(ANALYST, `proposed ${after} for birth date and asked ${COLLEAGUE} to review.`);
      status.textContent = `Correction proposed. Waiting for ${COLLEAGUE}. The earlier value is kept as its predecessor.`;
      const thread = h("div", { class: "card rv-thread" },
        h("p", { class: "rv-private", text: `Private discussion. Visible only to ${ANALYST} and ${COLLEAGUE}; kept out of general views, history and exports.` }),
        h("ul", { class: "rv-msgs" },
          h("li", {}, h("span", { class: "mono", text: ANALYST }), ` The order writes the date day first. Read that way it is 14 March 1991, and the register gives 1991.`),
          h("li", {}, h("span", { class: "mono", text: COLLEAGUE }), " Same line names the mother as ", h("bdi", { lang: "ar", text: "فاطمة" }), ", as the register does for P1. I will accept.")));
      const hold = holdButton(accept);
      steps.append(thread, hold);
    });

    function holdButton(done) {
      const btn = h("button", { class: "quiet rv-hold", type: "button", "aria-describedby": "rv-hold-hint" },
        h("span", { class: "rv-fill", "aria-hidden": "true" }), h("span", { class: "rv-hold-t", text: `Accept as ${COLLEAGUE}: press and hold` }));
      const hint = h("p", { class: "note", id: "rv-hold-hint", text: "Stands for the passkey gesture. Hold the button, or hold Space or Enter, until it fills." });
      let timer = null, t0 = 0;
      const fill = btn.querySelector(".rv-fill");
      const start = () => {
        if (timer || btn.disabled) return;
        t0 = performance.now();
        fill.style.transitionDuration = sealbook.reduced ? "0s" : `${HOLD_MS}ms`;
        fill.style.transform = "scaleX(1)";
        timer = setTimeout(() => { timer = null; btn.disabled = true; done(); }, HOLD_MS);
      };
      const stop = () => {
        if (!timer) return;
        clearTimeout(timer); timer = null;
        fill.style.transitionDuration = "0s";
        fill.style.transform = "scaleX(0)";
        if (performance.now() - t0 < HOLD_MS) hint.textContent = "Released early. Hold until it fills to sign.";
      };
      btn.addEventListener("pointerdown", (e) => { btn.setPointerCapture?.(e.pointerId); start(); });
      for (const ev of ["pointerup", "pointercancel", "pointerleave"]) btn.addEventListener(ev, stop);
      btn.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); if (!e.repeat) start(); } });
      btn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") stop(); });
      btn.addEventListener("click", (e) => e.preventDefault());
      btn.addEventListener("contextmenu", (e) => e.preventDefault());
      return h("div", { class: "rv-holdwrap" }, btn, hint);
    }

    let added = null;
    async function accept() {
      value.textContent = after;
      chip.className = "chip tier-accepted rv-chip";
      status.textContent = `Accepted by ${COLLEAGUE} with a passkey. The claim is solid; ${before} is kept as its predecessor.`;
      note(COLLEAGUE, `accepted birth date ${after}, signed with a passkey.`);
      added = await sealbook.chain.append(section, {
        title: "Correction accepted", signer: COLLEAGUE,
        text: `${COLLEAGUE} accepted birth date ${after} for P1 from ${file.name}, characters ${a} to ${b}, replacing ${before} as extracted.`,
      });
      const undo = h("button", { class: "quiet", type: "button", text: `Undo as ${COLLEAGUE}` });
      steps.querySelector(".rv-holdwrap").replaceWith(undo);
      undo.focus();
      undo.addEventListener("click", async () => {
        undo.disabled = true;
        value.textContent = before;
        chip.className = "chip tier-unconfirmed rv-chip";
        status.textContent = `Undone. ${before} is current again and unconfirmed. The accept is still in the history.`;
        note(COLLEAGUE, `undid the accept; ${before} restored. A further statement, nothing erased.`);
        await sealbook.chain.append(added.el, {
          title: "Accept undone", signer: COLLEAGUE,
          text: `${COLLEAGUE} undid the accept of birth date ${after} for P1 and restored ${before} as extracted. The accept stays in the history.`,
        });
      });
    }
  });
})();
