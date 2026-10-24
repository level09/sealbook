# Sealbook showcase site

Static HTML, CSS and JavaScript with no build step. Fonts are self-hosted in
`fonts/` under the SIL Open Font License (`fonts/OFL-*.txt`). The page makes no
third-party requests.

## Regenerate data

`build_data.py` rebuilds `data/case.json` and `media/` from the acceptance files,
a demo seed on a throwaway Postgres database (`build_live.py`) and a local speech
to text run. It needs local Postgres with CREATEDB, the downloaded speech model
(`stk speech-model`) and, for the video search part, local Ollama. The contract is
`data/SCHEMA.md`.

## Preview

```sh
cd site && python3 -m http.server 8080
```

Open http://localhost:8080. The page must be served; `fetch` does not work from `file://`.

## Structure

- `index.html`: seventeen sections, each a `section.link` in the demonstration chain.
- `js/site.js`: theme, data loading, the chain, and the `window.sealbook` API
  documented at the top of the file. Demo scripts call `sealbook.onReady(data => ...)`
  and mount into their `[data-demo]` element.
- `js/demo-*.js`, `css/demo-*.css`: one script and stylesheet per chapter demo.

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
