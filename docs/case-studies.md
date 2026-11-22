# Case studies

A case study is a short fictional puzzle whose twist rests on one capability of the
app. One directory serves as the demo, a starting template and a scored test, in the
manner of [Agent evaluation](agent-eval.md). Every case is fictional and says so.

## The contract

```
acceptance/cases/<slug>/case.json      the planted case and its truth
acceptance/cases/<slug>/make.py        regenerates case.json and the files
acceptance/cases/<slug>/files/         sheets, scans, pictures, ATTRIBUTION.md
```

```sh
uv run python acceptance/cases/<slug>/make.py                              # regenerate
uv run python acceptance/eval_agents.py --case acceptance/cases/<slug>/case.json
uv run python acceptance/eval_agents.py --case acceptance/cases/<slug>/case.json --live
uv run stk demo-seed --case <slug>                                         # into the app
```

`case.json` has the shape of `acceptance/agents/case.json` (`about`, `investigation`,
`today`, `places`, `sources`, `records`, `clusters`, `claims`, `batch`, `truth`) plus a
`story`: `title`, `kind`, `puzzle` (two or three sentences), `twist` (one or two),
`rests_on` (capability names) and `fictional: true`. The evaluation ignores `story`.

Keys the seeder reads beyond that shape, all optional:

- `sources.<key>.file`: a path under the case directory or its `files/`.
  `sources.<key>.text`: the source's words, ingested as a text item when it has no
  file. With neither, the seeder writes a text stand-in listing the source's claims.
  `sources.<key>.origin` overrides the acquisition origin.
- `sources.<key>.transcript`: a file of fixed words for a scan or a recording. A
  scan's text is written by the app's OCR tool and a recording's sentences
  (`{"duration_ms", "sentences": [{"text", "start_ms", "end_ms"}]}`) by the speech
  writer, as a demo run with fixed output; the source's claims quote that text.
- `sources.<key>.post`: `{"title", "text", "doc_type"}`, the post a file arrived
  with. It is its own text item beside the file, as a page capture keeps the page
  beside its media, and the source's claims quote it.
- `claims[].pin`: where the claim sits. A sheet cell (`{"sheet", "row", "column"}`,
  the header is row 1), a box on a scan (`{"page", "bbox"}`), or a quote in the
  source's text (`{"text", "after"}`). A claim with no pin quotes its own value from
  the source's text. Every extracted claim needs a span: the gate rejects an
  agent's claim without one.
- `claims[].record`: a key of `records` (a person), or a source key for a claim
  about the document itself, such as the event a post reports (`reports`).
- `claims[].event`: the kind of an `event` or `reports` claim.
- `places.<key>`: `name_en`, `name_ar` (may be empty), `lon`, `lat`, `precision_m`.
  Places with coordinates are imported as sites a person recorded, as a town
  (2 km or more), a neighbourhood (1 km) or a site, and every located claim that
  names one is confirmed. A place without coordinates stays a name and is never
  drawn. `places.<key>.fictional: true` marks an invented place; the seeder sets
  `place.fictional` from it, and the case map, Stories, a person's Track on the map
  and the place page then draw a schematic: no coastline, tiles or region outline,
  the places at their relative positions with a scale, labels moved apart with a
  leader line when they would overlap and wrapping rather than running off the
  map, no gazetteer credit, and the note "Invented places, drawn to
  relative position, not a real map". The four cases mark every place. Invented
  places lie in open sea so that no real town reads as the scene; the marker, not
  the coordinates, says they are invented. Sources arrive from the channel "Case
  study: <title>".
- `conclusion`: `{"text", "standard", "rests_on", "contradicts", "alternatives":
  [{"text", "why"}], "not_established": [...], "peer_review": true}`, the case's signed conclusion
  (`sealbook/cases/conclusions.py`). `standard` is a key of
  `sealbook/method/standards.csv`. Each `rests_on` item is a claim key, `"links"`
  (every accepted same-person link), `"same_person"` (the linker's proposals that
  join two records the truth plants as one person) or `"copies"` (the strong match
  proposals inside the planted family of copies, accepted and then cited, so the
  conclusion rests on the match itself; `"copies:<key>"` keeps those joining that
  source to another file and `"copies:<key>,<key>"` the match between two
  sources). `contradicts` (optional) lists claim keys the conclusion finds
  against, such as a statement it shows to be wrong: cited as what was said, not
  accepted, never counted toward what it rests on, and resolved like a
  `rests_on` claim key, so on a read source the claim extraction wrote stands
  for its key. Write an alternative without a closing full stop: the page adds
  one.

