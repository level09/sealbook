# Lessons

- 2026-11-01 | `stk smoke` timed out on a page that opens an EventSource on load under Playwright 1.60 (networkidle never reached) | require playwright>=1.64.1, which does not count the stream, and open streams at once | stk (covered upstream: stk requires playwright>=1.64, 2edbba4)
- 2026-11-01 | a `stk run` started in the background on a taken port exits with code 3 and logs "Address already in use" while the old server keeps serving | check the log or the port's listener before assuming edits were applied | stk (covered upstream: exits naming the pid, b29ccf0)
