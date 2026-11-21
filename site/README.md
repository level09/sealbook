# Sealbook showcase site

Static HTML, CSS and JavaScript with no build step. Fonts are self-hosted in
`fonts/` under the SIL Open Font License (`fonts/OFL-*.txt`). The page makes no
third-party requests. The one dynamic part is the invite form: `js/invite.js` posts
to `/api/invite`, which `worker.js` stores in the D1 (SQLite) database
`sealbook-invites` (`invites.sql`). It has no captcha, which would load a third-party
script; a honeypot field and five requests per hour per salted IP hash keep it quiet.
`worker.js` also serves the films under `media/` with byte ranges (206), because
Workers assets answer a Range request with the whole file and Chrome then cannot seek.

The form asks for an email and an optional note only. The page has a sticky header
with chapter links and a Contents popover listing every section; section jumps are
instant because demos grow as a smooth scroll passes them. Each tour section has a
small shape diagram (`js/diagrams.js`).

## Regenerate data

`build_data.py` rebuilds `data/case.json` and `media/` from the acceptance files,
a demo seed on a throwaway Postgres database (`build_live.py`) and a local speech
to text run. It needs local Postgres with CREATEDB, the downloaded speech model
(`stk speech-model`) and, for the video search part, local Ollama. The contract is
`data/SCHEMA.md`.

`uv run python site/build_data.py --live watch casemap` rebuilds only the named keys
from the throwaway app run and writes every other key back unchanged; it needs
Postgres only.

## Preview

```sh
cd site && python3 -m http.server 8080
```

Open http://localhost:8080. The page must be served; `fetch` does not work from `file://`.

## Structure

- `index.html`: the home page, written for someone who has never heard of Sealbook, in
  this order, each part answering one question:
  1. The opening (`#opening`): what it is and who it is for. The headline, one
     paragraph and the case film (`media/case_film.mp4`), with a storyboard of six
     stills under it, each seeking the film to its moment.
  2. How it works (`#how`): the film's three steps, beside the isometric picture of the
     flow (`js/iso.js`).
  3. Four cases (`#cases`): one card per case study with its twist and measured score,
     each linking to its page.
  4. What a person decides (`#decide`): approvals and the signed conclusion, shown on the
     conclusion of "The name nobody wrote down" with its confidence scale (labels from
     `sealbook/method/standards.csv`).
  5. Break the seal (`#finale`) and Proof (`#proof`): the seal, the tamper demo, the
     witnesses, the package for a court and the checker.
  6. Where the evidence goes (`#data`), then what is measured and what is not
     (`#measured`, pointing to Status), the closing line and the pilot request.

  Each `section.link` is a link in the demonstration chain; parts in `div.part` are not
  hashed. The precise technical text sits in a `details.how` fold inside `.copy`, so it is
  still part of the hashed chain. The headline is the owner's and stays as written.
- `js/film.js`: the film player on the home page and the fraud case page. One drawn play
  control on the poster, then the browser's own; captions drawn from the WebVTT file,
  not a `<track>`, because an iPhone refuses to play the film inline with one
  (`docs/lessons.md`). A figure's `data-start`, or `index.html#t=<seconds>` from a case
  page's "Watch this case in the film", sets where play starts; playing waits for the
  tap, so phones play inline and with sound.
- `film_board.tsv`, `build_film_board.py`: the storyboard's moments and each case's start
  in the film, kept in one file. After a new render, edit the seconds there and run
  `python3 site/build_film_board.py`: it writes the stills to `media/board/film-*.jpg`,
  the board's buttons in `index.html`, every case page's film start, and new `?v=` values
  for `case_film.mp4`, `.jpg` and `.vtt`.
