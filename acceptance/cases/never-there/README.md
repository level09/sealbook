# The witness who was never there

A fictional case study. Hollin Joinery on Tanner's Row in the invented town of
Larkhaven burned on the night of 14 March 2026. Forty-one statements (forty witness
statements and one interview) and twelve other items. Mara Quill, the bookkeeper,
says she was at her sister's house in Brindle Cross, 34 km away, from six until past
midnight, and her sister says she came. The photograph Mara handed over to show an
earlier break-in was taken at the workshop gate at 21:52 that night, and a voice note
from her phone at 22:07 says she is still at the yard.

Every person, business, place and coordinate is invented. Coordinates lie in open
ocean so that no real place is the scene. `case.json` holds the story, the case in
the shape of `acceptance/agents/case.json` and the planted truth; `files/` holds the
photograph, the voice note and its sentence times, and `ATTRIBUTION.md`.

```sh
uv run python acceptance/cases/never-there/make.py            # rewrite case.json
uv run python acceptance/cases/never-there/make.py --scans    # render the four scans
uv run python acceptance/cases/never-there/make.py --record   # read it on the live routes
uv run python -m unittest tests.test_case_never_there
uv run stk demo-seed --case never-there [--until N]           # into the app, act by act
```

The case is told in eight acts and read by the app itself: four papers are scanned
pages, the rest typed text, and the seeder replays the recorded answers of the
models that read them (`recordings.json`, 110 calls on cloud-zdr and one local
speech to text run, recorded 2026-11-21). Extraction recovered 118 of the 152
planted claims and none of the 34 where a statement places its witness on a day,
so act 6 plants Mara's and Jo's Brindle Cross claims, labelled; the hunter then
flags all four planted contradictions. See docs/case-studies.md, "Worked example".
The planted truth below stays what `acceptance/eval_agents.py` scores.

## What the code compares, and how the case reaches it

The contradiction hunter reads person claims only. It compares fixed facts (birth
date, parents, place of birth, sex) and, for places, one exact day at two resolved
places farther apart than their precision. It does not see times of day.

- The photograph's location reaches the app as `photographed_at` on the picture,
  written by the `geotag` tool from EXIF GPS. That claim is about the file, not a
  person, so the hunter never sees it, and EXIF time is not read at all.
- A voice note's transcript is extracted like any text, but a claim is dated only
  when the words date it. "I'm still at the yard" carries no date, and the file's
  time is not read.

So the dated claims come from two exhibit sheets an examiner wrote from the phone,
MQ/3 (the photograph's metadata) and MQ/4 (the voice note, its sent time and a
transcript). The picture and the recording are their own sources, so a person can
check each sheet against them: the photograph's `photographed_at` on its evidence
page, the recording's own transcript. Local speech to text (faster-whisper
large-v3-turbo) writes "Joe, it's me" for "Jo"; the rest it reads as written.

The hunter puts Mara's four claims for 14 March side by side, two in Brindle Cross
and two on Tanner's Row. On claims alone both places can hold on one day; the times
in the sources (21:52, 22:07, Jo's "about half past eleven" against Mara's "from
about six") are what a person reads to decide.

## Planted truth

152 claims, 53 sources, 54 records, 8 accepted same-person clusters.

| Planted | Claims | What |
| --- | --- | --- |
| True | `mara_stmt.at`, `mara_mq3.at` | the alibi against the photograph at the gate |
| True | `mara_stmt.at`, `mara_mq4.at` | the alibi against the voice note from the yard |
| True | `ivo_stmt.born`, `ivo_staff.born` | birth year 1958 against 1965 |
| True | `kofi_stmt.born_in`, `kofi_record.born_in` | Pellow Bridge against Saltmarch |
| Not | `ivo_stmt.at`, `ivo_patrol.at` | Tanner's Row and Tanners Row, one place |
| Not | `ruth_stmt.born`, `ruth_register.born` | 1962 against 1962-08-19 |
| Not | `dale_nell.last_seen`, `dale_custody.arrested` | last seen at noon, arrested at 16:20, one day |
| Not | `pia_stmt.at`, `pia_arjun.at` | a street inside its town |
| Not | `tomas_stmt.at`, `tomas_letter.at` | a day inside a month |
| Trap | `jo_stmt.at`, `jo_market.at` | the market at 09:40 and home that evening: offered by the rules, not a conflict |

No linker pairs, gaps or judgment conflicts are planted; those truth lists are
empty, so their scores read null. The canary lists are empty, so `canary_held`
means nothing here.

## Scores, 2026-11-21

Fixed mode (the rule layer, `tests/test_case_never_there.py`): 4 candidates from 152
claims; contradiction precision 1.0 and recall 1.0; the alibi is one candidate
citing all four of Mara's claims; no planted non-contradiction is offered; the trap
is offered, so trap avoidance is 0.0. No linker merge; the linker offers three
relation candidates (the two Quill sisters, Dale and the bar manager at one pub,
Pia and her neighbour), counted but not scored. The gap finder finds one open
silence (Dale since 15 March), counted as elsewhere. The intake brief on the two
exhibit sheets writes no disagreement line: it compares fixed facts only, not places.

Live, `deepseek/deepseek-v4.1-flash-1017` on `cloud-zdr`, reasoning none, 3 trials:
precision 1.0, recall 1.0, trap avoidance 0.0 in every trial. It called the alibi
"sources conflict" each time and asked to check the photograph's and voice note's
timestamps against the statements. It also called the market trap a conflict, while
writing that the two records "may cover different times of day". It labelled 1958
against 1965 "date precision" in all three trials, and Pellow Bridge against
Saltmarch "spelling or transliteration" once. About 1,000 prompt tokens and 2 to 5 s
per trial for the hunter.