`truth` keys the case does not plant may be left out or empty. The scores that read
them come out `null`, and so do their targets, instead of a failure. Same-person
pairs are scored per identity cluster: a planted pair names one record on each side,
and records already joined in `clusters` count as one side.

`stk demo-seed --case <slug>` refuses a case that is already seeded. Sources enter
through intake in date order, and the worker's local tools (geotag, thumbnail,
fingerprint, index) run on each before the next, so a clip is compared only with
clips already held and a photograph gets `photographed_at` from its EXIF. Each claim
is an extraction statement. A sheet's rows are separate mentions, as the sheet
intake writes them: a record's first row is the record and each later row a person
the desk has already joined to it. A case analyst accepts the claims planted as
`accepted` and each planted cluster's same-person links with a passkey signature
and confirms the planted places. Then the agents the planted truth scores run on
the fixed answer, so their proposals wait for a person: the linker for
`same_person` or `twins`, the contradiction hunter for `contradictions`,
`not_contradictions` or `trap_contradictions`, the gap finder for `gaps` or
`not_gaps`, and the intake brief for a `batch`. Last, with a `conclusion`, the case
analyst (Ines Halvorsen, an invented name) accepts exactly the items it rests on and
nothing else, signs the conclusion, which marks the case concluded, and a second
analyst (Tomas Reyes) signs agreement as peer reviewer. In four-names the
analyst declines the surveyor's proposal in act 7, with her reason; seed to
`--until 6` to leave it open. Old-footage has no weak matches left to
wait: a sound match is proposed only when `SOUND_MIN_LANDMARKS` (4) landmarks line
up, and its analyst confirms the strong matches inside the planted family as the same
recording in act 5. Since 2026-11-22 `"copies"` cites the strong matches it accepts,
and a conclusion can rest on them beside the two posts' words. In a worktree, set
`BLOB_DIR` to the main checkout's `instance/blobs` so the main server can read the
files.

What the pages read: Stories and the Case map show dated person claims of a
located kind (`seen_at`, `last_seen`, `arrested_at`, `detained_at`,
`detention_branch`, `transferred_to`); the map draws only those whose place a
person confirmed. Incidents come from accepted incident statements, which only a
person's decision on a finding writes.

Not handled yet: a sheet goes through the mapping step only when its source carries
`sheet` (`{dataset, key_column, mapping}`) and an act runs `sheets`; otherwise it is
ingested as a file with no dataset version; PDF spans fail (`pages_required`,
see `blobmeta.py`); audio and video `{"ms"}` pins are untried (claims quote the
transcript instead); incidents, findings and assignments are seeded only where an
act asks for them (unwritten-name seeds one accepted incident and one rejected
finding); an act's `day` is the story's, while the ledger records
seed time. The old-footage case has no people, so its Stories and Case
map stay empty.

## Acts: a case told in time

A case may carry an ordered `acts` list in `case.json`. Each act is a moment of the
investigation: sources arrive, then the app or a person takes steps. The seeder
plays the acts in order through the real write path, and
`stk demo-seed --case <slug> --until <n>` stops after act n, so the case stands
exactly as it did then: later sources absent, later decisions not made. The same
list is meant to drive the case's site page and the film. A case without `acts`
plays one act that does what the seeder always did (planted claims, accepts,
clusters, places, the agents its truth scores, the intake brief, the conclusion).

```json
{
  "title": "The phone exhibits and the voice note",
  "day": "2026-03-20",
  "story": "One or two plain sentences on what happened.",
  "shows": ["reading"],
  "sources": ["mq3", "mq4", "voice"],
  "steps": ["read", "places", "brief"],
  "see": ["/evidence/{source:voice}", "/people/{person:mara_stmt}"]
}
```

- `title`, `day` and `story`: what a reader sees. `day` is the investigation's day
  for the story; the ledger still records seed time (see the limits below).
- `shows`: what the act demonstrates, from a fixed vocabulary (`SHOWS` in
  `sealbook/monitor/study/__init__.py`): reading, first pass, search, same person,
  contradiction, gap, copies, standing question, assignment, peer review,
  discovery, sheet matching, timeline, map, conclusion, package. A new word is a
  one-line change there, with a reason.
