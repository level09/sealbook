# Original design and research

Historical reference copied from Notion on 2026-10-20. This document mixes
requirements, experiments and proposed controls. It is not an implementation or
legal-assurance report. Use [status.md](status.md) for what exists and
[decisions.md](decisions.md) for current engineering boundaries. Historical external
claims below need independent review before reuse in public or legal material.

---

# Sealbook: AI-First Evidence System

Working name. Part A is the build spec for v0. Part B is the full plan for
reference; where they conflict, Part A wins.

**Hard rule: no production or partner data.** Use synthetic or public
data shaped like real Syrian material.

**Positioning (research 2026-10-20).** Courts authenticate digital evidence through people:
the Caesar photos at Koblenz were authenticated by a forensic expert, police analysis and a
witness, not by hashes. No court has yet relied on OpenTimestamps or C2PA. So the product is
not "tamper-proof evidence"; it is the system that makes a custody witness's testimony easy
to give and hard to attack, and makes every AI step explainable on the stand.

## Before the build

1. **Name the first user.** One partner, one analyst, one real workflow this replaces.
   Without it the review-cost experiment cannot run, and that experiment decides adoption.
2. **Ownership.** Settle who owns Sealbook before writing code.
3. **Handwritten OCR gold set first.** Zero-shot VLMs measured ~70% CER on historical
   handwritten Arabic (arxiv 2608.22366); fine-tuned Qwen3-VL-4B reached ~9 to 11% on NAKBA
   data. Handwritten security-branch records are the reason anyone adopts this, so the gold
   set is on the critical path, not a side experiment.

## Usage: what changes for the analyst

The real job is "what do we know about this person". Sealbook is designed around six
workflows:

1. **Person lookup.** Type a name in any spelling. One person view assembled from every
   document: a track of arrest, transfers, sightings and last seen on map and time;
   conflicts as forks, silences as explicit gaps; every line clicks to the highlighted span
   in the source scan; candidate duplicates as proposals with evidence, never auto-merged.
2. **Use-driven review.** Review facts, not bulletins. Queue order is computed from use: a
   statement jumps the queue when it lands on a person's track, in a finding or in a
   package. Never ordered by model confidence. Unreviewed statements stay searchable,
   styled as drafts, so the machine-created backlog stops being a backlog.
3. **Watchlists.** An analyst watches a person. When ingest produces a candidate match
   (name variant plus a corroborating field such as birth date, father's name or detention
   branch), it appears in that analyst's intake feed as a proposal. Links come to the
   person who cares instead of waiting for a lucky search. A watch is a per-user
   subscription; the match is a proposal from a matcher run.
4. **Ingest without forms.** Drop files. Hashing, OCR, transcription and extraction run on
   their own; the analyst's first contact is a review card, not a form.
5. **Answering a request.** "What do we hold on this person" becomes a package where every
   sentence cites spans, with custody string and methodology report, verifiable offline.
   One package serves a family, a partner or a court, with different redactions.
6. **Agents as colleagues.** An analyst asks an agent over MCP to find every detention
   record mentioning a branch in a date range and link the people. It searches under that
   analyst's permissions; its proposals arrive as review cards attributed to the agent on
   behalf of the analyst. Nothing changes until a human accepts.

The proof is a timed comparison: one synthetic missing-person case, "assemble everything we
know plus an exportable answer", done in the team's current tool and in Sealbook. If Sealbook is not several
times faster with better sourcing, the thesis fails.

---

# Part A. Lean core v0 (build spec)

One idea carries the system: **everything is a signed statement, and trust is computed from
origins, never stored as a field.** Five tables, one pure gate function, a hash chain, an MCP
endpoint. Everything else waits until something real needs it.

## Core tables (Postgres only, one process, stk)

```
blob        sha256 PK, size, mime, storage_ref                 -- immutable bytes
acquisition id, blob, origin, collector, at, context            -- where bytes came from; one blob, many origins
principal   id, kind (human|agent), pubkey, on_behalf_of
run         id, principal, tool, model_served, prompt_hash,
            status, available_at                                -- also the job queue
statement   id, subject, prop, commitment, span (blob, range), author,
            run, valid_time, supersedes, based_on, hash, sig   -- INSERT only
payload     commitment, salt, value, value_key, idem_key        -- erasable values
chain       seq, kind, ref, row_hash, prev_hash, hash           -- one global order
```

