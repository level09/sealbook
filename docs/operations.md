# Local operation and verification

Use synthetic evidence. Do not access production or partner archives. Name
the environment and obtain confirmation before remote commands or database changes.
Do not print `.env`, keys, login tokens or database passwords into logs.

## Prerequisites

Sealbook needs PostgreSQL with `vector`, `pg_trgm` and PostGIS 3 available,
`ffmpeg`/`ffprobe` for media, and `c2patool` for verification/signing. The Python
environment is managed by uv. SQLite is useful for isolated framework tests, not the
evidence migrations.

PostGIS was checked locally with 3.6.4 (`brew install postgis`). The migration
`20261026_postgis` runs `CREATE EXTENSION IF NOT EXISTS postgis`, which needs a
superuser or a role allowed to create extensions; on a managed or hardened server,
have the database owner run that statement first, then migrate. Debian and Ubuntu
package it as `postgresql-<major>-postgis-3`. `docker-compose.yml` builds
`postgres/Dockerfile` (PostGIS 3.6 on Postgres 17, plus pgvector), and `postgres/init-db.sh` creates
postgis, vector and pg_trgm as the superuser at first start, so the restricted `stk`
role can run every migration. CI uses the same PostGIS image and installs pgvector
into the service container. The PostGIS image is published for amd64 only, so on an
ARM Mac it runs under emulation.

```sh
uv sync
uv run stk --help
uv run stk doctor
uv run stk inspect context --json
```

For an approved local database, apply migrations before serving new features:

```sh
uv run stk db upgrade
uv run stk run --port 5001
uv run stk worker
```

For a populated local app, `uv run stk demo-seed --full` adds `case-demo-02` to
the small `case-demo-01`: the acceptance fixture files through intake, invented
people (twins, cross-script spellings), an active `analyst-2@example.com` colleague
with an unshown random password, review requests both ways, a correction, an undone
decision, watchlists with matcher hits and a family brief. It needs an active admin
(`stk install`) and is additive: it refuses when `case-demo-02` exists. Follow it
with `stk worker`; [demo.md](demo.md) walks through what it writes. It builds no package: that needs `stk anchor`
receipts. Webm clips record C2PA as absent because c2patool refuses the container (the demo seed still skips the check for them).

Places and maps, on an approved local database:

```sh
uv run stk places import --fixture        # or a GeoNames SY.txt
uv run stk places boundaries syr_admin_boundaries.geojson.zip   # OCHA COD-AB from HDX cod-ab-syr
uv run stk basemap                         # needs the pmtiles CLI (brew install pmtiles)
```

`stk places boundaries` takes the GeoJSON zip or its unzipped folder, stores the set
with its licence, and is a no-op for the same bytes. `stk basemap` writes
`BASEMAP_PATH` (default `instance/basemap/syria.pmtiles`, outside git) and a
`.json` sidecar with build, bbox, size and sha256; `--bbox` and `--maxzoom` change
the extract. Restart workers after upgrading, or new `geotag` runs fail with an
unknown tool.

To measure tiles at scale, create a database whose name contains `scale`, migrate
it, then `SQLALCHEMY_DATABASE_URI=postgresql+asyncpg:///<name> uv run python -m
sealbook.maps.scale --count 100000` (`--measure-only` to time again). It writes ledger
rows directly, so never point it at a real database.

Anchoring requires its own configured services and partner root directories. Do not
infer that running the web server schedules or validates those services.

## Discovery configuration

| Setting | Meaning |
| --- | --- |
| `EMBEDDING_MODEL` | EmbeddingGemma 2 server alias for text, actor and media vectors; unset disables semantic search |
| `EMBEDDING_URL` | Local Ollama base URL, for example `http://127.0.0.1:11434`; no fallback |
| `EMBEDDING_API_KEY` | Optional credential for that service |
| `EMBEDDING_REVISION` | Pin weights, projector and runtime build in one revision string |
| `INDEX_BATCH_CHUNKS` | Text chunks per job, default 32 |
| `PDF_BATCH_PAGES` | PDF text pages per job, default 10 |
| `MEDIA_BATCH_SEGMENTS` | Prepared media inputs per job, default 8 |
| `PDQ_DISTANCE` | Visual retrieval Hamming distance, default 31 |
| `PDQ_QUALITY` | Minimum picture feature quality, default 50 |
| `MEDIA_CANDIDATES` | Final candidate cap, default 20 |
| `FINGERPRINT_MAX_SECONDS` | Decode duration limit, default 3600; longer media fail visibly |

