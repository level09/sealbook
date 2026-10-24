# Lessons and gotchas

Record a concrete failure, the canonical fix, and the checks that protect it.
Do not turn this file into a second task list.

## Evidence and review

- Private review payloads leaked when only the inbox enforced participants. Exclude
  discussion from general monitor, reveal, history and MCP paths as well.
- Assignment checks must run when passkey options are issued and again at commit.
  Undo must check the underlying proposal, not just the review statement.
- Accepted corrections must remove predecessors from current views and origin
  support. Reproject both old and new values. Undo must restore both.
- A confirmed same-recording link changes corroboration tiers on other claims.
  Queue their actor profiles too, or freshness checks correctly hide stale vectors.
- Media review needs both sources available. Checking only the subject blob can
  reveal an erased counterpart. Media relationships stay non-citable until package
  closure covers both sources and their fingerprint basis.
- A rebuild must honor a rejected media pair in either search direction. Log the
  skipped candidate; keep the fingerprint rebuild and the human rejection.
- A partial unique index turns a concurrent insert into IntegrityError; map it to a 400 where the write
  is committed, and validate every field of a PATCH on its own, not only when paired.
- A passkey assertion signs one challenge, and every stored sig was checked against
  its own row hash, so one gesture could not sign several statements. A webauthn sig
  may now carry `batch` (1 to 512 unique ordered row hashes): its challenge is the
  sha256 of `sealbook-gesture-v1\n` plus that list as compact JSON, and the row must
  be in it (`offline_sigs.gesture_digest`). `verify_db` and offline packages check
  each row on its own. Known limitation: a package's batch lists every sibling hash,
  so it reveals how many rows the gesture wrote even when some are withheld or erased.
- Gesture undo first checked the reviewer only on review rows, and the gate honours
  any attested retraction, so anyone holding a gesture id could retract another
  analyst's incident rows. Undo now refuses rows the caller did not author.
- A finding's key state lived apart from its review, so accepting or undoing a
  finding through the plain review path would leave the key open or accepted for
  ever. The commit route now moves the key for any review of a finding statement and
  refuses a decision on a key that is no longer open.

- Never render a raw statement value. `display()` used to `str()` any value without a
  known key, so a same-person proposal printed `{'basis': [...], 'person': ...}` on
  Review and every page that showed it. `display(value, prop)` now labels each shape
  in words and never prints a structure or a `person:`, `statement:` or `blob:` id;
  pages that need more render the rich fields themselves.
- A review page that shows only the prop name ("name", "arrested at") and a generic
  file tile cannot be decided. Show the claim as a sentence with its value, the
  source at the exact span, who proposed it and who asked, or the reviewer opens
  three other pages first.

- People, Watching and Packages listed every case under a label for one, because their
  APIs took no investigation. Each list API now takes it (people through the facts'
  source, a brief through the statements it cites; an empty brief shows everywhere so a
  new one does not vanish). A page that shows a case label must send the same case.
- Notices were stored as "Request 13f6e019" so erasure had nothing to clear, and the
  bell showed that. Keep stored text bare and name the claim and its source at read
  time (`notifications.describe`), field and title only, never the value.
- A rule about what a person may ask for ("due date in the past") belongs in the
  request layer. Put in the service, it broke the demo seed, which makes overdue work
  on purpose.
- A 403 the client can predict is still a console error. Lists must carry whether a
  sensitive source is revealed in this session (`revealed`, `locked`) and the tile must
  not request it; Chrome logs every failed request whatever the code catches.
- Template and browser checks go stale quietly: the workflow check still named a
  select the review rewrite removed. Update the check in the same commit as the UI.
  Playwright `exact` role names fail on buttons with an icon glyph; match a prefix.

## Search and media

- Audio landmark selection must depend on local time, not a whole-file strength
  ranking or threshold. Otherwise an excerpt and its parent select different hashes.
- Assert a known excerpt's recovered time offset. Permissive similarity thresholds
  can make synthetic tones appear to validate a broken matcher.
- A repeated or low-information frame must not inflate visual coverage. Store real
  decode timestamps and paired sample positions, not a guessed interval.