As built in wave 0 (`stk/ledger/`): one `chain` table links statements, acquisitions and
sealed runs in a single order, instead of `prev_hash` on each table. Value fingerprints
(`value_key`, `idem_key`) live in `payload`, so erasure removes them with the value. Tiers
are a rebuildable `statement_tier` projection written by `gate()`, never a statement field.
The write path is `prepare` (computes the hash: the agent signs it, a human's passkey uses
it as challenge, review cards dry-run through it) then `commit`.

- `statement` is INSERT only: UPDATE and DELETE revoked for the app role. Retraction and
  supersession are new statements.
- Legal erasure: the chain hashes a salted commitment of each value, not the value. Values
  of personal-data props live in a `payload` table keyed by commitment, deletable only by
  the erasure procedure (tombstone statement plus payload delete plus crypto-shredding of
  blob keys). Search indexes and views are projections, rebuilt after erasure. The chain
  still verifies; the data is gone. Never a silent delete.
- Keep MD5 alongside SHA-256 on blobs for the ICC e-Court Digital Signature field.
- Store each value's original Arabic-script form and language next to any normalised value.

## What collapses into statements

- **Proposals and reviews.** An agent or MCP proposal is an unconfirmed statement authored by
  the agent. A review is a human statement about that statement (accept or reject). No
  changeset or review tables. Proposal rules:
  - Proposals carry typed evidence kinds from an enum (e.g. `signature_block`,
    `registry_stamp`), never a confidence number.
  - Idempotent: the key is a hash of (author, subject, prop, canonical value, span), not
    the run, because every MCP call is a new run. A retry returns the existing statement.
  - The write path refuses, with a reason returned to the agent, any value identical to one
    a human rejected for that subject (checked before the idempotent return), and any
    duplicate of another author's pending value. Access records skip these rules and are
    accepted by the gate as records of use.
  - Each proposal records the subject's chain head it was based on. If the subject's
    accepted statements changed since, review shows it as stale, never applies it silently.
  - Review is per item: a batch can be partly accepted. Undo is a retraction statement.
- **Identity.** `same_as` and `not_same` are statements. Persons are a union-find view over
  accepted ones. Names alone never merge. Exact-name twins survive.
- **Access.** Views, searches and exports are statements on the same chain.
- **Packages.** A manifest blob plus one statement. C2PA signing happens here only.
  Derivatives (OCR text, transcripts, translations) record `derived_from` statements; no
  per-derivative manifests in v0. Every package also carries a methodology report generated
  from its runs (tools, versions, models served, prompts, measured error rates, reviewers):
  what Berkeley Protocol paras 25, 143(c) and 197 ask for, and what a defence will attack.
- **Answers and disclosure (v0, manual).** An analyst composes a brief from statements in
  the factual projection; each sentence carries the statement ids it rests on, and a
  sentence with none cannot be saved. A redaction profile per audience (family, partner,
  court) decides which props and spans are masked. Two different humans attest the exact
  package manifest hash before export. AI-generated briefs come later.
- **Jobs.** The `run` table is the queue: workers claim with `SKIP LOCKED`, honour
  `available_at`, survive restarts. No Celery, Redis or DBOS.
- **Entities.** Person, event and movement are SQL views, in two projections. The draft
  projection includes every non-rejected statement, styled by tier; it powers search,
  person tracks, watchlists and review priority. The factual projection includes only
  accepted (and, where policy allows, corroborated) statements; it alone feeds findings and
  packages.

## Trust engine

- `gate(statement, state, policy) -> tier` is a pure function: no I/O, no LLM, no clock.
  Same inputs, same decision, so every decision replays and is unit-testable. Policy is a
  table keyed on capability, document type and sensitivity.
- Tiers:
  - `unconfirmed`: default; visible in the draft projection only, never in a finding or
    package.
  - `corroborated`: enough distinct **origins**, counted from acquisitions, never from
    uploads or submitters. One video arriving through five channels is one origin.
  - `accepted`: human-attested, or the output of a deterministic capability the policy marks
    auto-accept (hashing, C2PA verify result, byte equality), signed by its run.
  - `rejected`.
- A gate relaxes only when a calibration artifact with a measured error rate exists for that
  capability and document type. No invented thresholds. Never use a model's self-reported
  confidence.
- An extracted value without a source span is rejected mechanically.

## Integrity

