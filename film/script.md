# Script: four stories of how Sealbook helps

185.1 seconds, 1920 x 1080, 30 fps, narrated at the voice's natural speed over a steady,
quiet score. Written for a viewer who has never heard of the app. The narration is
`narration.json`: each line's `at` is the time the picture is cut to, and
`audio.py timing` checks that no line runs into the next. The captions (`captions.vtt`)
are the narration, one cue per line.

Every story follows the same four beats, each with its own picture: the situation, the
question (shown large, as a question), what the app surfaced, and what the person
concluded (the app's conclusion sentence, verbatim, with "signed by a person, checked by
a colleague").

Story start times, for links into the film: one 32.95 s, two 92.9 s, three 119.6 s,
four 145.9 s. The seal starts at 168.9 s and the close at 176.6 s.

Picture sources: **app** is a capture of the seeded cases (`capture.py`); **file** is a
case's own file from `acceptance/cases/<slug>/files/`; **type** is words; **drawn** is
drawn from a case file's data.

## Opening, 0 to 32.9 (unchanged from the previous cut)

| At | Narration | Picture |
| --- | --- | --- |
| 0.8 | Investigators, journalists, lawyers and researchers share one problem. | file: a pile of the cases' material |
| 5.73 | Too much evidence to read, and a result nobody can later prove was not changed. | same |
| 10.94 | Sealbook is an app for exactly that. It works in three steps. | type |
| 14.85 | You bring the material in. The app reads all of it and proposes connections, each shown with the exact place it came from. | app: Sources; the agreement scan with its claims boxed |
| 22.6 | A person approves or declines each one with a signature, and the record is sealed, so anyone can check it later. | app: a reject form with its reason; site: the checker's verdict |
| 29.1 | Four invented cases show how that helps. | type: the four kinds of help |

## Story one: one person under several names, 32.95 to 92.9

| At | Beat | Narration | Picture |
| --- | --- | --- | --- |
| 32.98 | situation | One: a freight company pays a supplier, a consultant, a guarantor and a ship agent. Four desks, four clean files. | file: the four desk documents, labelled |
| 41.56 | question | Are these four people, or one man under four names? | type: the question large; app: the Sources list under "14 sources and 1,445 rows, more than anyone reads side by side" |
| 52.87 | surfaced | Kassar. Qassar. Khassar. Kasar. And on one licence, the name in Arabic. | file: each name lifts off its document as it is spoken |
| 58.16 | surfaced | Four spellings, two alphabets, one man. And one who is not him: a surveyor with the same name, born elsewhere. | file: the names gather beside one face (generated, labelled); the surveyor's register row pulled apart in red |
| 66.05 | surfaced | The app proposed these links, each with the passage behind it. Fixed rules with no AI model found 4 of the 6 true links, enough to join all his records. | app: the proposal, pushed in to the licence card |
| 76.49 | concluded | A person declines the surveyor, and signs each decision with a passkey, a key on their own device. | app: the reject form with its reason |
| 82.98 | concluded | The case closes on a conclusion, signed by a person and checked by a colleague: one man, on four desks, under four names. | app: the full conclusion panel; then its sentence large |

## Story two: a statement that does not fit, 92.9 to 119.6

| At | Beat | Narration | Picture |
| --- | --- | --- | --- |
| 93.04 | situation | Two: a workshop burns down at night. The bookkeeper says she spent the evening at her sister's house, 34 kilometres away. | file: the gate photograph, large |
| 101.76 | question | Was she? There are 41 statements to read. | type: "Was she?" |
| 105.14 | surfaced | The app flags that two exhibit sheets made from her own phone place her at the workshop that same day. | app: the one line "Two places on one day, 2026-03-14"; file: the two exhibit sheets in short |
| 111.54 | concluded | A person reads the times, 21:52 and 22:07, and concludes she was there. | 21:52 and 22:07 light up as spoken; then the conclusion sentence large |

## Story three: an old clip shared as new, 119.6 to 145.9

| At | Beat | Narration | Picture |
| --- | --- | --- | --- |
| 119.79 | situation | Three: a flood video spreads as breaking news. | file: the shared clip, large |
| 123.5 | question | Is it new? The tip line holds 36 clips that look alike. | type: "Is it new?"; file: twelve of the 36 clips |
| 127.77 | surfaced | The app compares their sound and ties the viral clip to a video posted two years earlier. It had been cropped and mirrored, so the picture no longer matched. | file: the two clips side by side; the 2024 one flips as "mirrored" is spoken; app: the Copies row, 118 of 118 sound points |
| 137.1 | concluded | 8 of 9 reposts were tied this way. One muted copy is missed. A person signs that the clip is old. | the conclusion sentence large, with the 8 of 9 and the missed copy beneath |

## Story four: a person who drops out of the record, 145.9 to 168.9

| At | Beat | Narration | Picture |
| --- | --- | --- | --- |
| 146.19 | situation | Four: a nurse boards a night bus and is never seen again. | file: the bus depot (an edited Commons photograph) |
| 150.08 | question | Where do you look? The bus company's logs run to several hundred lines. | type: the question; file: the gate book page, the 22:40 line with no driver marked |
| 155.13 | surfaced | A supervisor's statement says when the driver was last seen. From that night, the app finds 73 days in which no log names him. | drawn: one tick per day the gate log names the driver, the 73-day gap shaded from "last seen, 14 March" |
| 163.44 | concluded | A person records it as a lead to follow, not a finding. | the conclusion sentence large, "Reasonable suspicion" |

## Close (unchanged)

| At | Narration | Picture |
| --- | --- | --- |
| 169.15 | Every decision is signed by a person. A court can check the sealed package offline; here, on a test package. | site: the checker on a test package, labelled |
| 176.88 | The machine reads more than a person can, and finds the links. A person approves every one. | type: the two claims |

## Limits the film keeps

- Four of six true links, found by fixed rules with no model.
- The surveyor is shown up to "Reject and sign"; the capture account has no passkey.
- Never-there: the clash is by day, and a person reads the times from the exhibit sheets.
- Old footage: 8 of 9 copies, mostly by sound; one muted copy missed.
- Unwritten name: found because a statement says when he was last seen; a lead, not a
  finding. The drawn timeline uses the gate log only.
- Every case is invented; the portrait is generated and labelled.