- Negative audio offsets mean seek forward on the query side. Positive offsets mean
  seek forward on the reference side. Preview paired positions together.
- Coalesce queued actor jobs only for the same model. A running job needs a queued
  follow-up when facts change. A changed model alias needs explicit rebuild.
- Search vectors are not probabilities. DigUp's full-corpus statistical scale cannot
  be copied onto an approximate nearest-neighbor shortlist without evaluation.
- Normalized search must map back to original source offsets. Never normalize the
  stored evidence or use a shortened search string as citation coordinates.
- Apply identifier boundaries before the SQL candidate limit. Rejecting incidental
  substrings after limiting can hide a valid exact identifier.
- Backfill search keys with the same NFKC, Arabic, case and format-character rules
  as new indexing. Freeze conversion logic in the migration; test old rows through
  rollback/re-upgrade and the normal search query.
- Prepared media is still evidence-bearing data. Give each artifact a signed
  direct source link, and check every direct parent when bytes are shared. Segment
  cleanup alone cannot block access through a known artifact hash.
- A model alias and HTTP 200 do not prove that media was consumed. Ollama's
  `/v1/embeddings` rejects media; use native `/api/embed`. Distinct image/audio probes catch ignored
  inputs. Reference checks detect some same-alias drift, not all possible changes.
- Coverage must count vectors in the active configuration/reference generation.
  Historical completed jobs cannot make a new model configuration complete.
- Ledger canonicalization rejects floats. Hash reference vectors as hexadecimal
  float strings before sealing their digest with a run.
- pypdfium2 documents support a context manager, but pages require explicit close.
  Render and PDF extraction work runs outside the async request/worker loop.
- Every search embedded the query together with two image and two audio reference
  probes: about 2.5 s per query against 42 ms for the text alone. A text query only
  needs the text probes to detect drift; media consumption is checked when media is
  embedded. `embeddings.query` does this; never route a query through `call`.
- Exact and meaning hits were fused with RRF, so a meaning-only lead could outrank a
  literal hit, and one row per segment let one video's frames fill the list. Rank by
  exact signal tier first, then fused rank, and return one row per source.
- Phrase matching missed names said without a middle name ("Lina Najjar" against
  "Lina Omar Al-Najjar") and PDF lines extracted in visual right-to-left order
  ("999 الفرع"). An all-words match catches both; label it `words`, not literal.
- The people name keys found persons in any script but were never used to find the
  documents that name them, so "Lina Najjar" missed the Arabic list. Feed recorded
  spellings and gazetteer names back as needles and show which spelling matched.
- Search had no hash or file lookup, and a hash query returned meaning leads only.
- Cosine scores of irrelevant and relevant leads overlap on the demo data (0.69 for a
  place with no relevant item, 0.66 to 0.70 for right answers), as the gold set
  predicted. Keep leads unthresholded and labelled; never present a score as a
  probability.
- `stk demo-seed` never queued discovery for case-demo-01, so its register, order,
  testimony and post were not searchable; coverage reported `not_indexed` but the
  search page never showed it. The seed now queues indexing for every source it
  writes, as intake does.
- Two person records of one man at the same place and day are a shared presence to
  discovery: the demo's appeal and its recapture named Yousef as P1 and R1, and the
  run proposed "Both held at Douma". Expect this whenever duplicates are not yet
  linked; the reviewer rejects it with that reason and proposes a same-person link.
- An embedding failure aborted the whole search with 503. Exact results do not need
  the model: answer them and report the semantic failure next to them.

## Framework and UI

- Ctrl+C hung while any tab was open. Quart's Hypercorn runner waits for every
  connection before its grace timeout, and every page holds `/ws`; uvicorn waited
  forever on the monitor SSE stream. `stk run` now serves through uvicorn and every
  launch line sets `--timeout-graceful-shutdown 3`. Check with a monitor tab open.
- Test configs inherit `.env`. Pin every model/provider and auth switch on which a
  test depends; otherwise a local secret can cause a real network call.
- Fresh database fixtures can reuse user IDs while old tests leave process-global
  WebSocket queues. Isolate the registry for the review fixture, or unrelated test
  connections can trip its per-user limit. This does not change live delivery.
