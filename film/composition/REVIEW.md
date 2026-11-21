# Review, the story cut, 2026-11-21

185.1 s at 1920 x 1080, 30 fps. Web file `site/media/case_film.mp4`: H.264 High CRF 28
with AAC 128k stereo, 13.4 MB, -16.1 LUFS integrated, LRA 3.7 LU, true peak -1.2 dBFS.
The sound chain is the remix's: natural-speed voice, steady bed, one gain, limiter.

## What changed

The owner did not follow every use case. Stories two to four were about 13 s each, began
mid-thought, and showed dense app panels. Each story is now four beats, each with its own
picture:

1. the situation, in everyday words;
2. the question, set large as a question;
3. what the app surfaced, as the case's own material shown large, with one app line
   zoomed until it reads;
4. what the person concluded, as the app's conclusion sentence verbatim with "signed by
   a person, checked by a colleague".

Story one keeps one capture of the full conclusion panel. The marks follow the voice:
21:52 and 22:07 light up as they are spoken, the 2024 clip flips as "mirrored" is
spoken, and the 73-day gap is drawn on a timeline from the gate log dates.

## Comprehension test

A model with no context was given only the transcript, then separately a contact sheet of
each story with no audio. It restated the situation, the question, what the software found
and what the person concluded for all four stories from both channels. Two gaps found on
the first run were fixed:

- **Story four, transcript only.** The model could not say what the person concluded. The
  last line now says "A person records it as a lead to follow, not a finding."
- **Story four, pictures only.** The model could not tell whether software found the gap.
  The timeline's heading and label now say the app found it.

## Kept and weak

- 3:05, past the asked 2:50. The ElevenLabs quota ran out, so no line could be shortened
  by re-synthesis. Line 07b was cut to its question, and the pile is shown during the
  pause that leaves (45 to 52.9 s).
- The conclusion sentences in stories two to four are on screen for 5 to 9 s; the long
  one in story four cannot be read in full in that time. The narration and captions carry
  its meaning.
- `check` warnings kept as before: `dead_stretch` under the voice, `line_unread` on lines
  the voice speaks, `text_below_floor` inside captured app crops.