- `sources`: the source keys arriving in this act. Every source arrives in exactly
  one act; an act may bring none. Arrival is intake plus the worker's local tools.
- `steps`: what runs or who does what, in order. A string names a step; an object
  is `{"do": "<step>", ...arguments}`.
- `see`: app paths where the act shows, built from stable keys: `{source:KEY}`
  becomes the source's evidence id, `{person:RECORD}` the person written for that
  record (planted, or matched among what the app read), `{case}` the case slug.
  The seeder returns them resolved and `stk demo-seed` prints them per act.

The player refuses, before writing anything, an act without one of these keys, an
unknown step or `shows` word, a source arriving twice or never.

### Steps

| Step | Module | What it does |
| --- | --- | --- |
| `claims` | `claims.py` | The planted claims of arrived sources the app does not read, pinned where the case says |
| `accept` | `claims.py` | The case analyst accepts, signed, the claims `claims` names, or those planted as accepted |
| `clusters` | `people.py` | The desks' own accepted same-person links, and each sheet row joined to its record; `"only": "rows"` joins the rows and leaves the planted clusters to `join` |
| `links` | `people.py` | The analyst accepts the linker's open proposals that join records the case plants as one person (a cluster or a truth pair), and no other |
| `places` | `places.py` | Imports the planted places once; confirms the place of each written located claim |
| `agents` | `agents.py` | The agents the planted truth scores, on the fixed answer |
| `agent` | `agents.py` | One agent by name: `{"do": "agent", "agent": "linker" \| "contradictions" \| "gaps"}` |
| `brief` | `agents.py` | The intake brief for the case's `batch` |
| `read` | `reading.py` | The app reads the act's sources marked `read`, replaying recorded model answers |
| `first pass` | `work.py` | The analyst accepts the first pass the app proposed on `sources` |
| `assign` | `work.py` | The owner assigns `sources` to the colleague (Noor Adeyemi), due in `days` |
| `ask` | `work.py` | The owner asks the colleague to check the claim `claim`, with `why` |
| `reply` | `work.py` | The colleague answers on that claim with `text` |
| `mark` | `work.py` | The Bayanat work step on assigned `sources`: `done` (Updated, by the colleague) or `returned` (Returned for changes, by the owner) |
| `conclusion` | `conclusion.py` | The case's signed conclusion and the peer agreement |
| `join` | `people.py` | The analyst joins `records` (or every planted cluster) as one person, her own signed decision with `why` |
| `planted` | `claims.py` | Fallback for a read source: the planted `claims` extraction did not propose, by a run labelled planted, with `why` |
| `sheets` | `sheets.py` | The act's sheets with a `sheet` mapping: `sheets.service.confirm` writes the dataset version, then `extract_sheet` reads the released rows with fixed per-row answers from the case's cell values; each planted sheet claim is matched to its cell |
| `decline` | `decide.py` | The analyst declines, signed with `why`, each open same-person proposal joining records the truth plants as `twins` |
| `package` | `package.py` | `title`, `audience`, `sentences: [{text, cite}]`, `save`: writes the brief, runs `stk anchor` once, builds and signs (`stk demo-cert` in development), two analysts attest, finalizes, saves the export and checks it offline; without a certificate or receipts it stops and says what the build waits for |
| `question` | `standing.py` | The owner asks the standing `question`, with no schedule (it wakes on evidence only); its first check reads what the case holds |
| `wake` | `standing.py` | The extraction runs of the act's arrived claims wake the question (`tasks.evidence`) and the woken check runs now; nothing before a question exists |
| `discover` | `discovery.py` | Runs discovery, then the analyst decides `month`'s findings in one gesture each: `incident` `{title, keep, roles}` accepts the candidate incident keeping the named claims, `reject` rejects the shared presence with that reason |
| `matches`, `confirm copies`, `search` | `matches.py` | Count the match proposals; the analyst confirms, signed, each strong match inside the planted family as `relationship` (default same_recording) with `why`; the desk's keyword search for `query`, logged as the search page logs it, its hits in `out["search"]` |

Adding a kind of step: write `sealbook/monitor/study/<name>.py` with

```python
from sealbook.monitor.study import step

@step("my step")
async def my_step(st, act, args) -> None:
    ...  # st.session, st.s (Seeder), st.written, st.subjects, st.analyst, st.owner
```

