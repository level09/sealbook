# The case film

A 233.6 second narrated film for a newcomer: who has the problem, what Sealbook is in
three steps, then four invented cases, each told in the same four beats (the situation,
the question, what the app surfaced, what the person concluded), and the seal. Story
start times: 32.8, 90.7, 138.8 and 167.1 s. The shot list with the narration is
[script.md](script.md); the look is [composition/SPEC.md](composition/SPEC.md); what
was checked after the last render is [composition/REVIEW.md](composition/REVIEW.md).

```
film/narration.json      the voice, the lead and tail, every line with its pause and start
film/audio.py            voice lines, timing plan, pacing check, score, mix and captions
film/audio/              lines/*.mp3, lines.json, score.m4a (tracked)
film/capture.py          records the app screens again
film/composition/        the HyperFrames project (index.html is the whole film)
film/composition/assets/timing.js   line times for the picture, written by `audio.py plan`
film/captions.vtt        the narration as captions, one cue per line
```

Not tracked: `film/composition/assets/captures/` (what `capture.py` writes),
`film/build/` (the picture render and the mix) and `.hyperframes/`.

## Timing and pacing

Nothing in the film is timed by hand in seconds. Each line in `narration.json` has the
pause before it; `audio.py plan` lays the lines end to end from those pauses (the voice's
own length, measured from where it starts to where it stops in the file), writes each
line's `at` and the film's length back into `narration.json`, writes
`composition/assets/timing.js`, and sets `data-start` and `data-duration` on every clip
in `index.html`. In the composition, everything that comes and goes names its window
against the voice: `data-in="@14a-0.2"` is 0.2 s before line 14a starts,
`data-out="@14c$+1"` is 1 s after line 14c ends, `END` is the film's end. A screen
marked `data-screen` is checked as a screen.

`audio.py check` prints the figures and fails when one breaks:

- 0.8 to 1.5 s of picture and score before the first word, 1.8 to 3 s after the last;
- at least 1.2 s of silence between two parts (the opening, each story, the close);
- 135 to 165 words a minute inside each part, from its first word to its last;
- every piece of text on screen held at least (words / 3) + 1 seconds, and none leaving
  while a line is being spoken;
- every app screen on view at least 3 s, held 0.8 s after the last line spoken over it,
  and never leaving mid-line.

To change a line: edit its words (and `say`, when a number must be spelled out for the
voice), run `voice`, `plan`, `check`. If a hold fails, raise the next line's pause or
shorten the words on screen. Never speed the voice up.

## Sound

- **Voice**: ElevenLabs "George", model `eleven_multilingual_v2`, through fal
  (`fal-ai/elevenlabs/tts/multilingual-v2`, needs `FAL_KEY`), stability 0.6, similarity
  0.75, speed 1.0, synthesised 2026-11-22. The account's direct ElevenLabs quota ran out
  on 2026-11-21, so the whole film was voiced again through fal as one take; nothing
  but the narration text is sent. The voice plays at its natural speed: nothing in the
  mix stretches or pitches it (cut two sped lines up with ffmpeg `atempo`, and the
  overlap-add stretching made the voice sound doubled). Numbers are spelled out in the
  words sent for synthesis (`say`): the voice once read "1,445" as "1,045". Captions
  keep the digits. The lines are tracked (a new synthesis never sounds the same twice);
  `audio.py voice` re-synthesises only a line whose spoken words or voice settings
  changed, and prints the characters it sent.
- **Score**: generated with Google Lyria 2 through fal (`fal-ai/lyria2`) on 2026-11-21:
  five 32.8 s parts, seeds 11 to 15, joined with 4 s crossfades into 147.8 s. Prompt:
  "sparse felt piano and soft sustained strings, calm documentary underscore, patient and
  quietly serious, slow tempo around 72 bpm, no drums, no percussion, no vocals";
  negative prompt: "drums, percussion, vocals, choir, loud, aggressive, electronic beat".
  Terms of use are fal's and Google's for generated audio; regenerating gives different
  music.
