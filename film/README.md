# The case film

A 185 second narrated film for a newcomer: who has the problem, what Sealbook is in
three steps, then four invented cases, each told in the same four beats (the situation,
the question, what the app surfaced, what the person concluded), and the seal. Story
start times: 32.95, 92.9, 119.6 and 145.9 s. The shot list with the narration is [script.md](script.md);
the look is [composition/SPEC.md](composition/SPEC.md); what was checked after the last
render is [composition/REVIEW.md](composition/REVIEW.md).

```
film/narration.json      the voice, the length and every line with its start time
film/audio.py            voice lines, score, mix, timing and captions
film/audio/              lines/*.mp3, lines.json, score.m4a (tracked, 5 MB)
film/capture.py          records the app screens again
film/composition/        the HyperFrames project (index.html is the whole film)
film/captions.vtt        the narration as captions, one cue per line
```

Not tracked: `film/composition/assets/captures/` (what `capture.py` writes),
`film/build/` (the picture render and the mix) and `.hyperframes/`.

## Sound

- **Voice**: ElevenLabs, voice "George" (`JBFqnCBsd6RMkjVDRZzb`), model
  `eleven_multilingual_v2`, stability 0.6, similarity 0.75, style 0, speed 1.0, speaker
  boost on, synthesised 2026-11-21. The voice plays at its natural speed: nothing in the
  mix stretches or pitches it. Cut two sped `eleven_v4` lines up 1.16x with ffmpeg
  `atempo`, and that overlap-add stretching made the voice sound doubled. The lines are
  tracked (a new synthesis never sounds the same twice); `audio.py voice` re-synthesises
  only a line whose words or voice settings changed. Line 07b is cut from a longer take
  that went on to say the row count: a transcription heard 1,445 as 1,045 twice, so the
  numbers are on screen only. The account's ElevenLabs quota ran out on 2026-11-21, so a
  changed line needs new credits.
- **Score**: generated with Google Lyria 2 through fal (`fal-ai/lyria2`) on 2026-11-21:
  five 32.8 s parts, seeds 11 to 15, joined with 4 s crossfades into 147.8 s. Prompt:
  "sparse felt piano and soft sustained strings, calm documentary underscore, patient and
  quietly serious, slow tempo around 72 bpm, no drums, no percussion, no vocals";
  negative prompt: "drums, percussion, vocals, choir, loud, aggressive, electronic beat".
  Lyria RealTime through the Gemini API was tried first and refused the key. Terms of
  use are fal's and Google's for generated audio; regenerating gives different music.
- **Mix**: each line starts at its `at` in `narration.json` (the time the picture is cut
  to), laid end to end by sample count; the mono line goes to both channels unchanged.
  The score is a steady bed 16 dB down with no ducking, so nothing pumps behind the words;
  a film longer than the score plays it again from the top with the same 4 s crossfade.
  One gain brings the mix to -16 LUFS integrated, and a limiter holds peaks under
  -1.5 dBFS. `audio.py mix` also writes the two stems at their gain in the mix,
  `film/build/voice.wav` and `film/build/score.wav`, to check each on its own.

## Rebuild from nothing

From the repository root. The capture signs in through agent login and changes nothing
but the sign-in session and one logged reveal of the passport copy, which the four-names
conclusion rests on. It needs a database with all four cases seeded with their
conclusions (`stk demo-seed --case <slug>` for four-names, never-there, old-footage,
unwritten-name, then a worker run so the old-footage clips are fingerprinted). The film
was captured from the scratch database `sealbook_cases4`; in a worktree set `BLOB_DIR` to
the main checkout's `instance/blobs`.

1. Serve that database with agent login (development and testing only):

   ```sh
   SQLALCHEMY_DATABASE_URI=postgresql+asyncpg:///sealbook_cases4 \
     STK_ENV=development STK_ENABLE_AGENT_LOGIN=1 uv run stk run --port 5022 --no-reload
   ```

2. Record the screens (1600 x 1000 at 2x). It also copies the cases' scans, photograph,
   gate book, clips and portrait, the site fonts, and builds the checker's test package.

   ```sh
   SQLALCHEMY_DATABASE_URI=postgresql+asyncpg:///sealbook_cases4 SEALBOOK_URL=http://127.0.0.1:5022 \
     STK_ENV=development STK_ENABLE_AGENT_LOGIN=1 uv run python film/capture.py
   ```

3. Sound and captions (needs `ELEVENLABS_API_KEY` for new lines, `FAL_KEY` for a new score):

   ```sh
   python3 film/audio.py voice     # only lines that changed
   python3 film/audio.py timing    # line starts and ends; fails if a line runs into the next
   python3 film/audio.py vtt       # film/captions.vtt
   python3 film/audio.py mix       # film/build/mix.wav
   ```

   A line that gets longer may need its `at`, and the scenes in `composition/index.html`
   that go with it, moved before rendering.

4. Picture (the CLI the `hyperframes` skill runs; in a shell, `npx hyperframes@0.9.77`):

   ```sh
   cd film/composition
   npx hyperframes@0.9.77 check
   npx hyperframes@0.9.77 render -o ../build/master.mp4 -q delivery
   cd ../..
   ```

5. Join, encode, poster, captions:

   ```sh
   ffmpeg -y -i film/build/master.mp4 -i film/build/mix.wav -map 0:v -map 1:a \
     -c:v libx264 -profile:v high -pix_fmt yuv420p -crf 28 -preset slow -tune stillimage \
     -c:a aac -b:a 128k -ar 48000 -shortest -movflags +faststart site/media/case_film.mp4
   ffmpeg -y -ss 54 -i film/build/master.mp4 -frames:v 1 -q:v 3 site/media/case_film.jpg
   cp film/captions.vtt site/media/case_film.vtt
   ```

The site draws captions from the `.vtt` file instead of a `<track>`, because an iPhone
refuses to play the film inline when the video has one (see `docs/lessons.md`).

## When the app changes

Marks around rows are placed in pixels on the captured crops; after a recapture run
`npx hyperframes@0.9.77 snapshot --at 21,47,64,70,90,104,116` and check each mark still
sits on its row. The never-there disagreement list changes order between loads, so it
carries no marks. Numbers on screen and in the narration must keep matching the case
READMEs; change `narration.json`, `index.html` and `script.md` together.