- Every browser mutation sends a JSON object, usually `{item: {...}}`. An empty
  Axios POST failed the global body check on media reveal.
- Capture linked statement/document parameters before filter refresh replaces the
  URL. Otherwise the detail view silently opens the default source.
- Vue cannot see page script variables from template expressions. Expose them on
  the component or global properties, and inspect the rendered result.
- Place JSON boot scripts in the Jinja `js` block, outside the Vue mount element.
- The `${ }` delimiter ends at the first `}`, so an interpolation holding an object
  literal (`${ (x || {}).y }`) compiles to a broken render function and the page stays
  on the boot frame with only "Unexpected token ')'" in the console. Move such
  expressions into a method. A server started without debug also keeps its template
  cache, so restart it before judging a template edit.
- Selectin relations load on a query, not on `add()`. Accessing an unloaded relation
  in async code raises `MissingGreenlet`; fetch it through the session first.
- SQLite migration lessons apply only to framework tests: DDL is not transactional,
  new required columns on existing rows need defaults, and batch foreign keys need
  constraint names. The evidence application uses PostgreSQL.

## Working with agents and shared trees

- Several agents committing in one working tree sweep each other's staged files:
  `git add <path>` plus `git commit` commits the whole index, including what another
  agent staged. Commit with explicit paths, `git commit -m "..." -- <paths>`, which
  records only those paths whatever else is staged. Commits 710d231 and bbc3a69 each
  carried another task's work this way, the second while recording this lesson.
- An uncommitted edit to a shared file (`stk/cli/__init__.py`, `stk/cli/main.py`)
  vanished mid-task while another agent worked in the tree, and `stk discover` was
  briefly unregistered. Commit each edit soon after making it, with explicit paths
  (`git commit -m ... -- <paths>`), and check `git diff -- <paths>` right before
  committing so a reverted or foreign hunk is seen, not assumed.
- `acceptance/test_v0.py` runs its steps in the `ORDER` list through `load_tests`.
  pytest ignores `load_tests`, so steps ran alphabetically and consumers ran before
  producers. `acceptance/conftest.py` applies `ORDER` under pytest. Add a new step to
  `ORDER`, not only as a method.
- Removing a static fallback (the monitor gazetteer) silently emptied every track and
  map point, because nothing confirmed places yet. When a projection gains a human
  gate, give the scenario, the seeds and the local database a product path through
  that gate in the same change, and run the acceptance file, not only unit tests.
- After a migration lands, the owner's local database stays on the old revision until
  someone runs `uv run stk db upgrade`; pages then look emptier rather than failing.
  Report the upgrade with the commit. Seeds are additive and refuse an existing case,
  so a changed seed needs a rebuild of the local demo, and a rebuild drops the owner's
  login: back it up (`pg_dump`) and restore the admin row.
- `uv run alembic` in this repo can read a different config than `stk db`. Use
  `uv run stk db current|upgrade` for the app database.
- A file another agent has edited cannot be committed whole. Committing
  `EvidenceViewer.js` after one's own edit swept another task's hunks, which imported
  an untracked `focus.js`, so HEAD no longer loaded on a clean checkout. Before
  committing a shared file, read `git diff -- <path>`; when foreign hunks are there,
  build the blob with only your change and stage it with `git update-index
  --cacheinfo`, or apply your hunk with `git apply --cached`. Never chain a commit
  after a heredoc script that may fail: a failed rebuild left the amend to run on
  another agent's newer commit.
- A temporary index copied from `.git/index` and reset to HEAD goes stale the moment
  another agent commits. Commit 33230c6 was built on such an index while 4b2270c
  landed, so it silently reverted that commit's docs (restored in b7509dd). Build
  the temporary index with `git read-tree HEAD` right before staging, record HEAD,
  and commit only if HEAD is still that commit; then point the real index at the
  new HEAD for the committed paths (`git update-index --cacheinfo`), or `git
  status` shows them as staged reverts.

## Design work

- Mockups that nobody puts in front of the owner do not count as approved, and a goal
  worded "backend only" kept the approved redesign out of the app. Publish or link
  mockups the moment they exist, ask for the decision, and once approved build the
  real screens before more backend.
