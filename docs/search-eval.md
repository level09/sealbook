# Search evaluation on the local demo

`acceptance/eval_search.py` runs a battery of analyst queries against the local
demo database (`sealbook`, both demo cases seeded, `stk enrich` and `stk worker`
run) through the same functions the endpoints call: `service.search` and
`service.actor_search` behind `POST /api/evidence/search`, and `stk.search.find`
behind `POST /api/search`. It is read-only: each query runs in a transaction that
is rolled back.

```sh
uv run python acceptance/eval_search.py          # table
uv run python acceptance/eval_search.py --json   # one JSON report
```

It needs the local Ollama server from [models.md](models.md). Latency is the
second of two passes, so statement caches and the model are warm, as in a
running server. Ranks are per source. The data is synthetic and small (two cases,
49 acquisitions); the numbers say nothing about archive scale, real handwriting or
field audio. Leads by meaning stay leads; nothing here accepts a match.

## Battery

Each query lists the sources an analyst would expect, by acquisition file name.
"Hit at 1" means one of them is the first source returned.

| Query | Case | Expected | Tests |
| --- | --- | --- | --- |
| لينا عمر النجار | 02 | list, appeal | full Arabic name |
| لينا النجار | 02 | testimony, list, appeal | name without middle name |
| Lina Al-Najjar | 02 | note | English spelling, no middle name |
| Lina Najjar | 02 | note | transliteration, no article |
| لينا النجّار | 02 | testimony | shadda |
| محمد احمد الخطيب | 02 | list | alef without hamza |
| Mohamad al Khatib | 02 | note | loose transliteration |
| يوسف خالد علي | 02 | transfer | PDF page text |
| Yousef Khaled Al-Ali | all | post, post_v2 | English name in an Arabic page |
| حرستا, Harasta | all | register_page.png | place in each script |
| حي الجديدة, Jdeideh | 02 | list, appeal, note | neighbourhood in each script |
| الفرع 999, Branch 999, 999 | 02 | list, testimony, transfer, note | facility and bare identifier |
| 4471, أمر نقل رقم 4471 | all, 02 | transfer | order number, phrase in visual RTL PDF text |
| 2014-02-11, 11 February 2014 | 02 | list, appeal, note | dates |
| سجن ك المركزي | 02 | transfer, testimony | prison name |
| آخر مره شوهدت, اخيها | 02 | appeal | ta marbuta as ha, alef without hamza |
| سمعنا إنو نقلوها | 02 | testimony | Syrian dialect |
| who was moved to the central prison | 02 | transfer, testimony | English question, Arabic sources |
| woman held at the branch in March 2014 | 02 | testimony | English question about a testimony |
| checkpoint, حاجز عسكري | 02 | checkpoint images, note, appeal | visual and text |
| prison building with barred windows | 02 | photo | visual |
| market stalls | 02 | market | visual |
| رحلة عالساحل بالباص | all | speech (02), sy_bus (search-eval) | audio content in dialect |
| handwritten register of detainees | 02 | register, scan, list | scan |
| full SHA-256, 12-character prefix | all | transfer | hash lookup |
| destroyed street | 02 | none | negative: the local seed has no such picture |

The local database still holds the drawn facility picture as `photo`, so the
visual query names what that picture shows. The negative query is reported but
not scored.

A second list checks the grouped answer of `/api/search`: the expected title in
the people, places, sources, claims, passages, media, findings and incidents
groups.

## Results, 2026-10-24

Apple M2, 16 GB, Ollama 0.40.5, `embeddinggemma-2`. Other agents' test suites
were running during the "before" pass, so its latency is inflated by load as well
as by the cause below; the "after" passes ran with lighter load.

| Run | Hit at 1 | Hit at 3 | Missed | Person at 1 | Battery p50 / p95 |
| --- | --- | --- | --- | --- | --- |
| Before (commit b71717a code) | 0.74 | 0.85 | 2 | 0.89 | 3089 / 7090 ms |
| After the code fixes | 0.88 | 0.94 | 2 | 0.89 | 311 ms median |
| After also enriching case-demo-01 | 0.94 | 1.00 | 0 | 0.89 | 364 / 872 ms |