`load()` imports every module of the package, so nothing else is edited. A step
writes through the app's own services or the worker's tools, signs a person's
decision with `st.s.review` or `st.s.assertion`, and must be safe to run when its
inputs have not arrived yet (it acts on what `st.arrived` and `st.written` hold).
Keep state a later act needs on `st` (`st.others` for named things such as a
review request). Add a test of the new step on a small spec, as
`SeedStudyTest.test_acts_stop_at_their_boundary` does.

### Read by the app, recorded

A source marked `"read": true` gets no planted claims. Intake queues the worker's
own runs for it (OCR for a scan, speech to text for a recording, extraction and the
first pass), and the `read` step processes them, and the runs they queue, until
none is left. Each model answer comes from `recordings.json` beside `case.json`:

- `route` and `models` (extract, ocr, assess, speech, transcribe): the
  configuration it was recorded with. Seeding pins these for the case, so intake
  queues the same runs on any machine, and replay needs no route key. A recording
  made before `transcribe` (the hosted speech to text model) existed replays every
  recording on the local model, as it was read.
- `calls`: one per chat request, keyed by the hash of its model, messages and
  schema (the prompt and the input): `source`, `tool`, `input_sha256`,
  `prompt_hash`, `route`, `model_requested`, `model_served`, `provider_served`,
  `recorded_at`, `status`, `response` and `response_sha256`. A hosted
  transcription is a call too, keyed by its model, settings, source and window
  (`cursor`) rather than the audio bytes, with OpenRouter's `generation` id.
- `speech`: the local speech model's segments for each recording it read,
  replayed through the transcript writer.

Replay goes through the route code's own HTTP transport, so the tools, the route
checks and the provenance they write are the real ones, and each replayed run says
in its inputs that the answer was recorded and the model did not run. A request
with no recording (the prompt, the input or the model changed) fails its run and
seeding stops with the reason; a response whose hash no longer matches stops it
too. With no `recordings.json`, seeding a case with read sources refuses instead
of falling back to planted claims. `make.py --record` reads the case again on the
live routes in a throwaway database (`stk demo-seed --case <slug> --record`); it
needs OPENROUTER_API_KEY and the local speech weights (`SPEECH_MODEL_DIR`), resumes
after an interruption, and keeps only the answers asked for: a model added since
the last recording (as `transcribe` was on 2026-11-22) keeps the other tools'
answers, so only the calls whose input changed run again. Never edit the file.

