# Local embedding model selection

Research checked 2026-10-23. EmbeddingGemma 2 runs locally through Ollama.
One 768-dimensional space serves text passages, actor profiles and media; there
is no separate text model. It has been measured on the synthetic set below, not
on Syrian evidence.

| Candidate | Fit | Cost and limit |
| --- | --- | --- |
| [EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2) | 740M parameters; text, image, video and audio in one 768-dimensional space; Apache 2.0 | Newly released. Language performance varies; no validated Syrian-dialect or handwritten-security-record benchmark was found |
| [Qwen3-VL-Embedding-2B](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B) | Text, image, screenshots and video, up to 2048 dimensions; multilingual visual retrieval | Larger; the documented input list does not include audio, so our full workflow needs another model/service |
| Local captions/transcripts plus the existing text model | Reuses current retrieval and produces inspectable text | Adds a generative interpretation step that can omit visual details or mishear speech; cannot replace original-media retrieval or fingerprinting |

## Serving contract

Ollama's native [`/api/embed`](https://github.com/ollama/ollama/blob/main/docs/api.md)
takes a list mixing text strings and `{"image": base64}` or `{"audio": base64}`
items, plus `dimensions`, and returns `model` and `embeddings`. Its OpenAI-style
`/v1/embeddings` rejected media in our check (Ollama 0.40.5), so the adapter
uses the native endpoint. A content-sensitive reference check still decides
whether media was consumed, not the HTTP status.

