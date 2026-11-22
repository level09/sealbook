# Review, the paced cut, 2026-11-22

218.8 s (3:39) at 1920 x 1080, 30 fps. Web file `site/media/case_film.mp4`: H.264 High
CRF 28 with AAC 128k stereo, 15.3 MB, -16.3 LUFS integrated, LRA 3.0 LU, true peak
-2.3 dBTP. Captured from a scratch database seeded with all four cases.

## What changed

The owner did not follow every use case, asked for room at the start and end and a
reading pace, and missed the app reading pictures. So:

- **Pacing is a check, not a feel.** Lines carry the pause before them; `audio.py plan`
  derives every time from those pauses, and the picture names its windows against the
  lines. `audio.py check` passes: 1.0 s before the first word, 2.2 s after the last,
  1.4 to 3.6 s between parts, 147 to 164 words a minute per part, every text and screen
  held long enough and none leaving mid-line.
- **One voice, one take.** The whole narration was voiced again through fal (George,
  multilingual v2, the same settings), 3,218 characters in all over two passes.
- **Each story's surfaced beat is the real screen.** The accepted link with its licence
  card; the surveyor's rejected link with the birth date and birthplace rows marked;
  "Two places on one day" on Mara Quill's page; the Copies row (118 of 118 sound
  points); "No evidence for 73 days" on the driver's page.
- **A reading beat.** Story two shows the app reading exhibit MQ/3 (the scan with its
  claim boxes, the text it read, the claim "Seen at Tanner's Row, Larkhaven"), then
  the photograph's first pass and the voice note's transcript, each marked.
- **The seal is the real package**: the four-names package the seeded case built,
  checked in the site's checker ("Every check that ran passed", 10 ran, 4 not run on
  that page), and the film says so.
- **Corrected facts**: 6 of the 6 true links (was 4 of 6); the 14 sources and 1,445 rows
  are spoken again, spelled out for the voice.

## Checked

- Contact sheets of the render every 6 s and snapshots at each beat: marks sit on their
  rows (names, licence card, birth rows, 21:52, 22:07, 18:00 to 24:00, the voice note
  line, the gate book's 22:40), and nothing essential sits below y 950.
- The mix transcribed with faster-whisper large-v3-turbo matches the script, "1,445"
  included. Whisper skips line 08 (the four spellings; read alone it hears "Kasa" four
  times) and writes three lines of noise over the score after the last word.
- `hyperframes check`: no errors. Warnings kept: `dead_stretch` under the voice,
  `line_unread` and `text_below_floor` inside captured app crops,
  `first_frame_empty` (the first second fades in), `film_silent` (the sound is mixed
  outside the composition), and the lint notes on one large file.

## Kept and weak

- 3:39, at the edge of the asked 3:40. A shorter film needs a split, not faster lines.
- The photograph and voice note screens sit side by side at about real size; their
  body text is small, so each carries a mark on the line that matters.
- The surveyor's decline is shown as the seeded analyst left it; no signing gesture is
  filmed.
- Not checked: playback in the site's own player, and the film on a phone.
