"""Build the film's sound: narration lines, the score and the mix.

    uv run python film/audio.py voice    # synthesise any line whose text changed
    uv run python film/audio.py timing   # print when each line starts, and the length
    uv run python film/audio.py score    # generate the score (Google Lyria 2 on fal)
    uv run python film/audio.py mix      # place the lines, duck the score, -16 LUFS
    uv run python film/audio.py vtt      # write film/captions.vtt, one cue per line

Reads film/narration.json: the voice, the mix tempo and each line with the pause
before it (`gap`, default 0.45 s). Writes film/audio/lines/<id>.mp3 and
film/audio/lines.json and film/audio/score.m4a (all tracked: small, and a new
synthesis never sounds the same twice), and film/build/mix.wav (not tracked).
Needs ELEVENLABS_API_KEY for `voice`, FAL_KEY for `score`, ffmpeg for all steps.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

FILM = Path(__file__).resolve().parent
AUDIO = FILM / "audio"
LINES = AUDIO / "lines"
SPEC = json.loads((FILM / "narration.json").read_text())
SCORE = AUDIO / "score.m4a"
MIX = FILM / "build" / "mix.wav"
START, GAP, TAIL = 0.8, 0.45, 2.5

SCORE_PROMPT = (
    "sparse felt piano and soft sustained strings, calm documentary underscore, "
    "patient and quietly serious, slow tempo around 72 bpm, no drums, no percussion, no vocals"
)
SCORE_NEGATIVE = "drums, percussion, vocals, choir, loud, aggressive, electronic beat"
SCORE_SEEDS = [11, 12, 13, 14, 15]
FADE = 4.0  # crossfade between score parts, seconds


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


def line_key(text: str) -> str:
    """What a synthesis depends on: the words and the voice, not the mix tempo."""
    v = {k: x for k, x in SPEC["voice"].items() if not k.startswith("tempo")}
    return hashlib.sha256(json.dumps([text, v], sort_keys=True).encode()).hexdigest()


def voice():
    v = SPEC["voice"]
    LINES.mkdir(parents=True, exist_ok=True)
    index_path = AUDIO / "lines.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    for line in SPEC["lines"]:
        key = line_key(line["text"])
        path = LINES / f"{line['id']}.mp3"
        if index.get(line["id"], {}).get("key") == key and path.exists():
            continue
        body = json.dumps(
            {
                "text": line["text"],
                "model_id": v["model_id"],
                "voice_settings": v["voice_settings"],
            }
        ).encode()
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{v['voice_id']}?output_format={v['output_format']}",
            data=body,
            headers={
                "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req) as r:
            path.write_bytes(r.read())
        index[line["id"]] = {
            "key": key,
            "text": line["text"],
            "seconds": round(duration(path), 2),
        }
        print("voiced", line["id"], index[line["id"]]["seconds"])
    index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")


def timing():
    """Each line's start and end in the film, after the tempo, and the film's length."""
    tempo = SPEC["voice"].get("tempo", 1.0)
    index = json.loads((AUDIO / "lines.json").read_text())
    t, rows = START, []
    for i, line in enumerate(SPEC["lines"]):
        if i:
            t += line.get("gap", GAP)
        length = index[line["id"]]["seconds"] / tempo
        rows.append(
            {
                "id": line["id"],
                "at": round(t, 2),
                "end": round(t + length, 2),
                "text": line["text"],
            }
        )
        t += length
    return rows, round(t + TAIL, 1)


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


def mix():
    rows, total = timing()
    tempo = SPEC["voice"].get("tempo", 1.0)
    # The voice is one track laid end to end: a lead-in of silence, then each line
    # padded with silence up to the next line's start. Sample counts, not timestamps,
    # place the lines, so nothing can drift or be trimmed.
    rate = 48000
    inputs, chains, labels = [], [], []
    starts = [r["at"] for r in rows] + [total]
    for i, row in enumerate(rows):
        inputs += ["-i", str(LINES / f"{row['id']}.mp3")]
        span = int(round((starts[i + 1] - starts[i]) * rate))
        chains.append(
            f"[{i}:a]aresample={rate},aformat=channel_layouts=stereo,atempo={tempo},"
            f"apad=whole_len={span},atrim=end_sample={span},asetpts=N/SR/TB[v{i}]"
        )
        labels.append(f"[v{i}]")
    n = len(rows)
    inputs += ["-i", str(SCORE)]
    lead = int(round(rows[0]["at"] * rate))
    inputs += ["-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=stereo"]
    chains.append(f"[{n + 1}:a]atrim=end_sample={lead},asetpts=N/SR/TB[lead]")
    graph = ";".join(chains) + ";"
    graph += "[lead]" + "".join(labels) + f"concat=n={n + 1}:v=0:a=1[voice];"
    graph += "[voice]asplit=2[vo][key];"
    graph += (
        f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{total},volume=0.16,"
        f"afade=t=in:d=2,afade=t=out:st={total - 4}:d=4[bed];"
    )
    graph += "[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=60:release=900[ducked];"
    graph += "[vo][ducked]amix=inputs=2:normalize=0[out]"
    MIX.parent.mkdir(parents=True, exist_ok=True)
    raw = MIX.with_name("mix_raw.wav")
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
        "-ar",
        "48000",
        "-ac",
        "2",
        str(raw),
    )
    # Two-pass loudness: measure, then one linear gain to -16 LUFS, so the score
    # between lines is not lifted the way single-pass dynamic loudnorm lifts it.
    norm = "loudnorm=I=-16:TP=-1.5:LRA=11"
    err = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(raw),
            "-af",
            norm + ":print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    m = json.loads(err[err.rindex("{") : err.rindex("}") + 1])
    norm += (
        f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
        f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true"
    )
    run(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(raw),
        "-af",
        norm,
        "-ar",
        "48000",
        str(MIX),
    )
    raw.unlink()
    print("mix", MIX, total, "s")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    if step == "voice":
        voice()
    elif step == "timing":
        rows, total = timing()
        for r in rows:
            print(f"{r['id']}  {r['at']:7.2f}  {r['end']:7.2f}  {r['text']}")
        print("length", total)
    elif step == "vtt":
        rows, _ = timing()

        def stamp(s):
            return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}"

        cues = [
            f"{stamp(r['at'])} --> {stamp(r['end'] + 0.2)}\n{r['text']}" for r in rows
        ]
        (FILM / "captions.vtt").write_text("WEBVTT\n\n" + "\n\n".join(cues) + "\n")
        print("captions", FILM / "captions.vtt", len(cues), "cues")
    elif step == "score":
        score()
    elif step == "mix":
        mix()
    else:
        sys.exit(__doc__)
