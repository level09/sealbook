"""Score the app's fingerprints and copy proposals on the old-footage case.

  uv run python acceptance/cases/old-footage/eval.py [--json]

No database. Each clip is fingerprinted with sealbook.discovery.compute.fingerprint
and arrives in posting-date order, as the seeder ingests it. Against the clips already
held, candidates are retrieved the way sealbook.discovery.service.fingerprint does
(up to 32 sampled frames, the 8 nearest stored frames each within PDQ_DISTANCE; audio
hashes ranked by distinct shared hashes; RRF; MEDIA_CANDIDATES). Exact search stands
in for the HNSW index. A pair is proposed when compute.match_frames or audio.align
measures something, as the app does, and banded strong or weak by
evidence.copy_reason. Scores hold for this set only.
"""

import json
import sys
from collections import Counter
from pathlib import Path

from sealbook.discovery import audio, compute
from sealbook.discovery.evidence import copy_reason
from sealbook.settings import Config

HERE = Path(__file__).parent
FILES = HERE / "files"


def fingerprints(case):
    return {
        key: compute.fingerprint(
            FILES / s["file"], "video/mp4", Config.FINGERPRINT_MAX_SECONDS
        )
        for key, s in case["sources"].items()
    }


def usable(frames):
    return list(
        {f["hash"]: f for f in frames if f["quality"] >= Config.PDQ_QUALITY}.values()
    )


def distance(a, b):
    return (int(a, 16) ^ int(b, 16)).bit_count()


def candidates(query, held, prints):
    """The service's candidate retrieval, over the clips already held."""
    frames, marks = prints[query]["visual"], prints[query]["audio"]
    stored = [
        (key, f)
        for key in held
        for f in prints[key]["visual"]
        if f["quality"] >= Config.PDQ_QUALITY
    ]
    picture = {}
    sample = usable(frames)
    for frame in sample[:: max(1, (len(sample) + 31) // 32)]:
        nearest = sorted(
            ((distance(frame["hash"], f["hash"]), key) for key, f in stored),
            key=lambda row: row[0],
        )[:8]
        for measured, key in nearest:
            if measured <= Config.PDQ_DISTANCE:
                picture[key] = min(picture.get(key, 256), measured)
    pictures = sorted(picture, key=picture.get)[: Config.MEDIA_CANDIDATES]
    sounds = []
    if marks:
        hashes = sorted({m["hash"] for m in marks})
        hashes = set(hashes[:: max(1, (len(hashes) + 4095) // 4096)])
        shared = Counter(
            {
                key: len(hashes & {m["hash"] for m in prints[key]["audio"]})
                for key in held
            }
        )
        sounds = [key for key, n in shared.most_common(Config.MEDIA_CANDIDATES) if n]
    return compute.rrf(pictures, sounds)[: Config.MEDIA_CANDIDATES]


def proposals(case, prints):
    order = sorted(case["sources"], key=lambda k: (case["sources"][k]["date"], k))
    found = {}
    for i, query in enumerate(order):
        for key in candidates(query, order[:i], prints):
            picture = compute.match_frames(
                prints[query]["visual"],
                prints[key]["visual"],
                Config.PDQ_DISTANCE,
                Config.PDQ_QUALITY,
            )
            sound = audio.align(prints[query]["audio"], prints[key]["audio"])
            if picture or sound:
                value = {"picture": picture, "sound": sound}
                kind, why, strength = copy_reason(value)
                found[frozenset((query, key))] = {
                    "query": query,
                    "candidate": key,
                    "kind": kind,
                    "strength": strength,
                    "why": why,
                    "picture": picture
                    and {
                        k: picture[k]
                        for k in (
                            "query_coverage",
                            "candidate_coverage",
                            "max_distance",
                        )
                    },
                    "sound": sound,
                }
    return found


def linked(pairs, start):
    seen, todo = {start}, [start]
    while todo:
        node = todo.pop()
        for pair in pairs:
            if node in pair:
                (other,) = pair - {node}
                if other not in seen:
                    seen.add(other)
                    todo.append(other)
    return seen


def score(case, prints=None):
    prints = prints or fingerprints(case)
    found = proposals(case, prints)
    truth = case["truth"]
    family = {truth["original"], *(c["file"] for c in truth["copies"])}
    tied = linked(
        [k for k, v in found.items() if v["strength"] == "strong"], truth["original"]
    )
    rows = []
    for copy in truth["copies"]:
        p = found.get(frozenset((copy["file"], copy["of"])))
        rows.append(
            {
                "copy": copy["file"],
                "of": copy["of"],
                "transformations": copy["transformations"],
                "found": bool(p),
                "by": p and "+".join(x for x in ("picture", "sound") if p[x]),
                "strength": p and p["strength"],
                "picture": p and p["picture"],
                "sound": p and p["sound"],
                "tied_to_original": copy["file"] in tied,
            }
        )
    false = [
        {"pair": sorted(pair), **{k: v[k] for k in ("strength", "why")}}
        for pair, v in found.items()
        if not pair <= family
    ]
    by_transform = {}
    for row in rows:
        for t in row["transformations"]:
            hit, total = by_transform.get(t, (0, 0))
            by_transform[t] = (hit + row["tied_to_original"], total + 1)
    negatives = [*truth["lookalikes"], *truth["pile"]]
    return {
        "params": {
            "distance": Config.PDQ_DISTANCE,
            "quality": Config.PDQ_QUALITY,
            "candidates": Config.MEDIA_CANDIDATES,
        },
        "clips": len(case["sources"]),
        "recall": [sum(r["found"] for r in rows), len(rows)],
        "recall_strong": [sum(r["strength"] == "strong" for r in rows), len(rows)],
        "tied_to_original": [sum(r["tied_to_original"] for r in rows), len(rows)],
        "by_transformation": {t: list(v) for t, v in sorted(by_transform.items())},
        "copies": rows,
        "false_matches": false,
        "false_strong": sum(f["strength"] == "strong" for f in false),
        "negatives": len(negatives),
        "lookalikes_matched": sorted(
            {k for f in false for k in f["pair"] if k in truth["lookalikes"]}
        ),
        "misses": sorted(r["copy"] for r in rows if not r["tied_to_original"]),
    }


def main():
    case = json.loads((HERE / "case.json").read_text(encoding="utf-8"))
    report = score(case)
    if "--json" in sys.argv:
        print(json.dumps(report, indent=1))
        return
    print(
        f"{report['clips']} clips; copies matched to their source "
        f"{report['recall'][0]}/{report['recall'][1]} (strong {report['recall_strong'][0]}); "
        f"tied to the original {report['tied_to_original'][0]}/{report['tied_to_original'][1]}; "
        f"false matches {len(report['false_matches'])} (strong {report['false_strong']}) "
        f"across {report['negatives']} non-copies"
    )
    for r in report["copies"]:
        pic = r["picture"] and f"picture {r['picture']['query_coverage']}"
        snd = r["sound"] and (
            f"sound {r['sound']['aligned_landmarks']}/{r['sound']['shared_landmarks']}"
        )
        print(
            f"  {r['copy']:18} of {r['of']:12} {'+'.join(r['transformations']):48} "
            f"{'found' if r['found'] else 'MISSED':6} {r['strength'] or '':6} "
            f"{'tied' if r['tied_to_original'] else 'untied':6} {pic or ''} {snd or ''}"
        )
    for f in report["false_matches"]:
        print(f"  false {f['pair']} {f['strength']}: {f['why']}")
    print("by transformation:", report["by_transformation"])


if __name__ == "__main__":
    main()
