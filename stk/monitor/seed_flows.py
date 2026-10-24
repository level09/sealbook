"""The parts of `stk demo-seed --full` that exercise one feature end to end each:
model output fixed for the demo (a transcript, sheet rows), the sheet intake with
two versions, a real discovery run with decisions on its findings, and work
assigned across the team. Every write goes through the path the product uses; the
only stand-in is the model, and its runs say so in their provenance."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

import orjson
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import func, select

import stk.extensions as ext
import stk.speech as speech
from stk import blobs
from stk.collaboration import assignments
from stk.collaboration import service as collaboration
from stk.collaboration.models import Assignment
from stk.discovery import service as discovery
from stk.discovery.models import FindingKey
from stk.ingest.service import ingest_bytes, system_agent
from stk.ledger import offline_sigs, write
from stk.ledger.contracts import Prop
from stk.ledger.models import Acquisition, Blob, Payload, Run, RunEvent, Statement
from stk.places import resolve
from stk.review import findings
from stk.review.views import _after
from stk.runs import routes
from stk.runs.worker import Ctx, work_once
from stk.settings import Config
from stk.sheets import service as sheets
from stk.sheets.extract import extract_sheet
from stk.sheets.models import DatasetVersion

FIXED = "sealbook-demo-fixed-output"


def note(what: str) -> str:
    return (
        f"demo: {what} written by stk demo-seed, no model ran; synthetic fixture "
        "output for showing the workflow, not a measurement"
    )


@dataclass
class Fixed(Ctx):
    """A worker context whose model call answers from a fixed table instead of a
    route, so a tool runs its own writer on known output."""

    answer: object = field(default=None)

    async def call(self, body: dict) -> routes.Served:
        rows = orjson.loads(body["messages"][-1]["content"])
        content = {"rows": [{"row": r["row"], "facts": self.answer(r)} for r in rows]}
        data = {
            "model": FIXED,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": orjson.dumps(content).decode()},
                }
            ],
        }
        raw = orjson.dumps(data)
        self.run.endpoint, self.run.model_served = "demo:fixed-output", FIXED
        self.run.provider_served = "demo"
        stored = await blobs.put(self.s, raw, "application/json", Config.BLOB_DIR)
        self.run.raw_output_ref = stored.sha256
        await self.emit("model_called", rows=len(rows))
        return routes.Served(data, raw, "demo:fixed-output", FIXED, "demo")


async def _start(s, run: Run, acq: Acquisition, what: str, cls=Ctx, **extra) -> Ctx:
    """Take `run` (queued or new) as a fixed-output run: its own key, provenance
    naming the demo, events numbered after any it has."""
    key = Ed25519PrivateKey.generate()
    run.pubkey = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    run.status, run.model_requested = "running", FIXED
    run.model_served, run.provider_served = FIXED, "demo"
    run.endpoint = "demo:fixed-output"
    run.inputs = {**run.inputs, "demo": note(what)}
    s.s.add(run)
    await s.s.flush()
    run.seq = await s.s.scalar(
        select(func.coalesce(func.max(RunEvent.seq), 0)).where(
            RunEvent.run_id == run.id
        )
    )

    async def emit(stage, drop_reason=None, **counts):
        await s.event(run, stage, counts, drop_reason)

    return cls(
        s=s.s,
        run=run,
        acq=acq,
        emit=emit,
        sign=lambda h: {"kind": "ed25519", "sig": key.sign(h.encode()).hex()},
        transport=None,
        **extra,
    )


async def _acq(s, key: str) -> Acquisition:
    return await s.s.scalar(
        select(Acquisition)
        .where(Acquisition.blob_sha256 == s.blobs[key])
        .order_by(Acquisition.at)
        .limit(1)
    )


async def page_text(s, key: str) -> None:
    """Run the real PDF text reader on `key` now, so claims can point at its page
    text; registers that text as `<key>_p1`."""
    await s.s.commit()
    while await work_once(ext.async_session_factory, ["pdf"]):
        pass
    ref = await s.s.scalar(
        select(Run.raw_output_ref).where(
            Run.tool == "pdf",
            Run.inputs["blobs"][0].astext == s.blobs[key],
            Run.status == "done",
        )
    )
    out = orjson.loads(blobs.read(await s.s.get(Blob, ref), Config.BLOB_DIR))
    sha = next(p["text"] for p in out["pages"] if p["page"] == 1)
    s.blobs[f"{key}_p1"] = sha
    s.texts[f"{key}_p1"] = blobs.read(
        await s.s.get(Blob, sha), Config.BLOB_DIR
    ).decode()


async def transcript(s, key: str, sentences: list[dict], duration_ms: int) -> None:
    """A transcription of `key` from fixed lines (the script the synthetic clip was
    spoken from), written as the transcribe tool writes: one derived text blob per
    line located by ms, indexed, and the joined transcript. The run ingest queued,
    if any, becomes this run, so the worker never transcribes the clip twice.
    Registers the transcript as `<key>_tx`."""
    acq = await _acq(s, key)
    blob = await s.s.get(Blob, s.blobs[key])
    run = await s.s.scalar(
        select(Run).where(
            Run.tool == "transcribe",
            Run.inputs["blobs"][0].astext == blob.sha256,
            Run.status == "queued",
        )
    ) or Run(
        principal_id=(await system_agent(s.s, "transcriber")).id,
        tool="transcribe",
        route="local",
    )
    # No extract_* inputs: the claims below are the demo's, not a model's.
    run.inputs = {"blobs": [blob.sha256], "acquisition": str(acq.id)}
    c = await _start(
        s, run, acq, "transcript copied from acceptance/data/expected.json"
    )
    segments = [
        {"ms": [x["start_ms"], x["end_ms"]], "text": x["text"]} for x in sentences
    ]
    await c.emit("input_read", ms=[0, duration_ms], duration_ms=duration_ms)
    for seg in segments:
        child = await blobs.put(
            s.s, seg["text"].encode(), "text/plain; charset=utf-8", Config.BLOB_DIR
        )
        await speech._derived(c, child, blob)
        await discovery.queue(
            s.s,
            acq,
            child.mime,
            blob_sha256=child.sha256,
            location={"ms": seg["ms"]},
            ordinal=seg["ms"][0],
        )
    artifact = await blobs.put(
        s.s,
        orjson.dumps(
            {
                "note": run.inputs["demo"],
                "source": blob.sha256,
                "read": [0, duration_ms],
                "duration_ms": duration_ms,
                "language": {"language": "ar", "probability": 1.0},
                "model": {"repo": FIXED, "demo": True},
                "segments": segments,
            }
        ),
        "application/json",
        Config.BLOB_DIR,
    )
    run.raw_output_ref = artifact.sha256
    lines = [seg["text"] for seg in segments]
    await speech._finish(c, blob, lines)
    await write.seal_run(s.s, run, artifact.sha256)
    text = "\n".join(lines)
    s.blobs[f"{key}_tx"] = hashlib.sha256(text.encode()).hexdigest()
    s.texts[f"{key}_tx"] = text


# --- sheets --------------------------------------------------------------------

MAPPING = {
    "No.": "key",
    "الشخص / person": "person",
    "ما حدث / what happened": "event",
    "المكان": "place",
    "التاريخ": "date",
    "مصدر المعلومة": "ignore",
}
PERSON, WHAT, PLACE, DATE = 2, 3, 4, 5
# What a careful reader takes from each row, keyed by the person cell: (prop,
# column, quote, lang, norm, event kind, dated). A dated fact quotes the row's date
# cell, as the tool's prompt asks. Hand-written for the two messy_people sheets.
ROWS = {
    "سامر خليل النجار - ذكر - 29 سنة - نجار": [
        ("name", PERSON, "سامر خليل النجار", "ar", None, None, False),
        ("sex", PERSON, "ذكر", "ar", "male", None, False),
        ("age", PERSON, "29 سنة", "ar", "29", None, False),
        ("arrested_at", WHAT, "حرستا", "ar", None, None, True),
        ("transferred_to", WHAT, "الفرع 999", "ar", None, None, True),
    ],
    "Rania Haddad (female), teacher, 41": [
        ("name", PERSON, "Rania Haddad", "en", None, None, False),
        ("sex", PERSON, "female", "en", "female", None, False),
        ("occupation", PERSON, "teacher", "en", None, None, False),
        ("age", PERSON, "41", "en", "41", None, False),
        ("last_seen", WHAT, "Branch 999", "en", None, None, True),
    ],
    "يوسف عمر الحلبي ١٧ سنة طالب": [
        ("name", PERSON, "يوسف عمر الحلبي", "ar", None, None, False),
        ("age", PERSON, "١٧ سنة", "ar", "17", None, False),
        ("occupation", PERSON, "طالب", "ar", None, None, False),
        ("event", WHAT, "قتل بقصف على السوق", "ar", None, "killed", True),
    ],
    "Khaled M. - male - about thirty": [
        ("name", PERSON, "Khaled M.", "en", None, None, False),
        ("sex", PERSON, "male", "en", "male", None, False),
        ("age", PERSON, "about thirty", "en", "30", None, False),
        ("detained_at", WHAT, "Central Prison K", "en", None, None, True),
        ("event", WHAT, "released later", "en", None, "released", True),
    ],
    "ام سليم حوالي ستين سنة ربة منزل": [
        ("name", PERSON, "ام سليم", "ar", None, None, False),
        ("age", PERSON, "حوالي ستين سنة", "ar", "60", None, False),
        ("occupation", PERSON, "ربة منزل", "ar", None, None, False),
        ("event", WHAT, "نزحت من بابا عمرو مع أحفادها", "ar", None, "displaced", True),
    ],
    "Nour al-Din Sabbagh, 35, pharmacist": [
        ("name", PERSON, "Nour al-Din Sabbagh", "en", None, None, False),
        ("age", PERSON, "35", "en", "35", None, False),
        ("occupation", PERSON, "pharmacist", "en", None, None, False),
        (
            "event",
            WHAT,
            "Missing since he was stopped at a checkpoint",
            "en",
            None,
            "missing",
            True,
        ),
    ],
    "ليلى فؤاد العلي (أنثى) ممرضة": [
        ("name", PERSON, "ليلى فؤاد العلي", "ar", None, None, False),
        ("sex", PERSON, "أنثى", "ar", "female", None, False),
        ("occupation", PERSON, "ممرضة", "ar", None, None, False),
        ("last_seen", WHAT, "سجن ك المركزي", "ar", None, None, True),
    ],
    "Omar Taha engineer male 52": [
        ("name", PERSON, "Omar Taha", "en", None, None, False),
        ("occupation", PERSON, "engineer", "en", None, None, False),
        ("sex", PERSON, "male", "en", "male", None, False),
        ("age", PERSON, "52", "en", "52", None, False),
        ("arrested_at", PLACE, "Douma", "en", None, None, True),
    ],
    "محمد (لم يذكر اسمه الكامل) عامل بناء": [
        ("name", PERSON, "محمد", "ar", None, None, False),
        ("occupation", PERSON, "عامل بناء", "ar", None, None, False),
        ("event", WHAT, "اختفى بعد مداهمة منزله", "ar", None, "missing", True),
    ],
    "Huda S. - female - 23": [
        ("name", PERSON, "Huda S.", "en", None, None, False),
        ("sex", PERSON, "female", "en", "female", None, False),
        ("age", PERSON, "23", "en", "23", None, False),
        (
            "event",
            WHAT,
            "Wounded by sniper fire near the bakery",
            "en",
            None,
            "wounded",
            True,
        ),
    ],
    "عبد الله ياسين الكردي ذكر 44 سائق تكسي": [
        ("name", PERSON, "عبد الله ياسين الكردي", "ar", None, None, False),
        ("sex", PERSON, "ذكر", "ar", "male", None, False),
        ("age", PERSON, "44", "ar", "44", None, False),
        ("occupation", PERSON, "سائق تكسي", "ar", None, None, False),
        ("event", WHAT, "انشق عن الجيش", "ar", None, "defected", True),
    ],
    "Salma Darwish, 19, student": [
        ("name", PERSON, "Salma Darwish", "en", None, None, False),
        ("age", PERSON, "19", "en", "19", None, False),
        ("occupation", PERSON, "student", "en", None, None, False),
        ("detained_at", WHAT, "Branch 999", "en", None, None, True),
    ],
    "طارق نبيل حداد - ٣٨ - محامي": [
        ("name", PERSON, "طارق نبيل حداد", "ar", None, None, False),
        ("age", PERSON, "٣٨", "ar", "38", None, False),
        ("occupation", PERSON, "محامي", "ar", None, None, False),
        ("transferred_to", WHAT, "سجن ك المركزي", "ar", None, None, True),
    ],
    "Faten (no surname given), nurse": [
        ("name", PERSON, "Faten", "en", None, None, False),
        ("occupation", PERSON, "nurse", "en", None, None, False),
        ("seen_at", PLACE, "Aleppo", "en", None, None, True),
    ],
    "حسن أديب قاسم ذكر 60 مزارع": [
        ("name", PERSON, "حسن أديب قاسم", "ar", None, None, False),
        ("sex", PERSON, "ذكر", "ar", "male", None, False),
        ("age", PERSON, "60", "ar", "60", None, False),
        ("occupation", PERSON, "مزارع", "ar", None, None, False),
        ("event", WHAT, "مفقود", "ar", None, "missing", True),
    ],
    "Bassel Nader male 27 mechanic": [
        ("name", PERSON, "Bassel Nader", "en", None, None, False),
        ("sex", PERSON, "male", "en", "male", None, False),
        ("age", PERSON, "27", "en", "27", None, False),
        ("occupation", PERSON, "mechanic", "en", None, None, False),
        ("detained_at", WHAT, "Branch 999", "en", None, None, True),
    ],
    "رنا ماهر الخطيب أنثى 31 صيدلانية": [
        ("name", PERSON, "رنا ماهر الخطيب", "ar", None, None, False),
        ("sex", PERSON, "أنثى", "ar", "female", None, False),
        ("age", PERSON, "31", "ar", "31", None, False),
        ("occupation", PERSON, "صيدلانية", "ar", None, None, False),
        ("event", WHAT, "قتلت في قصف على حي بابا عمرو", "ar", None, "killed", True),
    ],
}


def answer(row: dict) -> list[dict]:
    cells = {c["column"]: c["text"] for c in row["cells"]}
    return [
        {
            "prop": prop,
            "column": column,
            "quote": quote,
            "lang": lang,
            "norm": norm,
            "kind": kind,
            "date": cells.get(DATE) if dated else None,
        }
        for prop, column, quote, lang, norm, kind, dated in ROWS[cells[PERSON]]
    ]


async def sheet_version(s, path, origin, collector, person, batch, dataset=None):
    """Ingest one sheet, confirm the mapping a person chose (the diff against the
    dataset's last version included), and read the released rows with fixed
    answers through extract_sheet itself."""
    sha = (
        await ingest_bytes(
            s.s,
            path.read_bytes(),
            blob_dir=Config.BLOB_DIR,
            mime="text/csv",
            origin=origin,
            collector_id=collector.id,
            doc_type="intake_sheet",
            sensitivity="ordinary",
            context={
                "investigation": s.case,
                "file": path.name,
                "title": f"Family intake sheet, version {2 if dataset else 1}",
                "batch": batch,
            },
        )
    )["sha256"]
    s.blobs[path.stem] = sha
    acq, blob = await _acq(s, path.stem), await s.s.get(Blob, sha)
    item = {"key_column": "No.", "mapping": MAPPING, "sheet": 1}
    item |= {"dataset": dataset} if dataset else {"dataset_name": "Family intake"}
    out = await sheets.confirm(s.s, acq, blob, item, person)
    version = await s.s.get(DatasetVersion, uuid.UUID(out["version"]["id"]))
    rows = [r["row"] for r in out["plan"]["rows"] if r["status"] in ("new", "changed")]
    # The run confirm queued when a model is configured, else the one it would have.
    run = await s.s.get(Run, version.run_id) if version.run_id else None
    run = run or Run(
        principal_id=(await system_agent(s.s, "extractor")).id,
        tool=sheets.TOOL,
        route="local",
        inputs={
            "blobs": [sha],
            "acquisition": str(acq.id),
            "version": str(version.id),
            "sheet": version.sheet,
            "rows": rows,
            "key_column": version.key_column,
            "mapping": version.mapping,
            "confirmed_by": str(person.id),
        },
    )
    c = await _start(
        s, run, acq, "answers hand-written per row", cls=Fixed, answer=answer
    )
    version.run_id = run.id
    await write.seal_run(s.s, run, await extract_sheet(c))
    await s.s.commit()
    return out["version"]["dataset"], sha


async def sheet_claim(s, sha: str, prop: Prop, text: str) -> Statement:
    return (
        await s.s.execute(
            select(Statement)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(
                Statement.span_blob == sha,
                Statement.prop == prop,
                Payload.value["text"].astext == text,
            )
        )
    ).scalar_one()


async def confirm_place(s, human, claim: Statement) -> None:
    """The person's place choice for one claim, as the confirm endpoint writes it."""
    name = (await s.s.get(Payload, claim.commitment)).value["text"]
    place = (await resolve.candidates(s.s, name))[0]
    await write.append(
        s.s, resolve.confirmation(claim.id, place.id, name, human.id), sign=s.assertion
    )


# --- findings ------------------------------------------------------------------


async def gesture(s, user, statements) -> list[Statement]:
    """Commit a planned finding decision the way /api/review/commit does: one
    passkey assertion over every row's hash, each row carrying the batch."""
    prepared = [await write.prepare(s.s, st) for st in statements]
    hashes = [p.hash for p in prepared]
    sig = s.assertion(offline_sigs.gesture_digest(hashes)) | {"batch": hashes}
    await write.lock_chain(s.s)
    written = []
    for one, p in zip(statements, prepared, strict=True):
        stmt = await write.commit(s.s, p, sig)
        await _after(s.s, one, stmt)
        written.append(stmt)
    await s.s.commit()
    return written


async def open_finding(s, kind: str, month: str) -> FindingKey:
    rows = (
        await s.s.execute(
            select(FindingKey, Payload.value)
            .join(Statement, Statement.id == FindingKey.statement_id)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(
                FindingKey.investigation == s.case,
                FindingKey.kind == kind,
                FindingKey.state == "open",
            )
        )
    ).all()
    found = [fk for fk, v in rows if v["month"] == month]
    if len(found) != 1:
        raise RuntimeError(f"expected one open {kind} in {month}, found {len(found)}")
    return found[0]


async def run_discover(s) -> None:
    """Queue and run the real discovery tool for the case."""
    s.s.add(
        Run(
            principal_id=(await system_agent(s.s, "discover")).id,
            tool="discover",
            route="local",
            inputs={"investigation": s.case},
        )
    )
    await s.s.commit()
    while await work_once(ext.async_session_factory, ["discover"]):
        pass


async def discover(s, user, human, accept: dict, reject: dict) -> dict:
    """Queue and run the real discovery tool for the case, then decide two of its
    findings through the review plan: accept a candidate incident with some of its
    members, reject a shared presence with a reason."""
    await run_discover(s)
    incident = await open_finding(s, accept["kind"], accept["month"])
    _, planned = await findings.plan(
        s.s,
        user,
        incident.key,
        [str(k) for k in accept["kept"]],
        accept["roles"],
        accept["title"],
        None,
        human.id,
    )
    written = await gesture(s, user, planned)
    rejected = await open_finding(s, reject["kind"], reject["month"])
    _, planned = await findings.reject(
        s.s, user, rejected.key, reject["reason"], human.id
    )
    await gesture(s, user, planned)
    return {
        "incident": next(w.subject for w in written if w.prop == Prop.INCIDENT),
        "open": await s.s.scalar(
            select(func.count()).where(
                FindingKey.investigation == s.case, FindingKey.state == "open"
            )
        ),
    }


# --- team work -----------------------------------------------------------------


async def assign(s, by, to, subjects, days, priority="normal") -> list[uuid.UUID]:
    """Assign as the bulk endpoint does; `days` from today, negative is overdue."""
    due = (date.today() + timedelta(days=days)).isoformat()
    await assignments.bulk(
        s.s,
        by,
        {
            "investigation": s.case,
            "assignee": to.id,
            "subjects": subjects,
            "due": due,
            "priority": priority,
        },
    )
    return list(
        await s.s.scalars(
            select(Assignment.id).where(
                Assignment.subject.in_(subjects),
                Assignment.status == "open",
            )
        )
    )


async def request(s, proposal, sender, reviewer, reason) -> Statement:
    """A review request and its notification, as the requests endpoint sends them."""
    row = await collaboration.request_review(s.s, proposal, sender, reviewer.id, reason)
    await s.s.commit()
    await collaboration.assigned(s.s, reviewer.id, row.id, proposal.id)
    return row


async def reply(s, request_id, author, text) -> None:
    """A thread comment and its reply or mention notifications."""
    found = await collaboration.comment(s.s, request_id, author, text)
    await s.s.commit()
    await collaboration.comment_notices(s.s, request_id, found, author, text)


async def agents(session, owner, batches: list[str], colleague=None) -> dict:
    """The linker and the intake brief for each batch, run by the worker's own code
    on the fixed answer (`sealbook-demo-fixed-output`, a demo note in the inputs).
    Batches belong to whoever collected them: the owner's, or the colleague's."""
    from stk.agents import brief, linker
    from stk.agents.read import FIXED
    from stk.runs.worker import process

    links = briefs = 0
    for batch in batches:
        collector = await session.scalar(
            select(Acquisition.collector_id)
            .where(Acquisition.context["batch"].astext == batch)
            .limit(1)
        )
        user = owner
        if colleague is not None:
            mine = await write.principal_for_user(session, owner.id, owner.name)
            user = owner if collector == mine.id else colleague
        case = await session.scalar(
            select(Acquisition.context["investigation"].astext)
            .where(Acquisition.context["batch"].astext == batch)
            .limit(1)
        )
        found = await linker.queue(session, case, user.id, FIXED, f"batch:{batch}")
        told = await brief.queue(session, batch, user.id, FIXED)
        await session.commit()
        for run in (found, told):
            await process(ext.async_session_factory, run.id)
        await session.refresh(found)
        out = orjson.loads(
            blobs.read(await session.get(Blob, found.output_hash), Config.BLOB_DIR)
        )
        links += len(out["written"])
        briefs += 1
    return {"links": links, "briefs": briefs}
