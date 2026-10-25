"""Discovery must preserve source offsets and separate similarity from identity."""

import unittest

import numpy as np

from sealbook.discovery.audio import align, landmarks
from sealbook.discovery.compute import chunks, fingerprint, match_frames, rrf, vector


def frame(bits, ms, quality=100):
    return {"hash": f"{bits:064x}", "ms": ms, "quality": quality}


class DiscoveryTest(unittest.TestCase):
    def test_chunks_point_to_exact_source_text(self):
        text = "الاسم: يوسف\n\nTransfer to Branch 999.\nLast seen in March."
        found = chunks(text, size=25)
        self.assertGreater(len(found), 1)
        self.assertEqual("".join(text[a:b] for a, b in found), text)
        self.assertTrue(all(0 < b - a <= 25 for a, b in found))

    def test_rrf_promotes_agreement_without_counting_repeated_ids(self):
        self.assertEqual(rrf(["a", "b", "b"], ["c", "b"]), ["b", "a", "c"])

    def test_embedding_rejects_wrong_dimensions_nonfinite_and_zero(self):
        self.assertEqual(vector([3, 4], dimensions=2), [0.6, 0.8])
        for bad in ([1], [0, 0], [float("nan"), 1], [float("inf"), 1]):
            with self.assertRaises(ValueError):
                vector(bad, dimensions=2)

    def test_partial_video_match_reports_both_time_ranges(self):
        query = [frame(1, 0), frame(2, 1000)]
        candidate = [frame(255, 0), frame(1, 5000), frame(2, 6000)]
        found = match_frames(query, candidate, distance=0, quality=50)
        self.assertEqual(found["query_ms"], [0, 1000])
        self.assertEqual(found["candidate_ms"], [5000, 6000])
        self.assertEqual(found["query_coverage"], [2, 2])
        self.assertEqual(found["candidate_coverage"], [2, 3])

    def test_low_information_frames_and_repeats_do_not_inflate_matches(self):
        query = [frame(1, 0), frame(1, 1000), frame(2, 2000, quality=0)]
        found = match_frames(query, [frame(1, 0)], distance=0, quality=50)
        self.assertEqual(found["query_coverage"], [1, 1])
        self.assertIsNone(match_frames([frame(1, 0, quality=0)], [frame(1, 0)]))

    def test_audio_alignment_recovers_excerpt_offset(self):
        query = [
            {"hash": 1, "time": 0},
            {"hash": 2, "time": 10},
            {"hash": 3, "time": 20},
        ]
        reference = [
            {"hash": 1, "time": 125},
            {"hash": 2, "time": 135},
            {"hash": 3, "time": 145},
            {"hash": 1, "time": 800},
        ]
        found = align(query, reference)
        self.assertEqual(found["offset_ms"], 8000)
        self.assertEqual(found["concentration"], [3, 4])
        self.assertIsNone(align(query, []))

    def test_silent_audio_generates_no_landmarks(self):
        self.assertEqual(landmarks(np.zeros(16000, dtype=np.float32)), [])

    def test_video_decode_keeps_real_frame_times_and_audio(self):
        import subprocess
        import tempfile
        from pathlib import Path

        witness = (
            Path(__file__).resolve().parents[1] / "acceptance/data/witness_clip.ogg"
        )
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "sample.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=64x64:rate=5:duration=4",
                    "-i",
                    str(witness),
                    "-t",
                    "4",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    str(video),
                ],
                check=True,
                capture_output=True,
            )
            found = fingerprint(video, "video/mp4")
            from sealbook.discovery.media import parts

            located, total = parts(video, "video/mp4", 0, 8)
            self.assertEqual(total, 3)
            self.assertEqual(
                [item["location"]["ms"] for item in located if item["kind"] == "frame"],
                [[0, 1], [3000, 3001]],
            )
            sound = next(item for item in located if item["kind"] == "audio")
            self.assertEqual(sound["location"]["ms"], [0, 4000])
            import io
            import wave

            with wave.open(io.BytesIO(sound["data"])) as decoded:
                self.assertEqual(
                    (decoded.getnchannels(), decoded.getframerate()), (1, 16000)
                )
        self.assertEqual([f["ms"] for f in found["visual"]], [0, 1000, 2000, 3000])
        self.assertEqual(found["audio_status"], "present")
        self.assertTrue(found["audio"])

    def test_a_frame_window_past_the_last_frame_is_skipped(self):
        import subprocess
        import tempfile
        from pathlib import Path

        from sealbook.discovery.media import parts

        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "longer_audio.webm"
            # Video ends at 6 s, audio at 6.4 s: the window at 6 s has no frame.
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=64x64:rate=5:duration=6",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=duration=6.4",
                    "-c:v",
                    "libvpx",
                    "-c:a",
                    "libopus",
                    str(video),
                ],
                check=True,
                capture_output=True,
            )
            located, _ = parts(video, "video/webm", 0, 8)
        frames = [i["location"]["ms"][0] for i in located if i["kind"] == "frame"]
        # Real decode times: older ffmpeg starts this webm a few ms after zero.
        self.assertEqual(len(frames), 2)
        self.assertLess(frames[0], 50)
        self.assertEqual(frames[1] - frames[0], 3000)