The [official model card](https://huggingface.co/google/embeddinggemma-2#best-practices)
specifies retrieval text prefixes, a shared token budget and mono 16 kHz audio.
Its PyTorch implementation requires bfloat16 or float32, not float16 activations.
Storing normalized output vectors as pgvector halfvec is a separate operation.
Model capacity is not the same as a particular runtime's decoded-audio limit.
Use bounded located windows and verify actual consumed content.

## Local setup

```sh
ollama pull embeddinggemma-2   # 1.3 GB, 744M parameters, nvfp4 quantization
```

`ollama show` reports a 512 embedding length; requesting 768 dimensions returns
768. Set these values before starting the web app and worker:

```dotenv
EMBEDDING_MODEL=embeddinggemma-2
EMBEDDING_URL=http://127.0.0.1:11434
EMBEDDING_REVISION="ollama=0.40.5;model=eaff2253e2ba"
```

Update the revision when Ollama or the model tag changes. See [.env-sample](../.env-sample) and
[operations.md](operations.md) for rebuilding and batch cursors. Nothing is
downloaded or started automatically.

The implemented recipe uses PNG images up to 1024 pixels, video frames every
three seconds with decoded timestamps, PDF page images and mono 16 kHz WAV windows
of up to 30 seconds with a 25-second hop. Queries use `task: search result | query: `
and passages/actor profiles use `title: none | text: `, the
EmbeddingGemma 1 format, which gave the results below; the EmbeddingGemma 2 card
has not been checked for a different one. No transcript is fabricated. Each
inference call includes non-sensitive
text, color and tone probes. Distinct content must produce distinct vectors, and
the reference set must stay within the drift tolerance. This checks the serving
contract, not semantic quality.

Runs seal the recipe and a SHA-256 hash of reference floats encoded as hexadecimal
strings. Model responses remain content-addressed raw outputs. Separate PDQ and
audio landmarks still determine reuse proposals; semantic similarity cannot
establish identity, independent origin or factual acceptance.

## Measured on the synthetic set

The set in `acceptance/retrieval/` has 36 queries over Arabic/English passages, a
scanned page, two real photographs from Wikimedia Commons (credits in
`acceptance/public/ATTRIBUTION.md`), 43 s of synthetic Syrian (Damascene)
Arabic speech (OpenAI `gpt-audio`), an excerpt and a tone. The audio queries are in
standard Arabic, so they also test fusha queries against dialect speech. Earlier
runs with robotic macOS speech and with standard-Arabic text gave the same figures.
Re-run on 2026-10-24 with the two photographs (the drawn images gave 0.84 and 0.94
overall, text to image 0.75 and 0.75) using `acceptance/eval_retrieval.py`, Ollama 0.40.5 and
`embeddinggemma-2:latest` on an Apple M2 with 16 GB:

| Query type | n | Recall@1 | Recall@5 |
| --- | --- | --- | --- |
| MSA, Syrian dialect, cross-lingual text | 21 | 1.00 | 1.00 |
| Text to image | 4 | 0.50 | 0.75 |
| Text to audio | 5 | 0.60 | 0.80 |
| Text to scanned page | 2 | 0.00 | 1.00 |
| All positive | 32 | 0.81 | 0.94 |

Negative queries topped out at 0.67 cosine against a 0.80 mean for positive top
hits, so a fixed score cut would not separate them. Median latency was 17 ms per
query, 700 ms per image and 500 ms per 30-second audio window. Scans are the
weakest path: the right page ranks in the top 5 but behind text passages.

The set is small and synthetic. It cannot rank close models, and it says nothing
about identity, independent corroboration or factual accuracy. Keep the PDQ and
audio-landmark tests independent: they measure reuse, not meaning.

Confidence: high on modality and API fit and on this set's results; unproven on
real handwritten records, field audio and archive scale.

## Speech to text model

Decision 2026-10-24: [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
1.2.1 (CTranslate2 4.8.3) with Whisper large-v3-turbo
(`mobiuslabsgmbh/faster-whisper-large-v3-turbo`, revision
`0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`, `model.bin` sha256 `e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da`,
1.5 GB), run inside the worker. No audio leaves the machine; the only network use is
the one-time weight download. Decoding: beam 5, Silero VAD (bundled with
faster-whisper), word timestamps, no conditioning on previous text (it lets Whisper
loop on a phrase). Language is detected on the first window; below 0.5 confidence
the recording is read as Arabic and the detected guess is recorded.

Measured 2026-10-24 on an Apple M2 (8 cores), CPU int8, with other agents' tests
loading the machine (load average 6 to 10 at the cleaner runs, above 100 earlier):

| Input | Result | Time |
| --- | --- | --- |
| `acceptance/data/witness_clip.ogg`, 33.7 s synthetic Damascene speech | Language `ar` 0.998; 4 segments; WER 0.217 (10 of 46 words) after folding diacritics, punctuation, alef, ya and ta marbuta | 16.8 s decoding in the worker (RTF 0.50); 23.9 s with language detection (RTF 0.71); model load 2 to 10 s once per process. Under load above 100 the same clip took 133 s (RTF 3.9) |
| `acceptance/public/jdeideh_2015.webm`, 19.8 s, no speech | Language guess `en` 0.59, no segments, run sealed with "no speech found" | 7.3 s (RTF 0.37) |
| `jdeideh_2017.webm`, 20.5 s, no speech | No segments | under 0.1 s decoding after VAD |

Nearly every error is dialect spelling pulled toward Modern Standard Arabic, not
mishearing: إنّو to انه, أهلو to اهله, وينو to وينه, شفتو to شفته, دوما to دومة,
بحرستا to بحرسته, قلّي to كلي, قدّام to جدام, تسعة to تسع. Names and the unit
(يوسف خالد العلي، الفرع تسعمية وتسع وتسعين) came through. The sentence naming
Yusuf starts at 9.94 s against 10.45 s in the script; Whisper merged the first two
sentences into one segment.

Limits: one short synthetic clip with a synthetic voice is not field audio (noise,
overlapping speakers, phone codecs). Place names in dialect spelling (بحرسته) will
not match an exact search for the standard form (حرستا). CTranslate2 has no Metal
backend, so Apple silicon runs on CPU; CUDA hosts use float16. Confidence: medium
on mechanics and on this clip; unmeasured on real recordings.

## Proactive agents on a hosted model (2026-10-24)

Measured once on a throwaway copy of the synthetic demo (case-demo-02), with the
agents' local route pointed at OpenRouter (`ROUTE_LOCAL_BASE_URL`,
`ROUTE_LOCAL_API_KEY`) and `AGENT_MODEL=google/gemini-3.1-flash-lite`. The product
keeps agents on the local route; this was a quality check on synthetic data only.

| Agent | Time | Tokens in / out | Result |
| --- | --- | --- | --- |
| Standing question "Was anyone held at Branch 999 after 2013?" | 55 s | 22.6k / 1.4k | 154 claims screened, 72 kept, 8 proposed, all meeting the question (standing-2 prompt); standing-1 proposed 14 with 3 wrong (same year, another prison, transferred out) |
| Contradiction hunter | 1 s | 0.5k / 0.1k | 1 candidate, 1 proposed with a usable question (contradictions-3); contradictions-2 wrote handles into the question and was dropped |
| Noticer, whole case | 19 s | 7.3k / 1.8k | 74 candidates, 16 judged (58 left for later checks), 10 shown, 6 screened as not related, 0 dropped; all 10 plausible, 4 new beyond existing proposals (intake sheet versions, voice note excerpt, transfer order spelling of Yousef, Yousef in case-demo-01); it called partial overlaps "identical" (noticer-1) |
| Gap finder | 5 s | 2.4k to 3.4k / 1.3k to 1.7k | 7 to 8 proposed from 17 gaps, 2 to 4 dropped for citing handles it was not given, 1 for a sensitive value |

One model, one synthetic case, judged by reading the output, not a scored set.