- The chain covers every row the custody story depends on: statements, acquisitions, and
  each run's provenance (principal, tool, route, model served, prompt hash, inputs, output
  hash) sealed as a manifest when the run completes. Run queue status stays mutable and
  outside the chain. Rows are hash-chained (`prev_hash`). An hourly Merkle root over new hashes is anchored
  twice: OpenTimestamps (trustless) and a qualified RFC 3161 TSA (under eIDAS Art. 41(2)
  only a qualified timestamp carries a legal presumption). Packages carry inclusion proofs
  and verify offline.
- Threat model: the chain alone only catches naive edits. An insider with database and key
  access can rewrite everything after the last anchor. Short anchor intervals bound that
  window; roots are also pushed to at least one partner who keeps copies.
- Humans attest the statement hash with a passkey (the hash is the WebAuthn challenge; store
  the assertion, authenticatorData, clientDataJSON and the public key). This is attribution,
  not an eIDAS advanced or qualified e-signature; describe it that way and get legal review.
  Agents sign with a per-run ed25519 key. Agents never act under a human's identity.
- Tessera and Rekor come later, only when a partner needs public mirroring.
- Open: synced passkeys prove "this account's key", not "this device", which weakens sole
  control. Key loss and rotation need a design.

## AI and agents

- Every AI step is a run you can swap in or out. Extractors get zero tools and return
  schema-constrained JSON statements with spans. Raw output is kept; a re-run is a new run.
  Record the model actually served, not just the one requested.
- **Model routes.** One OpenAI-compatible client; a route is a base URL plus key. Routes:
  `local` (vLLM or Ollama on our hardware), `cloud-zdr` (OpenRouter with ZDR enforced
  account-wide, not per request), `cloud`. The policy table decides which routes a
  capability, document type, sensitivity and partner may use; the model never picks.
  Sensitivity is set on the acquisition at intake (human or deterministic rule) before any
  model sees the bytes, and defaults to sensitive. Partners can be pinned to `local`.
  No fallback between routes: local down means the run fails and waits. Each run records
  route, endpoint, provider served and model served. Verify which OpenRouter response field
  carries provider and model before relying on it. EU-only processing needs OpenRouter
  enterprise. Synthetic data runs on `cloud` so the pipeline is testable before the GPU box.
- Runs emit a typed event stream (`run_id`, `seq`, stage, counts, drop reasons), masked for
  sensitive text. It drives the live UI and doubles as audit. Events also carry
  `causation_id` and `correlation_id`. `human_gesture` is set by the server only after a
  passkey assertion, never from a payload, and never on MCP or agent paths. Unreadable
  input (failed OCR, empty page) is an event, never a silent drop. Progress shows a
  percentage only when a real total exists; otherwise a sequence number and elapsed time.
- MCP server, per-user OAuth, every call under the caller's permissions. Effective
  permissions are the intersection of the user's role and the agent's grant; an agent with
  no grant gets nothing. One scope per tool, not one scope for everything. Tools:
  - `read {target: search|fetch|history|whoami}`, annotated `readOnlyHint` so clients can
    auto-allow it. Reads return neighbour ids (statements, entities, blobs) so an agent
    never dead-ends.
  - `propose_statements`: prop enum, required span, evidence kinds.
  - `propose_identity`: `same_as` or `not_same`, separate schema because the stakes differ.
    Its tool description carries the matching procedure: fail closed, one strong signal or
    two independent ones, names alone never.
  - No accept, delete or outward tool, ever. Write capability never sits with a model that
    reads documents. CI snapshots the `tools/list` payload so tool sprawl fails the build.
- Correction loop: recently accepted statements of the same document type are fed back as
  few-shot examples to the extractor, selected only from evidence the run is authorized to
  read (same partner and access groups as its input). Every input's access and route
  restrictions apply to the run and its output. This steers extraction; it is not
  calibration.
- Search is one Postgres query: pg_trgm, pgvector (halfvec, HNSW) and an Arabic name
  normaliser. Stay on pgvector: permissions and access logging stay in one transaction.
  Revisit only if filtered recall or latency fails at tens of millions of vectors.
- URL sources get a re-check loop: re-fetch, compare hashes, record liveness and diffs as
  statements ("deleted on X", "edited after capture").
- Fail loudly. A missing model, store or key is an error, never a silent no-op or a fake
  fallback.

## UI v0: evidence monitor