After reading, each planted claim of a read source is looked for among what
extraction wrote: same source, same person (matched by the record's name), same
property, the same words (for a place, one naming inside the other) and the same
date. A recovered claim stands for its key, so places, accepts and the conclusion
apply to what the app wrote; `out["read"]` counts planted, recovered and written.
The planted truth stays what `acceptance/eval_agents.py` scores.

### Worked example: never-there

`acceptance/cases/never-there/` is read by the app: every source is marked `read`,
four papers (Mara's and Jo's statements, the exhibit sheets MQ/3 and MQ/4) are
scanned pages from `make.py --scans`, and the other 47 sources stay typed text.
The recording holds 110 chat calls on cloud-zdr (5 OCR with
`google/gemini-4-argon`, the four scans and the photograph, and 53 first passes
with `google/gemini-3.8-flash`, both recorded on 2026-11-21; 52 extractions with
`anthropic/claude-haiku-5.5`, recorded again on 2026-11-22 after the extraction
prompt began asking for whereabouts, so only those 52 calls ran) and one hosted
speech to text call (`elevenlabs/scribe-v2`, recorded on 2026-11-22 with the voice
note's extraction and first pass, which read its new transcript; the counts did
not move). Replay takes about 12 seconds and calls nothing. Replay pins the models
recorded with it, so the cases still read with Argon and Gemini 3.8 Flash after the
defaults moved to GPT-6.2 Sol and GPT-6 Luna on 2026-11-22, and their scores are
unchanged. Recording them again on the new models is pending: the OpenRouter key
reached its spending limit during the comparison.

| Act | Day | Shows | Steps | See |
| --- | --- | --- | --- | --- |
| 1 The fire and the first statements | 15 Mar | reading, first pass | 18 sources arrive; `read`, `places`, the analyst accepts the fire report's first pass | the fire report, Sources |
| 2 Mara Quill's statement, on paper | 16 Mar | reading | 31 sources, two of them scans read through OCR | Mara's and Jo's scans |
| 3 The photograph of the gate | 16 Mar | reading, search | the photo: geotag reads its EXIF place, the first pass says what it shows | the photo, `/search?q=gate` |
| 4 The phone exhibits and the voice note | 20 Mar | reading | MQ/3, MQ/4, the voice note; speech to text, extraction, the intake brief | the voice note, MQ/3 |
| 5 One woman in four papers | 21 Mar | same person | the linker; the analyst accepts its proposal for the two sheets and joins the planted clusters herself | Mara's page |
| 6 Two places on one night | 21 Mar | contradiction | places, accepts, the contradiction hunter | Mara's page |
| 7 A colleague checks the lead | 23 Mar | assignment, peer review | assign the sheets, ask, reply, marked updated | To do, MQ/4 |
| 8 The conclusion | 25 Mar | conclusion, peer review | the signed conclusion and the reviewer's agreement | Overview |

What the app read, against the planted truth (`tests/test_case_studies.py` pins it):

- Extraction wrote 163 claims about people. 148 of the 152 planted claims were
  recovered (118 before 2026-11-22, when the prompt asked only where someone "was
  seen" and every first-person place was missed). Each place now carries the day,
  the time of day or range when the text gives one and in whose words: Mara's own
  "Brindle Cross", 18:00 to 24:00; her sister's "my house in Brindle Cross", 23:30,
  about Mara; MQ/3's 21:52 and MQ/4's 22:07 on Tanner's Row; Jo at the market at
  09:40. The four it missed: three witnesses who saw or heard something on
  Tanner's Row without saying they stood there, and Mara at the gate on 13 March.
- Nothing is planted: act 6 has no fallback. The hunter flags all four planted
  contradictions in four proposals: the alibi by the clock (her own evening
  overlaps 21:52 and 22:07, so both pairs are one proposal, and her sister's 23:30
  fits a drive after 22:07, so it is not a side), with the photograph's own capture
  time and place set beside the Tanner's Row side as context about the device;
  Ivo Brandt's birth year; Kofi Mensah's place of birth. The fourth proposal is the
  market trap: extraction read Jo's "all evening" without a time, so the day alone
  raises it and it says "same day, times not stated".
- The linker's rules proposed three things: the two exhibit sheets' Mara Quill as
  one person, Ruth Hollin's two papers as one, and relations; seeded on
  2026-11-22 it proposed 8 links, three of them relations (the Quill sisters, Nell
  Achebe and Dale Corrin, Pia Lund and Arjun Pillai), all "seen together": until
  that day two sightings in one place made them cellmates. Her statement and
  her sister's share only her name with the sheets, so no rule joins them; act 5
  has the analyst join the planted clusters herself, as her signed decision.
- Speech to text heard "Joe, it's me" for "Jo", the hosted transcriber as the
  local Whisper did before it. Extraction still writes "Joe" as
  a person named in the voice note, but the claim rests on spoken words, not
  printed text, and the name is marked heard, so the evidence page and Review read
  "heard as Joe". The proposer of every replayed claim reads "Extraction, replayed
  from a recording of 22 November 2026", with the model id in the detail.
- The planted score from `acceptance/eval_agents.py` is unchanged and separate
  (`tests/test_case_never_there.py`).

### Ready-made features for the next acts

Checked 2026-11-21 against the code; each is a function a new step module can call.

- **Standing question and its wake.** Create `AgentTask(user_id, investigation,
  kind="standing", spec={"question": ...}, every_seconds=86400)`, then
  `agents.tasks.queue(db, task, wakes, model=read.FIXED)` and
  `runs.worker.process(...)` (`seed_flows.agents` does this). A wake is
  `tasks.wake(db, task.id, key, reason, payload)` or, after a run that wrote an
  extracted claim, `tasks.evidence(db, run)`, which `worker.process` calls itself.
  `tasks.tick` only runs in `worker.serve` and needs AGENT_MODEL, so a step queues
  the woken run itself. The owner must be an active analyst and the case must exist.
  The fixed answer matches a claim that shares a capitalised word or a number with
  the question.
- **Assignment and work steps.** `collaboration.assignments.bulk(db, user, {investigation,
  assignee, subjects: ["blob:<sha>" | "finding:<key>"], due, priority})` and
  `assignments.update(db, user, id, {"status": "open" | "done" | "returned"})`;
  `seed_flows.assign` wraps the first. A return carries no note: the note is a
  review thread on a claim (`ask`, `reply`). The assignee must be an active analyst
  (the seeder's signing analysts are inactive demo accounts), and a subject has one
  open assignment at a time. Steps `assign` and `return` exist.
- **Peer review request.** `collaboration.service.request_review(db, proposal, user,
  reviewer_id, reason)` on an unconfirmed claim, or an accepted incident or
  conclusion; `service.comment(db, request_id, user, text)` replies;
  `seed_flows.request` and `seed_flows.reply` add the notifications. Steps `ask` and
  `reply` exist. A conclusion's peer agreement is `conclusions.seed_peer_review`.
- **Discovery and an incident.** `seed_flows.run_discover(s)` queues and runs the
  `discover` tool (local, no model) over accepted or corroborated, dated, placed
  claims; `seed_flows.open_finding(s, kind, month)` finds one;
  `review.findings.plan(db, user, key, kept, roles, title, None, author)` writes the
  incident and `seed_flows.gesture(s, user, statements)` signs it in one passkey
  gesture (`findings.reject` for the other way). Members must be accepted and
  placed first (`accept`, `places`).
- **Sheet intake with column mapping.** `sheets.service.confirm(db, acq, blob,
  {key_column, mapping: {header: field}, sheet, dataset_name}, person)` writes the
  dataset version and queues `extract_sheet` only when a model route is configured;
  `seed_flows.sheet_version` shows the whole path with a `Fixed` answer per row
  (`ROWS`, `answer()`). A case needs its own per-row answers, or a recording of the
  real one through the `read` step.
- **Frame reading for photos and video.** The first pass `assess` reads a photo
  (SEE schema, boxes) or eight video frames with the transcript (WATCH schema), and
  `_describe` indexes its words as a `description` segment, so search finds the
  picture by what it shows. On a read source the `read` step replays it; it needs
  ffmpeg for video frames.
- **Search indexing.** The `index` tool (local) runs on every text and derived text
  at arrival and in `read`; `/search?q=...` then finds passages, and a picture by
  its first pass. `embed` (meaning search) needs the embedding route and is left
  queued.
- **Package build.** The `package` step does it end to end (four-names act 9).
  Signing needs `C2PA_SIGN_CERT` and `C2PA_SIGN_KEY`; `stk demo-cert` writes a demo
  pair in development (docs/operations.md). Every cited row must be anchored, and the
  step runs the real `stk anchor` once, which needs the network; tests use
  `FakeAnchor` instead.

## One man, four names (fraud)

`acceptance/cases/four-names/`, investigation `case-four-names`.

A freight company at the invented Port Ondrel buys from a marine supplier, pays a
logistics consultant, holds a personal guarantee for a credit line and works through
a licensed ship agent. Each desk joined its own records and found nothing wrong. The
supplier Adrian Kassar, the consultant A. Qassar, the guarantor Adrian Khassar and
the ship agent Adrian Kasar, أدريان قصار on his licence, are one man born on
2 March 1974 in Velmora, son of Elias.

The pile: 14 sources, nine sheets with 1,445 rows and five scans, 230 person records
(222 people) and 422 claims. Each of the five forms says where and when it was
signed or seen in person, so the man's trail runs from June 2021 to March 2023
across the three invented sites of Port Ondrel. Three ledgers (purchases, payments, consultancy invoices)
carry 1,220 rows and no person records. The two scans with a face use one generated
portrait; see `files/ATTRIBUTION.md`.

| Planted | Count | What it tests |
| --- | --- | --- |
| Desk clusters, already accepted | 4 (9 records) | supplier, consultant, guarantor, ship agent |
| Same-person pairs across desks | 6 | four Latin spellings and one Arabic-script name |
| Namesake pairs that must never merge | 8 | a deck cadet Adrian Kassar born 1988, and a marine surveyor Adrian Kasar born 19 August 1974 in Torvane, against each desk |
| Gaps, contradictions, judgment calls | 0 | not this case's point |

### Results, 2026-11-22

| Metric | Fixed (rules) | Live |
| --- | --- | --- |
| Linker same-person recall | 1.0 (6 of 6 are candidates) | 1.0 (all 6 proposed) |
| False merges on namesakes | 1 | 0 (declined) |
| Other false proposals | 0 | 0 |
| Run complete (nothing cut by the cap) | yes, 7 candidates of 12 | yes |

Live is `deepseek/deepseek-v4.1-flash-1017` on `cloud-zdr`, reasoning `none`, one
trial, served by Nebius and OpenInference: 4.2 s and 3,444 tokens for the linker.
It declined the surveyor, as it did on 2026-11-21 when it gave "the places of birth
differ (Torvane vs Velmora)".

What the numbers say:

- All six pairs are candidates. Until 2026-11-22 two were not: the licence gives
  the father as إلياس, and `name_keys` stripped its first two letters as if they
  were the article ال, so it keyed as `s` against Elias's `ls` and a differing
  father ruled the pair out. A hamza on the alif is never the article, so it now
  keys as Elias. Accepting the six true proposals joins all nine records.
- The surveyor reaches Review through the consultant alone: the consultants
  register and the agreement give only the birth year 1974, which agrees with his
  full date, and a different place of birth does not rule a pair out. The deck
  cadet never reaches it: his birth date rules him out against every desk.
- A reader holding one desk's file sees nothing wrong. The rules build the seven
  candidates from 422 claims with no model, and a person still decides each link.

## The four cases, seeded

Each case's own README holds its story, planted truth and score. What
`stk demo-seed --case <slug>` puts on screen, checked 2026-11-21:

- **The witness who was never there** (`case-never-there`): read by the app in eight
  acts; see the worked example above. Mara Quill's person page sets her places on
  14 March side by side under Sources disagree, each with its time, and says why
  they were raised: at overlapping times, 18:00 to 24:00 and 21:52, and 18:00 to
  24:00 and 22:07. Every claim was read by the app.
- **The name nobody wrote down** (`case-unwritten-name`): 18 sources and 671
  claims, 608 of them log lines pinned to their sheet cells, and the roster's one
  claim, Petra Coyle's recorded leave. Each log line is its
  own mention joined to its driver, so Aron Vesk's page reads "86 records joined
  as one person". The gap finder proposes both planted gaps; his page shows "No
  evidence for 73 days" and the gap from 14 March to 26 May 2026. The supervisor's
  scanned statement is read by the OCR tool from its fixed text. Since 2026-11-22
  the gap finder also notices a regular presence that stops, so the logs alone,
  without the statement, give Aron's silence from his last named line on 12 March
  (75 days), carried by the place the three gate log batches record; with the
  statement the watch from 14 March holds and the routine is not offered twice.
  The depot photograph has the real operator's van cut off its right edge.
- **The footage that was two years old** (`case-old-footage`): read by the app in six
  acts (README). Each of the 36 clips gets speech to text and a first pass on eight
  frames, so search finds clips by what they show: "submerged trees" finds 8 of the 9
  copies, one look-alike and, once the archive is added, the 2024 original first. The
  2024 original arrives in act 4 and ties to seven reposts; all 29 match proposals are
  strong and inside the family, and the analyst confirms each as the same recording,
  side by side. The muted, cropped, mirrored short matches nothing. Hosted speech
  to text hears no word on any clip and each clip says "No speech found"; the
  local Whisper once heard "subscribe to the channel" in running water on two
  clips. Each caption is the event
  its post reports, unconfirmed but for the two the conclusion cites; Lindmark is
  invented and drawn as a schematic. The case has no people, so Stories and the Case
  map are empty.

Each case's Overview opens on its conclusion, signed by Ines Halvorsen and agreed
by Tomas Reyes, with the case marked concluded:

| Case | Conclusion | Confidence | Rests on |
| --- | --- | --- | --- |
| One man, four names | The four desks' names are one man | Reasonable grounds to believe | 11 same-person links: the desks' 5 and the linker's 6 true proposals |
| The witness who was never there | Mara Quill was at the yard on Tanner's Row that evening, not in Brindle Cross from six | Reasonable grounds to believe | The two exhibit sheets' claims, MQ/3 and MQ/4; it contradicts her own Brindle Cross claim, as extraction read it from her statement |
| The name nobody wrote down | Aron Vesk is named in no log for 73 days after the night Dana Kovar boarded his bus; a lead, not a finding about what he did | Reasonable suspicion | Both supervisor sightings and his last and next log lines |
| The footage that was two years old | The clip shared as new is a copy of the 2024 video | Clear and convincing | The strong matches between the clip and the 2024 video, and the two posts' words |

The four-names conclusion cites the passport's link, so it stays hidden until a
reader reveals that sensitive source, as the feature intends.