- Drawn or generated pictures read as fake to the people this tool serves. Use real,
  openly licensed incident photographs (Wikimedia Commons, licence checked on the file
  page, credits verbatim), never tie a real place to the synthetic case's fictional
  claims, and replace them everywhere at once: site, seeds and acceptance fixtures.
- Real photos lowered text-to-image recall@1 from 0.75 to 0.50 on the four-query set.
  Report measured drops; do not tune queries after measuring.
- Design for large cases from the first screen: server-side tables, paging, sorting
  and bulk actions. Two demo cases with 34 sources hide every scaling problem.
- The jump on every page load came from `v-cloak` on the whole `#app`: until the
  scripts at the end of `<body>` mounted Vue (about 0.8 s at 4x CPU throttle), the
  first paint was an empty, unthemed white page, so a dark session flashed white and
  the shell popped in at once. Inter came from Google with `display=swap` and started
  loading only once text appeared, then swapped. CLS missed all of it, since nothing
  visible moved; a per-frame log of `#app` display, `document.fonts.status` and body
  colour showed it. The layout now paints a static frame (`#sb-boot`, same boxes as
  the mounted bar, rail, hint bar and bottom bar) and sets the theme class on `<html>`
  in the head, preloads self-hosted Inter (`font-display: optional`) and the Tabler
  font, and removes the frame on mount. Async page content that arrives after mount
  reserves its slot (the evidence coverage note, the monitor's live label and map
  attribution) instead of inserting a box above other content.

## Sheets

- A CSV is `text/csv`, so the `text/` branch queued text extraction with character
  spans. Check sheet types before generic prefixes, and take the type from the
  extension: browsers send CSV as `application/vnd.ms-excel` or nothing.
- `csv.Sniffer` picked no delimiter on a short semicolon sample. Span validation needs
  the same parse every time; the header line's most frequent delimiter decides.
- Local gemma4 12B spent the whole 4096-token context thinking and answered nothing
  (394 s, empty content). Send `reasoning_effort: none` on the local route. Even then
  two rows took over the 120 s route timeout; one row per call is about 40 s.
- The model used `seen_at` as the default for the place column: 10 of 21 position
  claims said "seen at" where the row said killed or displaced there. Saying when
  `seen_at` applies cut that to 5; the rest is reviewer work, and events carry no place.
- Adding props to `EXTRACTED_PROPS` changes the MCP tool schema; regenerate
  `tests/snapshots/mcp_tools.json` from the live `tools/list`, and mask new personal
  props in `monitor.data.PERSONAL`.

## Thumbnails

- Pillow keeps no EXIF unless it is passed back to `save`, but orientation must be
  applied with `ImageOps.exif_transpose` before resizing or tiles come out sideways.
  Test with an image whose EXIF says rotated.
- A video's first frame is often black. Sample several positions and score contrast;
  use the earliest if all are dark.
- A new worker tool needs a `policy` row (capability `thumbnail`); without it the run
  fails at the route check with "no policy". Add the migration with the tool.
- Test fixtures that write human statements need a doc_type that has a policy row
  (`register`), not an invented one.
- A rebuild guard that refuses on any pending run of a source turns every new queued
  tool into a blocker: adding `thumbnail` to `queue()` made fingerprint rebuilds
  refuse. Refuse only on a run already reading the source (`running`); a queued run
  of the same tool becomes the rebuild.

## Speech to text

- faster-whisper returns numpy floats for times and probabilities. orjson refuses
  them, and the ledger's canonical JSON refuses any float (run inputs carry the
  language to continuations). Record ms as int and probabilities as decimal
  strings; `tests/test_speech.py` round-trips a fake model's numpy output through
  `chain.canon`.
- A worker process keeps the code it started with. During this build a worker
  started mid-change claimed the first transcribe runs with half-written code and
  failed them. Restart workers after changing a tool, and when checking one by hand,
  lease the runs yourself before processing so a stale worker cannot claim them.
- The queue lease is five minutes and is not renewed. A long transcription inside
  one run would be reclaimed by another worker. Each run reads a bounded window and
  stops taking segments a minute before its lease, then queues a continuation from
  the end of its last segment, so a slow machine reads less per run instead of
  losing the run. Decoding 30 s past the window lets the segment that straddles it
  finish, so words are not cut.
