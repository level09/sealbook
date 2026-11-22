/*
  site/verify.html: read a dropped package zip in the browser and show the checks
  from js/verify-package.js. Nothing leaves the page.
*/
(() => {
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

  const $ = (sel) => document.querySelector(sel);
  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) k === "text" ? (el.textContent = v) : el.setAttribute(k, v);
    el.append(...kids);
    return el;
  };
  const short = (hex) => `${hex.slice(0, 8)}…${hex.slice(-4)}`;
  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  const ICONS = {
    pass: "M5 12.5l4.5 4.5L19 7",
    fail: "M6 6l12 12M18 6L6 18",
    partial: "M12 7v5l3 2M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18",
    "not-run": "M12 7v5l3 2M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18",
    unreached: "M6 12h12",
  };
  const icon = (status) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", ICONS[status]);
    svg.append(path);
    return svg;
  };
  const NAMES = {
    manifest: "manifest layout", documents: "file hashes", attestations: "attestations", rows: "row hashes",
    chain: "chain links", proofs: "batch proofs", anchors: "anchor receipts", signatures: "statement signatures",
    values: "value commitments", blobs: "source files",
  };
  const AUDIENCE = { family: "a family", partner: "a partner", court: "a court" };

  function detail(check, r) {
    const n = check.count;
    const anchors = r.anchors.map((a) => {
      if (a.kind === "partner") return `the partner receipt in the package names root ${short(a.root)}`;
      if (a.kind === "ots") return `the OpenTimestamps file commits to root ${short(a.root)}`;
      return `the RFC 3161 receipt names root ${short(a.root)}${a.time ? ` and states the time ${a.time}` : ""}`;
    });
    return {
      manifest: "Layout version 1, with every field the offline verifier expects.",
      documents: `${plural(n, "file")} at the top of the package match their SHA-256 in the manifest, and no other file sits there unlisted.`,
      attestations: r.attesters.length
        ? `${r.attesters.map((a) => a.name).join(" and ")} signed manifest hash ${short(r.manifestSha)} with their passkeys.`
        : "No attestation was checked.",
      rows: `${plural(n, "row")} recomputed from their own fields.`,
      chain: `${plural(n, "entry", "entries")}: each hash recomputed from its contents, and each linked to the entry before it where both are in the package.`,
      proofs: `${plural(n, "entry", "entries")} rebuilt up to their batch root from the sibling hashes in the package.`,
      anchors: anchors.length ? `In this package, ${anchors.join("; ")}.` : "No anchor receipt was checked.",
      signatures: n ? `${plural(n, "signature")} over statement hashes verify, from run keys and passkeys.` : "No statement in this copy carries a signature.",
      values: n ? `${plural(n, "disclosed value")} match the salted commitments the chain holds.` : "This copy discloses no values.",
      blobs: n ? `${plural(n, "source file")} match both hashes in the manifest.` : "This copy ships no source files.",
    }[check.key];
  }

  function line(status, title, text, problems = []) {
    const body = h("div", {}, h("div", { class: "v-title", text: title }));
    if (status === "not-run") body.append(h("div", { class: "v-tag", text: "Not checked on this page" }));
    if (text) body.append(h("div", { class: "v-detail", text }));
    if (problems.length) {
      const list = h("ul", { class: "v-problems" });
      problems.slice(0, 5).forEach((p) => list.append(h("li", {}, h("code", { text: p }))));
      if (problems.length > 5) list.append(h("li", { text: `and ${problems.length - 5} more` }));
      body.append(list);
    }
    return h("li", { class: `v-line is-${status}` }, icon(status), body);
  }

  function show(r, name) {
    const failed = r.checks.filter((c) => c.status === "fail");
    const ran = r.checks.filter((c) => c.status !== "unreached");
    const notRun = r.notRun.length;
    const box = $("#result");
    box.hidden = false;
    box.className = `v-result ${failed.length ? "is-fail" : "is-pass"}`;

    const head = $("#verdict");
    head.replaceChildren(icon(failed.length ? "fail" : "pass"));
    const info = r.info || {};
    const meta = [];
    if (info.title) meta.push(info.title);
    if (info.audience) meta.push(`prepared for ${AUDIENCE[info.audience] || info.audience}`);
    if (info.sentences !== null && info.sentences !== undefined) {
      meta.push(`${plural(info.sentences, "sentence")}, ${plural(info.cited, "cited statement")}, ${plural(info.statements, "statement")} in the manifest`);
    }
    const text = h("div", {},
      h("h2", { id: "verdict-title", tabindex: "-1", text: failed.length ? "This package does not verify." : "Every check that ran passed." }),
      h("p", { class: "v-meta", text: meta.join(" · ") || name }));
    if (failed.length) {
      text.append(h("p", { class: "v-why", text: `Failed: ${failed.map((c) => NAMES[c.key]).join(", ")}. ${plural(r.problems.length, "problem")} below.` }));
    }
    text.append(h("p", { class: "v-count", text: `${plural(ran.length, "check")} ran here. ${plural(notRun, "check")} ${notRun === 1 ? "was" : "were"} not run on this page.` }));
    head.append(text);

    const list = $("#checks");
    list.replaceChildren();
    for (const c of r.checks) {
      if (c.status === "unreached") { list.append(line("unreached", c.title, "Not reached: the manifest could not be read.")); continue; }
      list.append(line(c.status === "partial" ? "partial" : c.status, c.title, c.status === "fail" ? "" : detail(c, r), c.problems));
    }
    for (const n of r.notRun) list.append(line("not-run", n.title, n.detail));

    const withheld = $("#withheld");
    withheld.replaceChildren();
    if (r.info) {
      const kinds = Object.entries(info.withheld || {});
      const parts = [];
      if (info.audience) parts.push(`This copy was made for ${AUDIENCE[info.audience] || info.audience}.`);
      parts.push(kinds.length
        ? `The values of ${kinds.map(([prop, n]) => plural(n, `${prop.replace(/_/g, " ")} statement`)).join(", ")} were withheld. Their salted commitments stay in the manifest, so the chain still verifies without them.`
        : "No statement value was withheld.");
      if (!info.sourcesShipped) parts.push("The source files are not included. Their hashes stay in the file records.");
      else if (info.withheldFiles) {
        const named = info.withheldTitles.length ? `: ${info.withheldTitles.join(", ")}` : "";
        parts.push(`${plural(info.withheldFiles, "source file")} ${info.withheldFiles === 1 ? "was" : "were"} withheld${named}. Each hash stays in its file record, so a copy can still be checked. The other source files are included and were checked above.`);
      } else parts.push("The source files are included and were checked above.");
      withheld.append(...parts.map((t) => h("p", { text: t })));
    }
    $("#withheld-card").hidden = !withheld.childElementCount;
    $("#verdict-title").focus();
  }

  async function run(file) {
    const status = $("#status");
    status.textContent = `Reading ${file.name}…`;
    try {
      const files = await SealbookVerify.readZip(new Uint8Array(await file.arrayBuffer()));
      const r = await SealbookVerify.verify(files);
      status.textContent = `${file.name}, read in this browser.`;
      show(r, file.name);
    } catch (e) {
      console.error(e);
      $("#result").hidden = true;
      status.textContent = `${file.name} could not be read as a package zip: ${e.message}.`;
    }
  }

  const input = $("#package");
  input.addEventListener("change", () => input.files[0] && run(input.files[0]));
  const zone = $(".v-drop");
  ["dragenter", "dragover"].forEach((t) => document.addEventListener(t, (e) => {
    e.preventDefault();
    zone.classList.add("is-over");
  }));
  ["dragleave", "drop"].forEach((t) => document.addEventListener(t, (e) => {
    e.preventDefault();
    if (t === "drop" || e.target === document.documentElement) zone.classList.remove("is-over");
    if (t === "drop" && e.dataTransfer.files[0]) run(e.dataTransfer.files[0]);
  }));
})();
