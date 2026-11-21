"""The case's agents on the fixed answer (rules, no model), so their proposals wait
for a person. `agents` runs those the planted truth scores; `agent` runs one by
name (`linker`, `contradictions`, `gaps`); `brief` writes the intake brief for the
case's `batch`."""

from __future__ import annotations

import stk.extensions as ext
from sqlalchemy import func, select

from sealbook.agents import brief as briefs
from sealbook.agents import read
from sealbook.ledger.contracts import Prop
from sealbook.ledger.models import Statement
from sealbook.monitor.seed_study import agents_for, run_agent
from sealbook.monitor.study import step
from sealbook.runs.worker import process


async def _one(st, name: str) -> None:
    got = await run_agent(st.session, name, st.inv, st.owner)
    st.out[f"{name} proposals"] = len(got.get("written", got.get("items", [])))
    if name == "contradictions":
        # The planted disagreements the hunter set side by side, on what was written
        # for them (planted, or read by the app and recovered).
        items = [set(i["claims"]) for i in got.get("items", [])]
        planted = st.spec["truth"].get("contradictions") or []
        st.out["planted contradictions flagged"] = sum(
            any(
                all(k in st.written and str(st.written[k].id) in i for k in c["claims"])
                for i in items
            )
            for c in planted
        )


@step("agents")
async def agents(st, act, args) -> None:
    matches = await st.session.scalar(
        select(func.count())
        .select_from(Statement)
        .where(
            Statement.prop == Prop.MEDIA_MATCH,
            Statement.subject.in_([f"blob:{sha}" for sha in st.s.blobs.values()]),
        )
    )
    if matches:
        st.out["match proposals"] = matches
    for name in agents_for(st.spec):
        await _one(st, name)


@step("agent")
async def agent(st, act, args) -> None:
    await _one(st, args["agent"])


@step("brief")
async def brief(st, act, args) -> None:
    if not st.batch:
        return
    run = await briefs.queue(st.session, st.batch, st.owner.id, read.FIXED)
    await st.session.commit()
    await process(ext.async_session_factory, run.id)
    st.out["briefs"] = 1
