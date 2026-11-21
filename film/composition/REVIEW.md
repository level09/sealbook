# Review, cut two, 2026-11-21

140.5 s at 1920 x 1080, 30 fps. Web file `site/media/case_film.mp4`: H.264 High CRF 26
with AAC 128k stereo, 13.4 MB. Loudness of the web file: -16.1 LUFS integrated, LRA
3.3 LU, true peak -1.2 dBFS (the mix itself: -16.0 LUFS, -1.5 dBFS).

## Creative calls

- Written for a newcomer: who has the problem, what Sealbook is, three steps, then four
  cases named as four kinds of help. The fraud case is longest and carries the reveal.
- The reveal is built from the case's files, not a screen: each name is cropped from
  its own document as it is spoken, the five crops gather beside one face, and the
  surveyor's register row is brought close and pulled apart.
- The picture is timed to the narration, not the other way round. The voice runs at
  1.16x to keep the film near the requested length; it is still 2:20, five seconds over.
- One `index.html` for the whole film; `check` warns on its size, kept on purpose.

## Measured and looked at

- `check`: no errors. Kept warnings: `dead_stretch` where a screen holds under the voice
  (34 to 39 s, 64 to 68 s, 93 to 98 s, 107 to 111 s, 120 to 124 s), `line_unread` on
  on-screen lines the narration speaks at the same time, `text_below_floor` on screen
  labels inside captured app crops.
- Contact sheets of the web file every 2.4 s; settled frames of every beat.
- Sound measured with `ebur128` and `volumedetect`: the score alone sits near -34 dB
  between lines, speech near -18 dB. I cannot hear; the transcript is the check.
- Transcribed the web file's audio (Gemini through OpenRouter): every line came back as
  written. The four spellings read as "Qassar" four times, since they sound alike; the
  screen carries the spelling.

## Newcomer test, on the transcript alone

What it is: an app that reads a pile of evidence, proposes connections with where each
came from, and seals a record anyone can check. Who it is for: investigators,
journalists, lawyers, researchers. Machine against person: the app reads and proposes; a
person approves or declines each link, signs, and writes the conclusion. All three can be
answered from the transcript. Weakest line: "Sealbook is an app for exactly that" leans on
the line before it; the three steps that follow carry the meaning.

## Weakest spots

- The first frame is empty paper (the pile enters from 0.3 s); the poster covers it on the
  site.
- The never-there disagreement panel has no marks, because its rows change order between
  page loads.
- Old footage: the two clips are the case's files; the match itself is the app's Copies
  tab, shown beside them.
