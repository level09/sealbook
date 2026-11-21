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

- `sources.<key>.file`: a path under the case directory. Without it the seeder writes
  a text stand-in listing the source's claims. `sources.<key>.origin` overrides the
  acquisition origin.
- `claims[].pin`: where the claim sits in its file. A sheet cell
  (`{"sheet", "row", "column"}`, the header is row 1), a box on a scan
  (`{"page", "bbox"}`), or a quote found in a text file (`{"text", "after"}`). Every
  extracted claim needs one: the gate rejects an agent's claim without a valid span.
  Stand-in sources pin their own claims.
- `claims[].event`: the event kind of an `event` claim, as in the agent case.
- `places.<key>`: `name_en`, `name_ar` (may be empty), `lon`, `lat`, `precision_m`.
  The seeder imports them as sites a person recorded and confirms every located
  claim that names one.

`truth` keys the case does not plant may be left out or empty. The scores that read
them come out `null`, and so do their targets, instead of a failure. Same-person
pairs are scored per identity cluster: a planted pair names one record on each side,
and records already joined in `clusters` count as one side.

`stk demo-seed --case <slug>` refuses a case that is already seeded. It ingests each
file through intake, writes every claim as an extraction statement, has a case
analyst accept the claims planted as `accepted` and each planted cluster's
same-person links with a passkey signature, confirms the planted places, then runs
the linker on the fixed answer so its proposals wait in Review. In a worktree, set
`BLOB_DIR` to the main checkout's `instance/blobs` so the main server can read the
files.

Not handled yet: sheets are ingested as files, not through the sheet mapping step
(`sheets.confirm`), so no dataset version exists; scans are pinned by box and no
OCR runs; PDF spans fail (`pages_required`, see `blobmeta.py`); audio and video
claims would need `{"ms"}` pins, which no case has tried; no incidents, findings,
assignments or briefs are seeded, and only the linker runs.

## One man, four names (fraud)

`acceptance/cases/four-names/`, investigation `case-four-names`.

A freight company at the invented Port Ondrel buys from a marine supplier, pays a
logistics consultant, holds a personal guarantee for a credit line and works through
a licensed ship agent. Each desk joined its own records and found nothing wrong. The
supplier Adrian Kassar, the consultant A. Qassar, the guarantor Adrian Khassar and
the ship agent Adrian Kasar, أدريان قصار on his licence, are one man born on
2 March 1974 in Velmora, son of Elias.

The pile: 14 sources, nine sheets with 1,445 rows and five scans, 230 person records
(222 people) and 417 claims. Three ledgers (purchases, payments, consultancy invoices)
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
  candidates from 417 claims with no model, and a person still decides each link.
