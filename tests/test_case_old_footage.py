"""The old-footage case: the app's fingerprints against its planted copies.

The measured values are pinned so any change, better or worse, has to be read and
written down in acceptance/cases/old-footage/README.md."""

import hashlib
import importlib.util
import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from sealbook.settings import Config

HERE = Path(__file__).resolve().parent.parent / "acceptance" / "cases" / "old-footage"
CASE = json.loads((HERE / "case.json").read_text(encoding="utf-8"))
FILES = [HERE / "files" / s["file"] for s in CASE["sources"].values()]


class SoundGateTest(unittest.TestCase):
    """A shared sound point or two is chance, not the same recording: below
    SOUND_MIN_LANDMARKS points lined up at one offset, no match is measured, so no
    proposal is written."""

    def test_one_shared_landmark_is_no_match(self):
        from sealbook.discovery import audio

        query = [{"hash": 7, "time": 10}, {"hash": 8, "time": 20}]
        reference = [{"hash": 7, "time": 40}]
        self.assertIsNotNone(audio.align(query, reference))  # measured
        self.assertIsNone(audio.align(query, reference, Config.SOUND_MIN_LANDMARKS))

    def test_enough_aligned_landmarks_still_match(self):
        from sealbook.discovery import audio

        n = Config.SOUND_MIN_LANDMARKS
        query = [{"hash": h, "time": h} for h in range(n)]
        reference = [{"hash": h, "time": h + 5} for h in range(n)]
        found = audio.align(query, reference, n)
        self.assertEqual(found["aligned_landmarks"], n)

    def test_the_service_asks_for_the_minimum(self):
        import inspect

        from sealbook.discovery import service

        self.assertIn("Config.SOUND_MIN_LANDMARKS", inspect.getsource(service))


