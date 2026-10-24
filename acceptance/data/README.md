# Synthetic v0 case files

Everything here is invented. Every person is fictional; "Branch 999" (الفرع 999) and "Central Prison K" (سجن ك المركزي) do not exist. Only public place names (Douma, Harasta, Homs, Qaboun) are real. No real documents or partner data were used.

- `register_page.png`: detention register page in a handwriting-style font (Aref Ruqaa, OFL, in `../fonts/`), two rows for P1 and the same-name twin P2, with scan skew, grain and uneven ink.
- `transfer_order.pdf`: printed transfer order with letterhead and registry stamp; real text layer.
- `witness_clip.ogg`: 33 s of synthetic Syrian Arabic speech (OpenAI `gpt-audio` via OpenRouter, voice ash, colloquial Damascene script chosen by a native speaker), Ogg Opus. The rendered sentences are cached in `../voice/`.
- `photo_damaged_building.jpg`: a real Wikimedia Commons photograph (destruction in the Bab Dreeb area of Homs, 2012, credits in `../public/ATTRIBUTION.md`), downscaled to 1024 px and signed with the c2patool test certificate (state Valid, signer untrusted). The manifest is ours; the original carries none. It is not a picture of anything in the synthetic case.
- `missing_post.html`, `missing_post_v2.html`: appeal post; v2 changes one sentence in paragraph 2.
- `messy_people.csv`, `messy_people_v2.csv`: 15-row intake sheet in mixed Arabic and English (name, sex, age and job in one cell; dates as ISO, day first, Arabic month names, Eastern Arabic digits, a year, "unknown"). All people are invented. v2 changes two dates and one place, drops row 9, adds two rows, repeats identifier 12 and has a row without one. Answers for `../eval_sheet.py` live in that script.
- `expected.json`: ground truth with exact locations (PNG pixel bboxes after skew, PDF char offsets in pypdf text, audio ms ranges, HTML offsets in `innerText`).

Regenerate (needs `ffmpeg`, `c2patool`, and `OPENROUTER_API_KEY` only if `../voice/` is missing): `uv run python acceptance/make_case.py`. PNG, OGG, HTML and JSON are byte-stable; the PDF (creation date) and the JPG (C2PA claim time and ids) differ per run.
