# Accepted next work

The user approved these DigUp-inspired features on 2026-10-23 after the first
connected-workflow build. Keep this table aligned with code and tests.

| Feature | State | Completion evidence |
| --- | --- | --- |
| Source-aware segments and exact jumps | Implemented | Original text ranges and PDF page jumps pass API and populated browser checks; media results retain time locations |
| Arabic source passage search | Implemented | Literal/normalized reasons and original offsets pass regression checks, including full-width identifiers in migrated data |
| Model/preprocessing drift protection | Implemented | Reference changes are refused without rebuild; new configuration generations exclude old vectors |
| Progressive indexing with coverage | Implemented | Bounded continuation jobs, acquisition cursor and visible current-generation coverage; fingerprint decode remains per file |
| Exact identifiers and weaker discovery leads | Implemented | Literal IDs rank first; incidental substrings cannot exhaust the candidate cap; semantic leads are labeled |
| Semantic image/audio retrieval | Implemented, synthetic evaluation done | EmbeddingGemma 2 on local Ollama in the single 768-dimensional space; canaries, signed recipe and raw outputs tested; synthetic set recall@5 0.94 |

## Further connected chunks

Incident/event profiles; structured scan navigation; source-backed pre-review
analysis; text repair; relationship export with two-source
closure; investigation access control; encrypted blob storage and complete physical
erasure; durable multi-process notifications and pending review state.

Product acceptance still needs a named first workflow, an Arabic gold set and a
timed comparison using [the synthetic acceptance case](../acceptance/data/README.md).
Local tests do not settle ownership, adoption, model quality or legal readiness.

## Proactive agents (proposed 2026-10-24; intake brief, linker and standing questions built)

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
4. **Gap finder.** Turns "no evidence for 60 days" into where to look: which sources, which
   web queries, which colleague has related work.
5. **Contradiction hunter** (built, manual trigger). Finds claims that disagree,
   explains why, and drafts the question for a colleague. The person page's Sources
   disagree panel checks the selected case, with cited sides and sensitive-source
   masking. Repeat claim sets are suppressed. Scheduled runs remain pending.
6. **Next step suggester.** Orders each analyst's queue by value and suggests flows, such
   as reviewing a register before the testimony that cites it.
7. **Shadow reviewer.** Predicts each human decision without acting and measures agreement
   per claim type; where agreement holds, analysts get batches to sign together.
8. **Research runner.** Runs approved web queries on schedule and proposes captures.
9. **Narrative drafter.** Drafts incident and person summaries in which every sentence cites
   its claims, as a proposal for review.

Guardrails: every output is a proposal in the existing review path, run under the
requesting person's permissions, logged and signed. Autonomy grows by evidence (observe,
shadow, draft, bounded) and never includes accepting semantic or perceptual matches.
Building blocks already exist: sealed runs, the MCP propose tools, findings,
notifications, watches and the research service.

## Gaps to close

1. **Descriptions and titles.** Authored, cited, versioned title and description statements
   with AI drafts as proposals, review, diffs and search.
2. **Labels with observed and alleged basis.** A versioned bilingual vocabulary and a
   span-pinned label statement.
3. **Typed relations with stated certainty.** Family, command, membership and document
   relations between subjects, pinned to evidence, with human certainty kept separate from
   machine scores.
