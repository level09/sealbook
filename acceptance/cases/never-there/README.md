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
models that read them (`recordings.json`, 111 calls on cloud-zdr, one of them the
voice note's hosted speech to text; the 52 extractions recorded again on
2026-11-22, and the voice note's transcription, extraction and first pass that
day too). Extraction
recovered 148 of the 152 planted claims, Mara's own evening in Brindle Cross and
her sister's account among them, each place with its time and in whose words, so
nothing is planted; the hunter flags all four planted contradictions. See
docs/case-studies.md, "Worked example". The planted truth below stays what
`acceptance/eval_agents.py` scores.

## What the code compares, and how the case reaches it

The contradiction hunter reads person claims. It compares fixed facts (birth date,
parents, place of birth, sex) and, for places, one exact day at two resolved places
farther apart than their precision. When both claims carry a time of day, two
places conflict only when the times overlap or are closer than travel at
`sealbook/method/travel.csv`'s speed (120 km/h) allows; without, the day alone
raises it and the candidate says "same day, times not stated".

- The photograph's location reaches the app as `photographed_at` on the picture,
  written by the `geotag` tool from EXIF GPS, and the hunter reads its EXIF capture
  time (21:52). That is about the file and the device, not a person, so it never
  becomes a side: it is set beside the Tanner's Row side as context.
- A voice note's transcript is extracted as spoken words: "I'm still at the yard"
  carries no date, and the file's own time is not read.

So the dated claims about Mara come from her statement, her sister's and two
exhibit sheets an examiner wrote from the phone, MQ/3 (the photograph's metadata)
and MQ/4 (the voice note, its sent time and a transcript). The picture and the
recording are their own sources, so a person can check each sheet against them.
Speech to text (ElevenLabs Scribe v2, hosted, zero retention) writes "Joe, it's
me" for "Jo", as the local Whisper did; the claim reads "heard as Joe". Its five
lines open within half a second of where the script puts each sentence.

By the clock, Mara's own 18:00 to 24:00 in Brindle Cross overlaps 21:52 and 22:07
on Tanner's Row, 34 km away. Her sister's "about half past eleven" fits a drive
after 22:07, so it is not a side.

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
| Trap | `jo_stmt.at`, `jo_market.at` | the market at 09:40 and home from 18:00 to 24:00: not a conflict, and not offered when both times are known |

No linker pairs, gaps or judgment conflicts are planted; those truth lists are
empty, so their scores read null. The canary lists are empty, so `canary_held`
means nothing here.

## Scores, 2026-11-22

Fixed mode (the rule layer, `tests/test_case_never_there.py`): 3 candidates from 152
claims (4 before the clock); contradiction precision 1.0 and recall 1.0; the alibi
is one candidate citing her own Brindle Cross claim and the two exhibit sheets
(before, her sister's 23:30 was a side too); no planted non-contradiction is
offered; the market trap is not offered, so trap avoidance is 1.0 (0.0 before). No
linker merge; the linker offers three relation candidates (the two Quill sisters,
Dale and the bar manager at one pub, Pia and her neighbour), counted but not
scored. The gap finder finds one open silence (Dale since 15 March), counted as
elsewhere. The intake brief on the two exhibit sheets writes no disagreement line:
it compares fixed facts only, not places.

Live, `deepseek/deepseek-v4.1-flash-1017` on `cloud-zdr`, reasoning none, 1 trial
(DeepInfra, Morph, Particle AI and Sail Research served the four calls): precision
1.0, recall 1.0, trap avoidance 1.0. It called the alibi "sources conflict": her
statement puts her in Brindle Cross from 18:00 past midnight while the phone
records place her on Tanner's Row, "in different places at overlapping times", and
asked where she was between 21:52 and 22:07. 763 prompt tokens and 2.5 s for the
hunter. On 2026-11-21, before the clock, 3 trials scored trap avoidance 0.0 in
every trial.

Read by the app (`tests/test_case_studies.py`): extraction left Jo's "all evening"
without a time, so on the seeded case the market at 09:40 is still raised, saying
"same day, times not stated"; the hunter makes four proposals there.