def load_eval():
    spec = importlib.util.spec_from_file_location("old_footage_eval", HERE / "eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
@unittest.skipUnless(all(f.exists() for f in FILES), "needs the case's video files")
class OldFootageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.multiple(
            Config,
            PDQ_DISTANCE=31,
            PDQ_QUALITY=50,
            MEDIA_CANDIDATES=20,
            SOUND_MIN_LANDMARKS=4,
        ):
            cls.report = load_eval().score(CASE)

    def test_files_are_the_ones_the_truth_names(self):
        for s in CASE["sources"].values():
            data = (HERE / "files" / s["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), s["sha256"], s["file"])

    def test_every_copy_but_the_muted_short_is_tied_to_the_original(self):
        self.assertEqual(self.report["recall"], [8, 9])
        self.assertEqual(self.report["tied_to_original"], [8, 9])
        self.assertEqual(self.report["misses"], ["copy_short_muted"])
        self.assertEqual(self.report["misses"], sorted(CASE["truth"]["known_misses"]))

    def test_crop_mirror_and_logo_are_found_by_sound_not_picture(self):
        by = {r["copy"]: r["by"] for r in self.report["copies"]}
        self.assertEqual(by["copy_lowrate"], "picture+sound")
        self.assertEqual(by["copy_trim"], "picture+sound")
        for key in ("copy_logo", "copy_crop", "copy_mirror", "copy_vertical"):
            self.assertEqual(by[key], "sound", key)
        self.assertEqual(by["copy_viral"], "sound")
        self.assertEqual(by["copy_viral_muted"], "picture")

    def test_no_strong_match_outside_the_planted_family(self):
        self.assertEqual(self.report["false_strong"], 0)
        # Before SOUND_MIN_LANDMARKS, one or two shared sound points wrote 36 weak
        # proposals; the gate leaves none (README).
        self.assertEqual(self.report["false_matches"], [])

    def test_true_copies_align_far_above_the_gate(self):
        aligned = [
            r["sound"]["aligned_landmarks"] for r in self.report["copies"] if r["sound"]
        ]
        self.assertEqual(min(aligned), 76)  # copy_trim, 12 s of shared sound


RECORDINGS = json.loads((HERE / "recordings.json").read_text(encoding="utf-8"))


def answer(call: dict) -> dict:
    return json.loads(json.loads(call["response"])["choices"][0]["message"]["content"])


class OldFootageActsTest(unittest.TestCase):
    """The case told in six acts, every clip read by the app from recordings."""

    def test_acts_are_playable(self):
        from sealbook.monitor import study

        study.load()
        study.check(CASE, CASE["acts"])
        self.assertEqual(
            [a["title"] for a in CASE["acts"]],
            [
                "The clip that spread",
                "Reposts and other floods",
                "Searching the pile by what it shows",
                "The archive from two years ago",
                "Side by side",
                "The conclusion",
            ],
        )
        self.assertEqual(CASE["acts"][3]["sources"], ["original"])

    def test_every_clip_is_read_once_on_its_frames(self):
        self.assertTrue(all(s["read"] for s in CASE["sources"].values()))
        calls = RECORDINGS["calls"]
        passes = {c["source"]: c for c in calls if c["tool"] == "assess"}
        self.assertEqual(set(passes), set(CASE["sources"]))
        for key, c in passes.items():
            self.assertEqual(c["input_sha256"], CASE["sources"][key]["sha256"])
        # 36 first passes and 30 hosted transcriptions of the clips with sound;
        # no transcript has words, so nothing is extracted: 66 calls.
        self.assertEqual(len(calls), 66)
        self.assertEqual(RECORDINGS["models"]["assess"], "google/gemini-3.8-flash")

    def test_speech_to_text_hears_no_words_in_running_water(self):
        """The local Whisper once wrote "subscribe to the channel" in Arabic on two
        clips with no speech. The hosted transcriber hears no word on any clip: it
        tags the sound instead, and a tag is not a word heard."""
        self.assertEqual(RECORDINGS["models"]["transcribe"], "elevenlabs/scribe-v2")
        self.assertEqual(RECORDINGS["speech"], [])
        heard = [c for c in RECORDINGS["calls"] if c["tool"] == "transcribe"]
        self.assertEqual(len(heard), 30)
        for c in heard:
            words = json.loads(c["response"])["words"]
            self.assertFalse(
                [w for w in words if w.get("type", "word") == "word"], c["source"]
            )
        said = [
            answer(c)["said_labels"]
            for c in RECORDINGS["calls"]
            if c["tool"] == "assess"
        ]
        self.assertFalse(any(said))

    def test_the_first_pass_says_what_the_frames_show_and_not_where(self):
        passes = {
            c["source"]: answer(c) for c in RECORDINGS["calls"] if c["tool"] == "assess"
        }
        viral = " ".join(x["line"] for x in passes["copy_viral"]["description"])
        self.assertIn("submerged trees", viral)
        for words in (passes["copy_viral"]["title"], viral):
            self.assertNotIn("Lindmark", words)
            self.assertNotIn("Ostra", words)


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
@unittest.skipUnless(all(f.exists() for f in FILES), "needs the case's video files")
class SeedOldFootageTest(unittest.IsolatedAsyncioTestCase):
    """The acts seeded from recordings on a throwaway database. No network."""

    def setUp(self):
        from tests.test_people import POSTGRES, _app

        if not POSTGRES:
            self.skipTest("case seeding needs Postgres")
        self.enterContext(mock.patch.object(Config, "EMBEDDING_MODEL", None))
        self.enterContext(mock.patch.object(Config, "RUN_MODELS", {}))
        self.app = _app(self)

    async def asyncSetUp(self):
        from datetime import datetime

        import stk.extensions as ext
        from quart_security import hash_password
        from stk.user.models import Role, User

        async with ext.async_session_factory() as s, s.begin():
            owner = User(
                email="owner@example.com",
                name="owner",
                password=hash_password("TestPassword123!", app=self.app),
                active=True,
                confirmed_at=datetime.now(),
            )
            owner.roles.append(Role(name="admin"))
            s.add(owner)

    async def asyncTearDown(self):
        import stk.extensions as ext

        await ext.engine.dispose()

    async def seed(self, until=None):
        import stk.extensions as ext

        from sealbook.monitor.seed_study import path_of, seed_study

        async with ext.async_session_factory() as s:
            return await seed_study(s, self.app, path_of("old-footage"), until)

    async def described(self, s) -> int:
        from sqlalchemy import func, select

        from sealbook.ledger.contracts import Prop
        from sealbook.ledger.models import Statement

        return await s.scalar(
            select(func.count(func.distinct(Statement.subject))).where(
                Statement.prop == Prop.DESCRIPTION
            )
        )

    async def test_first_act_reads_the_viral_clip_alone(self):
        import stk.extensions as ext

        out = await self.seed(until=1)
        self.assertEqual((out["sources"], out["claims"]), (1, 1))
        self.assertNotIn("match proposals", out)
        async with ext.async_session_factory() as s:
            self.assertEqual(await self.described(s), 1)

    async def test_the_whole_case(self):
        import stk.extensions as ext
        from sqlalchemy import select

        from sealbook.cases import conclusions
        from sealbook.discovery.evidence import copy_reason
        from sealbook.ledger.contracts import Prop, Tier
        from sealbook.ledger.models import Payload, Statement, StatementTier
        from sealbook.ledger.write import verify_db

        out = await self.seed()
        self.assertEqual((out["sources"], out["claims"]), (36, 16))
        # Every proposal is a strong one inside the planted family, and a person
        # confirmed each; nothing was proposed between clips of other footage.
        self.assertEqual(out["match proposals"], 29)
        self.assertEqual(out["copies confirmed"], 29)
        # A search for what the viral clip shows finds it and, once the archive is
        # in, the 2024 original first; one look-alike shares the words.
        found = out["search"]["submerged trees"]
        self.assertEqual(found[0], "original")
        self.assertIn("copy_viral", found)
        self.assertEqual(
            sorted(found),
            sorted(
                [
                    "original",
                    "copy_crop",
                    "copy_viral",
                    "copy_mirror",
                    "copy_logo",
                    "copy_lowrate",
                    "copy_viral_muted",
                    "copy_trim",
                    "copy_short_muted",
                    "flood_ford",
                ]
            ),
        )
        # "bridge" finds the bridges the app saw and one caption naming one; the
        # app saw no bridge in the viral clip.
        self.assertEqual(
            out["search"]["bridge"],
            [
                "flood_arches",
                "flood_footbridge",
                "copy_trim_post",
                "flood_footbridge_post",
            ],
        )
        async with ext.async_session_factory() as s:
            self.assertEqual(await verify_db(s), [])
            self.assertEqual(await self.described(s), 36)
            keys = {f"blob:{src['sha256']}": k for k, src in CASE["sources"].items()}
            rows = (
                await s.execute(
                    select(Statement.subject, Payload.value, StatementTier.tier)
                    .join(Payload, Payload.commitment == Statement.commitment)
                    .join(StatementTier, StatementTier.statement_id == Statement.id)
                    .where(Statement.prop == Prop.MEDIA_MATCH)
                )
            ).all()
            pairs = [
                (frozenset((keys[sub], keys[f"blob:{v['blob']}"])), v, tier)
                for sub, v, tier in rows
            ]
            self.assertEqual({tier for _, _, tier in pairs}, {Tier.ACCEPTED})
            self.assertEqual({copy_reason(v)[2] for _, v, _ in pairs}, {"strong"})
            tied, todo = {"original"}, ["original"]
            while todo:
                node = todo.pop()
                for pair, _, _ in pairs:
                    if node in pair and (pair - {node}) - tied:
                        tied |= pair
                        todo.append(next(iter(pair - {node})))
            family = {"original", *(c["file"] for c in CASE["truth"]["copies"])}
            self.assertEqual(family - tied, {"copy_short_muted"})
            self.assertEqual(tied - family, set())
            got = await conclusions.listing(s, CASE["investigation"])
            self.assertEqual(got["status"]["status"], "concluded")


if __name__ == "__main__":
    unittest.main()
