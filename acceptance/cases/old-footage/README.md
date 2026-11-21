# The footage that was two years old

A fictional verification case. On 2 June 2026 a clip of the river Ostra over its
banks in Lindmark spreads as new. It is a cropped, mirrored, trimmed, logo-stamped
re-encode of a video posted on 3 June 2024. Lindmark, the Ostra, the accounts,
captions and dates are invented. The footage is real and openly licensed, filmed
elsewhere; `files/ATTRIBUTION.md` says what each clip really shows.

```bash
uv run python acceptance/cases/old-footage/make.py           # derive copies, write case.json
uv run python acceptance/cases/old-footage/make.py --fetch   # also re-download the Commons clips
uv run python acceptance/cases/old-footage/eval.py           # score, no database
uv run python -m unittest tests.test_case_old_footage        # the pinned score
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

## Score (2026-11-21, PDQ distance 31, quality 50, 20 candidates)

Copies matched to the clip they were made from: 8 of 9, all banded strong. Tied to
the original through strong proposals: 8 of 9. Strong proposals outside the planted
family: 0.

Weak proposals outside the family: 36, touching all six look-alikes and several
unrelated clips, including one between the original and a look-alike. Each rests on
one or two shared sound landmarks: `audio.align` returns a result for any shared
hash, and the service writes a proposal for any result. The evidence page bands
these weak (`SOUND_STRONG_LANDMARKS = 4`), but they are listed.

What the code survives here: re-encoding, downscaling and trimming keep both
signals. A logo banner, a crop, a mirror or an upright cut defeat the frame hash
(PDQ has no flip or crop invariance in this code); the sound carries the match. With
the sound gone and the picture cropped and mirrored, nothing ties the copy.

The eval stands in for Postgres with exact search over the clips already held, in
posting-date order, using the service's sampling and candidate limits.
