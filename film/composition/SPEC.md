# Case film spec

GOAL: a stranger understands, in one viewing, that the app finds one man hidden under
four spellings in a pile no reader would cross-check, that a person approves every
link with a signature, and that the seal can be checked later; then sees three other
kinds of case get the same treatment.

OUTPUT: HyperFrames, 1920 x 1080, 30 fps, silent (captions come from `film/captions.vtt`,
drawn by the site over the bottom 130 px, so nothing essential sits below y 950).

TIME: 96 s x 30 = 2880 frames. No music.

LOOK: the site's own medium, paper and ink.
- Ground `#f6f3ec` (paper), panels `#ece8de`, rules `#d3cdbf`. Ink `#1b1a17`, second ink
  `#45423b`, third `#5f5b52`. One accent, violet `#5732c2`, only on the thing the viewer
  must look at (a mark around a row, a card or a button). `#a3221a` marks a namesake only.
- Display: Newsreader 400, 60 px headlines, 120 px for the title, tracking -0.01em.
  Body: IBM Plex Sans 400, 30 px subs, 24 px labels. Arabic falls back to IBM Plex Sans Arabic.
- Real app screens are captured at 2x and shown in a panel with a 1 px rule, 16 px
  radius and a soft shadow (0 30 60 -30, 35%). No grain, no vignette.

OBJECTS: app screens (people search, a proposed link, the passkey prompt, a person's
page, the package checker); the case's own scans, photograph, gate book page and clips.
Nothing is drawn except the marks around what to read.

MOTION: arrivals `expo.out` 0.7 s; camera moves `power2.inOut` 1.4 to 2.2 s; exits
`power2.in` 0.25 s with 8 px blur. Marks arrive with opacity and a 4% scale settle in
0.35 s. Text lands and stays still. Each beat holds its key screen at least 3 s.

SHOTS: see `../script.md` for the beat sheet with times and words.

BANNED: dark void, glow, gradients, glass, three cards in a row, a title over a dimmed
screenshot, text that moves while read, everything centred, everything fading in at once,
small caps labels, corner brackets, a docked logo, a full app page shown small.

QA: every headline readable at 360 px wide; every screen crop readable at 1080p;
the numbers on screen match the case READMEs (14 sources, 1,445 rows, 417 claims,
5 proposals, 4 of 6, 73 days, 8 of 9).
