// Connections: the demo case's open findings from a real discovery run, and one accepted
// finding whose single passkey gesture the reader can check in the browser.
(() => {
  const { h } = sealbook;
  const prop = (p) => p.replace(/_/g, " ");
  const enc = new TextEncoder();
  const b64url = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat(-s.length & 3)), (c) => c.charCodeAt(0));
  const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");
  const sha = async (bytes) => new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

  // The COSE key is a small CBOR map of integer keys to integers or byte strings.
  function cose(bytes) {
    let i = 0;
    const head = () => {
      const b = bytes[i++], major = b >> 5, info = b & 31;
      let n = info;
      if (info === 24) n = bytes[i++];
      else if (info === 25) { n = (bytes[i] << 8) | bytes[i + 1]; i += 2; }
      else if (info > 25) throw new Error("unsupported CBOR length");
      return [major, n];
    };
    const value = () => {
      const [major, n] = head();
      if (major === 0) return n;
      if (major === 1) return -1 - n;
      if (major === 2) { const v = bytes.slice(i, i + n); i += n; return v; }
      throw new Error("unsupported CBOR item");
    };
    const [major, size] = head();
    if (major !== 5) throw new Error("COSE key is not a map");
    const map = new Map();
    for (let k = 0; k < size; k++) map.set(value(), value());
    return map;
  }

  async function check(g, domain, batch) {
    const s = g.sig, out = [];
    const digest = hex(await sha(enc.encode(domain + JSON.stringify(batch))));
    out.push(["The challenge is the digest of the listed statement hashes", digest === s.challenge]);
    const client = b64url(s.client_data_json);
    const cd = JSON.parse(new TextDecoder().decode(client));
    out.push(["The browser data names that challenge and a passkey sign-in", cd.type === "webauthn.get" && hex(b64url(cd.challenge)) === digest && cd.origin === s.origin]);
    const auth = b64url(s.authenticator_data);
    out.push(["The authenticator data is for the stated site, with presence and verification flags", same([...auth.slice(0, 32)], [...await sha(enc.encode(s.rp_id))]) && (auth[32] & 5) === 5]);
    let sigOk = null;
    try {
      const key = cose(b64url(s.public_key));
      if (key.get(1) !== 1 || key.get(3) !== -8 || key.get(-1) !== 6) throw new Error("not an Ed25519 key");
      const pub = await crypto.subtle.importKey("raw", key.get(-2), { name: "Ed25519" }, false, ["verify"]);
      const signed = new Uint8Array([...auth, ...await sha(client)]);
      sigOk = await crypto.subtle.verify({ name: "Ed25519" }, pub, b64url(s.signature), signed);
    } catch (err) {
      console.warn(err);
    }
    out.push(["The Ed25519 signature verifies over that data", sigOk]);
    return out;
  }

  function excerpt(e) {
    if (!e) return null;
    return h("p", { class: "cn-ex", dir: "auto" }, e.before, h("mark", { class: "tier-accepted", text: e.span }), e.after);
  }

  function member(m) {
    return h("li", { class: "cn-member" },
      h("p", { class: "cn-who" }, h("bdi", { dir: "auto", text: m.name || "Name not recorded in this source" })),
      h("p", { class: "cn-what" }, h("span", { class: `chip tier-${m.tier}` }, `${prop(m.prop)} `, h("bdi", { dir: "auto", text: m.value })),
        h("span", { class: "mono cn-when", text: m.when })),
      h("p", { class: "note" }, m.derived ? `transcript line of ${m.file}` : m.file, m.sensitive ? ", sensitive, read after a logged reveal" : ""),
      excerpt(m.excerpt) || "");
  }

  sealbook.onReady((data) => {
    const root = sealbook.mount("connections");
    const c = data.connections;
    if (!root || !c) return;

    const list = h("ul", { class: "cn-list", "aria-label": "Open findings" });
    const detail = h("div", { class: "cn-detail", "aria-live": "polite" });
    const buttons = c.findings.map((f) => {
      const b = h("button", { type: "button", class: "cn-f", "aria-pressed": "false" },
        h("span", { class: "cn-kind", text: f.label }),
        h("span", { class: "cn-reason", text: f.reason }),
        h("span", { class: "cn-why" }, h("span", { class: `cn-band band-${f.band}`, text: `${f.band} band` }), ` ${f.why}`));
      b.addEventListener("click", () => open(f, b));
      list.append(h("li", {}, b));
      return b;
    });

    function open(f, btn) {
      buttons.forEach((x) => x.setAttribute("aria-pressed", String(x === btn)));
      detail.replaceChildren(
        h("p", { class: "cn-head" }, `${f.label}: ${f.reason}. `, h("span", { class: "note", text: `${f.members.length} member claims, ${f.window[0]} to ${f.window[1]}.` })),
        h("ol", { class: "cn-members" }, ...f.members.map(member)),
        h("p", { class: "note", text: "A band compares findings with each other. It is not a probability, and nothing here is a fact until a person accepts it." }));
    }

    const [accepted, rejected] = c.gestures;
    const results = h("ul", { class: "cn-checks", "aria-live": "polite" });
    const run = async (batch, label) => {
      results.replaceChildren(h("li", { text: "Checking." }));
      const rows = await check(accepted, c.gesture_domain, batch);
      results.replaceChildren(h("li", { class: "cn-check-head", text: label }),
        ...rows.map(([what, ok]) => h("li", { class: ok === null ? "unknown" : ok ? "ok" : "bad" },
          h("span", { class: "mark-ok", "aria-hidden": "true", text: ok === null ? "?" : ok ? "✓" : "✗" }),
          ` ${what}: ${ok === null ? "this browser cannot check Ed25519" : ok ? "yes" : "no"}`)),
        h("li", { class: "cn-check-head" }, rows.every(([, ok]) => ok) ? "Verdict: one passkey gesture signed exactly these statements."
          : rows.some(([, ok]) => ok === false) ? "Verdict: these statements are not the set that was signed." : "Verdict: not checked in this browser."));
    };
    const verify = h("button", { type: "button", class: "quiet solid", text: "Check the signature" });
    verify.addEventListener("click", () => run(accepted.batch, `All ${accepted.batch.length} statements, as signed`));
    const drop = h("button", { type: "button", class: "quiet", text: "Drop one statement and check" });
    drop.addEventListener("click", () => run(accepted.batch.slice(0, -1), `${accepted.batch.length - 1} statements, the review left out`));

    const gesture = h("div", { class: "cn-gesture" },
      h("h3", { text: `Accepted as one gesture: ${c.incidents[0]}` }),
      h("p", { class: "note", text: "The March 2014 candidate incident was accepted for two of its three people. One passkey assertion signed every statement it wrote:" }),
      h("ol", { class: "cn-rows" }, ...accepted.rows.map((r) =>
        h("li", {}, h("span", { class: "cn-prop", text: prop(r.prop) }), h("span", {}, h("bdi", { dir: "auto", text: r.what }), r.role ? `, ${r.role}` : ""),
          h("span", { class: "mono cn-hash", title: r.hash, text: r.hash.slice(0, 12) })))),
      h("div", { class: "cn-actions" }, verify, drop),
      results,
      h("p", { class: "note", text: `The build also checked every row with the app's offline verifier: ${accepted.verified_offline ? "all verified" : "failed"}. The demo signs with a software passkey held by the seed, through the same path a browser passkey uses.` }),
      rejected ? h("p", { class: "cn-rejected" }, h("b", { text: "Rejected, with its reason: " }), h("bdi", { dir: "auto", text: rejected.rows[0].what.replace(/^reject: /, "") }),
        h("span", { class: "note", text: " A rejected set is never proposed again." })) : "");

    root.append(
      h("div", { class: "cn-grid" }, h("div", {}, list, h("p", { class: "note", text: `At most ${c.per_kind} open findings of each kind per investigation; the rest wait by rank.` })), detail),
      gesture,
      h("p", { class: "note", text: `Read from ${c.case} through the app's discovery endpoints on a throwaway database. Reading these findings revealed ${c.reveals} sensitive sources and wrote ${c.access_records} access records.` }));
    open(c.findings[0], buttons[0]);
  });
})();
