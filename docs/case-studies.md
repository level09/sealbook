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
  drawn.
- `conclusion`: `{"text", "standard", "rests_on", "alternatives": [{"text", "why"}],
  "not_established": [...], "peer_review": true}`, the case's signed conclusion
  (`sealbook/cases/conclusions.py`). `standard` is a key of
  `sealbook/method/standards.csv`. Each `rests_on` item is a claim key, `"links"`
  (every accepted same-person link), `"same_person"` (the linker's proposals that
  join two records the truth plants as one person) or `"copies"` (the strong match
  proposals inside the planted family of copies). Write an alternative without a
  closing full stop: the page adds one.

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
analyst (Tomas Reyes) signs agreement as peer reviewer. The surveyor's proposal in
four-names and the weak matches in old-footage stay for a person. A match proposal
cannot be cited by a conclusion, so `"copies"` is accepted but not cited, and
old-footage's conclusion cites the two posts' words beside it. In a worktree, set
`BLOB_DIR` to the main checkout's `instance/blobs` so the main server can read the
files.

What the pages read: Stories and the Case map show dated person claims of a
located kind (`seen_at`, `last_seen`, `arrested_at`, `detained_at`,
`detention_branch`, `transferred_to`); the map draws only those whose place a
person confirmed. Incidents come from accepted incident statements, which only a
person's decision on a finding writes.

Not handled yet: sheets are ingested as files, not through the sheet mapping step
(`sheets.confirm`), so no dataset version exists; PDF spans fail (`pages_required`,
see `blobmeta.py`); audio and video `{"ms"}` pins are untried (claims quote the
transcript instead); no incidents, findings, assignments or briefs beyond the
intake brief are seeded; a conclusion cannot cite a match proposal, nor cite a claim
as the one it contradicts (never-there's conclusion leaves her own Brindle Cross
claim out). Invented places lie in open sea, so the maps draw dots on
an empty background. The old-footage case has no people, so its Stories and Case
map stay empty.

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

### Results, 2026-11-21

| Metric | Fixed (rules) | Live |
| --- | --- | --- |
| Linker same-person recall | 0.667 (4 of 6 are candidates) | 0.667 (all 4 proposed) |
| False merges on namesakes | 1 | 0 (declined) |
| Other false proposals | 0 | 0 |
| Run complete (nothing cut by the cap) | yes, 5 candidates of 12 | yes |

Live is `deepseek/deepseek-v4.1-flash-1017` on `cloud-zdr`, reasoning `none`, one
trial, served by DigitalOcean and Ionstream: 3.4 s and 2,603 tokens for the linker.
It declined the surveyor because "the places of birth differ (Torvane vs Velmora)".

What the numbers say:

- The two missed pairs join the licence to the guarantor and to the supplier. The
  licence gives the father as إلياس; `name_keys` strips its first two letters as if
  they were the article ال and keys it as `s`, while Elias keys as `ls`. A father
  that differs rules a pair out. The story was written expecting all six; it stands
  because the consultant's records, which name no father, are a candidate with each
  of the other three desks. Accepting the four true proposals joins all nine records.
  The stripping is a bug in `sealbook/search/arabic.py`, reported and not fixed here.
- The surveyor reaches Review through the consultant alone: the consultants
  register and the agreement give only the birth year 1974, which agrees with his
  full date, and a different place of birth does not rule a pair out. The deck
  cadet never reaches it: his birth date rules him out against every desk.
- A reader holding one desk's file sees nothing wrong. The rules build the five
  candidates from 422 claims with no model, and a person still decides each link.

## The four cases, seeded

Each case's own README holds its story, planted truth and score. What
`stk demo-seed --case <slug>` puts on screen, checked 2026-11-21:

- **The witness who was never there** (`case-never-there`): 53 sources and 152
  claims. The photograph goes through intake and geotag writes where it was taken
  from its EXIF; the voice note gets its fixed transcript through the speech
  writer, in English. The contradiction hunter proposes four disagreements, and
  Mara Quill's person page sets her four places on 14 March side by side under
  Sources disagree, with the exhibit titles carrying 21:52 and 22:07. Stories and
  the Case map show 41 people at 8 places.
- **The name nobody wrote down** (`case-unwritten-name`): 14 sources and 651
  claims, 608 of them log lines pinned to their sheet cells. Each log line is its
  own mention joined to its driver, so Aron Vesk's page reads "86 records joined
  as one person". The gap finder proposes both planted gaps; his page shows "No
  evidence for 73 days" and the gap from 14 March to 26 May 2026. The supervisor's
  scanned statement is read by the OCR tool from its fixed text. The depot
  photograph has the real operator's van cut off its right edge.
- **The footage that was two years old** (`case-old-footage`): 36 clips and the 16
  posts that came with a caption, each clip fingerprinted as it arrives in posting
  order. The viral clip's Copies tab lists "Same recording, Video posted by Ostra
  Valley Weather on 3 June 2024, strong". The weak single-landmark proposals wait
  for a person. Each caption is the event its post reports, unconfirmed but for the
  two the conclusion cites; Lindmark has no coordinates and is not drawn. The case
  has no people, so Stories and the Case map are empty.

Each case's Overview opens on its conclusion, signed by Ines Halvorsen and agreed
by Tomas Reyes, with the case marked concluded:

| Case | Conclusion | Confidence | Rests on |
| --- | --- | --- | --- |
| One man, four names | The four desks' names are one man | Reasonable grounds to believe | 9 same-person links: the desks' 5 and the linker's 4 true proposals |
| The witness who was never there | Mara Quill was at the yard on Tanner's Row that evening, not in Brindle Cross from six | Reasonable grounds to believe | The two exhibit sheets' claims, MQ/3 and MQ/4 |
| The name nobody wrote down | Aron Vesk is named in no log for 73 days after the night Dana Kovar boarded his bus; a lead, not a finding about what he did | Reasonable suspicion | Both supervisor sightings and his last and next log lines |
| The footage that was two years old | The clip shared as new is a copy of the 2024 video | Clear and convincing | The two posts' words; the strong matches are accepted but cannot be cited |

The four-names conclusion cites the passport's link, so it stays hidden until a
reader reveals that sensitive source, as the feature intends.
