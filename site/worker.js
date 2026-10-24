// Static files come from the assets binding; only POST /api/invite reaches this code.
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
  // The row is stored first, so a failed alert loses nothing; the binding refuses any other recipient.
  ctx.waitUntil(env.EMAIL.send({
    to: "nidal@level09.com",
    from: { email: "invites@sealbook.org", name: "Sealbook site" },
    replyTo: { email: row.email, name: row.email },
    subject: `Invite request: ${row.email}`,
    text: `${row.email}\n\n${row.use_case || "(no note)"}\n\nReply to this email to answer them.`,
  }).catch((err) => console.error("invite alert failed", err.code, err.message)));
  return reply(200, { ok: true });
}

export default {
  async fetch(request, env, ctx) {
    const { pathname } = new URL(request.url);
    if (pathname === "/api/invite") {
      return request.method === "POST" ? invite(request, env, ctx) : reply(405, { error: "Use POST." });
    }
    return env.ASSETS.fetch(request);
  },
};
