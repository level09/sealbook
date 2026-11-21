"""Build the film's sound: narration lines, the score and the mix.

    uv run python film/audio.py voice    # synthesise any line whose text changed
    uv run python film/audio.py timing   # print when each line starts, and the length
    uv run python film/audio.py score    # generate the score (Google Lyria 2 on fal)
    uv run python film/audio.py mix      # place the lines over a steady bed, -16 LUFS
    uv run python film/audio.py vtt      # write film/captions.vtt, one cue per line

Reads film/narration.json: the voice, the film length and each line with the time
it starts (`at`, which the picture is cut to). Writes film/audio/lines/<id>.mp3 and
film/audio/lines.json and film/audio/score.m4a (all tracked: small, and a new
synthesis never sounds the same twice), and film/build/mix.wav with its two stems,
voice.wav and score.wav (not tracked).
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
BED_DB = -16  # the score under the voice, before the mix gain

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
    """What a synthesis depends on: the words and the voice settings."""
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
    """Each line's start and end in the film, and the film's length.

    Lines start at the `at` the picture is cut to. A line that would run into the
    next one is an error: shorten the words or move the picture, never speed the
    voice up in the mix."""
    index = json.loads((AUDIO / "lines.json").read_text())
    rows = []
    for line in SPEC["lines"]:
        length = index[line["id"]]["seconds"]
        rows.append(
            {
                "id": line["id"],
                "at": line["at"],
                "end": round(line["at"] + length, 2),
                "text": line["text"],
            }
        )
    for a, b in zip(rows, rows[1:], strict=False):
        if a["end"] + 0.15 > b["at"]:
            raise SystemExit(
                f"line {a['id']} ends at {a['end']}, line {b['id']} starts at {b['at']}"
            )
    return rows, SPEC["length"]


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
    rows, total = timing()
    rate = 48000
    # The voice is one track laid end to end: a lead-in of silence, then each line
    # padded with silence up to the next line's start. Sample counts, not timestamps,
    # place the lines. The mono line goes to both channels unchanged.
    inputs, chains, labels = [], [], []
    starts = [r["at"] for r in rows] + [total]
    for i, row in enumerate(rows):
        inputs += ["-i", str(LINES / f"{row['id']}.mp3")]
        span = int(round((starts[i + 1] - starts[i]) * rate))
        chains.append(
            f"[{i}:a]aresample={rate},aformat=channel_layouts=stereo,"
            f"apad=whole_len={span},atrim=end_sample={span},asetpts=N/SR/TB[v{i}]"
        )
        labels.append(f"[v{i}]")
    n = len(rows)
    lead = int(round(rows[0]["at"] * rate))
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
        f"volume={gain:.2f}dB,alimiter=limit=0.841:level=false",
        str(MIX),
    )
    for p in (voice_raw, bed_raw, mix_raw):
        p.unlink()
    print("mix", MIX, total, "s, gain", round(gain, 2), "dB")


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
