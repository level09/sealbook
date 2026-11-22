# The footage that was two years old

A fictional verification case. On 2 June 2026 a clip of the river Ostra over its
banks in Lindmark spreads as new. It is a cropped, mirrored, trimmed, logo-stamped
re-encode of a video posted on 3 June 2024. Lindmark, the Ostra, the accounts,
captions and dates are invented. The footage is real and openly licensed, filmed
elsewhere; `files/ATTRIBUTION.md` says what each clip really shows.

```bash
uv run python acceptance/cases/old-footage/make.py           # derive copies, write case.json
uv run python acceptance/cases/old-footage/make.py --fetch   # also re-download the Commons clips
uv run python acceptance/cases/old-footage/make.py --record  # read the clips on the live routes
uv run python acceptance/cases/old-footage/eval.py           # score, no database
uv run python -m unittest tests.test_case_old_footage        # the pinned score
uv run stk demo-seed --case old-footage [--until N]          # into the app, act by act
```

## Planted truth

36 clips, 9.8 MB. One original (21 s), nine copies, six look-alikes (other real
recordings of rivers in flood, posted on the same days with similar captions) and
twenty unrelated clips (clouds, rain, snow, waterfalls, a rail line, a fountain).
Seven copies are made from the original and one from the viral copy:

| Copy | Made by | Found by | Tied to the original |
| --- | --- | --- | --- |
| copy_lowrate | 320 px, 120 kbit/s | picture and sound | yes |
| copy_trim | seconds 5 to 17 | picture and sound | yes |
| copy_logo | red banner and corner logo block | sound only | yes |
| copy_crop | central 70%, scaled up | sound only | yes |
| copy_mirror | mirrored | sound only | yes |
| copy_vertical | 9:16 centre cut | sound only | yes |
| copy_viral | 80% crop, mirror, trim, logo, 480 px | sound only | yes |
| copy_viral_muted | the viral copy, sound removed | picture, against copy_viral | yes, through copy_viral |
| copy_short_muted | 9:16 cut, mirrored, sound removed | nothing | no, a measured miss |

## Score (2026-11-22, PDQ distance 31, quality 50, 20 candidates, 4 sound points)

Copies matched to the clip they were made from: 8 of 9, all banded strong. Tied to
the original through strong proposals: 8 of 9. Proposals outside the planted
family: 0, strong or weak.

Until 2026-11-22 the score had 36 weak proposals outside the family, touching all
six look-alikes and several unrelated clips, each resting on one or two shared sound
landmarks: `audio.align` returned a result for any shared hash, and the service
wrote a proposal for any result. Now a sound match needs
`Config.SOUND_MIN_LANDMARKS` (4) points lined up at one offset before anything is
proposed. On this set every true copy aligns 76 points or more (copy_trim, 12 s of
shared sound), and no unrelated pair aligns more than 2. The cost: at about 6 to 8
landmarks a second on this footage, two clips sharing less than about a second of
sound are no longer proposed by sound; quiet or noisy audio gives fewer landmarks
and raises that floor.

What the code survives here: re-encoding, downscaling and trimming keep both
signals. A logo banner, a crop, a mirror or an upright cut defeat the frame hash
(PDQ has no flip or crop invariance in this code); the sound carries the match. With
the sound gone and the picture cropped and mirrored, nothing ties the copy.

The eval stands in for Postgres with exact search over the clips already held, in
posting-date order, using the service's sampling and candidate limits.

## The case in acts

`stk demo-seed --case old-footage [--until N]` plays six acts. Every clip is marked
`read`: the app listens for speech (ElevenLabs Scribe v2, hosted, zero retention), then its
first pass looks at eight frames and the transcript and proposes a triage, a title
in English and Arabic, description lines and labels, which are indexed so search
finds the clip by what it shows. The posts stay typed captures, and each caption is
planted as the event its post reports, unconfirmed. Clips arrive in posting order
inside each act and are fingerprinted against the clips already held, so the 2024
original, which arrives last, is compared with every repost.

