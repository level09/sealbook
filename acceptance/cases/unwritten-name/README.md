# The name nobody wrote down

A fictional missing person case. Ostry Coaches runs buses out of Wenmere, an
invented town. Dana Kovar boarded the 22:40 to Holt Cross on Saturday 14 March 2026
and never reached her night shift. Aron Vesk, who drove that bus, is named in every
log until that night and in none for the next 73 days. The gate line for his last
run has no driver.

`make.py` writes `case.json` and `files/` (`uv run python
acceptance/cases/unwritten-name/make.py`). Investigation `case-unwritten-name`,
`today` 2026-07-01. 669 claims over 46 records and 18 sources: 417 gate log lines,
150 fuel receipts and 108 dispatch rows, of which 608 name a driver and become
`seen_at` claims; the rest say "relief" or leave the driver blank. The roster is a plan, not a sighting, so it makes no claims.
The depot hands the gate log and the fuel receipts over in three dated batches
(February and March, April, May and June), each its own file and source, so a
driver has one record per log and batch, each with its name on its first row.

## What the gap finder really detects

`gaps.detect` watches a person only after an arrest, a detention, a last-seen
account or a missing report, and flags a silence longer than 60 days (61 or more)
until the next dated record. A sighting on the same day closes the watch. A
routine presence that simply stops is not flagged. So Aron's silence is found
because the night supervisor's statement says when she last saw him, and his last
named log line is 12 March. Without that statement the finder would not see it.

## Planted truth

| Planted | Window | Why |
| --- | --- | --- |
| Gap | Aron Vesk, 2026-03-14 to 2026-05-26 | last seen driving the 22:40, then in no log for 73 days |
| Gap | Dana Kovar, 2026-03-14, open | last seen and reported missing that night, nothing since |
| Not a gap | Nils Harrow, 2026-04-01 to 2026-05-30 | arrested, back 59 days later: under the threshold |
| Not a gap | Leon Abbot, 2026-04-21 to 2026-06-30 | 70 days off the logs, but released on bail first |
| Not a gap | Ivo Sand, 2026-02-20 to 2026-06-01 | 101 days, but a year-only Holt Cross record may fall inside it |
| Not a gap | Petra Coyle, 2026-04-04 to 2026-06-13 | 70 days of recorded leave; nothing starts a watch on her |

No same-person pairs, namesakes or contradictions are planted; the truth keeps
those keys empty so `acceptance/eval_agents.py` can read the file.

## Score, 2026-11-21

Split into batches on 2026-11-21, the score is unchanged; the relation candidates
rose from 20 to 24 with the extra records. Fixed mode (`evaluate(path=...)`, the rule layer, no model): gap recall 1.0, 2 of
2 planted gaps detected and reported, 0 planted non-gaps flagged, 0 other gaps, 0
deferred. The contradiction hunter gets no candidates and the linker proposes no
same person. The linker does offer 24 relation candidates (drivers seen together
at the depot, sources about one person); these are counted, not scored.
`tests/test_case_unwritten_name.py` pins these numbers.

Live, one trial, `deepseek/deepseek-v4.1-flash-1017` on `cloud-zdr`, reasoning
none: gap recall 1.0 (detection is the rules); the model's line for Aron's
silence was kept and its line for Dana's was dropped as `handle`. The linker
wrote 4 relations and declined 13. About 13,000 tokens in all.

## Not shown by this score

The timeline shows "no record after" only for open silences, so Dana's silence is
on it and Aron's closed one appears through the gap finder's run. With six drivers
at one depot every day, shared presence will find many "together" bands.

## The investigation in acts

`case.json` carries eleven acts (docs/case-studies.md, "Acts"), so
`uv run stk demo-seed --case unwritten-name --until <n>` leaves the case as it
stood after act n. Nothing is read by a model: every claim is planted and pinned
(sheet cells, quotes, a box), the supervisor's scan is read by the OCR tool from
its fixed text, and the agents run on the fixed answer.

