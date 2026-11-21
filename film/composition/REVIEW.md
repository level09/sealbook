# Review, cut two remix, 2026-11-21

140.5 s at 1920 x 1080, 30 fps. Web file `site/media/case_film.mp4`: H.264 High CRF 27
with AAC 128k stereo, 12.5 MB. Web file loudness: -16.1 LUFS integrated, LRA 3.4 LU,
true peak -1.3 dBFS. Stems in `film/build/`: voice -16.0 LUFS; score -31.9 LUFS, peak
-15.3 dBFS, so the bed sits about 16 LU under the voice the whole way.

## The doubled sound

Owner report: "an echo or double sound" in the first narrated cut.

Cause: the voice was sped up 1.16x in the mix with ffmpeg `atempo`, overlap-add time
stretching, which gives speech a phasey, doubled texture. The pumping score bed may have
added to it: it was ducked by a sidechain compressor (ratio 6, release 900 ms) behind
every word, and the score carries a hall reverb.

Fix: the voice is regenerated at its natural speed (`eleven_multilingual_v2`, speed 1.0)
and never stretched. The lines fit the existing picture times, three starts moved by up
to 0.6 s. The score is now a steady bed with no ducking.

What each measurement showed, cut two against this remix, on line 07 (10 s):

| Signal | Autocorrelation peak, 25 to 400 ms | Left against right | Model ("doubled, echoed, phasey?", 0 to 3) |
| --- | --- | --- | --- |
| Raw ElevenLabs line, cut two | 0.07 | mono | 0 |
| Same line after `atempo` | 0.075 at 25 ms | identical, no lag | 0 |
| Cut two voice stem | 0.035 | identical, no lag | 3, then 0 on a repeat |
| Cut two padding and concat only | 0.035 | identical | 3 ("two slightly misaligned copies") |
| Cut two final mix | 0.035 | voice centred, no lag | 3 ("doubling and flanging") |
| Score (both cuts) | 0.49 to 0.51 at 36 to 46 ms | wide stereo | 0, "warm concert hall reverb", no voice-like element |
| Remix raw line | 0.053 | mono | 0 |
| Remix voice stem | 0.052 | identical, no lag | 0, and 0 on a repeat |
| Remix final mix | 0.054 | voice centred, no lag | 0, and 0 on a repeat |

No measurement found a delayed copy of the voice at any stage. The voice was always
identical in both channels. The listening model rated the same cut-two stem 3 on one run
and 0 on the next, so it is a weak instrument. Taken together: the stretch is the known
cause, and nothing else in the chain doubles the voice. The score's high
autocorrelation is its reverb and sustained notes, not an echo of speech.

A transcription of the remixed film matches the captions line for line. The four
spellings come back as one, since they sound alike; the screen carries the spelling.

## Picture fixes in this pass

- Step 3 now reads "A person approves or declines each one with a signature", over the
  reject form, and the narration says the same.
- The film opens on a document already on the page, not on empty paper.
- The four conclusions are captured in a narrower window, so their lines wrap. They show
  about a third larger, and each scrolls from the sentence to the confidence, the
  alternatives set aside and what is not established.

## Kept warnings and weak spots

- `dead_stretch` where a screen holds under the voice, `line_unread` on lines the voice
  speaks, `text_below_floor` inside captured app crops.
- The never-there disagreement panel has no marks; its rows change order between loads.
- 2:20, five seconds over the asked length; the picture was not recut.
