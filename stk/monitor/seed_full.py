"""`stk demo-seed --full`: a second case, case-demo-02, that fills every analyst view.
The real fixture files in acceptance/ go through the intake path; the people who
appear in them, and in the text stand-ins below, are invented. Places are real
public geography. Run `stk enrich` and `stk worker` afterwards for search, media
matches and vectors."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import click
from PIL import Image
from quart_security import hash_password
from sqlalchemy import select

from stk import blobs
from stk.collaboration import assignments
from stk.collaboration import service as collaboration
from stk.discovery import service as discovery
from stk.ingest.service import agent_run, ingest_bytes, system_agent
from stk.ledger.contracts import EvidenceKind as E
from stk.ledger.contracts import Prop, Tier
from stk.ledger.models import Statement, StatementTier
from stk.ledger.write import principal_for_user, record_acquisition
from stk.monitor import seed_flows as flows
from stk.monitor.seed import P1, P2, Seeder, locate, seeded
from stk.packages import service as packages
from stk.packages.models import Brief
from stk.settings import Config
from stk.user.models import Role, User
from stk.watch import matcher
from stk.watch.models import Watch

CASE = "case-demo-02"
ROOT = Path(__file__).resolve().parents[2] / "acceptance"
R1, R2, K1, K2, K2E, L1, L2, Z1, RM, B1, F1, S1, W1 = (
    f"person:{uuid.uuid5(uuid.NAMESPACE_URL, f'sealbook-demo/{CASE}/{p}')}"
    for p in (
        "R1",
        "R2",
        "K1",
        "K2",
        "K2E",
        "L1",
        "L2",
        "Z1",
        "RM",
        "B1",
        "F1",
        "S1",
        "W1",
    )
)

LIST = """كشف بأسماء الموقوفين
الفرع 999 - قسم حلب
التاريخ: 2014-02-12

1. الاسم: محمد أحمد الخطيب | اسم الأم: ليلى | المواليد: 1979 | مكان التوقيف: حمص | تاريخ التوقيف: 2014-01-28
2. الاسم: محمد أحمد الخطيب | اسم الأم: هدى | المواليد: 1994 | مكان التوقيف: حلب | تاريخ التوقيف: 2014-02-03
3. الاسم: لينا عمر النجار | اسم الأب: عمر | المواليد: 1988 | مكان التوقيف: حي الجديدة | تاريخ التوقيف: 2014-02-11
4. الاسم: عمر حسن الزين | اسم الأب: حسن | المواليد: 1990 | مكان التوقيف: دوما | تاريخ التوقيف: 2014-02-09
"""
NOTE = """Documentation note 14/2014 (synthetic)
Lina Omar Al-Najjar, born 1988, a pharmacist from Aleppo, was arrested at a checkpoint in the Jdeideh quarter on 11 February 2014.
Her family reports that she was held at Branch 999.
Mohammed Ahmad Al-Khatib, born 1994, was detained in Aleppo in early February 2014.
"""
APPEAL = """<h1>نداء: لينا عمر النجار</h1>
<p>آخر مرة شوهدت لينا في حي الجديدة بحلب يوم 2014-02-11 قرب الحاجز.</p>
<p>من يعرف عنها أي شيء يرجى التواصل مع أخيها.</p>
"""
# Brought in by the owner as one intake batch: a cell roster and a published
# statement that put four people at Branch 999 in July 2013.
ROSTER = """قائمة نزلاء المهجع 4
الفرع 999
الشهر: تموز 2013

