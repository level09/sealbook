# Script: One man, four names

96 seconds, 1920 x 1080, 30 fps, no sound. On-screen words are set in the film; the
captions in `captions.vtt` add the limits and are drawn by the site over the bottom of
the frame, so nothing essential sits there. Every number comes from
`acceptance/cases/*/README.md`, `docs/case-studies.md` or the app screen itself.

Source of each picture: **app** is a capture of the seeded case on a local
development server (`capture.py`); **file** is the case's own file from
`acceptance/cases/<slug>/files/`.

| # | Time | Picture | On screen | Caption |
| --- | --- | --- | --- | --- |
| 1 | 0.0 to 5.5 | file: the five four-names scans fanned on the desk | One man, four names. A fictional case. Every person, company and place in it is invented. | A fictional fraud case. Every name, firm and place in it is invented. |
| 2 | 5.5 to 12.5 | app: Sources page, starting on the case description ("1,445 sheet rows and five scans, 14 sources, 225 people"), pulling out to the list | 14 sources. 1,445 sheet rows. Five scans. Each desk at the freight company checked its own file and found nothing wrong. | The app counts 225 people. Four of them are one man. |
| 3 | 12.5 to 19.5 | app: Review, "A. Qassar" boxed on the consultancy agreement scan, then the page | Every row and every scan is read. Each value is boxed where it sits, so a person can check it against the page. | Sheets are read cell by cell, scans box by box. |
| 4 | 19.5 to 29.5 | app: People search "Adrian Kassar", then "قصار"; four desk records marked violet, two namesakes red | One name, searched in any spelling or script. Four desks wrote him four ways: Kassar, Qassar, Khassar, Kasar. Then: In Arabic script too. And two namesakes sit in the same files. | One search finds every record that could be him. / His ship agent licence writes the name in Arabic. |
| 5 | 29.5 to 39.5 | app: Review, linker proposal "Same person? Adrian Qassar and Adrian Kasar", push into the licence card with the Arabic name and 2 Mar 1974 | The linker proposes. It shows why. Five proposals from 417 claims, by fixed rules, each with the passages behind it. | No model ran in this demo. The rules alone built the five proposals. / They found 4 of the 6 true links between his desks. |
| 6 | 39.5 to 48.5 | app: the surveyor proposal, born 1974 against 19 Aug 1974 (red), then the Reject button | A namesake it must not merge. A marine surveyor, Adrian Kasar, born 19 August 1974 in Torvane. A person declines him. | Here the rules list the surveyor, and a person declines him. / A live model declined him too: the places of birth differ. |
| 7 | 48.5 to 54.5 | app: "Confirm with your passkey" for the true link, the row and its hash marked | Nothing joins until a person signs. Their passkey signs exactly the row on screen, hash and all. | Signing needs the analyst's own passkey, so the film stops at the prompt. |
| 8 | 54.5 to 61.0 | app: Adrian Khassar's page, Candidate duplicates, the two "likely two people" lines | Same name is not same person. His page lists who may be him, and keeps the deck cadet and the surveyor apart. | Approving the four true links joins all nine of his records. |
| 9 | 61.0 to 68.0 | app (site page): `site/verify.html` on a test package, "Every check that ran passed" and the checks | The seal lets anyone check it later. A court can verify a package offline, without trusting the app. Note: Checker shown on a test package. Signed packages are in preview. | The checker runs in the browser and sends nothing. Shown here on a test package. |
| 10 | 68.0 to 75.0 | file: IMG_4471.jpg; app: Mara Quill's page, "Two places on one day, 2026-03-14", exhibit sheets MQ/3 and MQ/4 marked | Another fictional case. The witness who was never there. The app flags two places on one day. A person reads the times: 21:52 and 22:07. | The app finds the clash by day, not by hour. The times are for a person to read. |
| 11 | 75.0 to 82.0 | file: original.mp4 and copy_viral.mp4 side by side | Another fictional case. The footage that was two years old. 8 of 9 reposts tied to the 2024 original, mostly by sound. One cropped, mirrored, muted copy is missed. | The picture no longer matches. The sound does. |
| 12 | 82.0 to 89.0 | file: gate book page, the 22:40 line with no driver; app: Aron Vesk's track, "last seen" then "No evidence for 73 days" | Another fictional case. The name nobody wrote down. A statement says when the driver was last seen. The app finds 73 days with no record of him. | The app does not notice on its own that someone stopped appearing. The statement starts the watch. |
| 13 | 89.0 to 96.0 | type only | The machine reads more than a person can, and finds the links. A person signs every finding. The seal proves it later. Four fictional cases. sealbook.org | Every case in this film is fictional. |

## What the film does not show, and why

- **The signature itself.** A passkey gesture needs the analyst's own authenticator, so
  beat 7 stops at the prompt; nothing is signed or written by `capture.py`. The signed
  state in beat 8 is the seeded desk links, which a case analyst accepted with a passkey
  signature when the case was seeded.
- **The person declining the surveyor.** In the seeded app the linker ran without a
  model, so the surveyor proposal waits in Review; beat 6 shows the Reject button, not a
  rejection. The live model's decline is in `docs/case-studies.md`.
- **A four-names package.** Building a signed package is off on a server without a
  signing certificate, and the case has none. Beat 9 shows the site's checker on the
  offline verifier's own test package (`tests/test_verify_package.py`), and says so.
- **The old-footage match in the app.** When the film was made the 44 clips in
  `case-old-footage` were still "processing" on the development server (no worker had
  fingerprinted them), so beat 11 is built from the case's files. Recapture it from the
  evidence page of `copy_viral.mp4` once the worker has run, and replace the right clip
  with the match panel.

## Recapture later

- Beat 11 from app screens, as above.