| Act | Day | Shows | What happens | See |
| --- | --- | --- | --- | --- |
| 1 A nurse does not reach her night shift | 16 Mar | same person, timeline | flatmate, notice, photo arrive; the analyst joins Dana's three papers | Dana's page |
| 2 The night supervisor saw her board | 16 Mar | reading | the supervisor's scan (OCR from fixed text), the gate book page, the depot photo | the scan, the gate book page |
| 3 Who drives route 9 | 18 Mar | same person | the manager's note, roster, Holt Cross staff list; the intake brief over the first three papers | Aron's page |
| 4 The depot's logs to the end of March | 1 Apr | same person, timeline | gate log and fuel receipts for February and March, the March dispatch sheet; each line joined to its driver, the drivers joined across papers | gate log, Aron's page |
| 5 A standing question on the driver | 2 Apr | standing question | the owner asks "Tell me when Aron Vesk is named in a new record."; the first check matches the 5 papers that name him | Desk |
| 6 April's logs, and nothing on him | 4 May | standing question | April's gate log and fuel receipts, the charge sheet and the bail record; the question wakes on 4 runs and reports no new match | Desk, April gate log |
| 7 May and June: his name is back | 1 Jul | standing question | May and June logs; the question wakes on 2 runs and reports both: "A. Vesk" in the gate log, "VESK A" in the fuel receipts | Desk, May and June gate log |
| 8 Seventy-three days with no name | 1 Jul | gap, timeline | the gap finder proposes 14 March to 26 May for Aron and the open silence for Dana | Aron's page, Stories |
| 9 Who was at the depot that night | 2 Jul | discovery, map | the analyst accepts the 11 log lines of 14 March and the 3 last-seen accounts; discovery proposes a candidate incident (14 claims, 6 people) and a shared presence; she keeps 6 claims and 3 people (Dana injured party, Aron participant, Leon Abbot witness) as an incident and rejects the shared presence with a reason | Incidents, Case map |
| 10 A colleague works the lead | 3 Jul | assignment, peer review | the owner assigns the May and June gate log and the gate book page to Noor Adeyemi, asks her about the 26 May line; she answers and marks both updated | Inbox, May and June gate log |
| 11 The conclusion | 6 Jul | conclusion, peer review | the signed conclusion and Tomas Reyes's agreement | Overview |

Measured on 2026-11-21 (`tests/test_case_unwritten_name.py` pins them): the
standing question's checks matched 5, 0 and 2 claims; the gap finder proposed 2
gaps, both planted; discovery proposed 1 candidate incident and 1 shared presence
over the same 14 claims, 6 kept. Seeding all eleven acts takes about 50 seconds.

New steps (`sealbook/monitor/study/standing.py`, `discovery.py`):

- `question` (`question`): the owner's standing question, as the Desk form makes
  it, with no schedule (`every_seconds` null, so it wakes on evidence only), and
  its first check over what the case holds.
- `wake`: the extraction runs that wrote the act's arrived claims wake the
  question (`tasks.evidence`), and the woken check runs now; `tasks.tick` only runs
  in the worker loop. Does nothing before a question is asked.
- `discover` (`month`, `incident: {title, keep, roles}`, `reject`): runs the
  discovery tool and decides that month's open findings with one passkey gesture
  each. `keep` names claim keys (all members when left out); `roles` maps a record
  to a role in `method.ROLES`.
- `clusters` takes `"only": "rows"` to join sheet rows to their record and leave
  the planted clusters to the analyst's own `join`.

What reads badly or is not shown honestly:

- Checks run on the fixed answer: a claim matches when its value names a word of
  the question, so only name claims match ("Demo match: the value and the question
  both name Vesk"), never a dated line. The Desk says "Fixed demo output, no model
  ran". A live model was not recorded for the question.
- The Desk shows only the latest check, so after act 6 the five first matches sit
  under Past checks.
- After the logs, each joined driver is labelled by the fullest natural-order
  spelling among his records (`people.label_rank`): "Aron Vesk", "N. Harrow", "M.
  Lind". The logs name the other drivers only by an initial, so no fuller name
  exists for them; his person page header lists every spelling.
- The colleague's work shows on the gate log's page and the owner's Inbox, not on
  the owner's To do, which only lists work assigned to her.
- The incident row reads "violation not categorised"; the step sets no category.
  The Case map and Stories headers name the case by its slug,
  `case-unwritten-name`.
- An act's `day` is the story's; the ledger records seed time, so everything reads
  "now" or "1 min ago".
- Nothing is read by an extraction model; reading the statements and the photo
  for real (`"read": true`, `make.py --record`) is left for later.
