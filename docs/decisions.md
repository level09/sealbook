# Engineering decisions

| Decision | Reason and boundary |
| --- | --- |
| PostgreSQL remains the evidence substrate | Custody, permissions, policy, search and jobs share transactions; no second truth store |
| Signed statements remain the factual write path | New search features produce leads or proposals, never direct person-row edits |
| Two fingerprint layers | Picture catches visual reuse; sound catches reuse after crop or compression. Missing sound is explicit |
| Embeddings serve discovery | Actor profiles and text can be similar without depicting the same identity or independent origin |
| Local model route, no fallback | A failed local service is visible; evidence is not silently sent elsewhere |
| One embedding space | EmbeddingGemma 2 runs locally through Ollama. Text passages, actor profiles and media share one 768-dimensional space, one server and one reference set. The earlier separate 1024-dimensional text space was merged before any migration was applied |
| Located, bounded media inputs | Images and PDF pages become PNG inputs; video frames use decoded timestamps; audio uses overlapping 30-second mono 16 kHz windows. Each result opens its source location |
| Reference checks before reuse | Configuration hashes select the index generation; text/image/audio probes detect some hidden model changes. Changed references require explicit rebuild, with no automatic fallback |
| Rebuildable projections | Erasure and model/preprocessing changes must invalidate or rebuild affected search data |
| Human-gated origin grouping | Only accepted same-recording links collapse origins; contains/related links preserve compilation independence |
| Corrections preserve history | A pending suggestion does not replace the predecessor. Signed acceptance does; undo restores current support |
| Private discussion, generic notifications | Colleagues receive links. Sensitive values remain behind authenticated reveal and access logging |
| Only sensitive items ask before showing (owner, 2026-10-24) | Asking before every view made the archive unusable for viewing media. A source acquired as sensitive (graphic, or identifies victims or witnesses; also the default when no acquisition set it) still waits for one logged click. Any other source is shown at once; its first content, text or frame load in a session writes an `access` `view` statement, so the custody chain still names everyone who saw it. One view is logged per person, source and login session; the statement carries a keyed hash of the session, and concurrent first loads are serialised |
| Thumbnail tiles are not views (owner, 2026-10-24) | A 160 or 480 px tile in a list is not viewing evidence, so it writes no access record; opening the item does. A sensitive source's tiles still need the logged reveal |
| PostGIS is the spatial store (2026-10-24) | PostgreSQL stays the only database. Geometry, generated centroid and coordinates, GiST indexes; `lon` and `lat` are read-only generated accessors. Deployments need PostGIS 3 and a role that may create the extension |
| Every location carries precision and provenance | `precision_m` and `geo_source` on places, `precision_m` and `method` on evidence locations. A location is never finer than its source states; embedded camera coordinates default to 50 m. A place without a precision never joins another by distance |
| Kind ladder as a table | `place_kind` rows (admin kinds per country and level, settlement and facility kinds) replace the hardcoded Syrian list, so a second country adds rows. Default precisions per kind are builder calls (facility 250 m, checkpoint 100 m, neighbourhood 1 km, populated place 3 km) for the owner to check |
| Boundaries from OCHA COD-AB | UNCS and OCHA ROSC, CC BY 3.0 IGO, credited on every map that draws them; fetched from HDX (`cod-ab-syr`, v02, valid on 2020-12-17). geoBoundaries publishes the same source without Arabic names or P-codes. A four-governorate simplified subset is committed for tests with its source note |
| Tiles per reader, cached per reader | Tile bodies are `private, no-store`. The model behind them is reused only for the same user and query and only while no map-relevant statement or acquisition arrived, for at most 30 s; the cache is process memory, like the rest of live delivery |
| Offline basemap | Protomaps extract served by the app (range requests), OpenStreetMap credited; bbox 34.9,32.0,42.6,37.6 to zoom 14 (217 MB) covers Syria, Lebanon, northern Jordan and the Turkish and Iraqi border areas. Natural Earth outlines remain the fallback |
| Embedded locations are proposals | EXIF, container and C2PA coordinates are easy to strip or forge, so `geotag` writes unconfirmed agent statements; a person reviews them |
| Distance joins keep the finding key | Two places join for shared presence when their centroids lie within the larger of their precisions; the key stays investigation, people, places and month, so the place pair is part of the key |
| Review decides, Inbox talks | `/review` holds decisions with the evidence at the span; `/inbox` holds requests, threads and mentions. Review links to Inbox for the full thread and never to the monitor |
| Masking in review follows the source | A personal value is masked when its acquired source (the scan behind an OCR text, the recording behind a transcript) is sensitive and this session has not revealed it; revealing goes through the logged evidence reveal |
| No signing blind | Accept and reject wait until every sensitive source the decision rests on is revealed in this session; one action reveals them all, each logged like an evidence page reveal |
| Passkey refusal names the fix | Staging answers 409 `no_passkey` with a setup link back to the page, after the request is authorised, so a 403 or 404 still says more than "set up a passkey" |
| What a research query logs (2026-10-24) | Each Brave request writes one `access` statement before it is sent, committed so a crash cannot hide the disclosure: who (author), when, the scope (`person:`, `place:`, `incident:` or `blob:`), case, kind, provider, the exact query text and the people whose values built it. The text sits in the erasable payload; the chain keeps only the salted commitment. No plain hash of the text is kept outside the payload, because names are guessable and such a hash would survive erasure as a name record. Run inputs and acquisition context are chained and unerasable, so a capture carries only the access statement id, never the query. Erasing a person deletes queries on their page (subject) and any naming them in `people` |
| What research may send (2026-10-24) | Proposals use only accepted or corroborated values the reader sees unmasked, from ordinary sources with no acquisition in a sensitive or switched-off case; incident dates only when every supporting source qualifies. Session reveals do not widen this. What the analyst types is their own decision and is logged the same way |
| Research results stay in memory (2026-10-24) | Search results are kept per person in process memory for 30 minutes and never written to the database; Add as evidence accepts only result ids this person was shown, so capture cannot be pointed at arbitrary pages. Thumbnails are fetched by the server so the browser never contacts the provider |
| Capture is anonymous and bounded | No cookies, accounts or credentials are ever sent; a page with a password field, a bot check or a video that needs an account is refused, not worked around. Only the picked page is rendered, and only media it lists are fetched; nothing is crawled. Copies already held are flagged (same bytes, a link captured before, PDQ on the thumbnail, then the normal fingerprint run's unconfirmed `media_match`), never merged |
| Capture runs in a local browser (owner, 2026-10-24) | Pages are visited by headless Chromium on the Sealbook worker, not by Cloudflare Browser Rendering. Evidence of what a page showed should not pass through a third party that sees every URL an analyst picks and holds the rendering. Brave stays for search, which already sends only confirmed queries. Cost: workers need Chromium installed, sites see the server's address, and some sites refuse headless browsers (recorded as a refusal, never bypassed). Every request and redirect hop is checked against private addresses before it leaves |
| One process for now | Pending passkey operations and live delivery use local memory; scaling needs an explicit design |

## Decisions still open

The model/server choice is approved, implemented and measured on the synthetic
retrieval set. Real handwritten records, field audio and archive-scale latency
still need evaluation before enabling it for real evidence. See
[models.md](models.md).

Berkeley Protocol support means preserving methodology and provenance for human
review. It is not a certification or a guarantee of admissibility. The original
research in [design.md](design.md) is dated and may require legal revalidation.
