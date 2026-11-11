// Outside assistants: the consent screen a person sees, with the app's own scope
// wording, then what each ability leaves behind in the case once approved.
(() => {
  const { h } = sealbook;
  const AFTER = {
    read: ["Read", "tier-corroborated", "Each search and read lands in the access log as yours, made through the assistant."],
    propose_statements: ["Proposed fact", "tier-unconfirmed", "Arrives hatched in Review, pinned to the passage it was read from, recorded as the assistant's, made for you. Only a person accepts it."],
    propose_identity: ["Proposed same person", "tier-unconfirmed", "Arrives as a question in Review with the facts behind it. A name alone is refused, and so is a fact a person already rejected."],
  };

  sealbook.onReady((data) => {
    const root = sealbook.mount("assistants");
    const a = data.assistants;
    if (!root || !a) return;
    const approve = h("button", { class: "quiet solid", type: "button", text: "Approve" });
    const deny = h("button", { class: "quiet", type: "button", text: "Deny" });
    const result = h("ol", { class: "as-after", "aria-live": "polite" });
    const draw = (state) => {
      const row = (label, tier, text) => h("li", {}, h("span", { class: `chip ${tier}`, text: label }), h("p", { text }));
      result.replaceChildren(...(state === "approved" ? [
        ...a.scopes.map((s) => row(...AFTER[s.name])),
        row("Disconnect", "tier-accepted", "One click on the Agents page, and the assistant is refused on its very next call."),
      ] : [h("li", { class: "as-wait" }, h("p", { text: state === "denied" ? "Denied. The assistant gets nothing, and can ask again later." : "Nothing is allowed until you approve." }))]));
      approve.disabled = deny.disabled = state !== "asked";
    };
    approve.addEventListener("click", () => draw("approved"));
    deny.addEventListener("click", () => draw("denied"));
    const again = h("button", { class: "quiet", type: "button", text: "Start over" });
    again.addEventListener("click", () => draw("asked"));
    root.append(h("div", { class: "as-grid" },
      h("div", { class: "as-consent" },
        h("p", { class: "as-kicker", text: "Sealbook" }),
        h("h3", { text: "Your assistant asks to work for you" }),
        h("p", { class: "note", text: "Within your own access, never beyond your role." }),
        h("ul", {}, ...a.scopes.map((s) => h("li", { text: s.text }))),
        h("div", { class: "as-actions" }, deny, approve)),
      h("div", {}, h("h3", { text: "What it leaves in the case" }), result, h("div", { class: "as-again" }, again))),
      h("p", { class: "note", text: "The consent wording is the app's own. Nothing here connects to anything." }));
    draw("asked");
  });
})();