- `cases/<slug>.html`, `css/cases.css`, `js/cases.js`: one page per case study in
  `acceptance/cases/` (four-names, never-there, old-footage, unwritten-name), in the same
  order on every page: the story and its twist, the pile, what the machine found, what the
  person decided, the score, the limit, the way to court, and the credits from the case's
  `files/ATTRIBUTION.md`. The signed conclusion closes the story, read from the case's
  `case.json` when the pages were written. Separate pages give each case its own link. Every figure is one
  the case's README or its test (`tests/test_case_*.py`) states; change them together.
  Each page and card says the case is fictional. `build_cases.sh` rebuilds their pictures
  and clips in `media/cases/` from the case files (needs ffmpeg).
- `tour.html`: earlier demos on the old sample case, folded until opened or linked to,
  including the eight exhibits that used to sit on the home page. Nothing on the home
  page sends a newcomer there first; it is linked from Contents and the closing links.
- `status.html`: what we learned, and the implemented, measured and planned lists read
  from `data/case.json`.
- `verify.html`, `js/verify-package.js`, `js/verify-page.js`, `css/verify.css`: check a
  dropped package zip in the browser, nothing uploaded. `verify-package.js` ports
  `sealbook/ledger/verify_package.py` (layout v1) to WebCrypto and runs under node too;
  `tests/test_site_verify.py` runs every offline verifier case through both. It does not
  check the TSA CMS signature, the OpenTimestamps proof to Bitcoin, the C2PA claim or
  key pins, and lists those as not run.
- `js/closing-motion.js`, `css/closing.css`: the home page's styles and motion, and the
  tour's exhibit styles. Each exhibit's tag is stamped, the traditional line struck
  through, and the Sealbook line rises; the verdict lights up as it scrolls; the mic
  drop's letters fall. GSAP 3.15 with ScrollTrigger and SplitText is self-hosted in
  `js/vendor/` (GreenSock standard license, free for this use). Reduced motion leaves the
  page still.
- `media/walkthrough.*` and the six `media/board/*.jpg` stills of the old walkthrough are
  no longer referenced by any page; the tour's exhibits still use some `media/board/`
  stills as pictures.

## Keep it true

The public teaser has no "Try it" steps or setup instructions; it must not
point at the platform.

## Deploy

Public teaser at https://sealbook.org (and www.sealbook.org), a Cloudflare Worker
named `sealbook` that serves static files. `wrangler.jsonc`
holds the name and the custom domains. Publish only the page files, never the build
script, notes or app code:

```sh
rm -rf site/dist && mkdir -p site/dist/data
cp -R site/index.html site/tour.html site/status.html site/verify.html site/cases site/favicon.svg site/og.png site/css site/js site/fonts site/media site/dist/
cp site/data/case.json site/dist/data/
cd site && npx wrangler@4 deploy
```

Afterwards check that https://sealbook.org/build_data.py and /README.md return 404.

The Worker needs two secrets: `INVITE_SALT` (`npx wrangler@4 secret put INVITE_SALT`)
and `INVITE_TO`, the address that receives invite alerts
(`npx wrangler@4 secret put INVITE_TO`). For local testing put both in
`site/.dev.vars`, apply `invites.sql` with `--local` and run
`npx wrangler@4 dev --local`; the email binding is then simulated and logs the message.

## Invite requests

Each stored request also emails the `INVITE_TO` address, sent from
invites@sealbook.org with Reply-To set to the requester. The `send_email` binding in
`wrangler.jsonc` allows only that sender and names no recipient, so no personal
address is in the repository; the Worker sends only to `INVITE_TO`. Without the
secret the request is still stored and the failed alert is logged.
Sending needs sealbook.org onboarded once: `npx wrangler@4 email sending enable sealbook.org`.

```sh
cd site && npx wrangler@4 d1 execute sealbook-invites --remote \
  --command "SELECT created_at, email, use_case FROM invite ORDER BY id DESC"
```

## Film

`media/case_film.mp4` (with `case_film.jpg` and `case_film.vtt`) is built from the tracked
sources in `film/`; see `film/README.md` to record and render it again. It is narrated, with
a generated score, and its captions are drawn from the `.vtt` file by `js/film.js`.
