"""`stk demo-seed`: a small synthetic case shaped like acceptance/case.json, written
through the real write path. Blobs are UTF-8 text stand-ins for the case files, so
character spans point at real bytes. Every person and facility is invented."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta

import cbor2
import click
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from quart.cli import ScriptInfo
from quart_security.utils import naive_utcnow
from quart_security.webauthn import bytes_to_base64url
from sqlalchemy import select

import stk.extensions as ext
from stk import blobs
from stk.cases.service import ensure as ensure_case
from stk.cli.base import console, run_async
from stk.discovery import service as discovery
from stk.ledger.contracts import Event, Prop, StatementIn
from stk.ledger.contracts import EvidenceKind as E
from stk.ledger.models import (
    Acquisition,
    Payload,
    Policy,
    Principal,
    Run,
    Statement,
)
from stk.ledger.write import append, emit, record_acquisition, seal_run, subject_head
from stk.monitor.data import LOCATED, display
from stk.places import cli as places
from stk.places import resolve
from stk.settings import Config
from stk.user.models import User, WebAuthn

CASE = "case-demo-01"
P1, P2 = (
    f"person:{uuid.uuid5(uuid.NAMESPACE_URL, f'sealbook-demo/{CASE}/{p}')}"
    for p in ("P1", "P2")
)

REGISTER = """سجل الموقوفين اليومي
الفرع 999
التاريخ: 2013-05-03

