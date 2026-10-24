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

## Gaps to close

1. **Descriptions and titles.** Authored, cited, versioned title and description statements
   with AI drafts as proposals, review, diffs and search.
2. **Labels with observed and alleged basis.** A versioned bilingual vocabulary and a
   span-pinned label statement.
3. **Typed relations with stated certainty.** Family, command, membership and document
   relations between subjects, pinned to evidence, with human certainty kept separate from
   machine scores.
