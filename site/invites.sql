CREATE TABLE IF NOT EXISTS invite (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  email TEXT NOT NULL,
  use_case TEXT NOT NULL DEFAULT '',
  ip_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS invite_ip_time ON invite (ip_hash, created_at);
