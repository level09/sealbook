# Synthetic retrieval gold set

Everything here is invented except the two photographs listed below. No real documents, people or partner data were used. The passages are about markets, buses, clinics and a coastal trip.

- `*.txt`: 11 Arabic passages (modern standard and Syrian-dialect phrasing, some with Arabic-Indic digits and diacritics) and 3 English ones. One sentence per line.
- `scan_page.png`: three Arabic lines in a handwriting-style font (Aref Ruqaa, OFL, in `../fonts/`) with scan skew and grain.
- Two real photographs used by the image queries, not stored here: `../public/aleppo_checkpoint.png` and `../public/aleppo_souk.jpg` (Wikimedia Commons, credits in `../public/ATTRIBUTION.md`). The eval reads them from `acceptance/public/`. The drawn `image_checkpoint.png` and `image_market.png` are deleted; the app seed reads the same two photographs.
- `speech.wav`: about 43 s of synthetic Syrian Arabic speech (OpenAI `gpt-audio` via OpenRouter, voice ash, colloquial Damascene script chosen by a native speaker; rendered once and cached in `../voice/retrieval_*.flac`), mono 16 kHz, built from sentences with known ms ranges. `speech_excerpt.wav` is cut from its middle. `tone.wav` is an unrelated 660 Hz tone with noise.
- `queries.json`: 36 queries (Arabic, dialect, cross-lingual, text to image, scan and audio, plus 4 with no relevant item). Each lists the expected file and exact location: char offsets, PNG bbox after skew, or ms range in that file's own timeline. The generator computes them and re-checks them on every run.

Regenerate (needs `ffmpeg`, playwright chromium, and `OPENROUTER_API_KEY` only if the voice cache is missing): `uv run python acceptance/make_retrieval.py`.

Run against a local Ollama server with `embeddinggemma-2` pulled (see `docs/models.md`):

`EMBEDDING_URL=http://127.0.0.1:11434 EMBEDDING_MODEL=embeddinggemma-2 uv run python acceptance/eval_retrieval.py`

It refuses to run if either variable is unset. It embeds text per line, images whole, and audio in 30 s windows with a 25 s hop, then prints JSON: recall@1, recall@5, MRR, whether the top hit's location overlaps the expected one, the top score of the negative queries, and per-request latency. It stops if two distinct images or two distinct audio items get near-identical vectors, which means the server ignored the media. Images are one item each, so a bbox hit only means the right image was found.

Results measure retrieval on this small set only. They say nothing about identity, independent origin or factual accuracy, and the set is too small to rank models on fine differences.