- No speech is a result, not a failure: Retry would find none again, and soundless
  demo clips showed as red failures. Only a missing model, missing ffmpeg or an
  undecodable file fail.
- Content addressing means a second investigation with the same bytes gets no new
  transcription: runs are per blob. A throwaway case for measuring needs new bytes.
- Whisper writes Damascene suffixes in MSA spelling (إنّو as انه, بحرستا as بحرسته).
  Score word error after folding, and expect exact place-name search to miss
  dialect spellings.
- Browsers keep static files. After a deploy the owner's browser ran an old
  `components/index.js` that referenced a deleted component and every page stayed
  on the boot frame until a hard reload. Static URLs now carry `?v=<content hash>`
  (`asset()` in `stk/assets.py`), versioned responses are `immutable`, unversioned
  ones `no-cache`. Module imports are rewritten by one import map in the layout, so
  new JS files are covered without edits. Use `asset('...')` for any new static URL
  in a template; a raw `/static/...` path in markup is refetched only by validation.

## Places and maps

- Alembic `op` calls apply the metadata naming convention to names already written
  in full: `create_check_constraint("ck_place_x", ...)` became
  `ck_place_ck_place_x` and the drift check failed. Wrap full names in `op.f()`.
- PostGIS adds `spatial_ref_sys`, which autogenerate then offers to drop; the drift
  check and `env.py` skip extension tables. Framework tests that `create_all` on
  SQLite cannot create PostGIS columns or generated columns calling `ST_*`
  functions, so they create `portable_tables()` only.
- Generated columns change on flush; in async code a later attribute read lazy-loads
  and raises `MissingGreenlet`. `eager_defaults` on the mapper returns them with the
  insert or update.
- Per-row correlated subqueries (source lookup, erasure check, accepted links) made
  one tile take over nine minutes at 100,000 claims. Set-based CTEs brought it to
  seconds, but the planner still estimated one row for grouped CTEs and reran them
  in nested loops (100 s); `MATERIALIZED` plus `enable_nestloop = off` for the build
  transaction gave about 1 s. Measure on the scale database before trusting a plan.
- Recomputing resolution for every tile repeats the same second of work up to a dozen
  times per view. Build the reader's model once and cut tiles from it; key it on what
  can change the map, not only the chain head, or every access log empties the cache.
- Grid clusters whose cells are not aligned to tiles lose members: a cell owned by a
  tile the view never loads vanishes (5 of 13 demo points showed at zoom 5). Make
  cells divide the tile and test that every point lands in exactly one tile.
- The Protomaps build list refuses Python's default user agent (HTTP 403); send one.

## "Open span" that did nothing visible (2026-10-24)

On a short Arabic text source, choosing a claim gave its span a faint outline and
"Open span" only nudged the page. The span offsets were right (each slices out its
value exactly, RTL included). Three causes: the action only handled time media and
PDF page numbers, so text, image and sheet sources had no behaviour at all; it used
`scrollIntoView({block: 'nearest'})`, which scrolls the page instead of the preview's
own scroll box and does nothing when the span is already on screen; and the
selected style was a 2 px outline. A first fix then zoomed images to the wrong
region, because it measured with `getBoundingClientRect` while the zoom transform
was still transitioning. Lessons: give every source kind an explicit "show" result
(centre in the pane, strong tier highlight, pulse, position), scroll the owning
container rather than the page, and measure transformed content with layout
offsets (`offsetLeft`, `offsetWidth`), never client rects. Normalised claim values
(`2013-09-17`) do not occur in the source; search for the characters the span
covers in the text it was read from (`span_blob`).

## QA pass in a real browser (2026-10-24)

- A list of tiles asked `/thumb` for every row, and text, web and sheet sources never
  get one, so each answered 404 and Chrome logged it (117 console errors on one walk).
  A missing derivative is not an error: the tile route answers 204 and the component
  skips kinds that never have a thumbnail. Sensitive tiles still answer 403 by design.
