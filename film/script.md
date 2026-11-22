# Script: four stories of how Sealbook helps

218.8 seconds, 1920 x 1080, 30 fps, narrated at the voice's natural speed over a steady,
quiet score. Written for a viewer who has never heard of the app. The narration is
`narration.json`: each line has the pause before it, and `audio.py plan` turns the
pauses into start times, the film's length and the picture's timing. `audio.py check`
prints the pacing figures and fails when a rule breaks. The captions (`captions.vtt`)
are the narration, one cue per line.

Every story follows the same four beats, each with its own picture: the situation, the
question (shown large, as a question), what the app surfaced (the real app screen where
it happens), and what the person concluded (the app's conclusion sentence, verbatim,
with "signed by a person, checked by a colleague").

Story start times, for links into the film: one 32.8 s, two 90.7 s, three 138.8 s,
four 167.1 s. The seal starts at 200.7 s and the close at 210.7 s. Stills that stand for
each story: 62 s (one man under four names), 109 s (the app reading an exhibit sheet),
154 s (the two clips and the match), 184 s (the 73 days on the driver's page).

Picture sources: **app** is a capture of the seeded cases (`capture.py`); **file** is a
case's own file from `acceptance/cases/<slug>/files/`; **type** is words; **drawn** is
drawn from a case file's data.

## Opening, 0 to 32.8

| At | Narration | Picture |
| --- | --- | --- |
| 1.0 | Investigators, journalists and lawyers share one problem: too much evidence to read, and a result nobody can later prove was not changed. | file: a pile of the cases' material |
| 10.5 | Sealbook works in three steps. | type |
| 12.89 | You bring the material in. | app: Sources |
| 15.97 | The app reads all of it and proposes connections, each with the exact place it came from. | app: the agreement scan with its claims boxed |
| 21.98 | A person approves or declines each one with a signature, and the record is sealed, so anyone can check it later. | app: the surveyor's rejected link; site: the checker's verdict on the four-names package |
| 29.13 | Four invented cases show how that helps. | type: the four kinds of help |

## Story one: one person under several names, 32.8 to 90.7

| At | Beat | Narration | Picture |
| --- | --- | --- | --- |
| 32.96 | situation | One: a freight company pays a supplier, a consultant, a guarantor and a ship agent. Four desks, four clean files. | file: the four desk documents, labelled |
| 41.45 | question | Are they four people, or one man under four names? The desks hold 14 sources and 1,445 rows. | type: the question; app: Sources, under "14 sources and 1,445 rows" |
| 51.3 | surfaced | Kassar. Qassar. Khassar. Kasar. And on one licence, the name in Arabic. | file: each name lifts off its document as it is spoken |
| 57.64 | surfaced | Four spellings, two alphabets, one man. And one who is not him: a surveyor with the same name, born elsewhere. | file: the names beside one face (generated, labelled); the surveyor's register row in red |
| 65.74 | surfaced | The app proposed each link with the passage behind it. Fixed rules, with no AI model, found 6 of the 6 true links, and offered the surveyor too. | app: an accepted proposal, pushed in to the licence card |
| 75.8 | concluded | A person declines the surveyor, with a reason, and signs each decision with a passkey on their own device. | app: the surveyor's link, rejected; the birth date and birthplace rows marked |
| 82.71 | concluded | The case closes on a conclusion, signed by a person and checked by a colleague: one man, under four names. | the conclusion sentence large |

## Story two: a statement that does not fit, 90.7 to 138.8

| At | Beat | Narration | Picture |
| --- | --- | --- | --- |
| 90.86 | situation | Two: a workshop burns down at night. The bookkeeper says she spent the evening at her sister's, 34 kilometres away. | file: the gate photograph |
| 98.8 | question | Was she? There are 41 statements to read. | type: "Was she?" |
| 102.96 | surfaced | The app reads every paper itself. On this scanned exhibit sheet, it reads the page, then pulls out who was where, boxed where it is written. | app: exhibit MQ/3 with its claim boxes, pushed in; the text read from it; the claim "Seen at Tanner's Row, Larkhaven" |
| 112.75 | surfaced | It describes the photograph from her phone, and writes down the voice note she sent. | app: the photograph's first pass ("Broken wooden gate at night") and the voice note's transcript ("I'm still at the yard on Tanner's Row"), each marked |
| 118.14 | surfaced | Then it flags what does not fit: those two exhibits place her at the workshop at 21:52 and 22:07, while she says she was away from six. | app: "Two places on one day" on her page; 21:52, 22:07 and her own 18:00 to 24:00 marked as spoken |
| 129.18 | concluded | A person checks both against her statement and signs: she was at the yard on Tanner's Row that evening, not in Brindle Cross from six. | the conclusion sentence large |

## Story three: an old clip shared as new, 138.8 to 167.1

| At | Beat | Narration | Picture |
| --- | --- | --- | --- |
| 138.95 | situation | Three: a flood video spreads as breaking news. | file: the shared clip, large |
| 142.84 | question | Is it new? The tip line holds 36 clips that look alike. | type: "Is it new?"; file: twelve of the 36 clips |
| 147.91 | surfaced | The app compares their sound and ties the viral clip to a video posted two years earlier. It had been cropped and mirrored, so the picture no longer matched. | file: the two clips side by side, the 2024 one flips as "mirrored" is spoken; app: the Copies row, 118 of 118 sound points, strong |
| 157.85 | concluded | 8 of 9 reposts were tied this way; one muted copy is missed. A person signs that the clip is old. | the conclusion sentence large, with the 8 of 9 and the missed copy beneath |

## Story four: a person who drops out of the record, 167.1 to 200.7

| At | Beat | Narration | Picture |
| --- | --- | --- | --- |
| 167.32 | situation | Four: a nurse boards a night bus and is never seen again. | file: the bus depot (an edited Commons photograph) |
| 171.94 | question | Where do you look? The bus company's logs run to several hundred lines. | type: the question; file: the gate book page, the 22:40 line with no driver marked |
| 177.98 | surfaced | A supervisor's statement says when the driver was last seen. From that night, the app finds 73 days in which no log names him. | app: "No evidence for 73 days" on the driver's page; drawn: one tick per day the gate log names him, the gap shaded |
| 187.41 | concluded | The conclusion, signed and checked by a colleague: Aron Vesk is named in no log for the next 73 days; this is a lead to follow, not a finding about what he did. | the conclusion sentence large, held 13.4 s |

## Seal and close

| At | Narration | Picture |
| --- | --- | --- |
| 200.89 | Every decision is signed by a person, and the case is sealed. Here the first case's real package is checked in a browser, with nothing uploaded. | site: the checker on the four-names package the seeded case built: "Every check that ran passed", 10 checks ran, 4 were not run on that page |
| 210.94 | The machine reads more than a person can, and finds the links. A person approves every one. | type: the two claims |

## Limits the film keeps

- Six of six true links, found by fixed rules with no model, and one false proposal, the
  surveyor, which a person declines.
- The surveyor's decision is shown as the seeded analyst left it (rejected); the film
  shows no signing gesture, since the capture account has no passkey.
- Never-there: what the app read was recorded on the live routes and is replayed when
  the case is seeded; the screens say "replayed from a recording". The clash is by the
  clock, from the two exhibit sheets.
- Old footage: 8 of 9 copies, mostly by sound; one muted copy missed.
- Unwritten name: found because a statement says when he was last seen; a lead, not a
  finding. The drawn timeline uses the gate log only.
- The checker on the site runs 10 checks and says 4 were not run on that page; the film
  says so.
- Every case is invented; the portrait is generated and labelled.
