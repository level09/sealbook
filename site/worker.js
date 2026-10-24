// Static files come from the assets binding; only POST /api/invite reaches this code.
const LIMITS = { name: 120, email: 254, organisation: 160, use_case: 2000 };
const PER_HOUR = 5;

const reply = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

async function sha256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function invite(request, env) {
  let form;
  try { form = await request.json(); } catch { return reply(400, { error: "Send the form as JSON." }); }
  if (form.website) return reply(200, { ok: true }); // honeypot: bots fill every field
  const row = {};
  for (const [field, max] of Object.entries(LIMITS)) {
    const value = typeof form[field] === "string" ? form[field].trim() : "";
    if (value.length > max) return reply(400, { error: `The ${field.replace("_", " ")} is too long.` });
    row[field] = value;
  }
  if (!row.name || !row.organisation) return reply(400, { error: "Add your name and organisation." });
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(row.email)) return reply(400, { error: "Check the email address." });

  const ipHash = await sha256(env.INVITE_SALT + (request.headers.get("cf-connecting-ip") || ""));
  const recent = await env.DB.prepare(
    "SELECT count(*) AS n FROM invite WHERE ip_hash = ? AND created_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 hour')",
  ).bind(ipHash).first("n");
  if (recent >= PER_HOUR) return reply(429, { error: "Too many requests from this network. Try again in an hour." });

  await env.DB.prepare(
    "INSERT INTO invite (name, email, organisation, use_case, ip_hash) VALUES (?, ?, ?, ?, ?)",
  ).bind(row.name, row.email, row.organisation, row.use_case, ipHash).run();
  return reply(200, { ok: true });
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/api/invite") {
      return request.method === "POST" ? invite(request, env) : reply(405, { error: "Use POST." });
    }
    return env.ASSETS.fetch(request);
  },
};
