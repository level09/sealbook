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
with five chapter links and a Contents popover listing every section; section jumps
are instant because demos grow as a smooth scroll passes them. The opening shows a
credited Commons photo turning into its picture fingerprint (`js/hero.js`), and each
section has a small shape diagram (`js/diagrams.js`).

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

- `index.html`: the home page as a closing argument, each section a `section.link` in
  the demonstration chain. The opening sets the headline and the pilot request beside
  the walkthrough film (`media/walkthrough.mp4`), with a storyboard of six stills from it
  (`media/board/`, each seeking the film to its moment). "Four cases" (`#cases`) follows:
  one card per case study with its twist and its measured score, each linking to its page.
  "The charge" (`#charge`) states
  the case against traditional systems, and a sticky rail (`.rail`) numbers the
  exhibits. Exhibits 1 to 8 (`.link.exhibit`) each set the traditional way, struck
  through (`.then`), against what Sealbook does (`.now`), and stay folded until "See it
  run" opens their live demo. "Don't take our word for it" (`#finale`, `.mic`) leads
  into Proof, which is Exhibit 9, then the verdict (`#verdict`) and the pilot request.
  Comparisons say "traditional systems", never a named product. Seals stay to one quiet
  line along the way and show in full in Proof or once a seal breaks. The precise
  technical text sits in a `details.how` fold inside `.copy`, so it is still part of
  the hashed chain.
- `cases/<slug>.html`, `css/cases.css`, `js/cases.js`: one page per case study in
  `acceptance/cases/` (four-names, never-there, old-footage, unwritten-name), in the same
  order on every page: the story and its twist, the pile, what the machine found, what the
  person decided, the score, the limit, the way to court, and the credits from the case's
  `files/ATTRIBUTION.md`. Separate pages give each case its own link. Every figure is one
  the case's README or its test (`tests/test_case_*.py`) states; change them together.
  Each page and card says the case is fictional. `build_cases.sh` rebuilds their pictures
  and clips in `media/cases/` from the case files (needs ffmpeg).
- The case film slot (`figure.case-film`, on the home page and the fraud case page) points
  at `media/case_film.mp4` with `media/case_film.jpg` as its poster. `js/cases.js` asks
  for the film with a HEAD request and keeps the slot hidden until it exists, so adding
  the two files is all it takes to show it.
- `tour.html`: the nine other demos, folded until opened or linked to.
- `status.html`: what we learned, and the implemented, measured and planned lists read
  from `data/case.json`.
- `verify.html`, `js/verify-package.js`, `js/verify-page.js`, `css/verify.css`: check a
  dropped package zip in the browser, nothing uploaded. `verify-package.js` ports
  `sealbook/ledger/verify_package.py` (layout v1) to WebCrypto and runs under node too;
  `tests/test_site_verify.py` runs every offline verifier case through both. It does not
  check the TSA CMS signature, the OpenTimestamps proof to Bitcoin, the C2PA claim or
  key pins, and lists those as not run.
- `js/closing-motion.js`, `css/closing.css`: the home page's motion and exhibit styles.
  Each exhibit's tag is stamped, the traditional line struck through, and the Sealbook
  line rises; the charge and the verdict light up as they scroll; the mic drop's letters
  fall. GSAP 3.15 with ScrollTrigger and SplitText is self-hosted in `js/vendor/`
  (GreenSock standard license, free for this use). Reduced motion leaves the page still.
- `js/site.js`: theme, data loading, the chain, and the `window.sealbook` API
  documented at the top of the file. Demo scripts call `sealbook.onReady(data => ...)`
  and mount into their `[data-demo]` element.
- `js/demo-*.js`, `css/demo-*.css`: one script and stylesheet per chapter demo.
- `js/hero.js`, `css/hero.css`: the opening photo turning into its picture fingerprint. The
  PDQ value is hardcoded there; recompute it if `media/street_photo.jpg` changes.
- `js/iso.js`, `css/iso.css`: the opening's isometric picture of the whole flow
  (`figure[data-iso]`): files, the reader, hatched proposals, the signing ring and the
  sealed book, drawn from one `box()` helper in the page's own colours.
- `js/diagrams.js`, `css/diagrams.css`: an animated diagram above each chapter demo,
  one `figure[data-diagram]` per section. Values are copied from `data/case.json` by hand,
  so update them when the case is rebuilt. Outside `.copy`, so the chain does not hash them.

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
a generated score, and its captions are drawn from the `.vtt` file like the walkthrough's.
