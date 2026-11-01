# Lessons

- `stk smoke` loads pages until network idle, so a page that opens an EventSource on load never finishes; open the stream a beat after load, or smoke should ignore text/event-stream requests.
- A second `stk run` on a taken port fails with "Address already in use" in its log while the first, reloading Python but not templates, keeps serving: kill the port's listener before restarting, or template edits look ignored.