Reference: World Monitor (koala73/worldmonitor, AGPL-3.0: borrow patterns, never code). Its
density works because every panel is live and every panel answers the same filter. Applied
to evidence:

- **One shared filter.** Investigation, time window, place, entity. A brush on the timeline,
  a lasso on the map or a click on a person refilters every panel at once.
- **Four panels in v0**, keyboard-switchable, layout saved per investigation:
  1. Intake feed: new acquisitions and run events streaming in (the run event stream).
  2. Map with a time brush: MapLibre native clustering; layers for incidents, facilities,
     movements. Conflicting locations show as forks, gaps as gaps, never smoothed.
  3. Document with its statements and spans beside it.
  4. Review queue as keyboard cards.
- **Clusters show origins, not volume.** Near-duplicates (PDQ) and same-event items group
  into one card whose badge counts distinct origins. One video through five channels reads
  as one origin, visibly. This is the corroboration rule made visual.
- **Coverage, not scores.** No invented indices. Panels may show counts of accepted
  statements per place and time, and where evidence is missing. Gaps are first-class: "no
  evidence for this detainee between March and June" is a finding.
- **Tier is visual encoding.** Unconfirmed is hatched, corroborated outlined, accepted solid,
  rejected hidden by default. AI drafts can never look like accepted facts.
- **Every number clicks through** to its statements, then to bytes, run and signer (two-click
  rule).
- **Sensitive text masked by default.** Revealing it is an access statement on the chain.
- **Review cards show the ledger's own diff**, computed by dry-running the proposal through
  the real write path, styled as a draft. Never the agent's raw JSON. Accept stays disabled
  until invariants pass: span exists in the source bytes, date inside the document's range,
  subject not stale.
- **Actor on every row.** Source is one of human, extractor run, external agent (on behalf
  of whom), import. Agent-authored values carry a distinct style that survives every panel.

Mechanics (no build step, no new dependencies):

- The shared filter is a JSON AST in the URL, validated, compiled to SQL in one place. The
  UI shows when it differs from the saved view.
- Live panels: one SSE stream per tab. The tab registers its active filter; the server
  matches change events (Postgres LISTEN/NOTIFY) against that filter and the user's
  permissions and pushes only matching rows. Rows that stop matching leave the panel.
  Missed events replay on reconnect from the event `seq`.
- Dense lists use a fixed pool of DOM rows rebound on scroll, not a virtualization library.
- Commands live in a plain JS registry (scope, hotkey, availability); hotkeys resolve
  through a focus stack; the side panel is a page stack.
- **Cited brief panel** (after v0): an AI summary of the current filter where every
  sentence cites spans; an uncited sentence is dropped, not shown.

## Colour roles (implemented 2026-10-24)

Paper and ink stay the ground; colour names a role, never decorates. Tokens live in
`stk/static/css/shell.css` (inks mirrored for Vuetify colour props in
`stk/static/js/config.js`). Each role has an ink for text and glyphs (AA 4.5:1 on panel,
page and its own wash in both themes), a `-wash` for chip grounds and a `-line` for
edges and marks (3:1 on panel). Picked in OKLCH, stored as hex for MapLibre and canvas.
Primary actions stay ink. Colour is never the only cue: each role rides on the texture,
icon or word in the last column, and deuteranopia and protanopia simulations still read
through those.

Six hues only (2026-10-24): agent, accepted, conflict (and due), negative (and overdue),
sensitive and new. Below about 6 OKLab dE two roles read as one at chip size; the earlier
14 had pairs at 0.7 to 3 under colour-blind simulation (person, corroborated and story;
source and incident). So entity kinds are ink-2 and keep their icon, corroborated is the
accepted hue in outline texture, and overdue is the negative hue with its alert icon.
Agent violet and new blue still sit close under deuteranopia; the robot icon and the
dot separate them.

