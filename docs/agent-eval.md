# Agent evaluation on a planted case

`acceptance/eval_agents.py` scores the proactive agents against planted truth: the
linker (`link`), the gap finder (`gaps`), the contradiction hunter
(`contradictions`) and the intake brief (`brief`). It runs their real run
functions on `case-agent-eval`, a synthetic case held in memory. Each run's
database reads return the planted case and its writes are captured; candidate
building, model calls, validation and the cost and dropped counters are the
agents' own code. No app code changed for this, and no database is touched.

```sh
uv run python acceptance/eval_agents.py                    # fixed mode
ROUTE_LOCAL_BASE_URL=http://127.0.0.1:11434/v1 AGENT_MODEL=qwen3.5:9b \
  ROUTE_LOCAL_TIMEOUT=900 uv run python acceptance/eval_agents.py --live
uv run python acceptance/eval_agents.py --out instance/eval_agents.json
uv run python acceptance/make_agent_case.py                # regenerate the case
```

Fixed mode asks the fixed answer (`sealbook-demo-fixed-output`), which proposes
every candidate it is given, so its numbers are the rule-built candidate layer and
need no model. `tests/test_agent_eval.py` runs it in the unit suite. Live mode asks
the local model on the local route, as a worker would; it reports
`"skipped"` with the reason when `ROUTE_LOCAL_BASE_URL` or `AGENT_MODEL` is empty.
A worktree needs the main `.env` linked for `SECRET_KEY` (see
[development.md](development.md)), and the timeout above, or the runs' time budget
(lease less 90 s) defers candidates.

## The planted case

`acceptance/make_agent_case.py` writes `acceptance/agents/case.json`: 21 sources,
27 person records, 80 claims, places as an analyst confirmed them, and the truth.
P1 and his exact-name twin P2 come from `acceptance/case.json`; everyone else is
invented. `case-agent-eval` is never seeded into the demo database.

| Planted | Count | What it tests |
| --- | --- | --- |
| Same-person pairs | 7 | P1 in an Arabic register, an English post and an English family statement (3 pairs); Lina, Mohammad Al-Khatib, Bassel Nader and Omar Said in Arabic and Latin script |
| Namesake pairs that must never merge | 7 | P2 against each P1 record (birth year and mother differ); a Lina born 2001 last seen the same day in the same district (birth date differs); a Mohammed Ahmed Al-Khatib held at Branch 999 the same month (only the place of birth differs, in two scripts) |
| Gaps over 60 days | 4 | arrest to a sighting (167 days), arrest with nothing since, re-arrest to a sighting (73 days), a missing report to a sighting (129 days) |
| Not gaps | 4 | 59 days, a year-only detention, two releases |
| True contradictions | 3 | birth year 1979 against 1983, mother Amal against Samira, arrested in Harasta and in Homs on one day |
| Not contradictions | 4 | حرستا and Harasta (one place), last seen in Douma and arrested in Harasta on one day, 1990 against 1990-04-02, فاطمة against Fatima |

Gap scores cover the five people planted for gaps. Gaps of other people (open
silences after a detention in the linker set) are counted as `detected_elsewhere`,
not scored. Contradiction precision counts every reported item; a second figure
leaves out items the model itself labels spelling or date precision. The brief
reads one batch (the two witness statements) and is scored only on its
disagreement lines.

## Results, 2026-10-24

Apple M2, 16 GB. Fixed mode is the rule layer. Live is `qwen3.5:9b` on Ollama
0.40.5, `ROUTE_LOCAL_TIMEOUT=900`, one run, 11 minutes wall time, with other
agents' work on the same laptop.

| Metric | Target | Fixed (rules) | Live |
| --- | --- | --- | --- |
| Linker same-person recall | at least 0.8 | 0.714 (5 of 7 are candidates) | 0.714 (all 5 candidates proposed) |
| Linker false merges on namesakes | 0 | 1 | 0 (the model declined it) |
| Gap detection, planted gaps over 60 days | 1.0 | 1.0, no planted non-gap found | same rules |
| Contradiction precision | at least 0.8 | 0.75 (3 of 4), recall 1.0 | none: every item dropped |

Targets missed, as measured:

- Linker recall. The two pairs that join P1's Arabic register record are never
  candidates. Against the English post nothing but the name agrees (last seen in
  Douma and arrested in Harasta are different fields), and a candidate needs one
  agreeing field. Against the English family statement the father is خالد in one
  and Khaled in the other; `read.same_value` compares normalised text without
  transliteration, so a parent name in another script counts as a difference and
  rules the true pair out.
- Linker namesakes. Mohammed Ahmed Al-Khatib (born in Aleppo) and محمد احمد
  الخطيب (born in دوما) were both held at Branch 999 in February 2014. Place of
  birth does not rule a pair out, and the two values are in different scripts, so
  the candidate reaches the model. The fixed answer would propose it; the live
  model declined it.
- Contradiction precision. The rule layer reports فاطمة against Fatima (one name
  in two scripts) as a disagreement, the same cross-script comparison as above.
  The live model gave all four items, but cited `"c3: birth date is 1979 (...)"`
  instead of the bare handle, so validation dropped all four as `unknown`. Its
  kinds were also wrong: the Harasta and Homs arrest as "date precision", Amal
  against Samira as "spelling or transliteration".

Dropped and cost per run (live; fixed costs 0 s and no tokens):

| Run | Proposed | Dropped | Seconds | Tokens | Per finding |
| --- | --- | --- | --- | --- | --- |
| Linker, 8 candidates | 5 same person, 2 relations | 1 declined | 195.5 | 2,219 in, 799 out | 27.9 s, 431 tokens |
| Gap finder, 12 of 16 gaps | 8 | 4 `handle` | 278.9 | 2,473 in, 1,449 out | 34.9 s, 490 tokens |
| Contradictions, 4 candidates | 0 | 4 `unknown` | 109.2 | 611 in, 599 out | none |
| Brief, 2 sources | 5 model lines | 1 `leak` | 73.8 | 1,016 in, 365 out | 14.8 s, 276 tokens |

Also seen:

- One gap run reads at most 12 gaps and ranks them by length. Open silences are
  counted up to today, so on this case the 12 slots go to open silences since 2013
  and three of the four planted closed gaps (167, 129 and 73 days) wait for a later
  run, in both modes.
- The brief drops its line on فاطمة against Fatima as a leak in both modes, though
  the line cites both claims: `read.leaks` also flags a value that an uncited
  sensitive source holds, and P1's register gives his mother the same name. The
  only brief disagreement left (1983 against an accepted 1979) is true.
- The four gap lines dropped as `handle` matched the check for handles and
  identifiers in the suggestion text.

`tests/test_agent_eval.py` asserts the gap target and pins the linker and
contradiction rule layers at the values above; a change either way fails it until
this page and the pins are updated together.

## Known gaps

- One synthetic case, 80 claims. The numbers say nothing about archive scale or
  real handwriting; they are a regression check and a first quality read.
- The harness replaces `read.case`, so identity clusters, place resolution and the
  erasure and availability filters are planted, not computed. Place resolution is
  given as an analyst would have confirmed it.
- No incidents, findings, assignments or open same-person proposals are planted,
  so `involved` candidates, the brief's finding and colleague steps and the
  "possibly two people" contradictions are not exercised.
- Standing questions (`standing`) are not scored: the run screens and then judges per
  person, up to 38 local calls for the 19 people here, and no question truth is
  planted.
- Document and person relation proposals are counted, not scored.
- Live numbers are one run of one model at temperature 0 on a laptop shared with
  other work.
