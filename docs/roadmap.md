# Plan

This is the one plan. [Status](status.md) says what exists; this file says what comes
next, in order. Update both in the same change.

## Now: the analyst's method, done first by agents (2026-10-31)

Investigators coming from Bayanat (open source, AGPL-3.0) work by its published
methodology: triage each item by content and reliability, give it an analyst title and a
time-tagged description, tag it with verified (observed) and unverified (alleged) labels,
record people with their roles and events, then group items into incidents with a
possible violation category, and peer review. Sealbook keeps that method and does the
first pass of each step as a cited proposal, so the analyst judges instead of typing.
Bayanat's label, violation, event type and relation lists are reused with credit.

1. **First pass on each item.** Done 2026-10-31, see [status](status.md). After extraction, an agent proposes triage (content:
   incident, post-incident, non-incident and the document kinds; reliability: no, some
   or strong evidence; priority from both, by the method's table), an analyst title next
   to the original, description lines and alleged labels, each pinned to the passage it
   rests on; a transcript passage carries its time. Observed labels wait for frame
   reading (step 6). One signature accepts the pass.
2. **Their vocabulary.** Done 2026-10-31, see [status](status.md). Injured party, alleged perpetrator, witness, appeared and
   participant as roles; possible and claimed violation categories on incidents.
3. **Peer review.** Done 2026-10-31, see [status](status.md). Send an item or incident for review; the reviewer answers no review
   needed or needs review with +label, -label and notes; the owner revisits.
4. **One To do list.** Done 2026-10-31, see [status](status.md). Inbox, Review, Discover and the Desk's questions become one list,
   high priority and biggest decisions first; a link inside a same-event group is
   decided there, not twice.
5. **Upload shows its work.** Done 2026-10-31, see [status](status.md). Per-file progress, then the batch's summary of what was
   found, opening the batch's own decisions.
6. **Case in one place.** Done 2026-10-31, see [status](status.md); the case is one page with a tab per case page and video frames are read, both since the same day. People, places, incidents, evidence and the timeline and map as
   tabs of one case page with an incident list; Agents, Following, Help and Export in the
   sidebar footer. Frame reading for observed labels on photos and video.
7. **Walkthrough video.** Done 2026-10-31 as a 63 s film from real screens (scratch build, not in the repo). One batch (photo, voice note, video, web post) from upload to a
   signed incident, made with HyperFrames once 1 to 6 are in the app.

Mockups: the flow in five steps (add, what it found, one item read for you, decide,
the case) was approved on 2026-10-31.

## Earlier accepted work

The user approved these DigUp-inspired features on 2026-10-23 after the first
connected-workflow build. Keep this table aligned with code and tests.

| Feature | State | Completion evidence |
| --- | --- | --- |
| Source-aware segments and exact jumps | Implemented | Original text ranges and PDF page jumps pass API and populated browser checks; media results retain time locations |
| Arabic source passage search | Implemented | Literal/normalized reasons and original offsets pass regression checks, including full-width identifiers in migrated data |
| Model/preprocessing drift protection | Implemented | Reference changes are refused without rebuild; new configuration generations exclude old vectors |
| Progressive indexing with coverage | Implemented | Bounded continuation jobs, acquisition cursor and visible current-generation coverage; fingerprint decode remains per file |
| Exact identifiers and weaker discovery leads | Implemented | Literal IDs rank first; incidental substrings cannot exhaust the candidate cap; semantic leads are labeled |
| Semantic image/audio retrieval | Implemented, synthetic evaluation done | Gemini Embedding 2 on OpenRouter (zero retention) by default, EmbeddingGemma 2 on a private Ollama server optional, in the single 768-dimensional space; canaries, signed recipe and raw outputs tested; synthetic set recall@5 0.94 |

## Further connected chunks

Incident/event profiles; structured scan navigation; source-backed pre-review
analysis; text repair; relationship export with two-source
closure; investigation access control; encrypted blob storage and complete physical
erasure; durable multi-process notifications and pending review state.

Product acceptance still needs a named first workflow, an Arabic gold set and a
timed comparison using [the synthetic acceptance case](../acceptance/data/README.md).
Local tests do not settle ownership, adoption, model quality or legal readiness.

## Proactive agents (proposed 2026-10-24; intake brief, linker, standing questions and Noticer built)

Today agents are pipelines: extraction proposes claims, discovery proposes findings
from rules and embeddings, speech and thumbnails process media, and outside assistants
connect through MCP with three tools (read, propose statements, propose identity). None
of them notices, plans or suggests on its own. The next layer is agents that work the
case alongside the analyst, each one a signed run that can only propose.

1. **Intake brief** (built, see [status.md](status.md)). After each batch: what arrived, who, where and when, what it links
   to, what contradicts, and the suggested next steps.
2. **Linker** (built). Reads across sources and proposes same-person, same-place, related-incident
   and document relations with the passages that justify each, beyond today's rules.
3. **Standing questions** (built, database and browser checks pending). An analyst asks
   once and receives cited proposals from new evidence and scheduled case checks.
   The Desk supports Check now, Stop and Resume, with past checks retained.
4. **Gap finder** (implemented, database and browser checks pending). Finds silences
   longer than 60 days while held or missing, plus open ends after detention or last
   seen. Suggests existing sources at the resolved place and time, search text for
   human approval, and colleagues from public review and collection attribution.
   Manual checks on People and person pages, and a switch to check again as evidence
   arrives, through the same task queue as standing questions.
5. **Contradiction hunter** (built, manual trigger). Finds claims that disagree,
   explains why, and drafts the question for a colleague. The person page's Sources
   disagree panel checks the selected case, with cited sides and sensitive-source
   masking. Repeat claim sets are suppressed. A switch on the panel checks again as
   evidence arrives, through the same task queue as standing questions.
6. **Noticer** (built, hosted-model check on synthetic data). Compares new evidence with
   everything the person may read (meaning, fingerprints, name variants, other cases,
   shared wording) and proposes cited resemblances; a link is proposed only on a click.
   The Agents page lists every agent working for the person.
7. **Next step suggester.** Orders each analyst's queue by value and suggests flows, such
   as reviewing a register before the testimony that cites it.
8. **Shadow reviewer.** Predicts each human decision without acting and measures agreement
   per claim type; where agreement holds, analysts get batches to sign together.
9. **Research runner.** Runs approved web queries on schedule and proposes captures.
10. **Narrative drafter.** Drafts incident and person summaries in which every sentence cites
   its claims, as a proposal for review.

Guardrails: every output is a proposal in the existing review path, run under the
requesting person's permissions, logged and signed. Autonomy grows by evidence (observe,
shadow, draft, bounded) and never includes accepting semantic or perceptual matches.
Building blocks already exist: sealed runs, the MCP propose tools, findings,
notifications, watches and the research service.

## Gaps to close

Descriptions, titles and labels moved to Now, step 1.

1. **Typed relations with stated certainty.** Family, command, membership and document
   relations between subjects, pinned to evidence, with human certainty kept separate from
   machine scores.

## Next

- A Google Cloud Vision key usable from this machine, to check the vision OCR route live;
  then use its word boxes to draw OCR claims on the scan.
- Video frames chosen at scene changes rather than evenly; description lines in Arabic.
- Case tabs that switch without a page load, with a count on every tab.