| Act | Day | Shows | What happens | See |
| --- | --- | --- | --- | --- |
| 1 The clip that spread | 2 Jun | reading, first pass | the viral clip and its post arrive; `read`, `claims` | the viral clip's first pass |
| 2 Reposts and other floods | 2 Jun | reading, copies | 20 clips dated up to 2 June; the reposts match each other by sound | copy_trim's Copies tab, the footbridge clip |
| 3 Searching the pile by what it shows | 3 Jun | reading, search | 14 more clips; the desk searches "fast-moving muddy water" and "bridge" | `/search?q=fast-moving+muddy+water`, `/search?q=bridge` |
| 4 The archive from two years ago | 3 Jun | copies | the 2024 original; fingerprints tie it to seven reposts; the search again | the original's Copies tab, the search |
| 5 Side by side | 3 Jun | copies | the analyst confirms each strong match in the family as the same recording, signed | the viral clip beside the original |
| 6 The conclusion | 3 Jun | conclusion, peer review | the signed conclusion and the reviewer's agreement | Overview |

Steps of `sealbook/monitor/study/matches.py`:

- `matches`: counts the match proposals written so far, as `out["match proposals"]`.
- `confirm copies` (`relationship`, default `same_recording`; `why`): the analyst
  confirms, signed, each open strong match joining two clips of the planted family,
  naming the relationship as the evidence page's Confirm does. Weak proposals and
  any outside the family stay for a person.
- `search` (`query`): the desk's keyword search over the case, with the access
  logged as the search page logs it; `out["search"][query]` lists the source keys
  found (a post shows as `<clip>_post`).

No act declines a proposal: with the sound gate there is nothing outside the
family to decline. The muted, cropped, mirrored short matched nothing.

### What the app read (recorded 2026-11-22)

66 calls on cloud-zdr: 36 first passes with `google/gemini-3.8-flash` (eight
frames each), about 466,000 prompt and 42,000 completion tokens, USD 0.50 as the
route billed, and 30 speech to text calls with `elevenlabs/scribe-v2` on the clips
with sound (six have none), about USD 0.02. No transcript has a word, so nothing
is extracted. Replay calls nothing.

- The app described all 36 clips. No description names Lindmark, the Ostra or a
  date: the frames show a river in flood, trees and buildings, which fits the real
  Commons footage (filmed in Linz) as well as the story. The description is never
  evidence of the fictional place, and the captions stay unconfirmed claims.
- Search for "fast-moving muddy water", the viral clip's own description, finds 8
  of the 9 copies (copy_trim's says "muddy water" without "fast-moving") and no
  look-alike after act 3, and after act 4 the 2024 original
  first. Search for "bridge" finds the two look-alikes with a bridge the app saw
  and the posts that name one; the app saw no bridge in the viral clip or the
  original.
- Fingerprints wrote 29 proposals, all strong and all inside the family; the
  analyst confirms all 29. The original ties to the viral clip and six other
  reposts by sound (copy_lowrate and copy_trim by picture too), copy_viral_muted
  ties through copy_viral by picture, and copy_short_muted is not tied.
- Speech to text hears no word on any clip. It tags the sound instead ("[water
  flowing]", "[rain]", "[background noise]"), which is not a word heard, so each
  clip's Read tab says "No speech found" and the first pass sees "No speech." The
  local Whisper, recorded before 2026-11-22, heard "اشتركوا في القناة" ("subscribe
  to the channel") on copy_trim and flood_promenade and the first pass repeated it
  as said; the first passes of those two clips were recorded again.
- The first pass triages weather footage as a human-rights first pass would:
  non-incident, and "some evidence" on a few clips of rain or snow.

Meaning search is not used: a search's query is embedded when it is asked, and
the recordings cannot replay that, so the `embed` runs stay queued and the leads
by meaning stay empty.
