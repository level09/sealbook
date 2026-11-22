# One man, four names

A fictional case study. A freight company at the invented Port Ondrel buys from a
marine supplier, pays a logistics consultant, holds a personal guarantee for a
credit line and works through a licensed ship agent. Each desk checked its own file
and found nothing wrong. The supplier Adrian Kassar, the consultant A. Qassar, the
guarantor Adrian Khassar and the ship agent أدريان قصار are one man, born on
2 March 1974 in Velmora, son of Elias. A marine surveyor named Adrian Kasar, born
the same year in Torvane, is someone else, and the rules cannot tell them apart.

Every person, company, place and coordinate is invented. The two scans with a face
use one generated portrait; see `files/ATTRIBUTION.md`.

```sh
uv run python acceptance/cases/four-names/make.py            # rewrite case.json
uv run python acceptance/cases/four-names/make.py --scans    # render the five scans
uv run python acceptance/cases/four-names/make.py --record   # read the scans live
uv run python -m unittest tests.test_case_four_names
uv run stk demo-cert                                          # once, for the last act
uv run stk demo-seed --case four-names [--until N]           # into the app, act by act
```

The planted truth (422 claims, the linker's fixed-mode recall 1.0 since
2026-11-22, the surveyor offered by the rules) is scored by `acceptance/eval_agents.py`
(`FourNamesScoreTest` in `tests/test_case_studies.py`).

## The acts

Nine acts, from mess to a sealed package. Act 6 stops with the linker's proposals
open, so a person or the film can decide them; act 7 is that decision.

| Act | Day | Shows | Sources | Steps | See |
| --- | --- | --- | --- | --- | --- |
| 1 Procurement's files | 3 Jun 2024 | sheet matching, reading | vendor master, purchase ledger, payments, vendor form (scan) | `sheets`, `read`, `places`, `join` | the vendor master, the vendor form |
| 2 Finance's files | 5 Jun | sheet matching, reading | consultants register, invoices, agreement (scan) | the same | the register, the agreement |
| 3 The credit desk's files | 7 Jun | sheet matching, reading | guarantees register, guarantee deed and passport (scans) | the same, `recognise` the passport holder, `brief` | the register, the deed |
| 4 The port's lists | 10 Jun | sheet matching, reading | ship agents, licence (scan, Arabic), crew list, surveyors register | the same | the agents list, the licence |
| 5 One name in two alphabets | 11 Jun | search | none | none | `/search?q=Kassar`, `/search?q=قصار` |
| 6 The linker proposes | 12 Jun | same person | none | `agent` linker | `/review` |
| 7 A person decides | 13 Jun | same person | none | `links`, `decline` with the reason | the man's page, the surveyor's page |
| 8 The conclusion | 14 Jun | conclusion, peer review | none | `conclusion` | `/cases/overview` |
| 9 The sealed package | 17 Jun | package | none | `accept` the cited claims, `package` | `/packages` |

### How the files are read

- **Sheets** (`sheets`, `sealbook/monitor/study/sheets.py`). Each of the nine sheets
  goes through `sheets.service.confirm` with the mapping a person would choose
  (`sources.<key>.sheet`: dataset name, key column, a field per column), which
  writes a dataset version, and then `extract_sheet` itself reads the released
  rows: it pins each quote to its cell and writes one person per row. The model's
  answer per row is fixed, copied from the case file's cell values, and the run
  says "no model ran" in its provenance. The six registers map their name, father,
  birth and place of birth columns; every other column is Do not read. The three
  ledgers (1,220 rows of purchases, payments and invoices) name companies and
  payments, so all their columns but the key are Do not read: they are dataset
  versions whose rows are released with no cell to read, and the answer per row
  is empty by construction. No sheet row calls a model.
- **Scans** (`read`). The five scans are read by the app (OCR, extraction, the first
  pass), replaying `recordings.json`: 15 chat calls on cloud-zdr recorded
  2026-11-21 (5 OCR with `google/gemini-4-argon`, 5 extractions with
  `anthropic/claude-haiku-5.5`, 5 first passes with `google/gemini-3.8-flash`).
  Replay of the whole case takes about 15 seconds and calls no model.
- **The passport** (`recognise`). Extraction wrote the holder as the page prints him,
  "KASSAR / ADRIAN", and reading matches a record by the exact name, so the case
  names that written form. The step writes nothing to the ledger; it tells the
  seeder which written person is the passport holder.

### What was measured, 2026-11-21

- Sheets: 9 datasets, 1,445 rows mapped and released, 225 rows naming someone,
  400 of 400 planted sheet claims written in their cells.
- Scans: 16 of the 22 planted claims recovered from what the app read (18 written
  about people). Every name, father, birth date and place of birth the links rest
  on was read. Missed: the five "signed in person at" places (extraction wrote the
  signing date as the document's date, not where the man was), and the passport's
  sex, written as "M" without the male or female the property needs. So the man's
  trail across the three sites is not on the map in this telling; nothing is
  planted in its place, since the conclusion does not rest on it.
- Linker, on what the app wrote, fixed mode: 7 proposals, the 6 true links and the
  surveyor through the consultant's year-only birth date, as in the planted score.
  Re-measured 2026-11-22: before then the licence's pairs with the supplier and the
  passport were missed (إلياس keyed as the article plus a name).
- Decisions: the desks' 5 joins and the 6 true links accepted, the surveyor's link
  declined with the reason "Not the same man. The surveyors register gives Adrian
  Kasar 19 August 1974 and Torvane; the consultant's papers give only the year
  1974, and the other desks give 2 March 1974 and Velmora. A shared name and birth
  year are not enough." The conclusion rests on the 11 accepted links.
- Package: a court brief of 5 sentences citing 15 accepted claims, opening with the
  conclusion; 50 statements disclosed, 12 source files shipped. Anchored by the
  real `stk anchor` (a.pool and b.pool.opentimestamps.org, freetsa.org), signed
  with the `stk demo-cert` certificate, attested by Ines Halvorsen and Tomas
  Reyes, finalized. The offline verifier passes with no problem (7 OK lines) and
  c2patool reports the cover `Valid` with the signer untrusted, as a demo
  certificate should be.

## Steps this case adds

| Step | Module | Arguments | What it does |
| --- | --- | --- | --- |
| `sheets` | `sheets.py` | none; each source's `sheet` | Confirms the column mapping, then `extract_sheet` on the released rows with fixed per-row answers from the case file; matches each planted sheet claim to its cell |
| `recognise` | `decide.py` | `record`, `as` | Names the written person who stands for a record whose name extraction wrote in another form |
| `decline` | `decide.py` | `why` | The analyst declines, signed with `why`, each open same-person proposal that joins records the truth plants as different people (`twins`) |
| `package` | `package.py` | `title`, `audience`, `sentences: [{text, cite}]`, `save` | Writes the brief, runs `stk anchor` once, builds and signs the package, both analysts attest, finalizes, saves the export and checks it offline. Without a certificate or receipts it stops and says what the build waits for |