- Search merged a second probe's signal into a passage found by the first, but not
  the spelling that probe matched, so the reason read "gazetteer name undefined".
  When merging evidence for one hit, merge every field the reason reads.
- The case switcher is a shell contract (`useCase`), and pages that kept their own
  case picker (search, intake, the map) ignored it: intake defaulted to the first
  case, the map to the server's first. Every case-scoped page reads the stored case
  at start and implements `useCase`.
- A container's duration can run past the last video frame (a longer audio track,
  rounding). Sampling a frame window there decoded nothing and failed the whole
  embedding with "decoded frame has no source timestamp". An empty decode is a
  window without a frame, not a fault.
- Failure details showed whole ffprobe command lines, and Retry was offered for tools
  the server refuses to retry. The failure row says whether it can be retried, and a
  `CalledProcessError` reads as "could not decode this file".
- A confirmed sheet whose rows could not be queued (no model on an allowed route) said
  "Nothing to read", which was false. Report the reason the server gave.
- People were labelled by id slices almost everywhere because each page built its own
  label from the statement in hand, which is masked when its source is sensitive.
  One server helper (`people.names`) picks the best name the reader may see across
  linked records; pages number the rest "Unnamed person N" with a lock.
- Synthetic filler records (`storage_ref` "synthetic") have no bytes, and the content
  route raised a 500 the viewer reported as "the server did not answer". Missing
  bytes are `blobs.MissingBytes`, answered 404 `no_file`; the viewer separates no
  answer, no file, not allowed and undecodable media.
- The dev server does not reload Jinja templates; a template edit is live only after
  a restart (a Python edit restarts it). Check the served page before trusting a
  browser result.
- WebAuthn refuses an IP address as relying party ("invalid domain"). Browser tests
  that sign with a CDP virtual authenticator must use `localhost`, not `127.0.0.1`.

- The site's signature and hash checks use `crypto.subtle`, which browsers expose only on secure
  origins. sealbook.org also answered plain http without a redirect, so the checks threw. The page
  now redirects http to https before anything runs. Show model output as heard and mark its
  errors against the script; never correct it silently.

## Web research (2026-10-24)

- Vuetify styles live in CSS layers, so any unlayered rule wins. `.mon button {
  color: inherit }` in monitor.css turned the drawer's primary button inside the
  person page black on black. Mount drawers and dialogs outside the `.mon` wrapper.
- Killing `uv run stk run` left its server child listening, and the restarted server
  failed on the busy port while the old one kept serving a cached template. Stop a
  dev server you started by its port (`lsof -tiTCP:<port> -sTCP:LISTEN`), never by a
  pattern that could match the owner's server.
- A temporary index built with `git read-tree HEAD` goes stale when another agent
  commits before yours: the first version of 7488624 carried the previous
  `acceptance/check_workflow.py` and silently reverted 932be80 (amended before push), and the real index
  then showed every committed path as a staged reversal. Read the tree, stage and
  commit in one command, check `git show --stat HEAD` for paths you did not touch,
  and afterwards point the real index at the new blobs with `git update-index
  --cacheinfo` rather than any reset.
- A file removed with `git rm --cached` but still on disk comes back when committed
  with `git commit -- <path>`: a pathspec commit takes the working tree copy. The
  wrangler account cache stayed tracked that way. Untrack with `git rm --cached`,
  commit from the index, then check `git ls-files <path>` is empty.

## Disagreements and display text (2026-10-24)

- The person page flagged "seen at Aleppo" and "seen at حلب" on one day as a fork,
  then listed the pair twice (as a value and as a position) with raw statement ids.
  Track steps compared map labels, and labels are per spelling. Compare places by
  resolved id and other values by `places.normalise`, never by display text; build
  each disagreement once and name each side by its value and source file. Stories
  and the map read the same `track()` flag, so the fix lives there, and Stories now
  draws one marker per resolved place labelled with every spelling.
- The due date check compared to the UTC date, so an analyst west of UTC in the
  evening could not pick their own today. "Today" on the server is the earliest
  date anywhere (UTC minus 12 hours); the client already offers its local date.
- Desk "Findings waiting" printed place links with no separator. A `v-for` over
  links needs the same comma rule as the people list next to it.