1. يوسف خالد العلي
2. باسل نادر
3. فادي كرم
"""
RELEASE = """Statement of a released detainee (synthetic), published 4 October 2013
Salim Haddad says he was held at Branch 999 throughout July 2013, in dormitory 4.
He names three cellmates: Yousef Khaled Al-Ali, Bassel Nader and Fadi Karam.
He was released on 2 August 2013.
"""
TESTIMONY = """[00:00] الشاهدة: اسمي ريم.
[00:05] كنت موقوفة بالفرع 999 بشهر آذار 2014، ولينا النجار كانت معنا بالمهجع.
[00:19] بعدين نقلوها، سمعنا إنو على سجن ك المركزي.
"""

# key: (file, mime, origin, doc_type, sensitivity, extra context)
COMMONS = "url:https://commons.wikimedia.org/wiki/File:"
FILES = {
    "register": (
        "data/register_page.png",
        "image/png",
        "device:scanner-02",
        "detention_register",
        "sensitive",
        {},
    ),
    "transfer": (
        "data/transfer_order.pdf",
        "application/pdf",
        "channel:partner-b/batch-2",
        "transfer_order",
        "ordinary",
        {},
    ),
    "witness": (
        "data/witness_clip.ogg",
        "audio/ogg",
        "device:recorder-04",
        "witness_statement",
        "sensitive",
        {},
    ),
    "photo": (
        "data/photo_damaged_building.jpg",
        "image/jpeg",
        "device:camera-c2pa-02",
        "photo",
        "ordinary",
        {},
    ),
    "post": (
        "data/missing_post.html",
        "text/html",
        "url:http://127.0.0.1/synthetic/missing-post-share",
        "web_post",
        "ordinary",
        {},
    ),
    "post_v2": (
        "data/missing_post_v2.html",
        "text/html",
        "url:http://127.0.0.1/synthetic/missing-post-share",
        "web_post",
        "ordinary",
        {"recapture": True},
    ),
    "scan": (
        "retrieval/scan_page.png",
        "image/png",
        "device:scanner-02",
        "letter",
        "ordinary",
        {},
    ),
    "checkpoint": (
        "public/aleppo_checkpoint.png",
        "image/png",
        f"{COMMONS}A_Syrian_Arab_Army_checkpoint_abandoned_due_to_a_massive_rebel_offensive,_December_2024.png",
        "photo",
        "ordinary",
        {
            "author": "Unknown author",
            "licence": "Public domain (PD-VOA)",
            "date_original": "2024-12-02",
            "attribution": "acceptance/public/ATTRIBUTION.md",
        },
    ),
    "market": (
        "public/aleppo_souk.jpg",
        "image/jpeg",
        f"{COMMONS}Le_souk_d%27Alep_bombard%C3%A9.jpg",
        "photo",
        "ordinary",
        {
            "author": "Cagibi54 (own work)",
            "licence": "CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0",
            "date_original": "2019-10-06",
            "attribution": "acceptance/public/ATTRIBUTION.md",
        },
    ),
    "homs": (
        "public/homs_bab_dreeb_2.jpg",
        "image/jpeg",
        f"{COMMONS}Destruction_in_Homs_(9).jpg",
        "photo",
        "ordinary",
        {
            "author": "Bo yaser (own work)",
            "licence": "CC BY-SA 3.0, https://creativecommons.org/licenses/by-sa/3.0",
            "date_original": "2012-04-05",
            "attribution": "acceptance/public/ATTRIBUTION.md",
        },
    ),
    "raqqa": (
        "public/raqqa_destroyed_2017.png",
        "image/png",
        f"{COMMONS}Destroyed_neighborhood_in_Raqqa.png",
        "photo",
        "ordinary",
        {
            "author": "Mahmoud Bali (VOA)",
            "licence": "Public domain (PD-VOA)",
            "date_original": "2017-08-01",
            "attribution": "acceptance/public/ATTRIBUTION.md",
        },
    ),
    "speech": (
        "retrieval/speech.wav",
        "audio/wav",
        "device:recorder-04",
        "voice_note",
        "ordinary",
        {},
    ),
    "speech_excerpt": (
        "retrieval/speech_excerpt.wav",
        "audio/wav",
        "channel:messaging/forwarded",
        "voice_note",
        "ordinary",
        {},
    ),
    "tone": (
        "retrieval/tone.wav",
        "audio/wav",
        "device:recorder-04",
        "voice_note",
        "ordinary",
        {},
    ),
    "jdeideh_2015": (
        "public/jdeideh_2015.webm",
        "video/webm",
        f"{COMMONS}Jdeideh_Jdayde_Aleppo_Avril_2015.webm",
        "video",
        "ordinary",
        {
            "author": "Jdeideh Jdayde, https://www.youtube.com/channel/UCP1lupeS4ZwPSAnR5izmf7A",
            "credit": "YouTube, https://www.youtube.com/watch?v=f4tk4lDMFj0",
            "licence": "CC BY 3.0, https://creativecommons.org/licenses/by/3.0",
            "date_original": "2015-04-30",
            "attribution": "acceptance/public/ATTRIBUTION.md",
        },
    ),
    "jdeideh_2017": (
        "public/jdeideh_2017.webm",
        "video/webm",
        f"{COMMONS}Jdeideh_Alep_-_Waqf_complexes_of_Ibshir_Pasha_and_Bahram_Pasha_-_Sept_2017.webm",
        "video",
        "ordinary",
        {
            "author": "Ortho2017 (own work)",
            "licence": "CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0",
            "date_original": "2017-09-13",
            "attribution": "acceptance/public/ATTRIBUTION.md",
        },
    ),
}
# The retrieval gold set's news passages and its noise tone answer search
# questions, not case questions, so they live in a case of their own.
EVAL = "case-search-eval"
FILES["tone"] = FILES["tone"][:5] + ({"investigation": EVAL},)
FILES |= {
    p.stem: (
        f"retrieval/{p.name}",
        "text/plain",
        "channel:open-source/local-news",
        "news_article",
        "ordinary",
        {"investigation": EVAL},
    )
    for p in sorted((ROOT / "retrieval").glob("*.txt"))
}
# What an analyst would call each source. The `file` key stays the bare name the
# search battery and the seed itself refer to.
TITLES = {
    "register": "Branch 999 register, scanned page",
    "transfer": "Transfer order 4471",
    "witness": "Witness clip, recorder 04",
    "photo": "Damaged building, Aleppo",
    "post": "Missing person post, first capture",
    "post_v2": "Missing person post, recapture",
    "scan": "Handwritten letter, scanned",
    "checkpoint": "Abandoned army checkpoint, December 2024",
    "checkpoint_forward": "Checkpoint photo, forwarded copy",
    "market": "Aleppo souk after the shelling",
    "homs": "Bab Dreeb, Homs, April 2012",
    "raqqa": "Destroyed neighbourhood, Raqqa, 2017",
    "speech": "Voice note, coastal bus trip",
    "speech_excerpt": "Voice note, forwarded excerpt",
    "tone": "Test tone",
    "jdeideh_2015": "Jdeideh quarter, Aleppo, April 2015",
    "jdeideh_2017": "Jdeideh waqf complexes, September 2017",
    "jdeideh_2017_excerpt": "Jdeideh clip, re-uploaded excerpt",
    "list": "Branch 999 detainee list, 12 February 2014",
    "note": "Documentation note 14/2014",
    "appeal": "Appeal for Lina Omar Al-Najjar",
    "testimony": "Testimony of Reem, Branch 999",
    "roster": "Dormitory 4 roster, July 2013",
    "release": "Statement of a released detainee, October 2013",
}
TEXTS = {
    "list": (
        LIST,
        "text/plain",
        "channel:partner-b/batch-3",
        "detention_register",
        "ordinary",
    ),
    "note": (
        NOTE,
        "text/plain",
        "url:http://127.0.0.1/synthetic/documentation-note-14",
        "report",
        "ordinary",
    ),
    "appeal": (
        APPEAL,
        "text/html",
        "url:http://127.0.0.1/synthetic/appeal-lina",
        "web_post",
        "ordinary",
    ),
    "testimony": (
        TESTIMONY,
        "text/plain",
        "device:recorder-05",
        "witness_statement",
        "sensitive",
    ),
}


def ar(text):
    return {"text": text, "lang": "ar"}


def en(text):
    return {"text": text, "lang": "en"}


async def intake(s: Seeder, key, data, mime, origin, collector, doc_type, sens, ctx):
    named = {"title": TITLES[key]} if key in TITLES else {}
    context = {"investigation": s.case, "file": key} | named | ctx
    if mime == "video/webm":
        # ponytail: c2patool refuses webm ("Unsupported file type"), which fails
        # ingest(); record the bytes and hash like ingest, without C2PA, until
        # intake treats an unsupported container as "absent".
        blob = await blobs.put(s.s, data, mime, Config.BLOB_DIR)
        acq = await record_acquisition(
            s.s,
            blob_sha256=blob.sha256,
            origin=origin,
            collector_id=collector.id,
            at=datetime.now(UTC),
            context=context,
            sensitivity=sens,
            doc_type=doc_type,
        )
        subject = f"blob:{blob.sha256}"
        await agent_run(
            s.s,
            "hash",
            {"blobs": [blob.sha256]},
            [(subject, Prop.SHA256, blob.sha256), (subject, Prop.MD5, blob.md5)],
        )
        await discovery.queue(s.s, acq, mime)
        sha = blob.sha256
    else:
        sha = (
            await ingest_bytes(
                s.s,
                data,
                blob_dir=Config.BLOB_DIR,
                mime=mime,
                origin=origin,
                collector_id=collector.id,
                doc_type=doc_type,
                sensitivity=sens,
                context=context,
            )
        )["sha256"]
    s.blobs[key] = sha
    if mime.startswith("text/"):
        s.texts[key] = data.decode()


async def re_shared(path: Path) -> tuple[bytes, bytes]:
    """A recompressed copy of the checkpoint photo and a 12 s re-upload of the
    2017 clip: the shapes media matching exists to find."""
    buf = BytesIO()
    Image.open(ROOT / "public/aleppo_checkpoint.png").convert("RGB").save(
        buf, "JPEG", quality=70
    )
    out = path / "excerpt.webm"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        "20",
        "-t",
        "12",
        "-i",
        str(ROOT / "public/jdeideh_2017.webm"),
        "-c",
        "copy",
        str(out),
    )
    if await proc.wait():
        raise click.ClickException("ffmpeg could not cut the 2017 excerpt")
    return buf.getvalue(), out.read_bytes()


async def users(session, app, names=("analyst-2", "analyst-3")):
    """The owner (first active admin) and real active colleagues (analyst-2 and
    analyst-3 by default), whose random passwords are never shown: the owner resets
    one to sign in as them."""
    owner = await session.scalar(
        select(User)
        .where(User.active.is_(True), User.roles.any(Role.name == "admin"))
        .order_by(User.id)
        .limit(1)
    )
    if owner is None:
        raise click.ClickException("create the owner first: uv run stk install")
    colleagues = []
    for name in names:
        colleague = await session.scalar(
            select(User).where(User.email == f"{name}@example.com")
        )
        if colleague is None:
            role = await session.scalar(select(Role).where(Role.name == "analyst"))
            colleague = User(
                email=f"{name}@example.com",
                name=name,
                password=hash_password(os.urandom(24).hex(), app=app),
                active=True,
                confirmed_at=datetime.now(),
            )
            colleague.roles.append(role)
            session.add(colleague)
            await session.flush()
        colleagues.append(colleague)
    return owner, *colleagues


async def factual(session, subject, prop):
    return await session.scalar(
        select(Statement.id)
        .join(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.subject == subject,
            Statement.prop == prop,
            StatementTier.tier.in_([Tier.ACCEPTED, Tier.CORROBORATED]),
        )
        .order_by(Statement.recorded_at)
        .limit(1)
    )


async def seed_full(session, app) -> dict:
    """Additive: refuses when case-demo-02 exists, never deletes. Expects the
    case-demo-01 people (P1, P2) to be seeded first."""
    if await seeded(session, CASE):
        raise click.ClickException(f"{CASE} is already seeded")
    s = Seeder(session)
    s.case = CASE
    owner, colleague, third = await users(session, app)
    senior = await s.analyst("senior-analyst", CASE)
    extractor = await system_agent(session, "extractor")
    importer = await s.principal("importer")
    field = await principal_for_user(session, colleague.id, colleague.name)

    tmp = Path(tempfile.mkdtemp(prefix="sealbook-demo-"))
    try:
        jpeg, excerpt = await re_shared(tmp)
        for key, (file, mime, origin, doc_type, sens, ctx) in FILES.items():
            collector = field if origin.startswith("device:") else importer
            data = (ROOT / file).read_bytes()
            await intake(s, key, data, mime, origin, collector, doc_type, sens, ctx)
            await session.commit()
        await intake(
            s,
            "checkpoint_forward",
            jpeg,
            "image/jpeg",
            "channel:messaging/forwarded",
            importer,
            "photo",
            "ordinary",
            {"note": "recompressed copy of aleppo_checkpoint.png"},
        )
        await intake(
            s,
            "jdeideh_2017_excerpt",
            excerpt,
            "video/webm",
            "url:http://127.0.0.1/synthetic/reupload-jdeideh",
            importer,
            "video",
            "ordinary",
            FILES["jdeideh_2017"][5]
            | {"excerpt_of": "jdeideh_2017.webm, 20 s to 32 s"},
        )
        for key, (text, mime, origin, doc_type, sens) in TEXTS.items():
            collector = field if origin.startswith("device:") else importer
            await intake(
                s, key, text.encode(), mime, origin, collector, doc_type, sens, {}
            )
        mine = await principal_for_user(session, owner.id, owner.name or owner.email)
        batch = {"batch": str(uuid.uuid4())}
        await intake(
            s,
            "roster",
            ROSTER.encode(),
            "text/plain",
            "channel:partner-b/batch-4",
            mine,
            "detention_register",
            "ordinary",
            batch,
        )
        await intake(
            s,
            "release",
            RELEASE.encode(),
            "text/plain",
            "url:http://127.0.0.1/synthetic/released-detainee-statement",
            mine,
            "report",
            "ordinary",
            batch,
        )
        await session.commit()

        # Claims below point into text read from the PDF and the recording, so
        # read them first: the PDF by its real reader, the clip from its script.
        truth = json.loads((ROOT / "data/expected.json").read_text())
        await flows.page_text(s, "transfer")
        clip = truth["witness_clip.ogg"]
        await flows.transcript(s, "witness", clip["sentences"], clip["duration_ms"])
        await session.commit()

        llm = {
            "route": "local",
            "endpoint": "http://127.0.0.1:8000/v1",
            "model_requested": "qwen3-vl-4b",
            "model_served": "qwen3-vl-4b-sy-demo",
            "provider_served": "local-vllm",
            "prompt_hash": "0" * 64,
        }
        say = s.say
        names = []

        # The real register scan: located by pixel boxes from expected.json.
        run = await s.run(extractor, "extract", ["register"], **llm)
        await s.event(run, "page_read", {"page": 1, "lines": 3})
        n, reg = 0, {}
        for fact in truth["register_page.png"]["facts"]:
            who = {"P1": R1, "P2": R2}[fact["person"]]
            prop = Prop(fact["prop"])
            value = ar(fact["value"])
            st = await say(
                run,
                "register",
                who,
                prop,
                value,
                at={"page": fact["page"], "bbox": fact["bbox"]},
                valid=["2013-05-02"] if prop == Prop.ARRESTED_AT else None,
                evidence=[E.HANDWRITTEN_ENTRY],
            )
            n += 1
            reg[who, prop] = st
            if prop == Prop.NAME:
                names.append(st.id)
        await s.done(run, n)

        run = await s.run(extractor, "extract", ["post"], **llm)
        await say(
            run,
            "post",
            P1,
            Prop.NAME,
            en("Yousef Khaled Al-Ali"),
            "Yousef Khaled Al-Ali",
        )
        await say(
            run,
            "post",
            P1,
            Prop.LAST_SEEN,
            ar("دوما"),
            "دوما",
            valid=["2013-05-02"],
            evidence=[E.PRINTED_TEXT],
        )
        await s.done(run, 2)

        run = await s.run(extractor, "extract", ["list"], **llm)
        rows = {}
        for who, row, name, born, place, when, parent in (
            (K1, "1.", "محمد أحمد الخطيب", "1979", "حمص", "2014-01-28", "ليلى"),
            (K2, "2.", "محمد أحمد الخطيب", "1994", "حلب", "2014-02-03", "هدى"),
            (L1, "3.", "لينا عمر النجار", "1988", "حي الجديدة", "2014-02-11", "عمر"),
            (Z1, "4.", "عمر حسن الزين", "1990", "دوما", "2014-02-09", "حسن"),
        ):
            # The extractor misread the father's part of row 4's name.
            value = "عمر حسين الزين" if who == Z1 else name
            got = {"name": await say(run, "list", who, Prop.NAME, ar(value), name, row)}
            names.append(got["name"].id)
            got["born"] = await say(
                run, "list", who, Prop.BIRTH_DATE, ar(born), born, row
            )
            got["arrested"] = await say(
                run,
                "list",
                who,
                Prop.ARRESTED_AT,
                ar(place),
                place,
                f"{row} الاسم",
                valid=[when],
                evidence=[E.PRINTED_TEXT],
            )
            prop = Prop.FATHER_NAME if who in (L1, Z1) else Prop.MOTHER_NAME
            label = "اسم الأب: " if prop == Prop.FATHER_NAME else "اسم الأم: "
            await say(
                run,
                "list",
                who,
                prop,
                ar(parent),
                parent,
                f"{row} الاسم: {name} | {label}",
            )
            await say(
                run,
                "list",
                who,
                Prop.DETENTION_BRANCH,
                ar("الفرع 999"),
                "الفرع 999",
                evidence=[E.LETTERHEAD],
            )
            rows[who] = got
        await say(
            run,
            "list",
            f"blob:{s.blobs['list']}",
            Prop.DOCUMENT_DATE,
            {"date": "2014-02-12"},
            "2014-02-12",
            valid=["2014-02-12"],
        )
        await s.done(run, 21)

        run = await s.run(extractor, "extract", ["note"], **llm)
        for who, name, born in (
            (L2, "Lina Omar Al-Najjar", "1988"),
            (K2E, "Mohammed Ahmad Al-Khatib", "1994"),
        ):
            names.append((await say(run, "note", who, Prop.NAME, en(name), name)).id)
            await say(run, "note", who, Prop.BIRTH_DATE, ar(born), born, name)
        await s.done(run, 4)

        run = await s.run(extractor, "extract", ["appeal"], **llm)
        await say(
            run, "appeal", L1, Prop.NAME, ar("لينا عمر النجار"), "لينا عمر النجار"
        )
        jdeideh = await say(
            run,
            "appeal",
            L1,
            Prop.LAST_SEEN,
            ar("حي الجديدة"),
            "حي الجديدة",
            valid=["2014-02-11"],
            evidence=[E.PRINTED_TEXT],
        )
        await say(
            run,
            "appeal",
            L1,
            Prop.LAST_SEEN,
            ar("حلب"),
            "حلب",
            "الجديدة ب",
            valid=["2014-02-11"],
        )
        await s.done(run, 3)

        run = await s.run(extractor, "extract", ["testimony"], **llm)
        lina_held = await say(
            run,
            "testimony",
            L1,
            Prop.SEEN_AT,
            ar("الفرع 999"),
            "الفرع 999",
            valid=["2014-03-01", "2014-03-31"],
            evidence=[E.SPOKEN],
        )
        await say(
            run,
            "testimony",
            L1,
            Prop.TRANSFERRED_TO,
            ar("سجن ك المركزي"),
            "سجن ك المركزي",
            evidence=[E.SPOKEN],
        )
        # The witness herself, held in the same month.
        reem = await say(run, "testimony", RM, Prop.NAME, ar("ريم"), "ريم")
        reem_held = await say(
            run,
            "testimony",
            RM,
            Prop.DETAINED_AT,
            ar("الفرع 999"),
            "الفرع 999",
            valid=["2014-03-01", "2014-03-31"],
            evidence=[E.SPOKEN],
        )
        await s.done(run, 4)
        await session.commit()

        # Yousef (R1) across five sources: arrested in Harasta and last seen in
        # Douma on the same day (a fork), seen at Branch 999 in July on the voice
        # clip and the cell roster, held there and transferred in September.
        july = ["2013-07-01", "2013-07-31"]
        run = await s.run(extractor, "extract", ["witness_tx"], **llm)
        await say(run, "witness_tx", W1, Prop.NAME, ar("سامر"), "سامر")
        clip_name = await say(
            run,
            "witness_tx",
            R1,
            Prop.NAME,
            ar("يوسف خالد العلي"),
            "يوسف خالد العلي",
            evidence=[E.SPOKEN],
        )
        clip_seen = await say(
            run,
            "witness_tx",
            R1,
            Prop.SEEN_AT,
            ar("الفرع 999"),
            "بالفرع تسعمية وتسعة وتسعين",
            valid=july,
            evidence=[E.SPOKEN],
        )
        await s.done(run, 3)

        run = await s.run(extractor, "extract", ["transfer_p1"], **llm)
        page = "transfer_p1"
        await say(
            run,
            page,
            f"blob:{s.blobs['transfer']}",
            Prop.DOCUMENT_DATE,
            {"date": "2013-09-17"},
            "17-09-2013",
            valid=["2013-09-17"],
            evidence=[E.PRINTED_TEXT],
        )
        pdf_name = await say(
            run,
            page,
            R1,
            Prop.NAME,
            ar("يوسف خالد علي"),
            "يوسف خالد علي",
            evidence=[E.PRINTED_TEXT],
        )
        await say(run, page, R1, Prop.BIRTH_DATE, {"date": "1991-03-14"}, "14-03-1991")
        await say(run, page, R1, Prop.MOTHER_NAME, ar("فاطمة"), "فاطمة")
        pdf_held = await say(
            run,
            page,
            R1,
            Prop.DETAINED_AT,
            ar("الفرع 999"),
            "999  الفرع",
            valid=["2013-09-17"],
            evidence=[E.PRINTED_TEXT],
        )
        pdf_moved = await say(
            run,
            page,
            R1,
            Prop.TRANSFERRED_TO,
            ar("سجن ك المركزي"),
            "سجن ك المركزي",
            valid=["2013-09-17"],
            evidence=[E.PRINTED_TEXT, E.REGISTRY_STAMP],
        )
        await s.done(run, 6)

        run = await s.run(extractor, "extract", ["post_v2"], **llm)
        await say(
            run,
            "post_v2",
            R1,
            Prop.NAME,
            en("Yousef Khaled Al-Ali"),
            "Yousef Khaled Al-Ali",
        )
        douma = await say(
            run,
            "post_v2",
            R1,
            Prop.LAST_SEEN,
            ar("دوما"),
            "دوما",
            valid=["2013-05-02"],
            evidence=[E.PRINTED_TEXT],
        )
        await s.done(run, 2)

        run = await s.run(extractor, "extract", ["roster"], **llm)
        roster = {}
        for who, name in ((R1, "يوسف خالد العلي"), (B1, "باسل نادر"), (F1, "فادي كرم")):
            await say(run, "roster", who, Prop.NAME, ar(name), name)
            roster[who] = await say(
                run,
                "roster",
                who,
                Prop.DETAINED_AT,
                ar("الفرع 999"),
                "الفرع 999",
                valid=july,
                evidence=[E.PRINTED_TEXT],
            )
        await s.done(run, 6)

        run = await s.run(extractor, "extract", ["release"], **llm)
        public = {}
        for who, name in (
            (S1, "Salim Haddad"),
            (R1, "Yousef Khaled Al-Ali"),
            (B1, "Bassel Nader"),
            (F1, "Fadi Karam"),
        ):
            public[who] = await say(run, "release", who, Prop.NAME, en(name), name)
        salim_held = await say(
            run,
            "release",
            S1,
            Prop.DETAINED_AT,
            en("Branch 999"),
            "Branch 999",
            valid=july,
            evidence=[E.PRINTED_TEXT],
        )
        await say(
            run,
            "release",
            S1,
            Prop.EVENT,
            {"text": "released", "lang": "en", "kind": "released"},
            "released",
            "He was ",
            valid=["2013-08-02"],
            evidence=[E.PRINTED_TEXT],
        )
        await s.done(run, 6)
        await session.commit()

        # Decisions: two accepts, one reject, and one accept undone.
        await s.review(senior, rows[L1]["name"], "accept", "list row 3, matches appeal")
        await s.review(senior, rows[K1]["name"], "accept", "list row 1")
        await s.review(
            senior, rows[K2]["born"], "reject", "year read from the wrong row"
        )
        undone = await s.review(senior, rows[K2]["arrested"], "accept", "list row 2")
        await s.review(senior, undone, None, "undo", prop=Prop.RETRACTION)
        # The claims a story and discovery stand on, each signed by a person.
        accepted = [
            (reg[R1, Prop.ARRESTED_AT], "register row 1, arrest place column"),
            (douma, "recaptured appeal, same sentence as the first capture"),
            (clip_name, "the witness names him at 00:10"),
            (clip_seen, "the witness says July 2013 at Branch 999"),
            (roster[R1], "roster header, July 2013"),
            (roster[B1], "roster header, July 2013"),
            (roster[F1], "roster header, July 2013"),
            (salim_held, "published statement, first sentence"),
            (pdf_name, "transfer order, detainee line"),
            (pdf_held, "transfer order, holding branch line"),
            (pdf_moved, "transfer order, destination and stamp"),
            (lina_held, "the witness names Lina at 00:05"),
            (reem, "the witness gives her name at 00:00"),
            (reem_held, "the witness says March 2014 at Branch 999"),
            (jdeideh, "the appeal's first sentence"),
            *((st, "named in the published statement") for st in public.values()),
        ]
        for st, why in accepted:
            await s.review(senior, st, "accept", why)
        for st in (
            rows[L1]["name"],
            rows[K1]["name"],
            rows[K2]["arrested"],
            *(st for st, _ in accepted),
        ):
            await discovery.actor_changed(session, st)
        await session.commit()

        # Watchlists, then the matcher run over every new name mention.
        session.add_all(
            [
                Watch(user_id=owner.id, person=P1),
                Watch(user_id=owner.id, person=P2),
                Watch(user_id=owner.id, person=L1),
                Watch(user_id=colleague.id, person=K2),
            ]
        )
        await session.flush()
        run = await s.run(await matcher.matcher_agent(session), matcher.TOOL, ["list"])
        hits = await matcher.match(
            session,
            names,
            run,
            lambda h: {"kind": "ed25519", "sig": run.key.sign(h.encode()).hex()},
        )
        await s.done(run, len(hits["written"]))
        await session.commit()

        # Review together: one request each way, a correction, private discussion,
        # each with the notification its endpoint sends.
        sent = await flows.request(
            s,
            rows[K1]["born"],
            owner,
            colleague,
            "Two Mohammed Ahmad Al-Khatib entries share this list. Check the birth year belongs to row 1.",
        )
        await flows.reply(
            s, sent.id, colleague, "Row 1 reads 1979; the 1994 entry is row 2."
        )
        await flows.reply(
            s, sent.id, owner, "Good. Keep the twins apart until a field differs."
        )
        received = await flows.request(
            s,
            rows[L1]["arrested"],
            colleague,
            owner,
            "Is the Jdeideh quarter the arrest place, or the checkpoint she passed?",
        )
        await flows.reply(
            s,
            received.id,
            colleague,
            "The documentation note says the checkpoint was in the quarter.",
        )
        fixed = await collaboration.suggest(
            session, rows[Z1]["name"], colleague, {"value": ar("عمر حسن الزين")}
        )
        correction = await flows.request(
            s,
            fixed,
            colleague,
            owner,
            "The extractor read حسين; row 4 says حسن, same as the father column.",
        )
        await flows.reply(
            s, correction.id, colleague, "Span unchanged, only the value."
        )
        await flows.reply(
            s,
            correction.id,
            colleague,
            f"@{owner.email.split('@')[0]} can you sign this one today? "
            "The watchlist hit on row 4 waits on it.",
        )
        await session.commit()
        # Places for every claim so far; the sheets below keep theirs open.
        await locate(s, senior)
        await session.commit()

        # A family brief. Building it into a package needs every cited row
        # anchored (OTS and TSA receipts from `stk anchor`), so that stays manual.
        cited = {
            "name": await factual(session, P1, Prop.NAME),
            "branch": await factual(session, P1, Prop.DETENTION_BRANCH),
            "lina": await factual(session, L1, Prop.NAME),
        }
        if None in cited.values():
            raise click.ClickException(f"no factual statement to cite: {cited}")
        brief = Brief(
            title="Yousef Khaled Al-Ali and Lina Omar Al-Najjar: what the records say",
            audience="family",
            author_id=owner.id,
        )
        session.add(brief)
        await session.flush()
        for text, ids in (
            (
                "Yousef Khaled Al-Ali is named in a Branch 999 detention register.",
                [cited["name"], cited["branch"]],
            ),
            (
                "Lina Omar Al-Najjar is listed in a second register from the same branch.",
                [cited["lina"]],
            ),
        ):
            await packages.save_sentence(
                session, brief, owner, text, [str(i) for i in ids]
            )
        await session.commit()

        # Sheet intake: two versions of one family intake sheet, the mapping a
        # colleague confirmed, the diff, and proposals pinned to cells.
        sheet_batch = str(uuid.uuid4())
        origin = "channel:partner-c/family-intake"
        dataset, v1 = await flows.sheet_version(
            s, ROOT / "data/messy_people.csv", origin, field, field, sheet_batch
        )
        _, v2 = await flows.sheet_version(
            s,
            ROOT / "data/messy_people_v2.csv",
            origin,
            field,
            field,
            sheet_batch,
            dataset,
        )
        rania = await flows.sheet_claim(s, v1, Prop.LAST_SEEN, "Branch 999")
        bassel = await flows.sheet_claim(s, v2, Prop.DETAINED_AT, "Branch 999")
        for st, why in (
            (await flows.sheet_claim(s, v1, Prop.NAME, "Rania Haddad"), "row 2"),
            (rania, "row 2, what happened and date columns"),
            (await flows.sheet_claim(s, v2, Prop.NAME, "Bassel Nader"), "row 16"),
            (bassel, "row 16, what happened and date columns"),
        ):
            await s.review(senior, st, "accept", why)
            await discovery.actor_changed(session, st)
        for st in (rania, bassel):
            await flows.confirm_place(s, senior, st)
        await session.commit()
    finally:
        shutil.rmtree(tmp)

    # The real discovery run over the accepted, dated, placed claims, then two
    # decisions: the March 2014 incident accepted for Lina and Reem (Rania, seen in
    # the visiting room, left out), and the May 2013 shared presence rejected: its
    # two people are one man recorded twice. The March shared presence stays open
    # for a reviewer to decide.
    reviewer = await session.get(User, senior.user_id)
    found = await flows.discover(
        s,
        reviewer,
        senior,
        accept={
            "kind": "candidate_incident",
            "month": "2014-03",
            "kept": [lina_held.id, reem_held.id],
            "roles": {L1: "victim", RM: "witness"},
            "title": "Women held at Branch 999, March 2014",
        },
        reject={
            "kind": "shared_presence",
            "month": "2013-05",
            "reason": "Both records are Yousef Khaled Al-Ali, read from the appeal "
            "and from its recapture. One man recorded twice, not two people "
            "together; propose a same-person link instead.",
        },
    )

    # Work spread across the team, one due soon and one overdue for the owner.
    blob = {k: f"blob:{v}" for k, v in s.blobs.items()}
    cell = await flows.open_finding(s, "shared_presence", "2013-07")
    await flows.assign(s, colleague, owner, [blob["transfer"]], 2, "high")
    await flows.assign(s, colleague, owner, [blob["witness"]], -3)
    await flows.assign(
        s, owner, colleague, [blob[k] for k in ("messy_people", "messy_people_v2")], 5
    )
    await flows.assign(s, owner, colleague, [blob["list"], blob["appeal"]], 9, "low")
    await flows.assign(s, owner, third, [f"finding:{cell.key}"], 3, "high")
    await flows.assign(
        s, owner, third, [blob[k] for k in ("checkpoint", "homs", "raqqa")], 7, "low"
    )
    await flows.assign(s, third, colleague, [blob["testimony"]], -1)
    done = await flows.assign(s, owner, third, [blob["market"]], 4, "low")
    await assignments.update(session, third, done[0], {"status": "done"})
    await session.commit()
    return {
        "investigation": CASE,
        "counts": {
            "documents": len(s.blobs),
            "watch hits": len(hits["written"]),
            "review requests": 3,
            "brief sentences": 2,
            "open findings": found["open"],
            "incidents": 1,
        },
    }
