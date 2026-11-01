# Lessons

- `stk smoke` timed out on a page that opens an EventSource on load under Playwright 1.60 (networkidle never reached); Playwright 1.64 does not count the stream, so this app requires playwright>=1.64.1 and opens streams at once.
- A `stk run` started in the background on a taken port exits with code 3 and logs "Address already in use" while the old server keeps serving; check the log or the port's listener before assuming edits were applied.
