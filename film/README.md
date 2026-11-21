# The case film

A 96 second film for the site: "One man, four names" told in full from real app screens,
then one short beat for each other case study, then a close. The shot list is
[script.md](script.md); the look and timing rules are
[composition/SPEC.md](composition/SPEC.md); what was checked after the last render is
[composition/REVIEW.md](composition/REVIEW.md).

```
film/capture.py          records the screens again from the seeded cases
film/composition/        the HyperFrames project (index.html is the whole film)
film/captions.vtt        captions, drawn by the site over the film
film/script.md           beats, timings, words, and what each picture is
```

Not tracked: `film/composition/assets/captures/` (what `capture.py` writes),
`film/build/` (renders) and HyperFrames' own `.hyperframes/` work folder.

## Rebuild from nothing

Everything runs from the repository root, against the local development database.
Nothing here touches users, passkeys or MFA, and the capture signs nothing.

1. Seed the four case studies, once per database (each refuses a case already seeded).
   In a worktree set `BLOB_DIR` to the main checkout's `instance/blobs`.

   ```sh
   uv run stk demo-seed --case four-names
   uv run stk demo-seed --case never-there
   uv run stk demo-seed --case old-footage
   uv run stk demo-seed --case unwritten-name
   ```

2. Start a development server with agent login on (development and testing only; the
   app refuses it anywhere else). The capture signs in as `admin@example.com` through
   `/_test/login`; set `FILM_USER` for another `@example.com` account.

   ```sh
   STK_ENV=development STK_ENABLE_AGENT_LOGIN=1 uv run stk run --port 5021 --no-reload
   ```

3. Record the screens (Playwright, 1600 x 1000 at 2x). It also copies the cases'
   scans, photograph, gate book page and clips, the site fonts, and builds a test
   package for the checker shot. `SEALBOOK_URL` overrides the server address.

   ```sh
   STK_ENV=development STK_ENABLE_AGENT_LOGIN=1 uv run python film/capture.py
   ```

4. Check and render with HyperFrames (the CLI the `hyperframes` skill runs; in a shell,
   `npx hyperframes@0.9.77`):

   ```sh
   cd film/composition
   npx hyperframes@0.9.77 check
   npx hyperframes@0.9.77 render -o ../build/master.mp4 -q delivery
   cd ../..
   ```

5. Encode for the web, take the poster, copy the captions:

   ```sh
   ffmpeg -y -i film/build/master.mp4 -c:v libx264 -profile:v high -pix_fmt yuv420p \
     -crf 26 -preset slow -tune stillimage -movflags +faststart -an site/media/case_film.mp4
   ffmpeg -y -ss 34.5 -i film/build/master.mp4 -frames:v 1 -q:v 3 site/media/case_film.jpg
   cp film/captions.vtt site/media/case_film.vtt
   ```

The film has no sound track, so the web file carries no audio stream. The site draws
captions from the `.vtt` file instead of a `<track>`, because an iPhone refuses to play
the film inline when the video has one (see `docs/lessons.md`).

## When the app changes

The marks around rows and buttons are placed in pixels on the captured crops. After a
recapture, run `npx hyperframes@0.9.77 snapshot --at 8,23,36,43,53,59,73,87` and check
each mark still sits on its row. Crops whose layout moved show up there first. Numbers on
screen must keep matching the case READMEs; change `index.html`, `script.md` and
`captions.vtt` together.
