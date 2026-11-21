# Script: four stories of how Sealbook helps

140.5 seconds, 1920 x 1080, 30 fps, narrated, with a quiet score. Written for a viewer
who has never heard of the app. The narration is `narration.json`; `audio.py timing`
prints when each line starts. The captions (`captions.vtt`) are the narration, one cue
per line. Every number comes from `acceptance/cases/*/README.md`,
`docs/case-studies.md` or the app screen itself.

Picture sources: **app** is a capture of the seeded cases (`capture.py`, run against
the database that holds all four cases with their conclusions); **file** is a case's own
file from `acceptance/cases/<slug>/files/`; **type** is words only.

| Time | Narration | Picture |
| --- | --- | --- |
| 0.8 | Investigators, journalists, lawyers and researchers share one problem. | file: a pile of the cases' own material: a scan, the gate photograph, five payment rows, the gate book, the licence |
| 5.7 | Too much evidence to read, and a result nobody can later prove was not changed. | same, with the line on screen |
| 10.9 | Sealbook is an app for exactly that. It works in three steps. | type: Sealbook, one line on what it is |
| 15.5 | You bring the material in. The app reads all of it and proposes connections, each shown with the exact place it came from. | 1, app: the Sources list. 2, app: the consultancy agreement scan with each claim boxed and listed |
| 22.7 | A person approves each one with a signature, and the record is sealed, so anyone can check it later. | 3, app: a decision with its reason and "Reject and sign"; site: the package checker's verdict |
| 29.1 | Four invented cases show how that helps. | type: the four kinds of help |
| 33.0 | One: a person under several names. Four desks at a freight company each keep a clean file: 14 sources and 1,445 sheet rows. | file: the four desk documents (vendor form, consultancy agreement, guarantee deed, ship agent licence) |
| 44.2 | Kassar. Qassar. Khassar. Kasar. And on one licence, the name in Arabic. | file: each name lifts off its document as it is spoken, cropped from the scan; "Kasar" is row 28 of the ship agents register; the Arabic name is cropped from the licence |
| 51.0 | Four spellings, two alphabets, one man. And one who is not him: a surveyor with the same name, born elsewhere. | file: the names gather beside one face (the case's generated portrait, labelled), "Born 2 March 1974, Velmora"; the surveyor's row from the marine surveyors register is brought close, then pulled apart, red |
| 59.8 | The app proposed these links. Here, fixed rules with no AI model found 4 of the 6 true links, enough to join all his records. | app: the proposal joining Adrian Qassar and Adrian Kasar, pushing into the licence card with the Arabic name and 2 Mar 1974 |
| 68.8 | A person declines the surveyor, and signs each decision with a passkey, a key on their own device. | app: the surveyor proposal (1974 against 19 Aug 1974), then the reject form with its reason and "Reject and sign" |
| 75.3 | The case closes on a signed conclusion, checked by a colleague, saying how sure it is and what is not established. | app: the case Overview's conclusion: the four names are one man, reasonable grounds to believe, peer reviewed, the surveyor set aside, what is not established |
| 83.0 | Two: a statement that does not fit. A bookkeeper says she was 34 kilometres from a fire. Exhibit sheets from her phone put her at the yard. | file: her photograph of the gate; app: "Two places on one day, 2026-03-14" on her page |
| 92.5 | The app flags two places on one day. A person reads the times, and concludes she was there. | app: the case's conclusion, reasonable grounds to believe |
| 99.2 | Three: an old clip shared as new. The app ties 8 of 9 reposts to a flood video from two years earlier, mostly by sound. One cropped, mirrored, muted copy is missed. | file: the original and the shared clip side by side; app: the shared clip's Copies tab, the 2024 original, 118 of 118 sound points, strong; then the conclusion |
| 112.2 | Four: a person who drops out of the record. A statement says when a bus driver was last seen. The app finds 73 days with no log naming him. A lead, not a finding. | file: the gate book page, the 22:40 line with no driver; app: "No evidence for 73 days" on his track; then the conclusion, worded as a lead |
| 124.9 | Every decision is signed by a person. A court can check the sealed package offline; here, on a test package. | site: the package checker's list of checks, labelled "Checker shown on a test package" |
| 132.6 | The machine reads more than a person can, and finds the links. A person approves every one. | type: the two claims |

## Limits the film keeps

- Four of six true links, found by fixed rules with no model; the narration says so.
- The surveyor: the scratch database leaves his proposal open, so the film shows the
  reject form with a reason and stops before "Reject and sign". The capture account has
  no passkey, so no signing prompt is shown; the note on screen says signing needs the
  person's own passkey. Nothing is signed or written by the capture apart from the logged
  reveal of the passport (the four-names conclusion rests on it) and the sign-in session.
- Never-there: the clash is by day; a person reads the times from the exhibit sheets.
- Old-footage: 8 of 9 copies, mostly by sound; one copy missed.
- Unwritten-name: found because a statement says when he was last seen; a lead, not a
  finding.
- The checker runs on the offline verifier's own test package, and the screen says so.
- Every case is invented; the portrait is generated and labelled.