The embedding endpoint must return the model it served. Choose and evaluate a real
model deployment separately; transport fixtures test contracts, not model quality.
Never mix incompatible vector spaces or tune audio thresholds against synthetic
tones.

```sh
uv run stk enrich --investigation case-name --limit 100
uv run stk enrich --investigation case-name --limit 100 --after ACQUISITION_UUID
uv run stk enrich --investigation case-name --limit 100 --rebuild
```

`--after` uses the acquisition UUID printed by the previous batch, ordered by
acquisition time then UUID. Keep the same investigation filter. The cursor must
belong to that filter. Pending jobs for the same tool/model/input/page coalesce;
a rebuild request while one is pending is refused. Continuation jobs resume their
chunk/page/media cursor and yield briefly to new evidence.

After changing weights, projector, runtime or preprocessing, update the revision
and requeue acquisitions with `--rebuild`, following every printed batch cursor.
Reference drift under an unchanged alias is refused until explicit rebuild. Old
generations remain outside current semantic queries; coverage stays incomplete
until current vectors exist. A rebuild may repeat work and preserves prior signed
runs and human rejections.

Text extraction handles PDF text layers, not scanned-page OCR. Each page with text
gets its own claim extraction when a model is configured; pages with no text remain
visible gaps. Large text jobs still read the full source to compute their
chunk list; only batch writes and model work are progressive. Fingerprint decoding
remains one bounded file operation. Coverage lists at most 100 sources and marks
a larger filter incomplete. Search candidate limits do not establish absence.

## OCR and claim extraction

Images are read by OCR, then claims are read from the text; text files, transcripts
and each PDF page with text go straight to extraction. Nothing runs until a route
has a model. A run uses the most private route its policy row allows that has a
model set, so with `MODEL_LOCAL` set every source stays local; to send ordinary
sources to the cloud, an admin narrows the policy (for example `ocr`, `*`,
`ordinary` to `{cloud}`). Sensitive sources never leave the local route.

| Setting | Meaning |
| --- | --- |
| `ROUTE_LOCAL_BASE_URL` | Any OpenAI-compatible server; Ollama is `http://127.0.0.1:11434/v1` |
| `ROUTE_LOCAL_API_KEY` | Optional; Ollama needs none |
| `ROUTE_LOCAL_TIMEOUT` | Seconds one local call may take, default 120; the run lease is this plus a minute, at least 5 minutes |
| `MODEL_LOCAL` | Local model for OCR and extraction; it must accept images |
| `OPENROUTER_API_KEY` | Key for both cloud routes |
| `MODEL_CLOUD_ZDR` | OpenRouter model, sent with `provider.zdr` so only zero-retention providers serve it |
| `MODEL_CLOUD` | OpenRouter model for ordinary sources |

Checked here on 2026-10-24 (Apple M2, 16 GB, Ollama 0.40.5): `gemma4:12b-it-qat`,
`qwen3.5:9b` and `qwen3.5:2b` all report vision. On the demo register scan gemma4
read the layout and printed text in 40 to 70 s and misread several handwritten
place and mother names; qwen3.5:9b took 134 s with similar errors; qwen3.5:2b
could not extract (it labelled every cell a name). Local extraction runs at about
5 tokens a second on that laptop, so a register page takes minutes: set
`ROUTE_LOCAL_TIMEOUT=900` there. `google/gemini-3.1-flash-lite` on OpenRouter read
a photo's poster text in 4 s. Both local thinking models are called with
`reasoning_effort: none`.

```dotenv
ROUTE_LOCAL_BASE_URL=http://127.0.0.1:11434/v1
ROUTE_LOCAL_TIMEOUT=900
MODEL_LOCAL=gemma4:12b-it-qat
MODEL_CLOUD=google/gemini-3.1-flash-lite
```

Restart the app and every worker after changing these. On an evidence page with no
claims, Queue processing queues OCR or extraction and indexing, says what it queued
or why nothing can run, and lists each read with its state until the claims arrive.
`uv run python -m unittest tests.test_runs.RunsTest.test_live_openrouter_extract`
(with `OPENROUTER_API_KEY` set) makes one real OpenRouter call and checks that a
located name is written.

## Speech to text

The `transcribe` worker tool runs faster-whisper locally. Fetch the weights once
(about 1.5 GB, the only network use), then run workers as usual:

```sh
uv run stk speech-model   # downloads into SPEECH_MODEL_DIR and prints revision and hash
uv run stk worker         # or: uv run stk worker --tool transcribe
uv run stk enrich --investigation case-name   # queues recordings not yet transcribed
```

