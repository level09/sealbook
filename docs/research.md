# Sources and borrowed patterns

These sources explain the product direction. Code and local verification determine
implementation status. Follow [roadmap.md](roadmap.md) for delivery state.

## Sources read

Internal notes on video deduplication, embeddings and audio fingerprinting informed the
first design.

No production archive or partner evidence was accessed for this implementation.

## Patterns to borrow from DigUp

Reviewed [DigUp](https://github.com/ARahim3/DigUp) at commit
`d6f1f480427d151640196e4d6588906e1f3ee782`. The patterns below informed the
implemented discovery slice. See [roadmap.md](roadmap.md) for verification and
the live-model evaluation gap.

| Priority | Pattern in DigUp | Fit for Sealbook |
| --- | --- | --- |
| First | [Located segments](https://github.com/ARahim3/DigUp/blob/d6f1f480427d151640196e4d6588906e1f3ee782/Sources/DigUpKit/Indexer.swift#L690): page, frame, or overlapping audio window | Search results open a precise source location. Add semantic media segments beside the existing text spans and perceptual fingerprints. A discovery hit becomes a source-backed proposal only through the review gate. |
| First | [Arabic normalization with original-text mapping](https://github.com/ARahim3/DigUp/blob/d6f1f480427d151640196e4d6588906e1f3ee782/Sources/DigUpKit/SearchText.swift#L5) | Extend our existing Arabic person-name search to source-text passages, using the canonical normalizer and original-offset mapping. Keep original text, names, byte hashes and citation offsets unchanged. Show whether the hit was literal or normalized. |
| First | [Reference vectors](https://github.com/ARahim3/DigUp/blob/d6f1f480427d151640196e4d6588906e1f3ee782/Tests/LlamaRuntimeTests/ReferenceVectorTests.swift#L6) and [settings fingerprint](https://github.com/ARahim3/DigUp/blob/d6f1f480427d151640196e4d6588906e1f3ee782/Sources/DigUpKit/Model.swift#L80) | Detect vector drift even when a model alias stays unchanged. Pin preprocessing, tokenizer, model weights and runtime; compare a non-sensitive reference set before serving an existing index. |
| Next | [Partial indexing](https://github.com/ARahim3/DigUp/blob/d6f1f480427d151640196e4d6588906e1f3ee782/Sources/DigUpKit/Model.swift#L52) | Process new evidence before finishing a giant archive item. Expose pages or time ranges processed and pending. A search with incomplete coverage must never imply an archive-wide negative finding. |
| Next | [Exact-lookup ranking and weaker-match grouping](https://github.com/ARahim3/DigUp/blob/d6f1f480427d151640196e4d6588906e1f3ee782/Sources/DigUpKit/Searcher.swift#L174) | Preserve exact registry numbers and names, show why each hit appeared, and separate weaker discovery leads. Evaluate the ranker against an Arabic/English case set before replacing RRF. |

DigUp scores all vectors to establish its query-relative scale. Copying that
statistical gate onto only a pgvector nearest-neighbor shortlist would change its
meaning. Its thresholds and reported results are not validation on our evidence.

Sealbook uses one 768-dimensional EmbeddingGemma 2 space for text, actors and
media; live evaluation remains open.
Semantic audio asks what a recording contains; audio landmarks ask whether sound
was reused. Both belong in the platform, with distinct provenance and review rules.
