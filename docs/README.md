# Start here

Sealbook is a source-backed evidence application built on stk. This directory
describes the code, its decisions and its limits for contributors.

| Read | Purpose |
| --- | --- |
| [Status](status.md) | What the code does, what was checked, and what remains |
| [Demo tour](demo.md) | One section per flow, naming the demo items to open and what to notice |
| [Architecture](architecture.md) | Module map and boundaries between evidence and projections |
| [Decisions](decisions.md) | Current choices, reasons, and review gates |
| [Discovery and review](discovery.md) | Fingerprints, embeddings and analyst collaboration |
| [Search evaluation](search-eval.md) | Rerunnable analyst search battery, results and known gaps |
| [Agent evaluation](agent-eval.md) | Linker, gap, contradiction and brief scores against a planted case, fixed and live; extraction's invented-people check; counts from real data |
| [Operations](operations.md) | Configuration, local commands and verification |
| [Lessons](lessons.md) | Failures found during the build and how to avoid them |
| [stk upstream notes](stk-upstream.md) | Framework issues and reusable pieces found while building Sealbook |
| [Plan](roadmap.md) | The one plan: what comes next, in order |
| [Research](research.md) | Borrowed patterns and DigUp references |
| [Model choice](models.md) | Current local multimodal recommendation, alternatives and validation gap |
| [Development](development.md) | Detailed stk conventions |
| [Design](design.md) | Requirements, core model, controls and dated research |

Code and reproducible checks win when prose disagrees. Correct the prose in the
same change. Keep claims about legal compliance, live model quality and production
readiness separate from local implementation tests.
