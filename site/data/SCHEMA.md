# Site data contract

`site/build_data.py` writes `site/data/case.json` and copies media into
`site/media/`. Every value comes from a real file or a real run; nothing is
typed by hand. Pages read this file with `fetch("data/case.json")`.

`connections`, `story`, `sheet`, `agents`, `watch`, `casemap`, `disagree`, `entities`,
`research` and `grouped_search.queries` come from
`site/build_live.py`: `stk demo-seed --full` on a throwaway Postgres database
(created and dropped by `stk.migrations.throwaway_database_url`), read back
through the app's own endpoints by a signed-in test client, with model calls off.
`speech` is a run of `stk.speech` with the downloaded faster-whisper model.
Measured numbers in `status` and `grouped_search.battery|gold` are parsed from
docs/status.md, docs/search-eval.md and docs/models.md.

```jsonc
{
  "generated_at": "ISO time",
  "files": [{
    "id": "register",                  // stable id used by demos
    "name": "register_page.png",
    "kind": "image|pdf|audio|html",
    "media": "media/register_page.png", // what the page shows (PNG render for PDF, waveform PNG for audio plus the audio file)
    "audio": "media/witness_clip.ogg",   // audio only
    "bytes": 1378757,
    "sha256": "...", "md5": "...",
    "acquired": {"by": "R. Haddad (intake)", "at": "...", "how": "...", "origin": "device:scanner-01"},
    "c2pa": {"state": "valid|untrusted|absent|not_applicable", "note": "plain sentence from c2patool output"},
    "text": {"lang": "ar", "dir": "rtl", "content": "extracted text as the app would index it"},
    "claims": [{
      "prop": "name|birth_date|detention|transfer|place|...",
      "person": "P1|P2",
      "value": "display value",
      "where": {"chars": [start, end]} | {"page": 1, "bbox": [x0,y0,x1,y1]} | {"ms": [start, end]},
      "tier": "unconfirmed"
    }]
  }],
  "people": [{"id": "P1", "label": "...", "spellings": ["...", "..."], "twin_of": "P2|null", "note": "..."}],
  "duplicates": [{"a": "photo", "b": "photo_crop", "method": "pdq|audio", "distance": 18, "of": 256, "offset_ms": null, "verdict": "proposed"}],
  "search": {
    "model": "embeddinggemma-2", "runtime": "Ollama 0.40.5", "hardware": "Apple M2 16 GB",
    "summary": {"recall_at_1": 0.84, "recall_at_5": 0.94, "by_type": {}},
    "queries": [{"id": "q01", "type": "msa|dialect|cross|text_to_image|text_to_audio|text_to_scan|negative",
                 "text": "...", "results": [{"file": "ar_market.txt", "score": 0.81, "rank": 1, "hit": true,
                 "location": {"chars": [a,b]} | {"ms": [a,b]} | {"bbox": [...]}, "media": "media/..."}]}]
  },
  "video": {                             // two real, openly licensed clips (acceptance/public/)
    "clips": [{"id": "jdeideh_2015", "media": "media/jdeideh_2015.mp4", "poster": "media/....jpg", "poster_s": 5,
               "duration_ms": 19779, "place": "Jdeideh quarter, Aleppo",
               "credit": {"title": "...", "author": "...", "licence": "CC BY 3.0", "licence_url": "...",
                          "source": "Commons page", "date": "YYYY-MM-DD", "sha1": "..."},
               "frames_ms": [0, 1001],            // fingerprint frame timestamps, as decoded
               "sprite": "media/..._strip.jpg",   // one 240x135 thumbnail per frames_ms entry, left to right
               "audio_status": "present", "bytes": 0, "sha256": "...", "md5": "..."}],
    "copies": [{"id": "v_trim", "from": "jdeideh_2015", "how": "...", "media": "media/v_trim.mp4", "derived": true,
                "frames_ms": [], "sprite": "...",
                "picture": null | {"query_coverage": [16, 16], "candidate_coverage": [n, m], "query_ms": [a, b],
                                   "candidate_ms": [a, b], "max_distance": 30,
                                   "sample_pairs": [[copy_ms, original_ms]], "offset_ms": 4007},  // median of pair differences
                "sound": null | {"offset_ms": 3968, "aligned_landmarks": 19, "shared_landmarks": 45, "concentration": [19, 45]}}],
    "threshold": {"distance": 31, "of": 256, "quality": 50},   // Config.PDQ_DISTANCE, PDQ_QUALITY
    "places": [{"date": "YYYY-MM-DD", "place": "...", "clip": "jdeideh_2015", "poster": "..."}],
    "search": {"model": "...", "runtime": "Ollama x.y.z", "frames": 14, "every_s": 3,   // omitted when Ollama is down
               "queries": [{"text": "...", "lang": "en|ar", "results": [{"clip": "...", "ms": 6009, "score": 0.776}]}]}
  },
  "grouped_search": {                    // POST /api/search, semantic off (no model in the build)
    "queries": [{"query": "Branch 999", "ms": 181, "semantic": "off",
                 "groups": {"places": {"total": 1, "exact": 1, "items": [{"title": "...", "alt": ["..."], "subtitle": "...",
                   "exact": true, "signals": ["name"], "matched": "...", "masked": false,
                   "snippet": {"text": "...", "highlights": [[a, b]]}, "location": null}]}}}],
    "battery": {"scored": 34, "hit_at_1": 0.94, "hit_at_3": 1.0, "missed": 0, "p50_ms": 364, "p95_ms": 872, "date": "...", "setup": "..."},
    "gold": {"recall_at_1": 0.81, "recall_at_5": 0.94, "queries": 32}
  },
  "speech": {                            // stk.speech on acceptance/data/witness_clip.ogg during the build
    "file": "witness_clip.ogg", "audio": "media/witness_clip.ogg", "duration_ms": 33700,
    "model": {"model": "large-v3-turbo", "repo": "...", "revision": "...", "weights_sha256": "...", "runtime": "..."},
    "device": {"device": "cpu", "compute_type": "int8"}, "hardware": "sysctl machdep.cpu.brand_string",
    "language": {"language": "ar", "detected": "ar", "probability": "0.998"},
    "segments": [{"ms": [a, b], "text": "...", "words": [{"w": "...", "ms": [a, b]}]}],
    "script": [{"ms": [a, b], "text": "..."}],   // expected.json sentences
    "wer": 0.217, "words": 46,                   // tests/test_speech.py wer() and words()
    "decode_s": 20.9, "rtf": 0.62, "language_s": 0.0,
    "seen_at": {"ms": [a, b], "spoken_as": "..."}
  },
  "connections": {                       // GET /api/discover/findings and /findings/<key>, after logged reveals
    "case": "case-demo-02", "per_kind": 20,   // Config.FINDINGS_PER_KIND
    "findings": [{"kind": "shared_presence|candidate_incident", "label": "...", "reason": "...", "why": "...",
                  "band": "strong|moderate|weak", "month": "YYYY-MM", "window": ["from", "to"], "features": {},
                  "places": ["Branch 999"], "people": 4,
                  "members": [{"name": "...", "file": "...", "derived": false, "prop": "...", "value": "...",
                               "when": "...", "tier": "...", "sensitive": true,
                               "excerpt": {"before": "...", "span": "...", "after": "..."} | null, "span": {}}]}],
    "incidents": ["accepted incident titles"],
    "gestures": [{"rows": [{"hash": "...", "prop": "...", "what": "...", "role": "involved only", "valid": [from, to]}],
                  "batch": ["row hashes, as signed"], "verified_offline": true,   // stk.ledger.offline_sigs
                  "sig": {"challenge": "...", "client_data_json": "...", "authenticator_data": "...",
                          "signature": "...", "public_key": "COSE, base64url", "rp_id": "...", "origin": "...", "demo": true}}],
    "gesture_domain": "sealbook-gesture-v1\n",
    "reveals": 7, "access_records": 12   // what reading the findings wrote to the ledger
  },
  "story": {                             // GET /api/stories?lens=person&ref=<R1>
    "title": "...", "rows": [{"steps": [], "gaps": [{"from": "...", "to": "...", "days": 60}]}],
    "points": [{"id": "...", "prop": "...", "place": "...", "place_id": "...", "en": "...", "from": "...", "to": "...",
                "tier": "...", "sensitive": true, "file": "..."}],
    "pending": [{"kind": "...", "label": "...", "reason": "...", "band": "...", "window": ["from", "to"]}],
    "incidents": [],
    "places": {"<id>": {"ar": "...", "en": "...", "kind": "...", "lonlat": [lon, lat], "precision_m": 3000,
                        "geo_source": "geonames|cod-ab|analyst|...", "source": "...", "geonames_id": 0, "admin2": null}},
    "map": {"box": [x0, y0, x1, y1], "areas": [{"name": "...", "name_ar": "...", "pcode": "...", "rings": [[[lon, lat]]]}],
            "boundaries": "credit line from sealbook/places/fixtures/codab/SOURCE.txt"},
    "attribution": "GeoNames (CC BY 4.0)"
  },
  "sheet": {                             // GET /api/evidence/<sha>/sheet and /api/sheets/<acq>/proposals
    "file": "messy_people.csv", "dataset": "...", "header": ["..."], "rows": [["cell"]],
    "mapping": {"column": "field"}, "key_column": "No.",
    "versions": [{"number": 1, "file": "...", "diff": {"new": 15, "changed": 0, "unchanged": 0, "missing": 0, "invalid": 0}}],
    "claims": [{"row": 2, "column": 2, "chars": [a, b] | null, "prop": "...", "text": "...", "norm": null, "kind": null,
                "valid": [from, to], "tier": "unconfirmed"}],
    "fixed_output": true,                // the demo's sheet extraction is fixed output, not a model run
    "measured": "sentence from docs/status.md"
  },
  "agents": {                           // GET /api/agents/brief per batch, GET /api/review/claim/<id> per linker statement
    "briefs": [{"arrived|who|where|when|connects|disagrees|next": [{"text": "..." | null, "tag": "known|new|open" | null,
                "locked": ["sensitive source titles"], "cites": [{"prop": "...", "source": "title"}]}]}],
    "proposals": [{"kind": "same_person|involved|related|cellmate|...", "ask": "Cellmates?", "joins": ["...", "..."],
                   "reason": "..." | null, "proposer": "Linker", "tier": "unconfirmed",
                   "basis": [{"prop": "...", "value": "..." | null, "person": "...", "source": "...", "sensitive": true,
                              "where": "characters 71 to 83", "tier": "..."}]}],
    "candidates": 10, "already_known": 1,   // the demo's fixed-output runs (sealbook-demo-fixed-output)
    "measured": "the real-model sentence from docs/status.md"
  },
  "watch": {                             // tasks made with POST /api/agents/tasks, queued by tasks.tick and run by the worker's
                                         // work_once on the fixed answer (sealbook-demo-fixed-output), read back as their owner
    "case": "case-demo-02",
    "agents": [{"key": "standing|contradictions|gaps|noticer", "name": "...", "does": "...", "when": "...", "where": "...",
                "last": "outcome sentence ending in Demo output.",        // GET /api/agents/overview
                "on": "card note with keep checking on", "off": "note before it was switched on, or after PATCH enabled false"}],
    "question": {"text": "...", "outcome": {"proposed": 18, "reason": null},   // GET /api/agents/tasks/<id>
                 "people": [{"name": "...", "matches": [{"why": "...", "claims": [{"field": "...", "value": "...", "when": "..." | null, "source": "title"}]}]}
                            | {"locked": true, "count": 1, "source": "sensitive source title"}]},
    "contradictions": {"outcome": {}, "items": [{"kind": "...", "why": "...", "question": "draft for a colleague",   // GET /api/agents/contradictions
                       "sides": [{"field": "...", "value": "...", "when": null, "source": "...", "name": "...", "decided": false}]} | {"locked": ["title"]}]},
    "gaps": {"outcome": {}, "items": [{"name": "...", "window": "No record after ...", "claims": [{"text": "Field: value", "when": "...", "source": "..."}],   // GET /api/agents/gaps
             "leads": [], "where": "...", "searches": ["..."], "colleagues": ["..."], "maybe": "above|below" | null} | {"name": null, "locked": ["title"]}]},
    "noticer": {"outcome": {}, "deferred": 14, "items": [{"kind": "Copied wording", "why": "...", "next": "...", "a": "title", "b": "title",   // GET /api/agents/notice
                "other_case": null, "signals": ["..."], "claims": [], "link": "can|proposed" | null, "proposed": true} | {"locked": true, "count": 1, "source": "..."}]},
    "proposal": null | {"kind": "related", "ask": "Related documents?", "joins": ["...", "..."], "reason": "...", "proposer": "Noticer",
                        "tier": "unconfirmed", "basis": []}   // POST /api/agents/notice/link, then GET /api/review/claim/<id>; shaped as agents.proposals
  },
  "casemap": {                           // GET /api/case-map and /api/case-map/path; ids replaced by page ids (r rows, p places, c points)
    "case": "case-demo-02",
    "rows": [{"id": "r1", "label": "...", "named": true, "gaps": [{"from": "...", "to": "...", "days": 60}]}],
    "points": [{"id": "c1", "row": "r1", "prop": "detained_at", "place": "p1" | null, "written": "place as written",
                "from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "tier": "...", "file": "source title", "sensitive": false}],
    "places": [{"id": "p1", "en": "...", "ar": "...", "lonlat": [lon, lat], "precision_m": 250, "approximate": false}],
    "bands": [{"people": ["r1"], "places": ["p1"], "members": ["c1"], "proposed": true, "from": "...", "to": "...",
               "month": "YYYY-MM", "label": "held together, July 2013"}],
    "open_ends": [{"row": "r1", "after": "YYYY-MM-DD", "label": "No record after ..."}],
    "pairs": [["r2", "r3"]],             // records an open same-person proposal joins
    "unresolved": 3, "attribution": "GeoNames (CC BY 4.0)",
    "people": ["r1"],                    // rows whose names are unique on the map: the path pickers
    "paths": [{"from": "r1", "to": "r5", "found": true, "steps": [{"text": "...", "kind": "at|transfer|with|source|family|same",
               "sources": ["title"], "unconfirmed": false, "weak": "Not at the same time: ..." | null,
               "rows": ["r1"], "places": ["p1"], "points": ["c1"]}]}],   // every ordered pair of `people`
    "preset": ["r5", "r10"],             // first path with a held-together step and a weak step
    "depth": 8
  },
  "disagree": {                          // GET /api/people, /api/people/<id> and POST /api/search (people)
    "fork": {"person": "...", "prop": "name", "from": "YYYY-MM-DD", "sides": [{"value": "...", "file": "..."}]},
    "counted": {"person": "...", "screens": [["People list", 10, 3], ["Person page", 10, 3], ["Search", 10, 3]]},
    "forks": 2, "people": 34
  },
  "entities": {                          // GET /api/places/<id> (Branch 999) and /api/incidents/<id>
    "place": {"en": "...", "ar": "...", "kind": "Facility", "precision_m": 250, "geo_source": "analyst",
              "spellings": [{"name": "...", "claims": 13, "gazetteer": true}], "claims": 18, "sources": 9,
              "people": [{"label": "...", "named": true, "from": "...", "to": "...", "tier": "...", "points": 1}],
              "incidents": [{"title": "...", "window": ["from", "to"], "people": 2, "tier": "accepted"}],
              "findings": [{"label": "...", "reason": "...", "why": "...", "band": "..."}], "attribution": "..."},
    "incident": {"title": "...", "window": ["from", "to"], "places": ["..."], "signed_by": "...", "sources": 1,
                 "people": [{"label": "...", "named": true, "roles": ["victim"], "tier": "..."}],
                 "findings": [{"label": "...", "state": "accepted", "reason": "...", "why": "...", "band": "..."}]}
  },
  "research": {                          // POST /api/research/queries, then the case marked sensitive; nothing sent
    "person": "...", "queries": [{"text": "...", "kinds": ["web", "images", "videos"], "why": [{"label": "Name", "value": "..."}]}],
    "left_out": [{"prop": "...", "why": "from a sensitive source|unconfirmed, not yet accepted"}], "facts": 10,
    "refused": "the refusal sentence", "refused_queries": 0
  },
  "status": {"areas": [{"area": "...", "does": "...", "limits": "..."}], "measured": ["..."], "planned": ["..."], "updated": "YYYY-MM-DD"}
}
```

Rules: Arabic strings stay in logical order; pages render them with
`dir="auto"` or `<bdi>`. Omit any field the sources cannot produce rather than
inventing it.
