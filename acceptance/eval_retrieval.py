"""Measure retrieval on the synthetic gold set in acceptance/retrieval/.

Needs an Ollama /api/embed server with a multimodal embedding model:
  EMBEDDING_URL=http://127.0.0.1:11434 EMBEDDING_MODEL=embeddinggemma-2 \
    uv run python acceptance/eval_retrieval.py
Prints one JSON report. Measures retrieval on this set only.
"""

import base64
import io
import json
import os
import statistics
import sys
import time
import wave
from pathlib import Path

import httpx2
import numpy as np

DATA = Path(__file__).parent / "retrieval"
PUBLIC = Path(__file__).parent / "public"  # openly licensed photos, see ATTRIBUTION.md
DOC_PREFIX = "title: none | text: "
QUERY_PREFIX = "task: search result | query: "
WINDOW_MS, HOP_MS, RATE = 30_000, 25_000, 16_000
NEAR_IDENTICAL = 0.999


def b64(data):
    return base64.b64encode(data).decode()


def image_input(path):
    return {"image": b64(path.read_bytes())}


def audio_input(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return {"audio": b64(buf.getvalue())}


def corpus():
    """Items: file, unit, start, end, input. Text is one item per line, audio per window."""
    manifest = json.loads((DATA / "queries.json").read_text(encoding="utf-8"))
    items = []
    for name in manifest["corpus"]["text"]:
        text = (DATA / name).read_text(encoding="utf-8")
        pos = 0
        for line in text.split("\n"):
            if line:
                items.append(
                    {
                        "file": name,
                        "kind": "text",
                        "unit": "chars",
                        "start": pos,
                        "end": pos + len(line),
                        "input": DOC_PREFIX + line,
                    }
                )
            pos += len(line) + 1
    for name in manifest["corpus"]["image"]:
        items.append(
            {
                "file": name,
                "kind": "image",
                "unit": "whole",
                "input": image_input(
                    next(d / name for d in (DATA, PUBLIC) if (d / name).exists())
                ),
            }
        )
    for name in manifest["corpus"]["audio"]:
        with wave.open(str(DATA / name)) as w:
            assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (
                1,
                RATE,
                2,
            ), name
            pcm = w.readframes(w.getnframes())
        total = len(pcm) // 2 * 1000 // RATE
        start = 0
        while True:
            end = min(start + WINDOW_MS, total)
            items.append(
                {
                    "file": name,
                    "kind": "audio",
                    "unit": "ms",
                    "start": start,
                    "end": end,
                    "input": audio_input(pcm[start * 32 : end * 32]),
                }
            )
            if end == total:
                break
            start += HOP_MS
    return manifest, items


class Embedder:
    def __init__(self, url, model):
        self.url = url.rstrip("/") + "/api/embed"
        self.model = model
        self.client = httpx2.Client(timeout=300)
        self.latency_ms = []

    def __call__(self, value):
        t = time.perf_counter()
        r = self.client.post(
            self.url, json={"model": self.model, "input": [value], "dimensions": 768}
        )
        self.latency_ms.append((time.perf_counter() - t) * 1000)
        r.raise_for_status()
        vec = np.asarray(r.json()["embeddings"][0], dtype=np.float64)
        return vec / np.linalg.norm(vec)


def refuse_ignored_media(items, vecs):
    """Distinct images or audio with near-identical vectors mean the server dropped the media."""
    for kind in ("image", "audio"):
        idx = [i for i, it in enumerate(items) if it["kind"] == kind]
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                cos = float(vecs[idx[a]] @ vecs[idx[b]])
                if cos > NEAR_IDENTICAL:
                    x, y = items[idx[a]], items[idx[b]]
                    sys.exit(
                        f"refusing: {kind} items {x['file']}[{x['start'] if 'start' in x else ''}] and "
                        f"{y['file']}[{y['start'] if 'start' in y else ''}] have cosine {cos:.6f}; "
                        "the server is probably ignoring media"
                    )


def overlaps(item, rel):
    if rel["unit"] == "whole":
        return True  # whole image item covers any bbox; no sub-image localization
    if rel["unit"] == "bbox":
        return True
    return item["start"] < rel["end"] and rel["start"] < item["end"]


def file_ranking(items, scores):
    """Best item per file, files ordered by that score."""
    best = {}
    for i in np.argsort(-scores):
        best.setdefault(items[i]["file"], i)
    return list(best.items())  # (file, item index)


def evaluate(queries, items, vecs, embed):
    mat = np.stack(vecs)
    rows = []
    for q in queries:
        scores = mat @ embed(QUERY_PREFIX + q["text"])
        ranking = file_ranking(items, scores)
        top_file, top_i = ranking[0]
        row = {
            "id": q["id"],
            "type": q["type"],
            "top_file": top_file,
            "top_score": round(float(scores[top_i]), 4),
        }
        relevant = {r["file"] for r in q["relevant"]}
        if relevant:
            ranks = [n for n, (f, _) in enumerate(ranking, 1) if f in relevant]
            row["rank"] = ranks[0]
            row["location_hit"] = any(
                r["file"] == top_file and overlaps(items[top_i], r)
                for r in q["relevant"]
            )
        rows.append(row)
    return rows


def summarize(rows):
    pos = [r for r in rows if "rank" in r]
    neg = [r for r in rows if "rank" not in r]

    def agg(rs):
        n = len(rs)
        return {
            "n": n,
            "recall_at_1": round(sum(r["rank"] <= 1 for r in rs) / n, 3),
            "recall_at_5": round(sum(r["rank"] <= 5 for r in rs) / n, 3),
            "mrr": round(sum(1 / r["rank"] for r in rs) / n, 3),
            "location_hit_rate": round(sum(r["location_hit"] for r in rs) / n, 3),
        }

    out = {"positive": agg(pos), "by_type": {}}
    for t in sorted({r["type"] for r in pos}):
        out["by_type"][t] = agg([r for r in pos if r["type"] == t])
    pos_scores = [r["top_score"] for r in pos]
    out["negative"] = {
        "n": len(neg),
        "top_score_max": max(r["top_score"] for r in neg),
        "top_score_mean": round(statistics.mean(r["top_score"] for r in neg), 4),
        "positive_top_score_mean": round(statistics.mean(pos_scores), 4),
    }
    return out


def main():
    url, model = os.environ.get("EMBEDDING_URL"), os.environ.get("EMBEDDING_MODEL")
    if not url or not model:
        sys.exit("set EMBEDDING_URL and EMBEDDING_MODEL (an Ollama /api/embed server)")
    manifest, items = corpus()
    embed = Embedder(url, model)
    vecs = [embed(it["input"]) for it in items]
    refuse_ignored_media(items, vecs)
    corpus_latency = {k: [] for k in ("text", "image", "audio")}
    for it, ms in zip(items, embed.latency_ms, strict=True):
        corpus_latency[it["kind"]].append(ms)
    rows = evaluate(manifest["queries"], items, vecs, embed)

    def lat(xs):
        xs = sorted(xs)
        return {
            "n": len(xs),
            "median_ms": round(statistics.median(xs), 1),
            "max_ms": round(xs[-1], 1),
        }

    report = dict(
        model=model,
        items={k: sum(i["kind"] == k for i in items) for k in corpus_latency},
        dimensions=len(vecs[0]),
        **summarize(rows),
        corpus_latency={k: lat(v) for k, v in corpus_latency.items() if v},
        query_latency=lat(embed.latency_ms[len(items) :]),
        queries=rows,
        note="Retrieval on this synthetic set only; not identity or factual accuracy.",
    )
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
