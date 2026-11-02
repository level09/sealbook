# Lessons

- 2026-11-01 | `stk smoke` timed out on a page that opens an EventSource on load under Playwright 1.60 (networkidle never reached) | require playwright>=1.64.1, which does not count the stream, and open streams at once | stk (covered upstream: stk requires playwright>=1.64, 2edbba4)
- 2026-11-01 | a `stk run` started in the background on a taken port exits with code 3 and logs "Address already in use" while the old server keeps serving | check the log or the port's listener before assuming edits were applied | stk (covered upstream: exits naming the pid, b29ccf0)
- 2026-11-02 | on 17.4 a test calling `broadcast(msg, "fixture-target")` raised ValueError: int() of a non-numeric user id | test the bounded per-socket queue through `stk.websocket._deliver`; app code already passes numeric ids | stk (expected: user_id must be numeric; worth a clear error message naming the argument)
- 2026-11-02 | after 17.3.2 a test GET of `/static/css/app.css?v=abc` returned `no-cache`, not immutable | request the file's real versioned URL from `assets.asset()` | sealbook (stk caches only a matching version, as intended)
