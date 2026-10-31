// Static files come from the assets binding; only /api/* and the films reach this code.
const LIMITS = { email: 254, use_case: 2000 };
const PER_HOUR = 5;

const reply = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

async function sha256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function invite(request, env, ctx) {
  let form;
  try { form = await request.json(); } catch { return reply(400, { error: "Send the form as JSON." }); }
  if (form.website) return reply(200, { ok: true }); // honeypot: bots fill every field
  const row = {};
  for (const [field, max] of Object.entries(LIMITS)) {
    const value = typeof form[field] === "string" ? form[field].trim() : "";
    if (value.length > max) return reply(400, { error: `The ${field.replace("_", " ")} is too long.` });
    row[field] = value;
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(row.email)) return reply(400, { error: "Check the email address." });

  const ipHash = await sha256(env.INVITE_SALT + (request.headers.get("cf-connecting-ip") || ""));
  const recent = await env.DB.prepare(
    "SELECT count(*) AS n FROM invite WHERE ip_hash = ? AND created_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 hour')",
  ).bind(ipHash).first("n");
  if (recent >= PER_HOUR) return reply(429, { error: "Too many requests from this network. Try again in an hour." });

  await env.DB.prepare(
    "INSERT INTO invite (email, use_case, ip_hash) VALUES (?, ?, ?)",
  ).bind(row.email, row.use_case, ipHash).run();
  // The row is stored first, so a failed alert (or an unset INVITE_TO) loses nothing.
  ctx.waitUntil(alert(env, row).catch((err) => console.error("invite alert failed", err.code, err.message)));
  return reply(200, { ok: true });
}

// The recipient is the INVITE_TO secret, so no personal address sits in the repository.
async function alert(env, row) {
  await env.EMAIL.send({
    to: env.INVITE_TO,
    from: { email: "invites@sealbook.org", name: "Sealbook site" },
    replyTo: { email: row.email, name: row.email },
    subject: `Invite request: ${row.email}`,
    text: `${row.email}\n\n${row.use_case || "(no note)"}\n\nReply to this email to answer them.`,
  });
}

// Workers assets answer a Range request with the whole file (200), and Chrome cannot
// seek a video without 206 partial responses.
// ponytail: buffers the whole film per request, fine at 9 MB; move films to R2 past ~50 MB.
async function ranged(request, env) {
  const full = await env.ASSETS.fetch(new Request(request.url));
  if (!full.ok) return full;
  const body = await full.arrayBuffer();
  const size = body.byteLength;
  const range = request.headers.get("range");
  // Phone players check length and range support before playing.
  if (!range) {
    const headers = new Headers(full.headers);
    headers.set("accept-ranges", "bytes");
    headers.set("content-length", String(size));
    return new Response(request.method === "HEAD" ? null : body, { status: 200, headers });
  }
  const m = /^bytes=(\d*)-(\d*)$/.exec(range);
  let start = m && m[1] !== "" ? Number(m[1]) : m && m[2] !== "" ? size - Number(m[2]) : NaN;
  const end = m && m[1] !== "" && m[2] !== "" ? Math.min(Number(m[2]), size - 1) : size - 1;
  start = Math.max(start, 0);
  if (!m || Number.isNaN(start) || start > end) {
    return new Response(null, { status: 416, headers: { "content-range": `bytes */${size}` } });
  }
  const headers = new Headers(full.headers);
  headers.delete("content-length");
  headers.set("content-range", `bytes ${start}-${end}/${size}`);
  headers.set("accept-ranges", "bytes");
  return new Response(body.slice(start, end + 1), { status: 206, headers });
}

export default {
  async fetch(request, env, ctx) {
    const { pathname } = new URL(request.url);
    if (pathname === "/api/invite") {
      return request.method === "POST" ? invite(request, env, ctx) : reply(405, { error: "Use POST." });
    }
    if (pathname.startsWith("/media/") && pathname.endsWith(".mp4")) return ranged(request, env);
    return env.ASSETS.fetch(request);
  },
};
