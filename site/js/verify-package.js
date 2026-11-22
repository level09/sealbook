/*
  Sealbook package check, in the browser or under node (18+ with WebCrypto).

  A port of sealbook/ledger/verify_package.py (layout v1) for the checks a browser
  can do faithfully. Problem strings match the Python verifier's where the cause is
  the same, so tests can compare the two.

    SealbookVerify.readZip(bytes)        Map of path -> Uint8Array (stored and deflate)
    SealbookVerify.verify(files)         {problems, ok, checks, info, notRun}

  Not done here, and reported as not run: the RFC 3161 CMS signature and signer
  certificate of a TSA receipt, the OpenTimestamps proof tree and its Bitcoin
  attestation, the C2PA claim on package.svg, and pinning of signer keys, origins and
  relying parties. Ed25519 checks are reported as not run where WebCrypto lacks it.
*/
(function (root) {
  "use strict";
  const subtle = globalThis.crypto.subtle;
  const utf8 = new TextEncoder();
  const GENESIS = "0".repeat(64);

  // sealbook/ledger/contracts.py CHAINED_FIELDS; tests/test_site_verify.py holds them equal.
  const CHAINED_FIELDS = {
    statement: ["id", "subject", "prop", "commitment", "span_blob", "span", "author_id", "run_id",
      "valid_from", "valid_to", "supersedes", "evidence", "based_on", "recorded_at"],
    acquisition: ["id", "blob_sha256", "origin", "collector_id", "at", "context", "sensitivity",
      "doc_type", "partner"],
    run: ["id", "principal_id", "tool", "route", "endpoint", "model_requested", "model_served",
      "provider_served", "prompt_hash", "inputs", "output_hash", "pubkey", "finished_at"],
  };
  const COLLECTIONS = {
    statements: [...CHAINED_FIELDS.statement, "hash"],
    acquisitions: [...CHAINED_FIELDS.acquisition, "hash"],
    runs: [...CHAINED_FIELDS.run, "hash"],
    chain: ["seq", "kind", "ref", "row_hash", "prev_hash", "hash"],
    batches: ["first_seq", "last_seq", "size", "root"],
    proofs: ["seq", "index", "batch_root", "path"],
    anchors: ["root", "kind", "file"],
    blobs: ["sha256", "md5", "file"],
    values: ["commitment", "salt", "value"],
    signatures: ["hash", "sig"],
    documents: ["file", "sha256"],
  };
  const ATTESTATION = ["name", "credential_id", "sig"];
  const GESTURE_MAX = 512;
  const GESTURE_DOMAIN = "sealbook-gesture-v1\n";
  const OTS_HEAD = bytes([0, ...utf8.encode("OpenTimestamps"), 0, 0, ...utf8.encode("Proof"), 0,
    0xbf, 0x89, 0xe2, 0xe8, 0x84, 0xe8, 0x92, 0x94, 0x01, 0x08]);
  const OTS_BITCOIN = bytes([0x05, 0x88, 0x96, 0x0d, 0x73, 0xd7, 0x19, 0x01]); // Bitcoin attestation tag

  class Problem extends Error {}
  const must = (condition, message) => { if (!condition) throw new Problem(message); };
  // Python's KeyError text is the key's repr.
  const keyError = (key) => new Problem(typeof key === "string" ? `'${key}'` : String(key));
  const get = (obj, key) => {
    if (!isDict(obj) || !Object.prototype.hasOwnProperty.call(obj, key)) throw keyError(key);
    return obj[key];
  };

  // ---- bytes -----------------------------------------------------------------------

  function bytes(list) { return Uint8Array.from(list); }
  function concat(...parts) {
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const p of parts) { out.set(p, at); at += p.length; }
    return out;
  }
  function equal(a, b) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }
  function indexOf(hay, needle) {
    outer: for (let i = 0; i + needle.length <= hay.length; i++) {
      for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
      return i;
    }
    return -1;
  }
  function toHex(buf) {
    return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  function fromHex(text) { // bytes.fromhex: pairs of hex digits, whitespace between pairs
    must(isStr(text), "fromhex() argument must be str");
    const out = [];
    let i = 0;
    while (i < text.length) {
      if (/\s/.test(text[i]) && text.charCodeAt(i) < 128) { i++; continue; }
      const pair = text.slice(i, i + 2);
      if (!/^[0-9a-fA-F]{2}$/.test(pair)) {
        throw new Problem(`non-hexadecimal number found in fromhex() arg at position ${i}`);
      }
      out.push(parseInt(pair, 16));
      i += 2;
    }
    return Uint8Array.from(out);
  }
  async function sha256(data) { return new Uint8Array(await subtle.digest("SHA-256", data)); }
  async function sha256hex(data) { return toHex(await sha256(data)); }

  function b64(value) { // offline_sigs._b64: unpadded, canonical base64url
    must(typeof value === "string", "expected base64url string");
    must(/^[A-Za-z0-9_-]*$/.test(value) && value.length % 4 !== 1, "Incorrect padding");
    const std = value.replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(std + "=".repeat((4 - (std.length % 4)) % 4));
    const data = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    let again = "";
    for (let i = 0; i < data.length; i += 0x8000) again += String.fromCharCode(...data.subarray(i, i + 0x8000));
    must(btoa(again).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "") === value,
      "expected unpadded base64url");
    return data;
  }

  // ---- MD5 (RFC 1321), only for the blob md5 the manifest also records ---------------

  function md5hex(data) {
    const s = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
    const K = Array.from({ length: 64 }, (_, i) => (Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0);
    const len = data.length;
    const padded = new Uint8Array(((len + 8) >> 6) * 64 + 64);
    padded.set(data);
    padded[len] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, (len * 8) >>> 0, true);
    view.setUint32(padded.length - 4, Math.floor(len / 0x20000000), true);
    let [a0, b0, c0, d0] = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476];
    for (let off = 0; off < padded.length; off += 64) {
      let [a, b, c, d] = [a0, b0, c0, d0];
      for (let i = 0; i < 64; i++) {
        let f, g;
        if (i < 16) { f = (b & c) | (~b & d); g = i; }
        else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
        else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
        else { f = c ^ (b | ~d); g = (7 * i) % 16; }
        const sh = s[(i >> 4) * 4 + (i % 4)];
        const x = (a + f + K[i] + view.getUint32(off + g * 4, true)) >>> 0;
        a = d; d = c; c = b;
        b = (b + ((x << sh) | (x >>> (32 - sh)))) >>> 0;
      }
      a0 = (a0 + a) >>> 0; b0 = (b0 + b) >>> 0; c0 = (c0 + c) >>> 0; d0 = (d0 + d) >>> 0;
    }
    const out = new DataView(new ArrayBuffer(16));
    [a0, b0, c0, d0].forEach((w, i) => out.setUint32(i * 4, w, true));
    return toHex(new Uint8Array(out.buffer));
  }

  // ---- zip ---------------------------------------------------------------------------

  const CRC = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  function crc32(data) {
    let c = 0xffffffff;
    for (let i = 0; i < data.length; i++) c = CRC[(c ^ data[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }
  async function inflate(data) {
    const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  /* The package zip as {path: bytes}. Stored and deflate entries only; no zip64,
     no encryption. A package re-zipped inside one folder is read from that folder. */
  async function readZip(input) {
    const buf = input instanceof Uint8Array ? input : new Uint8Array(input);
    const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    let end = -1;
    for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) {
      if (view.getUint32(i, true) === 0x06054b50) { end = i; break; }
    }
    if (end < 0) throw new Error("not a zip file");
    const count = view.getUint16(end + 10, true);
    let at = view.getUint32(end + 16, true);
    if (count === 0xffff || at === 0xffffffff) throw new Error("zip64 archives are not supported");
    const files = new Map();
    const names = new TextDecoder("utf-8", { fatal: true });
    for (let n = 0; n < count; n++) {
      if (at + 46 > buf.length || view.getUint32(at, true) !== 0x02014b50) throw new Error("damaged zip directory");
      const flags = view.getUint16(at + 8, true), method = view.getUint16(at + 10, true);
      const crc = view.getUint32(at + 16, true);
      const size = view.getUint32(at + 20, true), full = view.getUint32(at + 24, true);
      const nameLen = view.getUint16(at + 28, true);
      const skip = nameLen + view.getUint16(at + 30, true) + view.getUint16(at + 32, true);
      const local = view.getUint32(at + 42, true);
      const name = names.decode(buf.subarray(at + 46, at + 46 + nameLen));
      at += 46 + skip;
      if (name.endsWith("/")) continue;
      if (flags & 1) throw new Error(`${name}: encrypted entries are not supported`);
      if (name.startsWith("/") || name.split("/").includes("..")) throw new Error(`${name}: unsafe path in zip`);
      if (local + 30 > buf.length || view.getUint32(local, true) !== 0x04034b50) throw new Error(`${name}: damaged zip entry`);
      const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
      const raw = buf.subarray(start, start + size);
      if (raw.length !== size) throw new Error(`${name}: truncated zip entry`);
      let data;
      if (method === 0) data = raw.slice();
      else if (method === 8) data = await inflate(raw);
      else throw new Error(`${name}: compression method ${method} is not supported`);
      if (data.length !== full || crc32(data) !== crc) throw new Error(`${name}: zip entry is damaged (CRC)`);
      files.set(name.split("/").filter((p) => p && p !== ".").join("/"), data);
    }
    if (!files.has("manifest.json")) {
      const tops = new Set([...files.keys()].map((k) => k.split("/")[0]));
      const [top] = tops;
      if (tops.size === 1 && files.has(`${top}/manifest.json`)) {
        return new Map([...files].map(([k, v]) => [k.slice(top.length + 1), v]));
      }
    }
    return files;
  }

  // ---- JSON as Python's json module reads it ----------------------------------------

  // A JSON number with a fraction or exponent: Python reads it as a float, which
  // chain.canon refuses.
  class PyFloat { constructor(text) { this.text = text; } }
  const isDict = (v) => v !== null && typeof v === "object" && !Array.isArray(v) && !(v instanceof PyFloat);
  const isInt = (v) => (typeof v === "number" && Number.isInteger(v)) || typeof v === "bigint";
  const isStr = (v) => typeof v === "string";

  /* json.loads with object_pairs_hook=_object when strict: duplicate keys refused.
     Integers beyond 2^53 stay exact as BigInt. */
  function parseJSON(text, strict) {
    let i = 0;
    const fail = (what) => { throw new Problem(`${what}: line ${text.slice(0, i).split("\n").length} column ${i - text.lastIndexOf("\n", i - 1)} (char ${i})`); };
    const ws = () => { while (" \t\n\r".includes(text[i]) && i < text.length) i++; };
    function value() {
      ws();
      const c = text[i];
      if (c === "{") {
        i++;
        const obj = Object.create(null);
        ws();
        if (text[i] === "}") { i++; return obj; }
        for (;;) {
          ws();
          if (text[i] !== '"') fail("Expecting property name enclosed in double quotes");
          const key = string();
          ws();
          if (text[i] !== ":") fail("Expecting ':' delimiter");
          i++;
          const v = value();
          if (strict && Object.prototype.hasOwnProperty.call(obj, key)) throw new Problem(`duplicate JSON key ${key}`);
          obj[key] = v;
          ws();
          if (text[i] === ",") { i++; continue; }
          if (text[i] === "}") { i++; return obj; }
          fail("Expecting ',' delimiter");
        }
      }
      if (c === "[") {
        i++;
        const arr = [];
        ws();
        if (text[i] === "]") { i++; return arr; }
        for (;;) {
          arr.push(value());
          ws();
          if (text[i] === ",") { i++; continue; }
          if (text[i] === "]") { i++; return arr; }
          fail("Expecting ',' delimiter");
        }
      }
      if (c === '"') return string();
      for (const [word, v] of [["true", true], ["false", false], ["null", null]]) {
        if (text.startsWith(word, i)) { i += word.length; return v; }
      }
      const m = /^-?(?:0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i, i + 4096));
      if (!m) fail("Expecting value");
      i += m[0].length;
      if (m[1] || m[2]) return new PyFloat(m[0]);
      const n = Number(m[0]);
      return Number.isSafeInteger(n) ? n : BigInt(m[0]);
    }
    function string() {
      i++;
      let out = "";
      for (;;) {
        if (i >= text.length) fail("Unterminated string starting at");
        const c = text[i];
        if (c === '"') { i++; return out; }
        if (c < " ") fail("Invalid control character at");
        if (c === "\\") {
          const e = text[i + 1];
          const simple = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };
          if (e in simple) { out += simple[e]; i += 2; continue; }
          if (e === "u" && /^[0-9a-fA-F]{4}$/.test(text.slice(i + 2, i + 6))) {
            out += String.fromCharCode(parseInt(text.slice(i + 2, i + 6), 16));
            i += 6;
            continue;
          }
          fail("Invalid \\escape");
        }
        out += c;
        i++;
      }
    }
    const result = value();
    ws();
    if (i !== text.length) fail("Extra data");
    return result;
  }
  function loadJSON(data, strict) {
    let text;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(data); }
    catch { throw new Problem("'utf-8' codec can't decode the file"); }
    return parseJSON(text, strict);
  }

  // ---- chain.canon and hashes --------------------------------------------------------

  const codepoints = (a, b) => {
    const x = [...a], y = [...b];
    for (let k = 0; k < Math.min(x.length, y.length); k++) {
      const d = x[k].codePointAt(0) - y[k].codePointAt(0);
      if (d) return d;
    }
    return x.length - y.length;
  };
  function canonText(v) {
    if (v === null) return "null";
    if (v === true) return "true";
    if (v === false) return "false";
    if (typeof v === "bigint") return v.toString();
    if (typeof v === "number") {
      if (!Number.isInteger(v)) throw new Problem("floats are not canonical; use int or str");
      return String(v);
    }
    if (v instanceof PyFloat) throw new Problem("floats are not canonical; use int or str");
    if (typeof v === "string") return JSON.stringify(v);
    if (Array.isArray(v)) return `[${v.map(canonText).join(",")}]`;
    return `{${Object.keys(v).sort(codepoints).map((k) => `${JSON.stringify(k)}:${canonText(v[k])}`).join(",")}}`;
  }
  const canon = (v) => utf8.encode(canonText(v));
  const rowHash = (kind, row) => {
    const picked = Object.create(null);
    for (const name of CHAINED_FIELDS[kind]) picked[name] = row[name];
    return sha256hex(canon(picked));
  };
  const link = (prev, seq, kind, rowHash_) => sha256hex(canon([prev, seq, kind, rowHash_]));
  const commitment = async (salt, value) => sha256hex(concat(salt, canon(value)));

  // ---- merkle.py (RFC 6962) -----------------------------------------------------------

  function mhash(value) {
    if (typeof value !== "string" || value.length !== 64) throw new Problem("expected a 32-byte hex hash");
    const out = fromHex(value);
    if (out.length !== 32) throw new Problem("expected a 32-byte hex hash");
    return out;
  }
  const node = (l, r) => sha256(concat([1], l, r));
  const split = (size) => { let k = 1; while (k * 2 < size) k *= 2; return k; };
  async function treeRoot(leaves) {
    if (!leaves.length) return sha256(new Uint8Array());
    if (leaves.length === 1) return leaves[0];
    const k = split(leaves.length);
    return node(await treeRoot(leaves.slice(0, k)), await treeRoot(leaves.slice(k)));
  }
  async function merkleRoot(hashes) {
    const leaves = [];
    for (const h of hashes) leaves.push(await sha256(concat([0], mhash(h))));
    return toHex(await treeRoot(leaves));
  }
  async function verifyInclusion(leafHash, index, size, proof, rootHex) {
    if (!isInt(index) || !isInt(size) || !(0 <= index && index < size)) return false;
    if (!Array.isArray(proof)) return false;
    try {
      const leaf = await sha256(concat([0], mhash(leafHash)));
      const expected = mhash(rootHex);
      let at = 0;
      const next = () => { if (at >= proof.length) throw new Problem("short"); return mhash(proof[at++]); };
      const rebuild = async (i, n) => {
        if (n === 1) return leaf;
        const k = split(n);
        if (i < k) return node(await rebuild(i, k), next());
        { const child = await rebuild(i - k, n - k); return node(next(), child); }
      };
      const actual = await rebuild(Number(index), Number(size));
      return at === proof.length && equal(actual, expected);
    } catch (e) {
      if (e instanceof Problem) return false;
      throw e;
    }
  }

  // ---- CBOR, enough for a COSE public key -------------------------------------------

  function cbor(data) {
    let i = 0;
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const need = (n) => { if (i + n > data.length) throw new Problem("invalid COSE key"); };
    function arg(info) {
      if (info < 24) return info;
      const n = { 24: 1, 25: 2, 26: 4, 27: 8 }[info];
      if (!n) throw new Problem("invalid COSE key");
      need(n);
      const v = n === 1 ? view.getUint8(i) : n === 2 ? view.getUint16(i) : n === 4 ? view.getUint32(i) : Number(view.getBigUint64(i));
      i += n;
      return v;
    }
    function item() {
      need(1);
      const b = data[i++], major = b >> 5, info = b & 31;
      if (major === 7) {
        const simple = { 20: false, 21: true, 22: null };
        if (info in simple) return simple[info];
        throw new Problem("invalid COSE key");
      }
      const n = arg(info);
      if (major === 0) return n;
      if (major === 1) return -1 - n;
      if (major === 2 || major === 3) {
        need(n);
        const s = data.slice(i, i + n);
        i += n;
        return major === 2 ? s : new TextDecoder("utf-8", { fatal: true }).decode(s);
      }
      if (major === 4) return Array.from({ length: n }, item);
      if (major === 5) {
        const map = new Map();
        for (let k = 0; k < n; k++) { const key = item(); map.set(key, item()); }
        return map;
      }
      throw new Problem("invalid COSE key");
    }
    const out = item();
    must(out instanceof Map && i === data.length, "invalid COSE key");
    return out;
  }

  // ---- signatures (offline_sigs.py) ---------------------------------------------------

  const SPKI = {
    ed25519: fromHex("302a300506032b6570032100"),
    p256: fromHex("3059301306072a8648ce3d020106082a8648ce3d030107034200"),
  };
  let ed25519 = null; // whether this WebCrypto has Ed25519
  async function hasEd25519() {
    if (ed25519 === null) {
      try {
        await subtle.importKey("raw", new Uint8Array(32).fill(1), { name: "Ed25519" }, false, ["verify"]);
        ed25519 = true;
      } catch (e) { ed25519 = e && e.name === "DataError"; }
    }
    return ed25519;
  }
  class NotRun extends Error {}

  async function ed25519Verify(key, signature, data) {
    if (!(await hasEd25519())) throw new NotRun("this browser has no Ed25519");
    must(key.length === 32, "An Ed25519 public key is 32 bytes long");
    let k;
    try { k = await subtle.importKey("raw", key, { name: "Ed25519" }, false, ["verify"]); }
    catch { throw new Problem("invalid Ed25519 public key"); }
    if (!(await subtle.verify({ name: "Ed25519" }, k, signature, data))) {
      throw new Problem("statement signature does not verify");
    }
  }

  function derToRaw(der) { // ECDSA-Sig-Value (strict DER) to r || s
    const bad = () => new Problem("statement signature does not verify");
    if (der.length < 8 || der[0] !== 0x30 || der[1] !== der.length - 2) throw bad();
    let i = 2;
    const int = () => {
      if (der[i] !== 0x02) throw bad();
      const n = der[i + 1];
      const v = der.subarray(i + 2, i + 2 + n);
      if (!n || v.length !== n || v[0] & 0x80 || (n > 1 && v[0] === 0 && !(v[1] & 0x80))) throw bad();
      i += 2 + n;
      const t = v[0] === 0 ? v.subarray(1) : v;
      if (t.length > 32) throw bad();
      const out = new Uint8Array(32);
      out.set(t, 32 - t.length);
      return out;
    };
    const r = int(), s = int();
    if (i !== der.length) throw bad();
    return concat(r, s);
  }

  /* verify_statement_signature: returns the WebAuthn key fingerprint, or null. */
  async function verifySignature(digest, sig, pubkey) {
    must(isDict(sig), "signature must be an object");
    if (sig.kind === "ed25519") {
      must(isStr(pubkey), "signature needs an exported run key");
      const key = fromHex(pubkey);
      const signature = fromHex(get(sig, "sig"));
      await ed25519Verify(key, signature, utf8.encode(digest));
      return null;
    }
    must(sig.kind === "webauthn", "unsupported signature kind");
    if ("batch" in sig) {
      const batch = sig.batch;
      must(Array.isArray(batch) && batch.length > 0 && batch.length <= GESTURE_MAX &&
        batch.every((h) => isStr(h) && /^[0-9a-f]{64}$/.test(h)) && new Set(batch).size === batch.length,
      "invalid gesture batch");
      must(batch.includes(digest), "statement is not in the signed gesture");
      digest = await sha256hex(concat(utf8.encode(GESTURE_DOMAIN), utf8.encode(JSON.stringify(batch))));
    }
    must(get(sig, "challenge") === digest, "WebAuthn challenge mismatch");
    must(b64(get(sig, "credential_id")).length > 0, "empty credential id");
    const clientBytes = b64(get(sig, "client_data_json"));
    const client = loadJSON(clientBytes, false);
    must(isDict(client), "clientDataJSON must be an object");
    must(client.type === "webauthn.get", "wrong clientDataJSON type");
    must(equal(b64(get(client, "challenge")), fromHex(digest)), "WebAuthn client challenge mismatch");
    must(isStr(get(sig, "origin")) && client.origin === sig.origin, "WebAuthn origin mismatch");
    must([undefined, null, false, 0].includes(client.crossOrigin), "WebAuthn cross-origin");
    must(isStr(get(sig, "rp_id")), "invalid rp id");
    const auth = b64(get(sig, "authenticator_data"));
    must(auth.length >= 37, "truncated authenticatorData");
    must(equal(auth.subarray(0, 32), await sha256(utf8.encode(sig.rp_id))), "WebAuthn rp id mismatch");
    must(auth[32] & 1, "WebAuthn user present flag missing");
    must(auth[32] & 4, "WebAuthn user verified flag missing");
    const cose = cbor(b64(get(sig, "public_key")));
    const data = concat(auth, await sha256(clientBytes));
    const signature = b64(get(sig, "signature"));
    const kind = (a, b, c) => cose.get(1) === a && cose.get(3) === b && cose.get(-1) === c;
    let spki;
    if (kind(2, -7, 1)) {
      const x = cose.get(-2), y = cose.get(-3);
      must(x instanceof Uint8Array && y instanceof Uint8Array && x.length === 32 && y.length === 32,
        "invalid P-256 coordinates");
      const point = concat([4], x, y);
      let key;
      try { key = await subtle.importKey("raw", point, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]); }
      catch { throw new Problem("Invalid EC key."); }
      const ok = await subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, derToRaw(signature), data);
      must(ok, "statement signature does not verify");
      spki = concat(SPKI.p256, point);
    } else {
      must(kind(1, -8, 6), "unsupported COSE algorithm or curve");
      const x = cose.get(-2);
      must(x instanceof Uint8Array, "invalid Ed25519 public key");
      await ed25519Verify(x, signature, data);
      spki = concat(SPKI.ed25519, x);
    }
    return sha256hex(spki);
  }

  // ---- anchor receipts (receipts.py) ---------------------------------------------------

  function derRead(buf, at) {
    if (at + 2 > buf.length) throw new Problem("truncated DER");
    const tag = buf[at];
    let len = buf[at + 1], start = at + 2;
    if (len & 0x80) {
      const n = len & 0x7f;
      if (!n || n > 4 || start + n > buf.length) throw new Problem("unsupported DER length");
      len = 0;
      for (let k = 0; k < n; k++) len = len * 256 + buf[start + k];
      start += n;
    }
    if (start + len > buf.length) throw new Problem("truncated DER");
    return { tag, start, end: start + len, body: buf.subarray(start, start + len) };
  }
  function derChildren(buf) {
    const out = [];
    for (let at = 0; at < buf.length;) { const t = derRead(buf, at); out.push(t); at = t.end; }
    return out;
  }
  const SHA256_OID = fromHex("608648016503040201");

  /* The part of tsa_check this page does: status granted, sha256 imprint equal to the
     root. Returns the receipt's stated time. CMS signature and signer: not here. */
  function tsaImprint(receipt, rootHex) {
    try { return readTsa(receipt, rootHex); }
    catch (e) { throw e instanceof Problem ? e : new Problem("could not read the TSA receipt"); }
  }
  function readTsa(receipt, rootHex) {
    const resp = derChildren(derRead(receipt, 0).body);
    const status = derChildren(resp[0].body)[0];
    const code = status.body.length === 1 ? status.body[0] : -1;
    if (code !== 0 && code !== 1) throw new Problem(`TSA status ${["granted", "granted_with_mods", "rejection", "waiting", "revocation_warning", "revocation_notification"][code] || code}`);
    must(resp[1], "TSA response has no token");
    const token = derChildren(resp[1].body);
    const signed = derChildren(derChildren(token[1].body)[0].body);
    const encap = derChildren(signed[2].body);
    const tst = derRead(derChildren(encap[1].body)[0].body, 0);
    const info = derChildren(tst.body);
    const imprint = derChildren(info[2].body);
    const alg = derChildren(imprint[0].body)[0];
    must(alg.tag === 0x06 && equal(alg.body, SHA256_OID), "TSA imprint is not sha256");
    must(toHex(imprint[1].body) === rootHex, "TSA imprint does not match root");
    const time = info.find((t) => t.tag === 0x18);
    return time ? new TextDecoder().decode(time.body) : null;
  }

  function otsCheck(receipt, rootHex) {
    must(equal(receipt.subarray(0, OTS_HEAD.length), OTS_HEAD), "not a sha256 detached .ots file");
    must(toHex(receipt.subarray(OTS_HEAD.length, OTS_HEAD.length + 32)) === rootHex,
      "ots file digest does not match root");
    return indexOf(receipt, OTS_BITCOIN) < 0; // pending: no Bitcoin attestation tag
  }

  function partnerCheck(receipt, rootHex) {
    must(receipt.every((b) => b < 128), "'ascii' codec can't decode the receipt");
    const text = new TextDecoder().decode(receipt).replace(/^[\s\x1c-\x1f]+|[\s\x1c-\x1f]+$/g, "");
    must(text === rootHex, "partner root does not match");
  }

  // ---- verify_package -----------------------------------------------------------------

  function hex(value, length = 64) {
    must(isStr(value), "expected hex string");
    must(value.length === length && /^[0-9a-f]*$/.test(value), `expected ${length} lowercase hex characters`);
    return value;
  }
  function integer(value, minimum = 1) {
    must(isInt(value) && value >= minimum, "invalid integer");
    return Number(value);
  }
  function file(files, area, name) {
    must(isStr(name) && name.length > 0, "invalid file name");
    must(!name.startsWith("/") && !name.split("/").includes(".."), "file path must stay inside its directory");
    const parts = name.split("/").filter((p) => p && p !== ".");
    const key = [...(area === "." ? [] : [area]), ...parts].join("/");
    must(files.has(key), `file missing or outside ${area}: ${name}`);
    return key;
  }
  const sameFields = (row, fields) =>
    isDict(row) && Object.keys(row).length === fields.length && fields.every((f) => f in row);

  // The lines a reader sees, in order. Each problem lands in one of them.
  const CHECKS = [
    ["manifest", "The manifest is a version 1 package"],
    ["documents", "Every file matches the hash the manifest gives it"],
    ["attestations", "Two people attested this exact manifest"],
    ["rows", "Every statement, file record and run matches its row hash"],
    ["chain", "The chain links, entry by entry"],
    ["proofs", "Every entry reaches its batch root"],
    ["anchors", "Every batch root is bound to its receipt"],
    ["signatures", "Statement signatures verify"],
    ["values", "Disclosed values match their commitments"],
    ["blobs", "Source files match their SHA-256 and MD5"],
  ];

  async function verify(files) {
    const problems = [], ok = [];
    const lines = Object.fromEntries(CHECKS.map(([key, title]) => [key, { key, title, status: "pass", problems: [], notes: [], count: 0 }]));
    const notRun = [];
    let bucket = "manifest";
    const report = (message) => { problems.push(message); lines[bucket].problems.push(message); };
    async function check(label, fn) {
      try { await fn(); }
      catch (e) {
        if (e instanceof NotRun) { lines[bucket].skipped = (lines[bucket].skipped || 0) + 1; lines[bucket].skipWhy = e.message; return; }
        if (!(e instanceof Problem)) throw e;
        report(`${label}: ${e.message}`);
      }
    }
    const finish = (stopped) => {
      const checks = CHECKS.map(([key]) => lines[key]);
      for (const c of checks) {
        if (c.problems.length) c.status = "fail";
        else if (stopped) c.status = "unreached";
        else if (c.skipped) c.status = "partial";
      }
      return { problems, ok, checks, notRun };
    };

    if (!files.has("manifest.json")) { report("manifest: [Errno 2] No such file or directory: 'manifest.json'"); return finish(true); }
    const raw = files.get("manifest.json");
    let manifest;
    try { manifest = loadJSON(raw, true); }
    catch (e) { if (!(e instanceof Problem)) throw e; report(`manifest: ${e.message}`); return finish(true); }
    if (!isDict(manifest)) { report("manifest: expected an object"); return finish(true); }
    if (manifest.version !== 1) report("manifest: unsupported version");
    const top = ["version", ...Object.keys(COLLECTIONS)];
    if (!sameFields(manifest, top)) report("manifest: incorrect fields");
    const records = {};
    for (const [name, fields] of Object.entries(COLLECTIONS)) {
      records[name] = [];
      const collection = manifest[name];
      if (!Array.isArray(collection)) { report(`${name}: expected a list`); continue; }
      collection.forEach((row, index) => {
        const label = `${name}[${index}]`;
        if (!sameFields(row, fields)) report(`${label}: incorrect fields`);
        else records[name].push([label, row]);
      });
    }

    bucket = "rows";
    const rows = new Map(), commitments = new Set(), statements = new Map(), runs = new Map();
    for (const [kind, name] of [["statement", "statements"], ["acquisition", "acquisitions"], ["run", "runs"]]) {
      for (const [label, row] of records[name]) {
        await check(label, async () => {
          must(isStr(row.id), "id must be a string");
          const key = `${kind}\u0000${row.id}`;
          must(!rows.has(key), "duplicate row");
          const computed = await rowHash(kind, row);
          rows.set(key, computed);
          must(computed === hex(row.hash), "row hash mismatch");
          lines.rows.count++;
          if (kind === "statement") { commitments.add(hex(row.commitment)); statements.set(computed, row); }
          else if (kind === "run") runs.set(row.id, row);
        });
      }
    }

    bucket = "signatures";
    const signers = new Set();
    for (const [label, signature] of records.signatures) {
      await check(label, async () => {
        const digest = hex(signature.hash);
        must(statements.has(digest), "signature has no exported statement");
        const runId = statements.get(digest).run_id;
        must(runId === null || isStr(runId), "invalid run id");
        const run = runId === null ? undefined : runs.get(runId);
        const fp = await verifySignature(digest, signature.sig, run ? run.pubkey : undefined);
        if (fp) signers.add(fp);
        lines.signatures.count++;
      });
    }

    bucket = "documents";
    const listed = new Set();
    for (const [label, document] of records.documents) {
      await check(label, async () => {
        const key = file(files, ".", document.file);
        must(!key.includes("/"), "document must sit at the root");
        must(!listed.has(key), "duplicate document");
        listed.add(key);
        must((await sha256hex(files.get(key))) === hex(document.sha256), "sha256 mismatch");
        lines.documents.count++;
      });
    }
    for (const key of files.keys()) {
      if (!key.includes("/") && !listed.has(key) && key !== "manifest.json" && key !== "attestations.json") {
        report(`${key}: file not covered by the manifest`);
      }
    }

    bucket = "attestations";
    const manifestSha = await sha256hex(raw);
    const attesters = [];
    if (!files.has("attestations.json")) report("attestations: missing required attestations.json");
    else {
      await check("attestations", async () => {
        const signed = loadJSON(files.get("attestations.json"), true);
        must(Array.isArray(signed), "expected a list");
        const keys = new Set();
        for (let index = 0; index < signed.length; index++) {
          const row = signed[index];
          await check(`attestations[${index}]`, async () => {
            must(sameFields(row, ATTESTATION), "incorrect fields");
            must(isDict(row.sig) && row.sig.kind === "webauthn", "not a passkey sig");
            must(row.credential_id === get(row.sig, "credential_id"), "credential id mismatch");
            const fp = await verifySignature(manifestSha, row.sig);
            keys.add(fp);
            signers.add(fp);
            attesters.push({ name: row.name, key: fp });
            ok.push(`attestation by '${row.name}': key ${fp}`);
          });
        }
        // A key this browser could not check is counted, and the line says it was not checked.
        must(keys.size + (lines.attestations.skipped || 0) >= 2, "fewer than two distinct attesting keys");
      });
    }

    bucket = "chain";
    const entries = new Map(), rowEntries = new Set();
    for (const [label, row] of records.chain) {
      await check(label, async () => {
        const seq = integer(row.seq);
        must(!entries.has(seq), "duplicate sequence");
        must(isStr(row.kind) && Object.prototype.hasOwnProperty.call(CHAINED_FIELDS, row.kind), "unknown row kind");
        must(isStr(row.ref), "ref must be a string");
        for (const field of ["row_hash", "prev_hash", "hash"]) hex(row[field]);
        const key = `${row.kind}\u0000${row.ref}`;
        must(!rowEntries.has(key), "duplicate row entry");
        entries.set(seq, row);
        rowEntries.add(key);
        lines.chain.count++;
        if ((await link(row.prev_hash, seq, row.kind, row.row_hash)) !== row.hash) report(`${label}: entry hash mismatch`);
        if (!rows.has(key)) report(`${label}: chained row missing`);
        else if (rows.get(key) !== row.row_hash) report(`${label}: row hash mismatch`);
      });
    }
    for (const [seq, entry] of entries) {
      const prev = seq === 1 ? GENESIS : entries.has(seq - 1) ? entries.get(seq - 1).hash : null;
      if (prev !== null && entry.prev_hash !== prev) report(`seq ${seq}: prev_hash mismatch`);
    }
    for (const key of rows.keys()) {
      if (!rowEntries.has(key)) report(`${key.replace("\u0000", " ")}: missing chain entry`);
    }

    bucket = "proofs";
    const batches = new Map();
    for (const [label, batch] of records.batches) {
      await check(label, async () => {
        const [first, last, size] = ["first_seq", "last_seq", "size"].map((f) => integer(batch[f]));
        must(last - first + 1 === size, "batch size does not match sequence range");
        const root = hex(batch.root);
        must(!batches.has(root), "duplicate batch root");
        batches.set(root, batch);
        const selected = [...entries.keys()].filter((s) => first <= s && s <= last).sort((a, b) => a - b);
        if (selected.length === size) {
          must((await merkleRoot(selected.map((s) => entries.get(s).hash))) === root, "batch root mismatch");
        }
      });
    }
    const proven = new Set();
    for (const [label, proof] of records.proofs) {
      await check(label, async () => {
        const seq = integer(proof.seq);
        const index = integer(proof.index, 0);
        const root = hex(proof.batch_root);
        if (!batches.has(root)) throw keyError(root);
        const batch = batches.get(root);
        must(batch.first_seq <= seq && seq <= batch.last_seq, "proof outside batch range");
        must(index === seq - batch.first_seq, "proof index does not match sequence");
        if (!entries.has(seq)) throw keyError(seq);
        must(await verifyInclusion(entries.get(seq).hash, index, batch.size, proof.path, batch.root), "invalid inclusion proof");
        proven.add(seq);
        lines.proofs.count++;
      });
    }
    for (const seq of entries.keys()) if (!proven.has(seq)) report(`seq ${seq}: missing valid inclusion proof`);

    bucket = "anchors";
    const anchored = new Set(), anchorInfo = [];
    for (const [label, anchor] of records.anchors) {
      await check(label, async () => {
        const root = hex(anchor.root);
        must(batches.has(root), "anchor has no batch");
        must(["ots", "tsa", "partner"].includes(anchor.kind), "unknown anchor kind");
        const data = files.get(file(files, "anchors", anchor.file));
        const seen = { kind: anchor.kind, root };
        if (anchor.kind === "partner") partnerCheck(data, root);
        else if (anchor.kind === "ots") seen.pending = otsCheck(data, root);
        else seen.time = tsaImprint(data, root);
        anchored.add(root);
        anchorInfo.push(seen);
        lines.anchors.count++;
        ok.push(`anchor ${anchor.kind} ${root}: root binding verified`);
      });
    }
    for (const root of batches.keys()) if (!anchored.has(root)) report(`batch ${root}: missing anchor`);

    bucket = "blobs";
    for (const [label, blob] of records.blobs) {
      await check(label, async () => {
        const data = files.get(file(files, "blobs", blob.file));
        let good = true;
        for (const [name, actual, length] of [["sha256", await sha256hex(data), 64], ["md5", md5hex(data), 32]]) {
          await check(`${label} ${name}`, async () => {
            good = false;
            must(actual === hex(blob[name], length), `${name} mismatch`);
            good = true;
          });
        }
        if (good) lines.blobs.count++;
      });
    }

    bucket = "values";
    for (const [label, value] of records.values) {
      await check(label, async () => {
        const c = hex(value.commitment);
        if (!commitments.has(c)) report(`${label}: commitment does not match a statement`);
        const salt = value.salt;
        must(isStr(salt) && salt.length % 2 === 0, "salt must be hex bytes");
        hex(salt, salt.length);
        must((await commitment(fromHex(salt), value.value)) === c, "value commitment mismatch");
        lines.values.count++;
      });
    }

    if (!problems.length) {
      ok.push("rows, chain entries, batch proofs, blobs, documents, disclosed commitments and supplied signatures match");
    }

    // What this page did not check, said plainly.
    const tsa = anchorInfo.filter((a) => a.kind === "tsa");
    const ots = anchorInfo.filter((a) => a.kind === "ots");
    if (tsa.length) notRun.push({ key: "tsa", title: "The timestamp authority's signature on its receipt", detail: `${tsa.length} RFC 3161 receipt${tsa.length > 1 ? "s" : ""}. This page checked that each receipt names the batch root, not the CMS signature or which authority signed it.` });
    if (ots.length) {
      const pending = ots.filter((a) => a.pending).length;
      notRun.push({ key: "ots", title: "OpenTimestamps proof to Bitcoin", detail: `${ots.length} OpenTimestamps file${ots.length > 1 ? "s" : ""} for the batch root, ${pending ? `${pending} still waiting for a Bitcoin attestation` : "each carrying a Bitcoin attestation tag"}. Neither this page nor the offline verifier follows the proof to a Bitcoin block; that needs the network.` });
    }
    if (files.has("package.svg")) notRun.push({ key: "c2pa", title: "Content credentials on the cover (package.svg)", detail: "The C2PA claim on the cover is not checked here. Its file hash is covered by the manifest above." });
    if (signers.size) notRun.push({ key: "keys", title: "Who holds the passkeys", detail: `Each passkey signature carries its own key, origin and relying party, so it proves that some key signed. Compare these key fingerprints with the ones you were given: ${[...signers].sort().join(", ")}.` });
    for (const line of Object.values(lines)) {
      if (line.skipped) notRun.push({ key: `${line.key}-skipped`, title: `${line.title}: ${line.skipped} not checked`, detail: `${line.skipWhy}. Use a current browser or the offline verifier.` });
    }

    const result = finish();
    result.manifestSha = manifestSha;
    result.attesters = attesters;
    result.anchors = anchorInfo;
    result.info = describe(files, manifest);
    return result;
  }

  /* Title, audience and counts, and what the copy leaves out, from the package itself. */
  function describe(files, manifest) {
    const info = { title: null, audience: null, sentences: null, cited: null, withheldTitles: [] };
    try {
      const brief = loadJSON(files.get("brief.json"), false);
      if (isDict(brief)) {
        info.title = isStr(brief.title) ? brief.title : null;
        info.audience = isStr(brief.audience) ? brief.audience : null;
        const held = isDict(brief.files) && Array.isArray(brief.files.withheld) ? brief.files.withheld : [];
        info.withheldTitles = held.filter((f) => isDict(f) && isStr(f.title)).map((f) => f.title);
        if (Array.isArray(brief.sentences)) {
          info.sentences = brief.sentences.length;
          info.cited = new Set(brief.sentences.flatMap((s) => (isDict(s) && Array.isArray(s.statements) ? s.statements : []))).size;
        }
      }
    } catch { /* no readable brief: the documents check reports it */ }
    const list = (name) => (isDict(manifest) && Array.isArray(manifest[name]) ? manifest[name].filter(isDict) : []);
    const disclosed = new Set(list("values").map((v) => v.commitment));
    const withheld = Object.create(null);
    for (const s of list("statements")) {
      if (!disclosed.has(s.commitment)) withheld[s.prop] = (withheld[s.prop] || 0) + 1;
    }
    // A file the package withholds: its acquisition row carries a hash no shipped blob has.
    const shipped = new Set(list("blobs").map((b) => b.sha256));
    const withheldFiles = new Set(list("acquisitions").map((a) => a.blob_sha256).filter((s) => !shipped.has(s))).size;
    Object.assign(info, {
      withheldFiles,
      statements: list("statements").length,
      acquisitions: list("acquisitions").length,
      runs: list("runs").length,
      blobs: list("blobs").length,
      values: list("values").length,
      signatures: list("signatures").length,
      withheld,
      sourcesShipped: list("blobs").length > 0,
    });
    return info;
  }

  const api = { readZip, verify, canonOf: (text) => canonText(parseJSON(text, true)), md5hex, CHAINED_FIELDS };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SealbookVerify = api;
})(typeof self !== "undefined" ? self : globalThis);
