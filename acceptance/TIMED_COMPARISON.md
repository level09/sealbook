# Timed comparison (acceptance test 0)

PLAN.md: "one synthetic missing-person case, assemble everything we know plus an
exportable answer, done in the team's current tool and in Sealbook. If Sealbook is not several times
faster with better sourcing, the thesis fails." A person runs it; nothing here is
automated, and no result is recorded until someone has done it.

## Rules

- Synthetic data only: the files in `acceptance/data/` (regenerate with
  `uv run python acceptance/make_case.py`). Never production or partner data.
- The current tool runs locally on a fresh database, Sealbook on a fresh
  database plus `stk create-db`. Both start empty apart from one analyst account.
- The same operator does both, the current tool first one day and Sealbook first another, and
  the two runs are averaged per system, so learning the case does not favour one side.
- The missing-person post is captured by URL: host `missing_post.html` at a public
  URL you control (Sealbook refuses local addresses), and swap in `missing_post_v2.html`
  before the re-check.
- The clock starts when the operator has the four files, the post URL and the request,
  and stops when the exported answer exists as a file. Model and worker time count.

## The request

"What do we hold on Yousef Khaled Al-Ali (يوسف خالد العلي), born 1991? Send us an
answer we can give his family."

## Done means

1. All five sources ingested: register page, transfer order, witness clip, facility
   photo and the missing-person post, with the post re-checked after its edit.
2. Every fact about P1 in `acceptance/expected.json` is recorded and attributed to P1,
   and none of P2's (the exact-name twin) are.
3. An exported answer for the family exists.

## Sourcing

Count, in the exported answer, the sentences that state a fact about P1 and how many
of those point to the exact place in a source (page and region, line, or time in the
clip). Sealbook has better sourcing when its share of pointed sentences is higher
and it has no fact attributed to P2.

## Record

Write `acceptance/timed_comparison.json` and commit it; acceptance test 0 reads it:

```json
{
  "date": "YYYY-MM-DD",
  "operator": "role, not name",
  "baseline_seconds": 0,
  "sealbook_seconds": 0,
  "baseline_sourced": [0, 0],
  "sealbook_sourced": [0, 0],
  "sealbook_better_sourcing": false,
  "notes": "what slowed each run down"
}
```

`*_sourced` is `[sentences pointing to a source, sentences stating a fact]`. Test 0
passes when `baseline_seconds / sealbook_seconds >= 3` and `sealbook_better_sourcing`
is true; the 3 reads "several times faster" and is open to change before the first
run, not after.
