# Review, 2026-11-21

Rendered 96.0 s at 1920 x 1080, 30 fps. Web file `site/media/case_film.mp4` is H.264
High, CRF 26, no audio stream, 7.9 MB.

## Creative calls

- One storyline, given by the brief: the four-names case in full, three short beats,
  a close. No alternative storylines were built.
- No voice and no music. The words are on screen; `film/captions.vtt` carries the
  limits (no model ran, 4 of 6 links, the test package, by day not by hour), drawn by
  the site over the bottom of the frame. `check` warns `film_silent`; kept on purpose.
- The whole film is one `index.html` (13 scenes, one timeline) instead of one file per
  scene. `check` warns about file size and density; kept, because every scene shares
  the same camera and mark helpers and the timings read top to bottom in one place.
- Real screens are 2x captures shown large inside a panel, with a camera that pushes to
  the part that matters and a violet mark around it; red dashed marks are namesakes and
  the blank driver line.

## What was measured, what was only looked at

- `check`: no errors. Warnings kept: `film_silent`, `composition_file_too_large`,
  `timeline_track_too_dense`, `nested_structure_needs_subcomposition`.
- Looked at: contact sheets of the full render every 1.5 s, settled frames of every
  beat, the web encode at full size on the densest frame (beat 8) and the poster.
- Not checked on a phone or an iPhone. Sub lines are 32 px on a 1080 frame, so on a
  phone-width player they are small; the headlines carry the story there.

## What a first-time viewer saw, and what was fixed

- The sources page first showed three rows only (the crop was too short); recaptured.
- The People search was too small to read; now shown at 1.5x on the name column.
- The two sub lines in the search beat overlapped; the second now waits for its cue.
- The passkey push cropped the dialog's sides; the dialog now has room to move.
- The candidate duplicates text was too small; the camera now pushes to the two
  "likely two people" lines.
- The marks on Mara Quill's exhibits cut through the times; they now hold both lines.
- The gate book first zoomed onto the wrong row; it now shows the DRIVER header, the
  blank line, 22:41 out and "22:40 only".

## Weakest shots

- Beat 8 leaves the lower part of its panel empty once the camera pushes in.
- Beat 11 (old footage) is the case's files only; the app had not fingerprinted the
  clips when this was made. Recapture it from the evidence page (see `../script.md`).
- Every seam is the same blur swap through the paper ground; one or two cuts through an
  object on screen would read less like slides.