| Setting | Meaning |
| --- | --- |
| `SPEECH_MODEL` | faster-whisper model name, default `large-v3-turbo`; empty turns transcription off |
| `SPEECH_DEVICE` | `auto` (CUDA when present, else CPU), `cpu` or `cuda` |
| `SPEECH_COMPUTE_TYPE` | `auto` is int8 on CPU and float16 on CUDA |
| `SPEECH_MODEL_DIR` | Weight cache, default `instance/models/speech`; workers only read it |
| `SPEECH_CHUNK_SECONDS` | Media decoded per run, default 600; a run also stops a minute before its lease ends and continues after its last segment |

Ingest queues one transcription per recording, on the local route, so sensitive
items qualify. Without downloaded weights the run fails with "The speech model ...
is not downloaded" and Retry on the intake page; recordings with no speech seal as
done. Restart running workers after upgrading: a worker keeps the code it started
with. `STK_SPEECH_LIVE=1 uv run python -m unittest tests.test_speech` also runs the
downloaded model on the witness clip and checks word error below 0.35.

## Proactive agents

The intake brief and the linker call `AGENT_MODEL` (default `qwen3.5:9b`) on the
local route, `ROUTE_LOCAL_BASE_URL`, an OpenAI-compatible server. With Ollama:
`ollama pull qwen3.5:9b` and `ROUTE_LOCAL_BASE_URL=http://127.0.0.1:11434/v1` for
the app and the worker. Unset, no agent run is queued and the brief panel names the
missing setting. Each run's model, seconds and tokens show under its brief and in
the run's output; a laptop needs about 35 to 50 s for a brief and about 20 s per
linker candidate.

To run the agents on OpenRouter instead, on a machine that holds only synthetic
data, set `AGENT_ROUTE=cloud-zdr` and `AGENT_MODEL=deepseek/deepseek-v4.1-flash` (compared in agent-eval.md) (with
`OPENROUTER_API_KEY`), then allow that route per agent tool in the policy table,
since agent runs have no document and fall under the sensitive row:

```sql
insert into policy (capability, doc_type, sensitivity, routes, auto_accept)
select t, '*', '*', array['local', 'cloud-zdr'], false
from unnest(array['brief', 'link', 'standing', 'contradictions', 'gaps', 'notice', 'approximate']) t;
```

Without those rows every agent run fails with "route cloud-zdr not allowed".

## Web research and capture

Built and tested against synthetic recorded responses only; it stays off until the
key is set. With no key the Research drawer names the missing settings and sends
nothing. Set the key in the server environment (never in fixtures or git), install
the browser once per machine that runs workers, then restart the app and every
worker:

```bash
uv run playwright install chromium   # capture is off until this exists
```

On Linux add `uv run playwright install-deps chromium` for the system libraries.
Capture is configured when Chromium is installed; the drawer lists `Chromium` as
missing otherwise. The check is cached per process, so restart after installing.

| Setting | Meaning |
| --- | --- |
| `BRAVE_API_KEY` | Brave Search API subscription token; turns on search (web, images, videos) |
| `BRAVE_BASE_URL` | Default `https://api.search.brave.com/res/v1` |
| `RESEARCH_QUERIES_PER_HOUR` | Brave requests per person per hour, default 60 (one query in three kinds is three requests) |
| `RESEARCH_CAPTURES_PER_HOUR` | Captures per person per hour, default 30 |
| `RESEARCH_SEARCH_INTERVAL` | Seconds between Brave requests in one send, default 1.1 (plans are limited per second) |
| `RESEARCH_RESULTS` | Results asked per request, default 20 (Brave caps web at 20, videos at 50) |
| `RESEARCH_MEDIA_PER_PAGE` | Media files taken from one captured page, default 8 |
| `RESEARCH_MEDIA_MAX_BYTES` | Size cap per media file and per yt-dlp download, default 500 MB |
| `RESEARCH_CANDIDATE_SECONDS` | How long results stay pickable in server memory, default 1800 |
| `RESEARCH_YOUTUBE_SUBTITLES` | YouTube results per send whose subtitles are read, default 5 |
| `RESEARCH_YOUTUBE_SUBTITLE_SECONDS` | Time for all those subtitle reads together, default 30; a video still running reads as not checked |
| `RESEARCH_YOUTUBE_SECONDS` | A YouTube capture's download stops after this many seconds, default 900 |

An admin can mark a case sensitive or switch research off for it with
`POST /api/research/case` (`{"item": {"investigation", "sensitive"?, "research"?}}`);
both refuse search and capture for that case, and values from its sources are never
proposed for any other case.