Grouped `/api/search` (`stk.search.find`, all groups): p50 441 ms, p95 616 ms.

What changed for the analyst, query by query:

- "Lina Al-Najjar" and "Lina Najjar": the English note ranked 4th and 5th behind
  meaning-only Arabic leads. It is now first (`name_variant` + `words`), followed
  by the Arabic list and appeal found through the recorded spelling.
- "Mohamad al Khatib": 3rd, now 1st through the recorded spelling.
- "أمر نقل رقم 4471": the PDF text reads "ن/ 4471 أمر نقل رقم" (visual order),
  so only a meaning lead found it. Now an exact `words` hit.
- Hash and prefix: 5th and 3rd, as unrelated meaning leads. Now the only result.
- حرستا and Harasta: missed. The case-demo-01 register was never indexed. After
  `stk enrich --investigation case-demo-01` the register is found by its words for حرستا
  and through the gazetteer name حرستا for "Harasta".
- "Branch 999": the Arabic list was 14th, now 3rd through the gazetteer name.
- "checkpoint": the two checkpoint pictures now also come back by file name.
- Every search was about 2.5 s slower than needed (see below).

Still weak, reported as measured:

- "woman held at the branch in March 2014" ranks the testimony 2nd behind the
  English note; "handwritten register of detainees" ranks the register image 2nd
  behind the transfer order. Meaning-only queries depend on the model.
- "destroyed street" returns 20 leads with similarity around 0.7, the same range
  as right answers. No threshold separates them on this data.
- Grouped answer: masked people. Names from sensitive sources stay hidden in
  results, so "Mohamad al Khatib" shows "Person 373f30bc" first, which is a correct
  keyword match whose name is masked. No findings or incidents exist in the local
  database, so those groups are empty. The "checkpoint" picture ranks 3rd in the
  media group behind frames near the exact text hit.

The synthetic retrieval gold set (`acceptance/eval_retrieval.py`) measures the
model directly and is unchanged: recall@1 0.81, recall@5 0.94 over 32 positive
queries, negative top score 0.67 against 0.80 for positives.

## Causes found

1. Each query embedding sent two image and two audio reference probes with the
   query: 2.5 s per search against 42 ms for the text alone (measured with direct
   calls). `embeddings.query` now re-checks only the text probes against the
   active space and still refuses drift.
2. Exact and meaning ranks were fused with RRF, so a meaning-only lead could rank
   above a literal hit; literal priority applied only to identifier queries.
   Exact signal tiers now rank first.
3. Phrase-only matching: a missing middle name or PDF words in visual order made
   an exact hit invisible. An all-words match (`words`) fixes both.
4. Person name keys were used only for the people list, never to find documents
   naming the person in another script. Recorded spellings of matched people and
   gazetteer names of a matched place are now searched, labelled with the spelling.
5. No hash, prefix, file name or origin lookup.
6. One row per segment: a video's eight frames could fill eight result slots. One
   row per source now, with further located hits in `also`.
7. case-demo-01 was seeded without queuing discovery, so its five sources had no
   search index. Coverage said `not_indexed`; the search page did not.
8. An embedding failure aborted the whole search with 503, hiding exact results.

Checked and fine: index and query use the same `folded` normalization; keyword
search uses the trigram index on the normalized column; vectors come from one
active space; every result row passes `available()` for erasure and the case filter.

## Not fixed

- Hamza on waw or ya (ؤ, ئ) is not folded; changing it needs a backfill migration
  of `evidence_chunk.normalized` and the SQL function.
- Two seeded PNGs (`744494558f5f…`, `ba60a04ff575…`) are random bytes, so their
  embed and fingerprint runs failed.
- No OCR for scans and no transcript for audio: scans and audio are found by
  meaning only, and their results have no snippet.
- HTML sources are indexed with their markup; snippets blank it out.
