# Sealbook showcase site

Static HTML, CSS and JavaScript with no build step. Fonts are self-hosted in
`fonts/` under the SIL Open Font License (`fonts/OFL-*.txt`). The page makes no
third-party requests. The one dynamic part is the invite form: `js/invite.js` posts
to `/api/invite`, which `worker.js` stores in the D1 (SQLite) database
`sealbook-invites` (`invites.sql`). It has no captcha, which would load a third-party
script; a honeypot field and five requests per hour per salted IP hash keep it quiet.

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

- `index.html`: nineteen sections, each a `section.link` in the demonstration chain,
  in four parts. The opening sets the headline and the pilot request beside the
  walkthrough film (`media/walkthrough.mp4`), with a storyboard of six stills from it
  (`media/board/`, each seeking the film to its moment). "By hand, and with Sealbook"
  (`#work`) holds six chapters, each setting a task as done by hand (`.byhand`) beside
  what Sealbook proposes (`.with`), with its live demo. "The tour" (`#tour`) indexes the
  nine other sections, which stay folded (`.link.folded`) until opened or linked to.
  "Now break the seal" (`#finale`) leads into Proof. Seals stay to one quiet line
  along the way and show in full in Proof or once a seal breaks. Each section leads
  with a plain `.job` line; the precise technical text sits in a `details.how` fold
  inside `.copy`, so it is still part of the hashed chain.
- `js/site.js`: theme, data loading, the chain, and the `window.sealbook` API
  documented at the top of the file. Demo scripts call `sealbook.onReady(data => ...)`
  and mount into their `[data-demo]` element.
- `js/demo-*.js`, `css/demo-*.css`: one script and stylesheet per chapter demo.
- `js/hero.js`, `css/hero.css`: the opening photo turning into its picture fingerprint. The
  PDQ value is hardcoded there; recompute it if `media/street_photo.jpg` changes.
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
cp -R site/index.html site/favicon.svg site/og.png site/css site/js site/fonts site/media site/dist/
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
