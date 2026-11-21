# The name nobody wrote down

A fictional missing person case. Ostry Coaches runs buses out of Wenmere, an
invented town. Dana Kovar boarded the 22:40 to Holt Cross on Saturday 14 March 2026
and never reached her night shift. Aron Vesk, who drove that bus, is named in every
log until that night and in none for the next 73 days. The gate line for his last
run has no driver.

`make.py` writes `case.json` and `files/` (`uv run python
acceptance/cases/unwritten-name/make.py`). Investigation `case-unwritten-name`,
`today` 2026-07-01. 651 claims over 28 records and 14 sources: 417 gate log lines,
150 fuel receipts and 108 dispatch rows, of which 608 name a driver and become
`seen_at` claims; the rest say "relief" or leave the driver blank. The roster is a plan, not a sighting, so it makes no claims.

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

Fixed mode (`evaluate(path=...)`, the rule layer, no model): gap recall 1.0, 2 of
2 planted gaps detected and reported, 0 planted non-gaps flagged, 0 other gaps, 0
deferred. The contradiction hunter gets no candidates and the linker proposes no
same person. The linker does offer 20 relation candidates (drivers seen together
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