Ground (2026-10-24), tuned for long sessions: light is warm paper (bg #ecebe6, panel
#fbfaf7, ink #1f2124, 15.5:1) instead of pure white; dark is lifted off near-black (bg
#151619, panel #1c1d21, ink #d9dad6, 12:1) to cut halation. monitor.css, app.css and
config.js mirror these values.

| Role | Meaning | Light | Dark | Where | Non-colour pair |
|---|---|---|---|---|---|
| `--agent` | AI proposals, agent runs, findings, leads | #5a46c8 | #ab9ef7 | actor text, finding chips, strength bands, unconfirmed agent marks, a ring on confirmed agent marks | robot icon, "agent" or run name |
| `--accepted` | a person accepted it | #0d6a58 | #7bd1ba | tier fills on chips, swatches, bboxes, map and timeline points | solid texture, the word accepted |
| `--corroborated` | independent origins agree | = accepted | = accepted | tier outlines | outline texture |
| unconfirmed | not yet decided | ink-2 / ink-3 | ink-2 / ink-3 | hatched marks in the author's hue | hatch, dashed edge |
| `--conflict` | places or values disagree | #915c08 | #f6b669 | timeline forks, map fork lines, fork notes | fork icon, "conflict" |
| `--negative` | rejected, erased, failed | #a04034 | #ea998b | rejected strike, failed chips and checks | line-through, cross, "failed" |
| `--sensitive` | sensitive source | #964267 | #e9a3bd | sensitive chips, thumbnail hatch and lock | eye-off or lock icon, hatch, "sensitive" |
| `--due` | needs your action, has a date | = conflict | = conflict | Needs you chips, assignee due | clock icon, "due" or "asks you" |
| `--overdue` | past its due date | = negative | = negative | Needs you chips, Sources workload and assignee | alert icon, "overdue" |
| `--new` | unread or new since last look | #0d6abb | #77b5fb | bell badge and unread icons, new chips | dot icon, "new" |
| `--person` | person | ink-2 | ink-2 | search glyphs and group dots | user icon |
| `--place` | place | ink-2 | ink-2 | search glyphs and group dots | pin icon |
| `--source` | source file | ink-2 | ink-2 | thumbnail type icons, search glyphs | file-type icon |
| `--incident` | incident | ink-2 | ink-2 | Stories incident bands, search glyphs | band label, alert icon |
| `--story` | story | ink-2 | ink-2 | reserved for story links | story icon |

Washes and lines per role are in shell.css. Utilities: a role class (`.is-person`,
`.is-place`, `.is-source`, `.is-incident`, `.is-story`, `.is-finding`, `.tier-accepted`,
`.tier-corroborated`, `.tier-unconfirmed`, `.tier-rejected`, `.state-conflict`,
`.state-negative`, `.state-sensitive`, `.state-due`, `.state-overdue`, `.state-new`)
sets `--r`; `.r-ink`, `.r-chip` and `.r-dot` draw it. Rows get no role edge: the
inset left edge already marks the selected row. Vuetify props take the
role names (`color="overdue"`); `success`, `warning` and `error` alias accepted,
conflict and negative.

Rule for tier against author: before a decision a mark shows its author (violet agent,
ink person) on a hatch; once confirmed it shows its tier hue, and an agent-authored
value keeps a violet ring or ground, so AI work stays visible and an AI draft never
looks like an accepted fact.

Actor avatars (ActorChip): six low-chroma pairs clear of the status hues and of agent
violet, picked by a stable hash of the user. Agents use the violet pair with a robot,
imports the neutral pair with an import icon. All pairs are 7:1 or better.

| Token (bg, `-ink`) | Light bg / ink | Dark bg / ink |
|---|---|---|
| `--av-1` cyan | #c0e6eb / #024c52 | #133f44 / #bae8ed |
| `--av-2` blue | #c7e2f6 / #0d4768 | #1f3b4e / #c2e3fb |
| `--av-3` mauve | #ebd6ee / #56335b | #442f47 / #eed4f1 |
| `--av-4` stone | #e7dcd2 / #513e2f | #41352d / #e9dbd0 |
| `--av-5` slate | #d7dfe8 / #364452 | #313941 / #d5dfea |
| `--av-6` sand | #e2ded0 / #494229 | #3c382a / #e3decd |
| `--av-agent` | #e6e2f8 / #4a37b0 | #2d2850 / #c9c0fb |
| `--av-import` | #e9e9e6 / #44464a | #2a2c31 / #c8cace |

## Cut from v0

Tessera, Rekor, DBOS, per-derivative C2PA, Splink, dockview, Sigma.js, Perspective, deck.gl,
cosmos.gl. Each returns when something real needs it.

## v0 acceptance

The demo is one synthetic missing-person case: a handwritten detention register page, a
printed transfer order, a witness audio clip, a C2PA photo and a URL, shaped like real
Syrian material. The six usage workflows run against it end to end. Tests:

0. The timed comparison from the Usage section is run and recorded.
1. Ingest a PDF scan, a C2PA photo and a URL: blobs, acquisitions, hashes, C2PA verify
   result.
2. An extraction run produces statements with spans; a value without a span is rejected.
3. A gated statement appears in the review queue; a human accepts it with a passkey.
4. An external agent connects over MCP, searches, proposes; the proposal shows attributed to
   the agent on behalf of its user.
5. Export a package and verify it offline with `c2patool` plus the inclusion proof, with no
   database access.
6. Edit one statement row directly in Postgres; verification catches it.
7. Rewrite anchored history as an insider with DB and key access; verification against the
   OpenTimestamps, TSA and partner-held roots catches it. Rows after the last anchor show as
   provisional, and a package cannot be finalized until its rows are anchored.
8. Every statement in the UI traces to its bytes, run and signer in at most two clicks.
9. The exported package includes a methodology report generated from its runs.
10. With sensitivity set, a sensitive document cannot reach a `cloud` route, and a stopped
    local model fails the run instead of rerouting.
11. A time brush on the timeline refilters map, feed, document list and review queue.
12. An agent retries the same proposal: one statement exists. It re-proposes a value a human
    rejected: refused with a reason. A subject changes during review: the card shows stale.
13. An MCP session never sees or does more than the human behind it.
14. A name searched in three spellings opens one person view with track, forks and gaps.
15. A new document matching a watched person appears in the watcher's feed as a proposal.
16. A statement placed on a person's track moves ahead of untouched ones in the queue.
17. Editing an acquisition's origin or a completed run's model served is caught by
    verification.
18. After erasing a person's data, the chain still verifies and no search, view or payload
    returns their values.
19. A brief sentence without statement ids cannot be saved; export needs two distinct
    attestations over the manifest hash.

## Borrowed from voidaccess

KatrielMoses/voidaccess (MIT, v2.1.0; one contributor, created 2026-04, a dark-web OSINT
pipeline with no custody model: patterns only, never a dependency): origin-vs-contributor corroboration,
quarantine-by-default tiers, pure deterministic gate, calibration-required matching, typed
run event stream, source re-check loop. Not borrowed: its silent no-op fallbacks and SHA-256
fallback embedder.

## Borrowed from agent-first apps (code read 2026-10-20)

Patterns only; none of these is a dependency.

- **Twenty** (twentyhq/twenty): approval with snapshot conflict check, role intersection for
  agents, actor composite on rows, MCP tool annotations, filter-matched live SSE, filter AST
  in the URL, command registry and focus stack, fixed row-pool virtualization.
- **Relaticle**: `whoami`, read tools that return neighbour ids, idempotent proposals,
  per-item batch review.
- **Comp AI CRM**: evidence kinds instead of scores, refusals with reasons at the single
  write path, identity-matching procedure as fail-closed guidance.
- **Nomi**: review preview by dry-running the real kernel, proposal staleness against a
  base revision, event envelope with causation ids, server-only human gesture, honest
  progress, MCP payload snapshot in CI.
- **keni**: accepted values as few-shot steering, deterministic invariants that block Save.

Not borrowed: auto-applied agent facts and numeric evidence weights (Comp AI); direct CRUD
and delete over MCP, one scope for all tools (Relaticle, keni); approval left to the agent's
judgment, timeline merge keys that fold history, last-write-wins drafts, runtime custom
objects (Twenty); "full auto" mode, provider fallback chains, model-to-model certification
(Nomi); proposal expiry (evidence proposals go stale, never silently die).

## 1. The idea

Build an **evidence ledger**, not a records database. Every byte, every fact and every
decision, by a human or an agent, is a signed statement in an append-only log that anyone can
verify. Persons, documents, events and incidents are **views** computed from those
statements. The audit trail is not a log kept beside the data; it is the data. AI is native
everywhere. Humans step in where a decision is sensitive or consequential, such as
handwritten OCR, identity merges, contradictions, legal classification and disclosure.

## 2. Principles

- **Capture without forms.** Ingest stores bytes, context and hashes. Structure comes later,
  from agents and analysts.
- **Claims, not records.** Conflicting sources coexist as separate statements. Nothing is
  overwritten. Current state is a projection.
- **AI native, human gated.** Every AI step runs by default. A review gate opens according to
  sensitivity, document type and measured accuracy, never according to the model's
  self-reported confidence.
- **Every action is signed.** Humans sign with passkeys, agents with run-scoped keys. Viewing
  and searching are recorded too.
- **C2PA end to end.** Verified at ingest, signed on every export (per-derivative manifests
  deferred in v0).
- **Dense workspace.** One screen to find, read, compare and decide, with time and space as
  first-class axes.
- **Agent-operable.** Any agent can operate the system through the same permissioned tools as
  the UI, and can only propose writes.

## 3. Core model (full)

| Object | What it is | Rule |
|---|---|---|
| Blob | Immutable bytes, keyed by SHA-256; MD5 kept for ICC e-Court | Never modified. Derivatives are new blobs |
| Acquisition | Who collected which blob, when, where, how, under which restrictions | Created at capture, incl. verified C2PA, ProofMode or WACZ context |
| Run | Tool, version, model served, prompt text and hash, inputs, outputs, timings, cost | Raw output stored. A new run supersedes |
| Statement | Subject, property, value, original Arabic value, language, source span, author, valid time | INSERT only |
| Judgement | Same, not same, unsure, with evidence and author | Reversible (v0: statements) |
| Principal | Human (passkey) or agent (run key), with on-behalf-of | Agents never act under a human's identity |
| Package | Frozen disclosure set: manifest, Doc IDs, redaction suffixes, custody string, inclusion proofs | Signed with C2PA, verifiable offline |

Shape borrowed from OpenSanctions FollowTheMoney `Statement` and nomenklatura's
merge-judgement graph. Own schema for violations, detention, witnesses and movements. W3C
PROV-O vocabulary for runs, agents and generation.

## 4. Flow

Capture (file, URL, field app) -> Blob + Acquisition (SHA-256, C2PA verify) -> Agent runs
(OCR, ASR, translate, entities, PDQ, embeddings) -> Statements with spans (draft projection,
searchable) -> Gate (used in a track, finding or package, sensitive, or below proven
precision?) -> review card attested by a human ->
Views (persons, events, movements) -> Workspace and Packages (C2PA + proofs + e-Court load
file).

## 5. AI defaults and review gates

| Capability | Default | Human gate when |
|---|---|---|
| Hashing, C2PA verify, PDQ/vPDQ, embeddings | Automatic | Never (deterministic) |
| Printed Arabic and English OCR | Automatic, searchable immediately | A field is used in a finding or package |
| Handwritten OCR | Draft, unverified | Always, until a fine-tuned model passes the gold set for that type |
| Speech to text, Syrian dialect | Draft, unverified | Quotes used in findings |
| Translation | Automatic draft | Legal or disclosure use |
| Entity and event extraction | Statements with spans | No span: rejected. People, violations, dates gated until precision proven |
| Dedup: same bytes, near-duplicate media | Automatic clustering | Never for byte equality; review near-duplicates used as evidence |
| Identity resolution | Proposals only | Always. Names alone never merge |
| Movement and timeline | View over accepted statements | Conflicts and gaps flagged, never smoothed |
| Labels, legal classification | Suggestions | Always |
| Redaction, disclosure | Suggestions | Always, two people for disclosure |

Gates are policy data, not code. Each human verdict is a label that feeds calibration.

## 6. Audit trail

- Human signatures: statement hash as WebAuthn challenge, assertion stored with the
  statement.
- Agent signatures: short-lived run-scoped keys.
- Merkle log and witnessing: v0 is a hash chain plus an hourly root anchored with
  OpenTimestamps and a qualified RFC 3161 TSA (Part A). Later: Trillian Tessera (posix
  driver), Sigstore Rekor v2.
- Access is evidence: views, searches, exports are statements (Berkeley paras 127 to 131).
- Postgres enforcement: INSERT only for the app role.

## 7. C2PA

- Ingest: verify existing manifests (camera, ProofMode, Tella, C2PA phones), record the result
  as statements.
- Export: package manifest signed, carries inclusion proofs. c2pa-rs via c2pa-python.
- Later: every derivative as a new blob with its own manifest listing the source as an
  ingredient.
- Caveats: public verifiers show "trusted" only with a recognised CA certificate. The Interim
  Trust List froze 2026-01-01; budget a certificate from a C2PA Trust List CA (DigiCert,
  SSL.com) or ship our own trust anchor with packages. C2PA proves who handled which bytes,
  never that content is true, and its security is contested (IACR eprint 2026/804), so no
  claim rests on C2PA alone.

## 8. Workspace (after v0)

Dense, investigation-oriented, panels saved per user and investigation: hybrid search with
facets; brushable time series that filters every panel; a person's movement track on
MapLibre with a time scrubber (conflicts as forks, gaps explicit); space-time and
co-occurrence clusters as proposals; ego relation graph; provenance badges on every item;
cited answers where every sentence cites spans; keyboard review cards.

## 9. Agent-operable

- MCP server (2026-07-28 spec, Python SDK 2.3), per-user OAuth, `read`,
  `propose_statements` and `propose_identity` tools (see Part A).
  The spec is stateless (no session id). Long jobs via the `io.modelcontextprotocol/tasks`
  extension (no longer core); approvals via `resultType: "input_required"` round trips.
  OAuth must validate the RFC 9207 `iss`; use Client ID Metadata Documents, not Dynamic
  Client Registration (deprecated).
- stk conventions: `inspect --json`, `verify` and `smoke` gate changes. Agents extend code
  only in isolated dev environments.
- Prompt-injection containment: extraction model has zero tools and returns
  schema-constrained JSON; write capability sits with a separate planner scoped per run; no
  egress tools.

## 10. Stack (checked 2026-10-19)

| Layer | Choice |
|---|---|
| App | stk: Quart, async SQLAlchemy, Vue 3 without a build step, passkeys |
| Data | Postgres 18, pgvector 0.8.7, PostGIS; content-addressed blobs on S3 or posix |
| Provenance | c2pa-rs and c2pa-python |
| Capture | Bellingcat auto-archiver v1.2.9; ProofMode and Tella bundles verified on ingest |
| Dedup | ThreatExchange PDQ and vPDQ; later Splink v5 for person linkage |
| Models | vLLM on one GPU box. OCR: dots.ocr, Qwen3-VL or Gemma 4, LoRA on a Syrian gold set. ASR: Whisper large-v3-turbo fine-tuned. OpenRouter (ZDR) as the cloud route, only where policy allows |
| Timestamps | OpenTimestamps plus a qualified RFC 3161 TSA |
| Embeddings | bge-m3 or Qwen3-Embedding-0.6B |
| Evals | inspect_ai with span-grounded field scoring and hallucinated-entity rate |
| UI | MapLibre, fixed row pool (no virtualization library), SSE; later dockview, Sigma.js, Perspective, deck.gl |

Licence watch: pg_search, Browsertrix, replaywebpage and World Monitor are AGPL. Jina v4/v5 and
EmbeddingGemma have restrictive licences; skip them.

## 11. Experiments that could change the design

- **Syrian OCR gold set.** ~200 pages: printed forms, handwritten security-branch records,
  civil registry extracts, name lists. CER, field F1, hallucinated-entity rate per type.
  Feeds gate calibration. On the critical path (see Before the build).
- **Arabic name resolution.** Splink vs bge-m3 vs Qwen3-Embedding-0.6B on known duplicates.
  If embeddings lose, extend the rule-based Arabic normaliser.
- **Passkey signing.** Prove assertions over statement hashes verify years later; design key
  loss and rotation.
- **Review cost.** Time per accepted fact vs typing it into a form, with a real analyst.

## 12. Requirements sources and landscape

- Berkeley Protocol (OHCHR 2022): para 25 (documenting automated tools), 143(c) (tool
  versions), 155(h) (hashing at collection), 167 to 172 (custody, working copies, fixity),
  197 (documenting processing).
- ICC e-Court Protocol v2.0 (ICC-01/21-01/25-511-Anx, Oct 2026): Table 1 fields, MD5 Digital
  Signature, Chain of Custody string, 2026 URL field.
- Embeddings never establish identity; hashes never establish truth.
- Landscape: Uwazi (ML suggestions, no custody), OpenAleph (MIT fork of Aleph), Datashare
  (search only), Hunchly (capture integrity, no analysis), Relativity aiR (AI audit trail,
  closed, eDiscovery; the ICC OTP reportedly runs RelativityOne), OCCRP Aleph Pro (hosted,
  closed since Dec 2025), Starling Lab (capture prototypes, no case model), Benetech
  JusticeAI (self-hostable). No open tool found that combines a structured case model,
  capture-grade integrity and a court-producible trail of AI processing (one search pass,
  not exhaustive).
- Buyers are cloud-wary: IIIM names OCR, MT and transcription as needs but says "no
  automation replaces human efforts"; ICIJ translates locally on purpose.
