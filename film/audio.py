"""Build the film's sound and timing: narration lines, the score, the mix, pacing.

    uv run python film/audio.py voice    # synthesise any line whose words changed
    uv run python film/audio.py plan     # place the lines; write their times everywhere
    uv run python film/audio.py check    # print the pacing figures; fail on a broken one
    uv run python film/audio.py timing   # print when each line starts and ends
    uv run python film/audio.py score    # generate the score (Google Lyria 2 on fal)
    uv run python film/audio.py mix      # place the lines over a steady bed, -16 LUFS
    uv run python film/audio.py vtt      # write film/captions.vtt, one cue per line

Reads film/narration.json: the voice, the lead before the first word, the tail after
the last, and each line with its group (a part of the film), the pause before it,
its caption text and, when numbers must be spelled out for the voice, the words
sent for synthesis (`say`). `plan` writes each line's start (`at`) and the film's
length back into narration.json, writes film/composition/assets/timing.js for the
picture, and sets data-start and data-duration on every clip in
composition/index.html that names its window with data-in and data-out.

A window bound is `@<line>` (the line's start), `@<line>$` (its end), `0` or `END`,
with an optional `+s` or `-s`, so the picture moves with the voice.

Writes film/audio/lines/<id>.mp3 and film/audio/lines.json (tracked: a new
synthesis never sounds the same twice), film/audio/score.m4a, and
film/build/mix.wav with its two stems, voice.wav and score.wav (not tracked).
Needs FAL_KEY for `voice` and `score`, ffmpeg for the sound steps.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

FILM = Path(__file__).resolve().parent
AUDIO = FILM / "audio"
LINES = AUDIO / "lines"
NARRATION = FILM / "narration.json"
SPEC = json.loads(NARRATION.read_text())
SCORE = AUDIO / "score.m4a"
MIX = FILM / "build" / "mix.wav"
COMPOSITION = FILM / "composition" / "index.html"
TIMING_JS = FILM / "composition" / "assets" / "timing.js"
BED_DB = -16  # the score under the voice, before the mix gain
SILENCE = "silencedetect=n=-45dB:d=0.08"  # what counts as no voice, for line edges

SCORE_PROMPT = (
    "sparse felt piano and soft sustained strings, calm documentary underscore, "
    "patient and quietly serious, slow tempo around 72 bpm, no drums, no percussion, no vocals"
)
SCORE_NEGATIVE = "drums, percussion, vocals, choir, loud, aggressive, electronic beat"
SCORE_SEEDS = [11, 12, 13, 14, 15]
FADE = 4.0  # crossfade between score parts, seconds

# Pacing rules `check` enforces, in seconds and words a minute.
LEAD = (0.8, 1.5)  # picture and score before the first word
TAIL = (1.8, 3.0)  # after the last word
STORY_GAP = 1.2  # silence between two groups
WPM = (135, 165)  # inside a group, from its first word to its last
SCREEN_MIN = 3.0  # a screen on view at least this long
SCREEN_AFTER = 0.8  # and held this long after the last line spoken over it


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def duration(path: Path) -> float:
    return float(
        run(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        )
    )


def spoken(line) -> str:
    return line.get("say", line["text"])


def words(text: str) -> int:
    return sum(1 for w in text.split() if re.search(r"\w", w))


def line_key(text: str) -> str:
    """What a synthesis depends on: the words and the voice settings."""
    return hashlib.sha256(
        json.dumps([text, SPEC["voice"]], sort_keys=True).encode()
    ).hexdigest()


def edges(path: Path) -> tuple[float, float]:
    """Where the voice starts and stops inside a synthesised file."""
    total = duration(path)
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", SILENCE, "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    onset = ends[0] if starts and starts[0] < 0.02 and ends else 0.0
    offset = starts[-1] if starts and (len(ends) < len(starts)) else total
    if starts and len(ends) == len(starts) and abs(ends[-1] - total) < 0.05:
        offset = starts[-1]
    return max(onset - 0.03, 0.0), min(offset + 0.05, total)


def synthesise(text: str) -> bytes:
    """One line through fal's ElevenLabs route: the voice, stability, similarity
    and speed of narration.json, and nothing but the narration text."""
    v = SPEC["voice"]
    body = json.dumps(
        {
            "text": text,
            "voice": v["voice_name"],
            "stability": v["stability"],
            "similarity_boost": v["similarity_boost"],
            "speed": v["speed"],
        }
    ).encode()
    req = urllib.request.Request(
        v["endpoint"],
        data=body,
        headers={
            "Authorization": f"Key {os.environ['FAL_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        url = json.load(r)["audio"]["url"]
    with urllib.request.urlopen(url, timeout=180) as r:
        return r.read()


def voice():
    LINES.mkdir(parents=True, exist_ok=True)
    index_path = AUDIO / "lines.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    sent = 0
    for line in SPEC["lines"]:
        key = line_key(spoken(line))
        path = LINES / f"{line['id']}.mp3"
        if index.get(line["id"], {}).get("key") == key and path.exists():
            continue
        path.write_bytes(synthesise(spoken(line)))
        sent += len(spoken(line))
        start, stop = edges(path)
        index[line["id"]] = {
            "key": key,
            "text": spoken(line),
            "start": round(start, 3),
            "seconds": round(stop - start, 3),
        }
        print("voiced", line["id"], index[line["id"]]["seconds"])
        index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")
    ids = {line["id"] for line in SPEC["lines"]}
    for stale in set(index) - ids:
        index.pop(stale)
        (LINES / f"{stale}.mp3").unlink(missing_ok=True)
    index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")
    print("characters sent", sent)


def lengths() -> dict:
    return json.loads((AUDIO / "lines.json").read_text())


def rows() -> list[dict]:
    index = lengths()
    out = []
    for line in SPEC["lines"]:
        at = line["at"]
        out.append(
            {
                **line,
                "end": round(at + index[line["id"]]["seconds"], 2),
                "start": index[line["id"]]["start"],
            }
        )
    return out


def timing():
    """Each line's start and end, and the film's length. A line that would run
    into the next is an error: change a pause, never speed the voice up."""
    rs = rows()
    for a, b in zip(rs, rs[1:], strict=False):
        if a["end"] + 0.15 > b["at"]:
            raise SystemExit(
                f"line {a['id']} ends at {a['end']}, line {b['id']} starts at {b['at']}"
            )
    return rs, SPEC["length"]


# Picture windows -----------------------------------------------------------

BOUND = re.compile(r"^(?:@(\w+)(\$)?|(0|END))([+-][\d.]+)?$")


def resolve(bound: str, times: dict, length: float) -> float:
    m = BOUND.match(bound.strip())
    if not m:
        raise SystemExit(f"bad window bound {bound!r}")
    line, end, fixed, shift = m.groups()
    if line:
        if line not in times:
            raise SystemExit(f"window bound {bound!r} names no line")
        base = times[line][1 if end else 0]
    else:
        base = length if fixed == "END" else 0.0
    return round(base + float(shift or 0), 3)


def plan():
    """Line starts from the pauses, then the film's length, the picture's
    timing file and the clip attributes of the composition."""
    index = lengths()
    t = None
    for line in SPEC["lines"]:
        t = SPEC["lead"] if t is None else t + line["pause"]
        line["at"] = round(t, 2)
        t = line["at"] + index[line["id"]]["seconds"]
    SPEC["length"] = round(t + SPEC["tail"], 2)
    NARRATION.write_text(json.dumps(SPEC, indent=1, ensure_ascii=False) + "\n")
    times = {r["id"]: [r["at"], r["end"]] for r in rows()}
    length = SPEC["length"]
    TIMING_JS.write_text(
        "// Written by film/audio.py plan from narration.json; do not edit.\n"
        "window.FILM = "
        + json.dumps({"lines": times, "length": length}, separators=(",", ":"))
        + ";\n"
    )
    html = COMPOSITION.read_text()

    def clip(m):
        tag = m.group(0)
        if 'class="' not in tag or not re.search(r'class="[^"]*\bclip\b', tag):
            return tag
        a = re.search(r'data-in="([^"]+)"', tag)
        b = re.search(r'data-out="([^"]+)"', tag)
        if not (a and b):
            return tag
        start = resolve(a.group(1), times, length)
        stop = min(resolve(b.group(1), times, length) + 0.3, length)
        tag = re.sub(r'data-start="[^"]*"', f'data-start="{start:g}"', tag)
        return re.sub(
            r'data-duration="[^"]*"', f'data-duration="{round(stop - start, 3):g}"', tag
        )

    html = re.sub(r"<(?:div|video|audio)\b[^>]*>", clip, html)
    html = re.sub(
        r'(id="root"[^>]*data-duration=")[^"]*"', rf'\g<1>{length:g}"', html, count=1
    )
    COMPOSITION.write_text(html)
    print("length", length, "s,", len(times), "lines")


class Windows(HTMLParser):
    """Every text item and screen in the composition with the window it shows in."""

    INLINE = {"b", "span", "small", "i", "em", "strong", "br", "sup"}
    VOID = {"img", "br", "meta", "source", "link", "input"}

    def __init__(self, times, length):
        super().__init__()
        self.times, self.length = times, length
        self.stack = []  # (tag, window, item)
        self.items, self.screens = [], []
        self.skip = 0

    def window(self, attrs):
        outer = self.stack[-1][1] if self.stack else (0.0, self.length)
        a, b = attrs.get("data-in"), attrs.get("data-out")
        lo = resolve(a, self.times, self.length) if a else outer[0]
        hi = resolve(b, self.times, self.length) if b else outer[1]
        return (max(lo, outer[0]), min(hi, outer[1]))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
            return
        win = self.window(attrs)
        if "data-screen" in attrs:
            self.screens.append(
                {
                    "name": attrs.get("id") or attrs["data-screen"],
                    "win": win,
                    "bounds": (attrs.get("data-in"), attrs.get("data-out")),
                }
            )
        if tag in self.VOID:
            return
        item = None
        if tag in self.INLINE and self.stack:
            item = self.stack[-1][2]
        if item is None:
            item = {"id": attrs.get("id", tag), "text": "", "win": win}
            self.items.append(item)
        self.stack.append((tag, win, item))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
            return
        if tag in self.VOID:
            return
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        if self.skip or not self.stack or not data.strip():
            return
        self.stack[-1][2]["text"] += " " + data.strip()


def check() -> bool:
    """Print the pacing figures and say which rule a figure breaks."""
    rs, length = timing()
    times = {r["id"]: (r["at"], r["end"]) for r in rs}
    bad = []

    def rule(ok, what):
        if not ok:
            bad.append(what)
        return "" if ok else "   <-- FAIL"

    lead, tail = rs[0]["at"], round(length - rs[-1]["end"], 2)
    print(
        f"lead before the first word {lead:.2f} s{rule(LEAD[0] <= lead <= LEAD[1], f'lead {lead}')}"
    )
    print(
        f"tail after the last word   {tail:.2f} s{rule(TAIL[0] <= tail <= TAIL[1], f'tail {tail}')}"
    )
    print(f"length                     {length:.2f} s")
    print("\ngroup      start     end   words   wpm   gap before")
    groups = []
    for r in rs:
        if not groups or groups[-1][0] != r["group"]:
            groups.append((r["group"], []))
        groups[-1][1].append(r)
    prev_end = None
    for name, members in groups:
        n = sum(words(m["text"]) for m in members)
        span = members[-1]["end"] - members[0]["at"]
        wpm = n / span * 60
        gap = None if prev_end is None else members[0]["at"] - prev_end
        flag = rule(WPM[0] <= wpm <= WPM[1], f"{name} at {wpm:.0f} wpm")
        if gap is not None:
            flag += rule(gap >= STORY_GAP, f"gap before {name} {gap:.2f} s")
        gaps = "" if gap is None else f"{gap:6.2f}"
        print(
            f"{name:8} {members[0]['at']:7.2f} {members[-1]['end']:7.2f} {n:7d} {wpm:5.0f} {gaps:>9}{flag}"
        )
        prev_end = members[-1]["end"]

    def speaking(t):
        return next((r["id"] for r in rs if r["at"] + 0.05 < t < r["end"] - 0.05), None)

    parser = Windows(times, length)
    parser.feed(COMPOSITION.read_text())
    print("\ntext on screen               words   shown   needs")
    for item in parser.items:
        n = words(item["text"])
        if not n:
            continue
        lo, hi = item["win"]
        shown, need = hi - lo, n / 3 + 1
        flag = rule(
            shown >= need - 0.01, f"text {item['id']} {shown:.1f} < {need:.1f} s"
        )
        line = speaking(hi) if hi < length - 0.01 else None
        flag += rule(line is None, f"text {item['id']} leaves while line {line} plays")
        print(f"{item['id'][:28]:28} {n:5d} {shown:7.2f} {need:7.2f}{flag}")
    print("\nscreen                     shown   after its last line")
    for s in parser.screens:
        lo, hi = s["win"]
        over = [r for r in rs if r["at"] < hi and r["end"] > lo]
        after = hi - over[-1]["end"] if over else None
        flag = rule(hi - lo >= SCREEN_MIN, f"screen {s['name']} shown {hi - lo:.1f} s")
        if after is not None and hi < length - 0.01:
            flag += rule(
                after >= SCREEN_AFTER, f"screen {s['name']} held {after:.1f} s after"
            )
        line = speaking(hi) if hi < length - 0.01 else None
        flag += rule(line is None, f"screen {s['name']} leaves while line {line} plays")
        aft = "-" if after is None else f"{after:.2f}"
        print(f"{s['name'][:24]:24} {hi - lo:7.2f} {aft:>8}{flag}")
    print()
    if bad:
        print(f"{len(bad)} broken:")
        for b in bad:
            print("  " + b)
        return False
    print("every pacing rule holds")
    return True


# Score and mix -------------------------------------------------------------


def score():
    parts = []
    with tempfile.TemporaryDirectory() as tmp:
        for seed in SCORE_SEEDS:
            body = json.dumps(
                {
                    "prompt": SCORE_PROMPT,
                    "negative_prompt": SCORE_NEGATIVE,
                    "seed": seed,
                }
            ).encode()
            req = urllib.request.Request(
                "https://fal.run/fal-ai/lyria2",
                data=body,
                headers={
                    "Authorization": f"Key {os.environ['FAL_KEY']}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=300) as r:
                url = json.load(r)["audio"]["url"]
            part = Path(tmp) / f"part{seed}.wav"
            urllib.request.urlretrieve(url, part)
            parts.append(part)
            print("score part", seed, round(duration(part), 1), "s")
        inputs = [x for p in parts for x in ("-i", str(p))]
        graph, last = "", "[0:a]"
        for i in range(1, len(parts)):
            out = f"[x{i}]"
            graph += f"{last}[{i}:a]acrossfade=d={FADE}:c1=tri:c2=tri{out};"
            last = out
        run(
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            *inputs,
            "-filter_complex",
            graph.rstrip(";"),
            "-map",
            last,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(SCORE),
        )
    print("score", SCORE, round(duration(SCORE), 1), "s")


def loudness(path: Path) -> dict:
    err = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    return json.loads(err[err.rindex("{") : err.rindex("}") + 1])


def mix():
    """Voice at its natural speed, a steady score bed under it, one gain to -16 LUFS.

    Writes film/build/voice.wav and film/build/score.wav (each stem at the gain it has
    in the mix) and film/build/mix.wav."""
    rs, total = timing()
    rate = 48000
    # The voice is one track laid end to end: a lead-in of silence, then each line
    # from where its voice starts, padded with silence up to the next line's start.
    # Sample counts, not timestamps, place the lines. The mono line goes to both
    # channels unchanged.
    inputs, chains, labels = [], [], []
    starts = [r["at"] for r in rs] + [total]
    for i, row in enumerate(rs):
        inputs += ["-i", str(LINES / f"{row['id']}.mp3")]
        span = int(round((starts[i + 1] - starts[i]) * rate))
        chains.append(
            f"[{i}:a]atrim=start={row['start']},asetpts=N/SR/TB,"
            f"aresample={rate},aformat=channel_layouts=stereo,"
            f"apad=whole_len={span},atrim=end_sample={span},asetpts=N/SR/TB[v{i}]"
        )
        labels.append(f"[v{i}]")
    n = len(rs)
    lead = int(round(rs[0]["at"] * rate))
    inputs += ["-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=stereo"]
    chains.append(f"[{n}:a]atrim=end_sample={lead},asetpts=N/SR/TB[lead]")
    graph = (
        ";".join(chains)
        + ";[lead]"
        + "".join(labels)
        + f"concat=n={n + 1}:v=0:a=1[out]"
    )
    build = MIX.parent
    build.mkdir(parents=True, exist_ok=True)
    voice_raw, bed_raw, mix_raw = (
        build / f"{k}_raw.wav" for k in ("voice", "score", "mix")
    )
    run(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        *inputs,
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        str(voice_raw),
    )
    # A steady bed: one fixed level the whole way, no ducking, so nothing pumps
    # behind the words. A film longer than the score plays it again from the top,
    # joined with the same crossfade the score's own parts use.
    plays = int(total // (duration(SCORE) - FADE)) + 1
    joins = (
        "[0:a]anull[s0];"
        + "".join(
            f"[s{i - 1}][{i}:a]acrossfade=d={FADE}:c1=tri:c2=tri[s{i}];"
            for i in range(1, plays)
        )
        if plays > 1
        else "[0:a]anull[s0];"
    )
    run(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        *[x for _ in range(plays) for x in ("-i", str(SCORE))],
        "-filter_complex",
        joins
        + f"[s{plays - 1}]aresample={rate},aformat=channel_layouts=stereo,apad,atrim=0:{total},"
        f"volume={BED_DB}dB,afade=t=in:d=2,afade=t=out:st={total - 4}:d=4[bed]",
        "-map",
        "[bed]",
        str(bed_raw),
    )
    run(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(voice_raw),
        "-i",
        str(bed_raw),
        "-filter_complex",
        "amix=inputs=2:normalize=0",
        str(mix_raw),
    )
    gain = -16 - float(loudness(mix_raw)["input_i"])
    for src, dst in ((voice_raw, build / "voice.wav"), (bed_raw, build / "score.wav")):
        run(
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-af",
            f"volume={gain:.2f}dB",
            "-c:a",
            "pcm_f32le",
            str(dst),
        )
    run(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(mix_raw),
        "-af",
        f"volume={gain:.2f}dB,alimiter=limit=0.708:level=false",
        str(MIX),
    )
    for p in (voice_raw, bed_raw, mix_raw):
        p.unlink()
    print("mix", MIX, total, "s, gain", round(gain, 2), "dB")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    if step == "voice":
        voice()
    elif step == "plan":
        plan()
    elif step == "check":
        sys.exit(0 if check() else 1)
    elif step == "timing":
        rs, total = timing()
        for r in rs:
            print(f"{r['id']:4} {r['at']:7.2f}  {r['end']:7.2f}  {r['text']}")
        print("length", total)
    elif step == "vtt":
        rs, _ = timing()

        def stamp(s):
            return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}"

        cues = [
            f"{stamp(r['at'])} --> {stamp(r['end'] + 0.2)}\n{r['text']}" for r in rs
        ]
        (FILM / "captions.vtt").write_text("WEBVTT\n\n" + "\n\n".join(cues) + "\n")
        print("captions", FILM / "captions.vtt", len(cues), "cues")
    elif step == "score":
        score()
    elif step == "mix":
        mix()
    else:
        sys.exit(__doc__)