Costs: each Brave request (one query in one kind) counts against the Brave plan.
A capture costs this machine a few seconds of one Chromium process (a fresh browser
per capture, closed afterwards) and nothing else; pages, media and yt-dlp downloads
all come from this server's own address, so a site sees the server, not a service.
The Brave prices and plan limits were not checked in this build; read them off the
Brave Search API pricing page before turning the key on, and set the hourly limits
above to fit the plan. A 429 from Brave is shown to the analyst as a quota refusal,
never retried automatically.

What a capture does: a fresh headless Chromium with no cookies, no service workers,
no saved state, downloads and new windows blocked, and the browser's own media
streaming blocked (media files are fetched afterwards through the pinned client with
the size cap). It waits for the load event, then up to 8 seconds for the network to
settle, and gives up after 90 seconds in all. Every request, including each redirect
hop, is paused and refused if its host is not public; the address Chromium connected
to is checked again afterwards and a private one fails the capture. The visit records
the final URL, the HTTP status, the rendered HTML, a full-page PNG (cut at 16,000 px)
and a JSON network log of the document and image requests; each is hashed and enters
intake as its own acquisition by the analyst. A status of 400 or above, a Cloudflare
bot-check page, a password field or a download fails the capture with a plain
message and Retry; Sealbook never solves a challenge or logs in. The browser sends
its normal headless identity and is not disguised.

YouTube needs no key and no browser: search, subtitles and capture all go through
yt-dlp from this server, with no cookies and no config file. With no Brave key the
drawer offers YouTube alone. Each search kind counts against the same hourly limit;
each send also asks YouTube for the subtitles of up to 5 videos. YouTube answers a
bot check to addresses it distrusts (this development machine got one on every
watch page while search still worked); Sealbook reports it and stops. yt-dlp wants a
JavaScript runtime (deno by default) and the `yt-dlp-ejs` scripts for full format
access on YouTube; neither is installed here, so check a download from the worker
machine before relying on capture. A capture needs `ffmpeg` to merge video and audio.

`yt-dlp` is pinned in `pyproject.toml` (2026.10.21). Video sites change often;
upgrade it with `uv add yt-dlp==<version>` and restart workers when downloads start
failing. The capture run records the version it used.

## Verification

```sh
export OPENROUTER_API_KEY='' ROUTE_LOCAL_BASE_URL='' MODEL_LOCAL='' MODEL_CLOUD='' ROUTE_LOCAL_TIMEOUT=120
uv run python -m unittest discover -s tests
uv run python -m unittest acceptance.test_v0
uv run stk verify --json
uv run stk smoke --json
uv run python -m acceptance.check_workflow
```

The acceptance steps share state and run in the `ORDER` list in `test_v0.py`, not
by number. pytest ignores unittest's `load_tests`, so `acceptance/conftest.py`
applies the same order there.

The unit suite uses disposable PostgreSQL databases when PostgreSQL is configured.
The local role must have `CREATEDB`; fixture cleanup removes only its generated
databases. Setting an empty OpenRouter key disables the external model test. The empty route
settings matter once `.env` configures OCR: the demo seed tests run intake brief and
linker runs, which would otherwise wait on the real local model.
An isolated `STK_TEST_REDIS_URL` enables the real Redis session test.

`stk verify` runs ruff, stk's checks (`python -m stk.checks`) against the app, and migration drift. It does not run the full
unit suite. `stk db check` migrates a disposable database and compares models; it
does not upgrade the working database. The checks start the configured app, so
know its database environment before running it.

`stk smoke` uses a disposable database of the configured dialect. It checks
parameterless pages, browser errors, requests and contrast. Exercise new populated
search/review flows separately; empty-page smoke does not prove them.

The populated workflow command uses a disposable PostgreSQL database, synthetic
sources and local Chromium. It checks colleague assignment/discussion, two logged
image previews, Arabic text jumps, PDF page previews and coverage. The original
case acceptance scenario remains in `acceptance/test_v0.py`.

With a live embedding server, `EMBEDDING_URL=... EMBEDDING_MODEL=... uv run python
acceptance/eval_retrieval.py` scores the synthetic retrieval set. It is not part
of the offline checks.

`uv run python acceptance/eval_search.py` runs the analyst search battery against
the local demo database, read-only; see [search-eval.md](search-eval.md). Let
`stk worker` finish after `stk demo-seed` so both cases are indexed.

In the restricted desktop sandbox, local PostgreSQL sockets and Chromium may need
an approved execution escalation. `UV_CACHE_DIR=/tmp/sealbook-uv-cache` avoids the
restricted default uv cache. This is a local test detail, not a production setting.