- **Mix**: each line starts at its `at` from where its voice starts, laid end to end by
  sample count; the mono line goes to both channels unchanged. The score is a steady bed
  16 dB down with no ducking; a film longer than the score plays it again from the top
  with the same 4 s crossfade. One gain brings the mix to -16 LUFS integrated, and a
  limiter holds peaks under -3 dBFS, so the AAC encode stays under -1 dBTP. `audio.py
  mix` also writes the two stems at their gain in the mix, `film/build/voice.wav` and
  `film/build/score.wav`.

## Rebuild from nothing

From the repository root. The capture signs in through agent login and changes nothing
but the sign-in session and one logged reveal of the passport copy, which the four-names
conclusion rests on. It needs a scratch database with all four cases seeded, never the
owner's own database.

1. A scratch database with the four cases. In a worktree, put `SQLALCHEMY_DATABASE_URI`,
   `STK_ENV=development`, `BLOB_DIR` (the main checkout's `instance/blobs`) and the two
   `C2PA_SIGN_*` lines that `stk demo-cert` prints into the worktree's `.env`.
   Four-names' last act anchors its package with `stk anchor`, which needs the network.

   ```sh
   createdb sealbook_film
   uv run stk db upgrade
   uv run stk install
   uv run stk demo-cert
   uv run stk demo-seed --case four-names      # builds instance/packages/case-four-names.zip
   uv run stk demo-seed --case never-there
   uv run stk demo-seed --case unwritten-name
   uv run stk demo-seed --case old-footage     # several minutes
   ```

   Run long commands under `timeout`. A seed that hangs is usually waiting on the
   ledger's advisory lock behind a session idle in a transaction: look in
   `pg_stat_activity` rather than wait.

2. Serve it with agent login (development and testing only), on a free port:

   ```sh
   STK_ENABLE_AGENT_LOGIN=1 uv run stk run --port 5031 --no-reload
   ```

3. Record the screens (1600 x 1000 at 2x). It also copies the cases' scans, photograph,
   gate book, clips and portrait and the site fonts, and opens the seeded four-names
   package in the site's checker (`FILM_PACKAGE` names another).

   ```sh
   STK_ENABLE_AGENT_LOGIN=1 SEALBOOK_URL=http://127.0.0.1:5031 uv run python film/capture.py
   ```

4. Voice, timing, pacing, captions, sound:

   ```sh
   uv run python film/audio.py voice    # only lines that changed
   uv run python film/audio.py plan     # line starts, length, timing.js, clip windows
   uv run python film/audio.py check    # fails when a pacing rule breaks
   uv run python film/audio.py vtt      # film/captions.vtt
   uv run python film/audio.py mix      # film/build/mix.wav
   ```

5. Picture (the CLI the `hyperframes` skill runs; in a shell, `npx hyperframes@0.9.77`):

   ```sh
   cd film/composition
   npx hyperframes@0.9.77 check
   npx hyperframes@0.9.77 render -o ../build/master.mp4 -q delivery
   cd ../..
   ```

6. Join, encode, poster, captions:

   ```sh
   ffmpeg -y -i film/build/master.mp4 -i film/build/mix.wav -map 0:v -map 1:a \
     -c:v libx264 -profile:v high -pix_fmt yuv420p -crf 28 -preset slow -tune stillimage \
     -c:a aac -b:a 128k -ar 48000 -shortest -movflags +faststart site/media/case_film.mp4
   ffmpeg -y -ss 62 -i film/build/master.mp4 -frames:v 1 -q:v 3 site/media/case_film.jpg
   cp film/captions.vtt site/media/case_film.vtt
   ```

The site draws captions from the `.vtt` file instead of a `<track>`, because an iPhone
refuses to play the film inline when the video has one (see `docs/lessons.md`).

## When the app changes

Marks around rows are placed in pixels on the captured crops; after a recapture run
`npx hyperframes@0.9.77 snapshot --at 56,72,79,109,116,126,154,184` and check each mark
still sits on its row. Numbers on screen and in the narration must keep matching the case
READMEs and `docs/case-studies.md`; change `narration.json`, `index.html` and `script.md`
together.
