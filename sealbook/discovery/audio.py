"""Local audio landmarks and time alignment.

Selection stays local in time so a clip and its parent retain the same landmarks.
"""

from collections import Counter, defaultdict

import numpy as np

SAMPLE_RATE, WINDOW, HOP = 8000, 1024, 512
DENSITY = 15


def landmarks(samples):
    if len(samples) < WINDOW * 2:
        return []
    frames = np.lib.stride_tricks.sliding_window_view(samples, WINDOW)[::HOP]
    bands = np.linspace(0, WINDOW // 2 + 1, 7).astype(int)
    found = []
    for start in range(0, len(frames), 512):
        spectrum = np.log1p(
            np.abs(
                np.fft.rfft(frames[start : start + 512] * np.hanning(WINDOW), axis=1)
            )
        )
        for low, high in zip(bands[:-1], bands[1:], strict=True):
            best = spectrum[:, low:high].argmax(axis=1) + low
            strength = spectrum[np.arange(len(spectrum)), best]
            found.extend(
                (int(start + i), int(best[i]), float(strength[i]))
                for i in np.flatnonzero(strength > 0.05)
            )
    found.sort(key=lambda row: (row[0], row[1]))
    times = np.array([row[0] for row in found])
    buckets = defaultdict(list)
    for time, frequency, strength in found:
        first, last = np.searchsorted(times, [time + 1, time + 64])
        targets = [row for row in found[first:last] if abs(row[1] - frequency) <= 128]
        for target_time, target_frequency, _ in sorted(
            targets, key=lambda row: -row[2]
        )[:5]:
            packed = (frequency << 22) | (target_frequency << 12) | (target_time - time)
            buckets[time * HOP // SAMPLE_RATE].append((strength, packed, time))
    out = []
    for rows in buckets.values():
        out.extend(
            {"hash": packed, "time": time}
            for _, packed, time in sorted(rows, key=lambda row: -row[0])[:DENSITY]
        )
    return sorted(out, key=lambda row: (row["time"], row["hash"]))


def align(query, reference):
    index = defaultdict(set)
    for mark in reference:
        index[mark["hash"]].add(mark["time"])
    votes = Counter(
        ref_time - mark["time"] for mark in query for ref_time in index[mark["hash"]]
    )
    if not votes:
        return None
    offset, count = votes.most_common(1)[0]
    return {
        "offset_ms": round(offset * HOP * 1000 / SAMPLE_RATE),
        "aligned_landmarks": count,
        "shared_landmarks": sum(votes.values()),
        "concentration": [count, sum(votes.values())],
    }
