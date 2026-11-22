"""`sheets`: the act's spreadsheets go through the app's sheet intake. A person
confirms the column mapping the case gives (`sources.<key>.sheet`), which writes a
dataset version and releases the new rows, and `extract_sheet` itself reads them:
it pins every quote to its cell and writes one person per row. The model's answer
per row is fixed, copied from the case file's planted cell values, and the run
says so in its provenance; a row with no planted claim gets an empty answer. A
sheet whose columns are all marked Do not read is a dataset version whose rows
are released with no cell to read, so its answer is empty by construction.

Afterwards each planted claim of the sheet is matched to the statement written in
its cell (same row, column and property), and the record to the person written
for its row, so later steps act on what the app wrote. `out["sheets"]` counts
datasets, rows mapped, rows released, rows that named someone and claims written
and matched."""

from __future__ import annotations

import uuid

import stk.extensions as ext
from sqlalchemy import select

from sealbook.ledger import write
from sealbook.ledger.models import Blob, Run, Statement
from sealbook.monitor import seed_flows as flows
from sealbook.monitor.seed_study import ARABIC
from sealbook.monitor.study import step
from sealbook.runs.worker import process
from sealbook.sheets import service
from sealbook.sheets.extract import extract_sheet
from sealbook.sheets.models import DatasetVersion

LOCAL = ("match", "index")


def answers(st, key: str) -> dict[int, list[dict]]:
    """Row number -> the facts extraction is answered with: each planted claim of
    a record on that row, quoted from its cell."""
    out: dict[int, list[dict]] = {}
    for c in st.claims[key]:
        pin = c.get("pin") or {}
        if "row" not in pin:
            continue
        out.setdefault(pin["row"], []).append(
            {
                "prop": c["prop"],
                "column": pin["column"],
                "quote": c["value"],
                "lang": "ar" if ARABIC.search(c["value"]) else "en",
                "norm": c["value"].lower() if c["prop"] in ("sex", "age") else None,
                "kind": None,
                "date": None,
            }
        )
    return out


@step("sheets")
async def sheets(st, act, args) -> None:
    for key in act["sources"]:
        plan = st.spec["sources"][key].get("sheet")
        if plan:
            await _one(st, key, plan)


async def _one(st, key: str, plan: dict) -> None:
    s, db = st.s, st.session
    acq = await flows._acq(s, key)
    blob = await db.get(Blob, s.blobs[key])
    person = st.analyst  # the principal who confirms the mapping, as the page does
    item = {
        "key_column": plan["key_column"],
        "mapping": plan["mapping"],
        "sheet": 1,
        "dataset_name": plan["dataset"],
    }
    out = await service.confirm(db, acq, blob, item, person)
    version = await db.get(DatasetVersion, uuid.UUID(out["version"]["id"]))
    rows = [r["row"] for r in out["plan"]["rows"] if r["status"] in ("new", "changed")]
    # The run confirm queued when a route is configured, else the one it would have.
    run = await db.get(Run, version.run_id) if version.run_id else None
    run = run or Run(
        principal_id=st.extractor.id,
        tool=service.TOOL,
        route="local",
        inputs={
            "blobs": [blob.sha256],
            "acquisition": str(acq.id),
            "version": str(version.id),
            "sheet": version.sheet,
            "rows": rows,
            "key_column": version.key_column,
            "mapping": version.mapping,
            "confirmed_by": str(person.id),
        },
    )
    facts = answers(st, key)
    readable = any(f not in ("key", "ignore") for f in plan["mapping"].values())
    what = (
        "each row's answer copied from the case file's cell values"
        if readable
        else "an empty answer per row: every column but the key is marked Do not read"
    )
    c = await flows._start(
        s, run, acq, what, cls=flows.Fixed, answer=lambda r: facts.get(r["row"], [])
    )
    run.inputs = {**run.inputs, "answer": "case file"}
    version.run_id = run.id
    await write.seal_run(db, run, await extract_sheet(c))
    await db.commit()
    await _local(st, acq.id)
    matched = await _match(st, key, blob.sha256)
    total = st.out.setdefault(
        "sheets",
        {"datasets": 0, "rows": 0, "released": 0, "named": 0, "claims": 0},
    )
    total["datasets"] += 1
    total["rows"] += len(out["plan"]["rows"])
    total["released"] += len(rows)
    total["named"] += len({r for r in facts if r in rows})
    total["claims"] += matched


async def _local(st, acq_id) -> None:
    """The local runs the sheet's extraction queued (the watchlist match, the
    index), processed now as a worker keeping up would."""
    queued = await st.session.scalars(
        select(Run.id)
        .where(
            Run.status == "queued",
            Run.tool.in_(LOCAL),
            Run.inputs["acquisition"].astext == str(acq_id),
        )
        .order_by(Run.available_at)
    )
    for run_id in list(queued):
        await process(ext.async_session_factory, run_id)


async def _match(st, key: str, sha: str) -> int:
    """Each planted claim of the sheet to the statement written in its cell."""
    written = {
        (x.span["row"], x.span["column"], x.prop): x
        for x in await st.session.scalars(
            select(Statement).where(
                Statement.span_blob == sha, Statement.span.is_not(None)
            )
        )
        if x.subject.startswith("person:")
    }
    n = 0
    for c in st.claims[key]:
        pin = c.get("pin") or {}
        found = written.get((pin.get("row"), pin.get("column"), c["prop"]))
        if found is None:
            st.missed.append(c["key"])
            continue
        st.written[c["key"]] = found
        st.subjects.setdefault(c["record"], found.subject)
        n += 1
    return n