1. الاسم: يوسف خالد العلي | اسم الأب: خالد | اسم الأم: فاطمة | المواليد: 1991 | مكان التوقيف: حرستا | تاريخ التوقيف: 2013-05-02
2. الاسم: يوسف خالد العلي | اسم الأب: خالد | اسم الأم: سعاد | المواليد: 1985
"""
TRANSFER = """الجمهورية العربية السورية
الفرع 999
أمر نقل رقم 4471
التاريخ: 2013-09-17
ينقل الموقوف: يوسف خالد علي | المواليد: 1991-03-14
من: الفرع 999
إلى: سجن ك المركزي
"""
WITNESS = """[00:00] الشاهد: اسمي سامر.
[00:06] شفت يوسف بالفرع 999 بشهر تموز 2013، كان معنا بنفس المهجع.
[00:21] (ضجيج، مقطع غير مفهوم)
[00:24] بعدها ما عاد شفته.
"""
PHOTO = "JPEG stand-in: a damaged building (the real photograph is acceptance/data/photo_damaged_building.jpg). C2PA manifest present (test).\n"
POST = """<h1>Missing: Yousef Khaled Al-Ali</h1>
<p>Yousef was last seen in دوما on 2013-05-02. Anyone with information, please contact the family.</p>
"""

DOCS = {
    "register": (
        "register_page.png",
        REGISTER,
        "device:scanner-01",
        "detention_register",
        "sensitive",
    ),
    "transfer": (
        "transfer_order.pdf",
        TRANSFER,
        "channel:partner-a/batch-7",
        "transfer_order",
        "sensitive",
    ),
    "witness": (
        "witness_clip.ogg",
        WITNESS,
        "device:recorder-03",
        "witness_statement",
        "sensitive",
    ),
    "photo": (
        "photo_damaged_building.jpg",
        PHOTO,
        "device:camera-c2pa-01",
        "photo",
        "ordinary",
    ),
    "post": (
        "missing_post.html",
        POST,
        "url:http://127.0.0.1/synthetic/missing-post",
        "web_post",
        "ordinary",
    ),
}


def span(text: str, needle: str, after: str = "") -> dict:
    start = text.index(needle, text.index(after) + len(after) if after else 0)
    return {"start": start, "end": start + len(needle)}


def ar(text: str) -> dict:
    return {"text": text, "lang": "ar"}


class Seeder:
    def __init__(self, session):
        self.s = session
        self.blobs: dict[str, str] = {}
        self.texts = {k: v[1] for k, v in DOCS.items()}

    async def principal(self, name, kind="agent", on_behalf_of=None):
        p = Principal(kind=kind, name=name, on_behalf_of=on_behalf_of)
        self.s.add(p)
        await self.s.flush()
        return p

    async def analyst(self, name, case=CASE):
        """A human principal whose reviews carry real passkey signatures: an
        inactive demo user with a software ed25519 credential, so verification and
        the gate treat them exactly like a browser assertion."""
        user = User(
            email=f"{name}@{case}.invalid",
            password=os.urandom(32).hex(),
            active=False,
            fs_webauthn_user_handle=os.urandom(16).hex(),
        )
        self.s.add(user)
        await self.s.flush()
        self.passkey = Ed25519PrivateKey.generate()
        self.cose = cbor2.dumps(
            {
                1: 1,
                3: -8,
                -1: 6,
                -2: self.passkey.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                ),
            }
        )
        self.credential_id = os.urandom(16)
        self.s.add(
            WebAuthn(
                credential_id=self.credential_id,
                public_key=self.cose,
                sign_count=0,
                lastuse_datetime=naive_utcnow(),
                name="demo passkey",
                usage="secondary",
                backup_state=False,
                device_type="single_device",
                user_id=user.fs_webauthn_user_handle,
            )
        )
        p = Principal(kind="human", name=name, user_id=user.id)
        self.s.add(p)
        await self.s.flush()
        return p

    def assertion(self, digest):
        rp_id, origin = "localhost", "http://localhost"
        auth = hashlib.sha256(rp_id.encode()).digest() + bytes([0x05]) + bytes(4)
        client = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": bytes_to_base64url(bytes.fromhex(digest)),
                "origin": origin,
            }
        ).encode()
        signed = self.passkey.sign(auth + hashlib.sha256(client).digest())
        return {
            "kind": "webauthn",
            "challenge": digest,
            "credential_id": bytes_to_base64url(self.credential_id),
            "authenticator_data": bytes_to_base64url(auth),
            "client_data_json": bytes_to_base64url(client),
            "signature": bytes_to_base64url(signed),
            "public_key": bytes_to_base64url(self.cose),
            "rp_id": rp_id,
            "origin": origin,
            "demo": True,
        }

    async def run(self, agent, tool, doc_keys, **fields):
        key = Ed25519PrivateKey.generate()
        run = Run(
            principal_id=agent.id,
            tool=tool,
            inputs={"blobs": [self.blobs[k] for k in doc_keys]},
            pubkey=key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
            status="running",
            **fields,
        )
        self.s.add(run)
        await self.s.flush()
        run.key, run.seq = key, 0
        return run

    async def event(self, run, stage, counts=None, drop_reason=None):
        run.seq += 1
        await emit(
            self.s,
            Event(
                run_id=run.id,
                seq=run.seq,
                stage=stage,
                counts=counts or {},
                drop_reason=drop_reason,
            ),
        )

    async def say(
        self,
        run,
        doc,
        subject,
        prop,
        value,
        needle=None,
        after="",
        valid=None,
        evidence=(),
        at=None,
    ):
        """`at` is an explicit span (a page bbox) for sources without text."""
        if needle:
            at = span(self.texts[doc], needle, after)
        lo, hi = (
            (date.fromisoformat(valid[0]), date.fromisoformat(valid[-1]))
            if valid
            else (None, None)
        )
        st = StatementIn(
            subject=subject,
            prop=prop,
            value=value,
            author_id=run.principal_id,
            run_id=run.id,
            span_blob=self.blobs[doc] if at else None,
            span=at,
            valid_from=lo,
            valid_to=hi,
            evidence=tuple(evidence),
            # The head the author saw; review shows the card stale if it moves.
            based_on=await subject_head(self.s, subject),
        )
        return await append(
            self.s,
            st,
            sign=lambda h: {"kind": "ed25519", "sig": run.key.sign(h.encode()).hex()},
        )

    async def done(self, run, statements):
        await self.event(run, "done", {"statements": statements})
        await seal_run(
            self.s, run, output_hash=hashlib.sha256(run.id.bytes).hexdigest()
        )

    async def review(self, human, target, verdict, reason, prop=Prop.REVIEW):
        return await append(
            self.s,
            StatementIn(
                subject=f"statement:{target.id}",
                prop=prop,
                value={"verdict": verdict, "reason": reason}
                if prop == Prop.REVIEW
                else {"reason": reason, "at": datetime.now(UTC).isoformat()},
                author_id=human.id,
            ),
            sign=self.assertion,
        )


async def seed(session) -> dict:
    """Write the demo case. Commits per step so an open monitor sees it stream in."""
    s = Seeder(session)
    if await seeded(session):
        raise click.ClickException(f"{CASE} is already seeded")
    await ensure_case(session, CASE)

    analyst = await s.analyst("analyst-1")
    hasher = await s.principal("hasher")
    extractor = await s.principal("extractor")
    asr = await s.principal("transcriber")
    c2pa = await s.principal("c2pa-verifier")
    checker = await s.principal("url-checker")
    importer = await s.principal("importer")
    assistant = await s.principal("case-assistant", on_behalf_of=analyst.id)
    # Corroboration needs a calibrated policy. This one is synthetic: no measured
    # error rate stands behind it, so it exists only in demo databases.
    session.add(
        Policy(
            capability="extract",
            doc_type="*",
            sensitivity="*",
            routes=["local"],
            min_origins=2,
            calibration={
                "synthetic": True,
                "note": "demo only, not a measured error rate",
            },
        )
    )

    captured = datetime.now(UTC) - timedelta(days=2)
    acqs = []
    for i, (key, (file, text, origin, doc_type, sensitivity)) in enumerate(
        DOCS.items()
    ):
        raw = text.encode()
        mime = "text/html" if file.endswith(".html") else "text/plain"
        sha = (await blobs.put(session, raw, mime, Config.BLOB_DIR)).sha256
        s.blobs[key] = sha
        acq = await record_acquisition(
            session,
            blob_sha256=sha,
            origin=origin,
            collector_id=importer.id if origin.startswith("channel:") else analyst.id,
            at=captured + timedelta(hours=i),
            context={"investigation": CASE, "file": file, "stand_in": True},
            sensitivity=sensitivity,
            doc_type=doc_type,
        )
        # Index and embed like any intake, so the case is searchable after the worker.
        await discovery.queue(session, acq, mime)
        acqs.append(acq)
        run = await s.run(hasher, "hash", [key])
        await s.event(run, "acquired", {"bytes": len(raw)})
        await s.say(
            run,
            key,
            f"blob:{sha}",
            Prop.SHA256,
            {"hex": sha},
            evidence=[E.BYTE_EQUALITY],
        )
        await s.say(
            run, key, f"blob:{sha}", Prop.MD5, {"hex": hashlib.md5(raw).hexdigest()}
        )  # noqa: S324
        await s.done(run, 2)
        await session.commit()

    llm = {
        "route": "local",
        "endpoint": "http://127.0.0.1:8000/v1",
        "model_requested": "qwen3-vl-4b",
        "model_served": "qwen3-vl-4b-sy-demo",
        "provider_served": "local-vllm",
        "prompt_hash": "0" * 64,
    }

    run = await s.run(extractor, "extract", ["register"], **llm)
    await s.event(run, "started", {"pages": 1})
    await s.event(run, "page_read", {"page": 1, "lines": 6})
    say = s.say
    await say(
        run,
        "register",
        f"blob:{s.blobs['register']}",
        Prop.DOCUMENT_DATE,
        {"date": "2013-05-03"},
        "2013-05-03",
        valid=["2013-05-03"],
        evidence=[E.HANDWRITTEN_ENTRY],
    )
    p1_name = await say(
        run,
        "register",
        P1,
        Prop.NAME,
        ar("يوسف خالد العلي"),
        "يوسف خالد العلي",
        after="1.",
        evidence=[E.HANDWRITTEN_ENTRY],
    )
    await say(
        run,
        "register",
        P1,
        Prop.FATHER_NAME,
        ar("خالد"),
        "خالد",
        after="1. الاسم: يوسف خالد العلي | اسم الأب: ",
    )
    await say(run, "register", P1, Prop.MOTHER_NAME, ar("فاطمة"), "فاطمة", after="1.")
    await say(
        run, "register", P1, Prop.BIRTH_DATE, {"date": "1991"}, "1991", after="1."
    )
    await say(
        run,
        "register",
        P1,
        Prop.ARRESTED_AT,
        ar("حرستا"),
        "حرستا",
        after="1.",
        valid=["2013-05-02"],
        evidence=[E.HANDWRITTEN_ENTRY],
    )
    p1_branch = await say(
        run,
        "register",
        P1,
        Prop.DETENTION_BRANCH,
        ar("الفرع 999"),
        "الفرع 999",
        evidence=[E.LETTERHEAD],
    )
    await say(
        run,
        "register",
        P2,
        Prop.NAME,
        ar("يوسف خالد العلي"),
        "يوسف خالد العلي",
        after="2.",
    )
    wrong = await say(
        run, "register", P2, Prop.MOTHER_NAME, ar("فاطمة"), "فاطمة", after="1."
    )
    await say(run, "register", P2, Prop.MOTHER_NAME, ar("سعاد"), "سعاد", after="2.")
    await say(
        run, "register", P2, Prop.BIRTH_DATE, {"date": "1985"}, "1985", after="2."
    )
    await s.event(run, "statements", {"proposed": 11, "with_span": 11})
    await s.done(run, 11)
    await session.commit()

    await s.review(analyst, p1_name, "accept", "matches register row 1")
    await s.review(analyst, p1_branch, "accept", "register header")
    await s.review(analyst, wrong, "reject", "mother's name read from row 1")
    await session.commit()

    run = await s.run(extractor, "extract", ["transfer"], **llm)
    await s.event(run, "started", {"pages": 1})
    await say(
        run,
        "transfer",
        f"blob:{s.blobs['transfer']}",
        Prop.DOCUMENT_DATE,
        {"date": "2013-09-17"},
        "2013-09-17",
        valid=["2013-09-17"],
        evidence=[E.PRINTED_TEXT],
    )
    await say(
        run,
        "transfer",
        P1,
        Prop.NAME,
        ar("يوسف خالد علي"),
        "يوسف خالد علي",
        evidence=[E.PRINTED_TEXT],
    )
    await say(
        run,
        "transfer",
        P1,
        Prop.BIRTH_DATE,
        {"date": "1991-03-14"},
        "1991-03-14",
        evidence=[E.PRINTED_TEXT],
    )
    await say(
        run,
        "transfer",
        P1,
        Prop.TRANSFERRED_TO,
        ar("سجن ك المركزي"),
        "سجن ك المركزي",
        valid=["2013-09-17"],
        evidence=[E.PRINTED_TEXT, E.REGISTRY_STAMP],
    )
    await say(
        run,
        "transfer",
        P1,
        Prop.DETENTION_BRANCH,
        ar("الفرع 999"),
        "الفرع 999",
        evidence=[E.LETTERHEAD],
    )
    await s.event(run, "statements", {"proposed": 5, "with_span": 5})
    await s.done(run, 5)
    await session.commit()

    run = await s.run(
        asr,
        "asr",
        ["witness"],
        **(
            llm
            | {
                "model_requested": "whisper-large-v3-turbo",
                "model_served": "whisper-large-v3-turbo-sy-demo",
            }
        ),
    )
    await s.event(run, "started", {"seconds": 40})
    await s.event(run, "segment", {"segment": 1, "ms": [0, 6000]})
    await s.event(run, "segment", {"segment": 2, "ms": [6000, 21000]})
    await s.event(
        run,
        "segment_unreadable",
        {"segment": 3, "ms": [21000, 24000]},
        drop_reason="unreadable audio",
    )
    await s.event(run, "segment", {"segment": 4, "ms": [24000, 40000]})
    await s.done(run, 0)
    run = await s.run(extractor, "extract", ["witness"], **llm)
    await s.event(run, "started", {"segments": 4})
    await say(
        run,
        "witness",
        P1,
        Prop.SEEN_AT,
        ar("الفرع 999"),
        "الفرع 999",
        valid=["2013-07-01", "2013-07-31"],
        evidence=[E.SPOKEN],
    )
    await s.done(run, 1)
    await session.commit()

    run = await s.run(c2pa, "c2pa_verify", ["photo"])
    await say(
        run,
        "photo",
        f"blob:{s.blobs['photo']}",
        Prop.C2PA_VERIFY,
        {"result": "valid"},
        evidence=[E.C2PA_MANIFEST],
    )
    await s.done(run, 1)
    run = await s.run(importer, "import", ["post"])
    await s.event(run, "fetched", {"bytes": len(POST.encode())})
    await say(
        run,
        "post",
        P1,
        Prop.NAME,
        {"text": "Yousef Khaled Al-Ali", "lang": "en"},
        "Yousef Khaled Al-Ali",
        evidence=[E.PRINTED_TEXT],
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
    run = await s.run(checker, "url_check", ["post"])
    await s.event(run, "refetched", {"changed": 1})
    await say(
        run,
        "post",
        f"blob:{s.blobs['post']}",
        Prop.URL_LIVENESS,
        {"text": "edited after capture", "lang": "en"},
    )
    await s.done(run, 1)
    await session.commit()

    # An external agent working for analyst-1 over MCP. Its date lands after the
    # cited document, which the review card's invariant catches.
    run = await s.run(assistant, "propose", ["transfer"], route="cloud-zdr")
    await s.event(run, "proposed", {"statements": 1})
    await say(
        run,
        "transfer",
        P1,
        Prop.DETAINED_AT,
        ar("الفرع 999"),
        "الفرع 999",
        after="من: ",
        valid=["2013-10-01"],
        evidence=[E.PRINTED_TEXT],
    )
    await s.done(run, 1)
    await locate(s, analyst)
    for person in (P1, P2):
        await discovery.queue_actor(session, person, acqs[0])
    await session.commit()
    return {"investigation": CASE, "blobs": s.blobs, "P1": P1, "P2": P2}


async def locate(s: Seeder, human) -> None:
    """Import the sample places and have `human` confirm a place for every position
    claim (a person decides each name; ambiguous ones are never auto-resolved)."""
    await places.import_file(s.s, places.FIXTURE)
    claims = (
        await s.s.execute(
            select(Statement.id, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(Statement.prop.in_(LOCATED))
        )
    ).all()
    for sid, value in claims:
        found = await resolve.candidates(s.s, display(value))
        # Prefer the village or facility over a governorate sharing its name.
        found.sort(key=lambda p: p.kind == "governorate")
        if not found:
            continue
        await append(
            s.s,
            resolve.confirmation(sid, found[0].id, display(value), human.id),
            sign=s.assertion,
        )


async def seeded(session, case=CASE) -> bool:
    return bool(
        (
            await session.execute(
                select(Acquisition.id)
                .where(Acquisition.context["investigation"].astext == case)
                .limit(1)
            )
        ).scalar()
    )


@click.command("demo-seed")
@click.option(
    "--full",
    is_flag=True,
    help="Also seed case-demo-02: real fixture files, colleagues, watchlist, brief.",
)
@click.option(
    "--more",
    is_flag=True,
    help="Also seed case-demo-03 to 06: siege, displacement, detention, shelling.",
)
@click.option(
    "--scale",
    type=click.IntRange(min=0),
    default=0,
    help="Also add N synthetic filler sources to case-scale-demo (no model runs).",
)
def demo_seed(full, more, scale):
    """Write a small synthetic case through the ledger write path (dev only)."""
    app = click.get_current_context().ensure_object(ScriptInfo).load_app()
    if app.config.get("STK_ENV") not in ("development", "testing"):
        raise click.ClickException("demo-seed writes synthetic data; development only")

    async def _run():
        from stk.monitor.seed_full import seed_full
        from stk.monitor.seed_more import seed_more
        from stk.sources import scale as filler

        async with ext.async_session_factory() as session:
            if scale and await seeded(session, filler.DEMO):
                raise click.ClickException(f"{filler.DEMO} is already seeded")
            if not (more or scale) or full and not await seeded(session):
                result = await seed(session)
                console.print(
                    f"Seeded {result['investigation']}: {len(result['blobs'])} documents."
                )
            if full:
                out = await seed_full(session, app)
                console.print(
                    f"Seeded {out['investigation']}: "
                    + ", ".join(f"{v} {k}" for k, v in out["counts"].items())
                )
            if more:
                for case, got in (await seed_more(session, app))["cases"].items():
                    console.print(
                        f"Seeded {case}: {got['documents']} documents, "
                        f"{got['claims']} position claims."
                    )
            if scale:
                await filler.fillers(session, scale)
                await session.commit()
                console.print(f"Seeded {filler.DEMO}: {scale:,} synthetic sources.")

    run_async(_run())
    console.print("Open /monitor")
