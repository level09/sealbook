"""Five synthetic recordings about one fictional missing man, for the demo's batch
answer: a mother, a sister, a released detainee and a neighbour each say part of what
the Branch 999 detainee list holds about its row 1, and one clip has no speech.

Spoken by `make_case.speak` (OpenAI gpt-audio through OpenRouter, cached per sentence
in `voice/`), joined with pauses and written to `demo/recordings/` with the script and
each sentence's time in `recordings.json`. Every name is invented.

    uv run python acceptance/make_recordings.py
"""

import json
import subprocess
import wave
from pathlib import Path

import make_case
from make_case import GAP_MS, RATE, speak

OUT = Path(__file__).parent / "demo" / "recordings"

# key: (voice, sentences). The transcript writes احمد without its hamza, as speech to
# text usually does; the list on paper writes أحمد.
SCRIPTS = {
    "mother": (
        "coral",
        [
            "أنا أم محمد، من حمص.",
            "ابني اسمو محمد احمد الخطيب، خلقان سنة الألف وتسعمية وتسعة وسبعين.",
            "أخدوه من البيت بحمص بآخر كانون التاني، سنة الألفين وأربعتعش.",
            "بعد كم يوم قالولنا إنّو بالفرع تسعمية وتسعة وتسعين.",
            "من وقتها ما وصلنا عنّو ولا خبر، وما منعرف وينو.",
        ],
    ),
    "sister": (
        "shimmer",
        [
            "أنا أخت محمد، وعم سجّل هالحكي لحتى ما يضيع.",
            "أخي اسمو محمد احمد الخطيب، وأمّي اسمها ليلى.",
            "خلقان بحمص سنة الألف وتسعمية وتسعة وسبعين، وكان يشتغل نجّار.",
            "سمعنا من واحد طلع إنّو كان بالفرع تسعمية وتسعة وتسعين.",
            "رحنا سألنا عنّو أكتر من مرة، وما حدا جاوبنا.",
        ],
    ),
    "released": (
        "echo",
        [
            "كنت محجوز بالفرع تسعمية وتسعة وتسعين بشهر شباط، سنة الألفين وأربعتعش.",
            "كان معي بالمهجع محمد احمد الخطيب، من حمص.",
            "وكان معنا كمان عمر حسن الزين، من دوما.",
            "بآخر شباط نقلوهم لمكان تاني، وما عاد شفت محمد من وقتها.",
            "ما بعرف لوين أخدوهم، ما حدا قلّنا شي.",
        ],
    ),
    "neighbour": (
        "sage",
        [
            "أنا جار بيت الخطيب بحمص.",
            "شفت العسكر عم ياخدوا محمد من بيتو بكانون التاني.",
            "أمّو ليلى ضلّت تسأل عنّو بكل مكان.",
            "وما رجع عالحارة من يومها.",
        ],
    ),
}
SILENT_MS = 20_000


def ogg(path: Path, pcm: bytes) -> None:
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", "libopus"]
        + ["-b:a", "32k", path],
        check=True,
    )
    wav.unlink()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    truth = {}
    for key, (voice, lines) in SCRIPTS.items():
        make_case.VOICE = voice
        frames, sentences, t = [], [], 0
        for i, text in enumerate(lines):
            for attempt in range(4):
                try:
                    data = speak(f"rec_{key}_{i}", text)
                    break
                except AssertionError as said:
                    # The voice paraphrased the line; ask again.
                    print(f"{key} {i}: retry {attempt + 1}: {said}")
            else:
                raise SystemExit(f"{key} line {i} was never read as written")
            ms = len(data) * 1000 // (2 * RATE)
            sentences.append({"text": text, "start_ms": t, "end_ms": t + ms})
            frames += [data, b"\0" * (2 * RATE * GAP_MS // 1000)]
            t += ms + GAP_MS
        ogg(OUT / f"{key}.ogg", b"".join(frames))
        truth[f"{key}.ogg"] = {"duration_ms": t - GAP_MS, "sentences": sentences}
        print(key, f"{(t - GAP_MS) / 1000:.1f}s")
    # A recording with no speech: room tone, so the reader has nothing to find.
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
        + [f"anoisesrc=d={SILENT_MS / 1000}:c=brown:a=0.01:r={RATE}", "-c:a", "libopus"]
        + ["-b:a", "24k", OUT / "silence.ogg"],
        check=True,
    )
    truth["silence.ogg"] = {"duration_ms": SILENT_MS, "sentences": []}
    (OUT / "recordings.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=1) + "\n"
    )


if __name__ == "__main__":
    main()
