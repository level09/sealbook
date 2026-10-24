# Architecture

Sealbook requires PostgreSQL. Quart serves async routes, SQLAlchemy manages
request-scoped sessions, Alembic owns schema changes, and Vue/Vuetify runs without
a frontend build. The supported evidence deployment is one process.

| Module | Responsibility |
| --- | --- |
| `stk/ledger` | Typed statements, salted payload commitments, signatures, chain, trust gate and source-origin groups |
| `stk/ingest` | Content-addressed bytes, acquisitions, hashes, C2PA and URL capture |
| `stk/runs` | Durable PostgreSQL run queue, route policy, worker, raw outputs and typed events |
| `stk/discovery` | Located text/PDF/media segments, separate text and media vectors, coverage, fingerprints and search |
| `stk/sheets` | CSV and XLSX intake: parsing into cells, datasets with remembered column mappings, version diffs and the `extract_sheet` run that proposes claims pinned to cells |
| `stk/thumbs` | Derived pictures per source (thumbnails, video strip, PDF pages, audio peaks) made by a sealed `thumbnail` run and served under the source's own access rules |
| `stk/collaboration` | Participant-only requests/discussion, stored notifications (titles and links only, then pushed live), work assignment with an append-only event log |
| `stk/review` | Passkey options and commit, assignment checks, acceptance/rejection/undo |
| `stk/people`, `stk/watch` | Person projections, identity candidates, erasure and watch subscriptions |
| `stk/monitor` | Shared filters, masked evidence display, review cards and SSE feed |
| `stk/packages`, `stk/anchor` | Cited disclosure, attestations, receipts and offline verification |
| `stk/places` | PostGIS gazetteer: geometry with precision and provenance, kind ladder, dated names, COD-AB boundary sets, point-in-polygon governorate and district, claim-to-place resolution |
| `stk/entities` | Place and incident pages on one layout: story, relations, paged evidence, threads and signed history, read through the story view's rules; the places index and unresolved names |
| `stk/maps` | Claim vector tiles, per-reader map models, offline basemap serving and `stk basemap`, the `geotag` intake tool for embedded photo and video locations |
| `stk/research` | Web research: query proposals from shareable values, Brave Search client, Cloudflare Browser Rendering client, yt-dlp, per-user result store, per-case switches and the `capture` run tool that feeds normal intake |
| `stk/mcp` | Per-user delegated agent reads and proposals; no acceptance tool |
| `stk/user`, `stk/websocket.py` | Session/MFA security and bounded recipient-specific connections |

## Evidence and projections

Immutable bytes and signed chain entries are the custody record. Payload values
are erasable; a salted commitment remains. Tiers, person views, search chunks,
vectors, picture samples and sound landmarks are projections. A projection cannot
create a factual claim or weaken the human review gate.

Worker runs keep actual model/provider/endpoint and output references. Claims need
real source spans. Current factual queries exclude accepted predecessors. Only a
human-confirmed same-recording relationship changes whole-source origin groups.
Embedding runs also seal their preprocessing recipe and reference-vector hash.
Active configuration/reference generations select usable vectors; old vectors
cannot silently join a changed space. Coverage counts only current generations.

Requests and comments use ledger statements, but their payloads belong only to
participants and administrators. General monitor reads, reveal APIs and MCP omit
them. New read surfaces must enforce this boundary too.

The monitor uses SSE. Colleague notices use WebSockets. They serve different
purposes and both rely on durable database state after reconnect. WebSocket
delivery is process-local and never carries sensitive evidence text.

## Places and maps

A place's shape is `place.geom` in PostGIS; `centroid`, `lon` and `lat` are
generated from it, so readers of the old numeric columns keep working and nothing
drifts. Every geometry carries a precision and a source; boundary polygons live in
versioned `boundary_set` and `admin_area` rows with their licence, and only the
newest set assigns governorate and district.

Which place a claim is at is a projection: accepted `located_at` links, then a
confirmed name variant in the same investigation and governorate. Python
(`resolve.resolved`) and SQL (`maps.tiles.claims`) both compute it; the SQL twin
exists for set-based tiles at scale and the resolution tests run against both. Change
the two together.

Tiles are built per reader from visible claims only and never shared between
users. A reader's model is reused while the ledger has not moved, or while nothing
map-relevant arrived (an access log moves the head, not the map), for at most 30
seconds. The basemap is static OpenStreetMap data the app serves itself, so a map
view never asks a third party for tiles. Embedded photo and video locations enter
as unconfirmed agent proposals and follow the normal review gate.

## Source map

One root blob may have many acquisitions. OCR text, PDF text and prepared media are
derived blobs linked directly to that root through signed `derived_from` records.
Located segments store the explicit root, page/time location and text offsets.
A spreadsheet stays one root blob: a claim read from it carries a cell span
`{"sheet", "row", "column"}` (numbered from 1 as the file shows them, optionally with
`start`/`end` characters inside the cell), validated against the same parse of the
bytes. Datasets and versions store headers, mappings and diff counts, never cell values.
Shared derived bytes can have several parents; availability checks all direct
parents to prevent an erased source from reappearing through another parent.
The older general filter resolves one derivation hop. Do not add deeper chains
without extending filters, erasure, package closure and tests together.
