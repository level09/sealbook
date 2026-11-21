"""A standing question that watches the case and wakes when new evidence arrives.

- `question`: the case owner asks `question` (`{"do": "question", "question":
  "..."}`), as the Desk's Standing questions form does, and its first check reads
  what the case already holds. The task keeps no schedule (`every_seconds` is
  null): it wakes on evidence only, so a worker left running never re-asks it.
- `wake`: the extraction runs that wrote the claims of the act's arrived sources
  wake the question (`tasks.evidence`, as the worker does after a run it seals),
  and the woken check runs now. `tasks.tick` only runs in the worker loop and
  needs an agent model, so the step queues the woken run itself.

Both checks run the real standing tool on the fixed answer (`read.FIXED`): a
claim matches when its value names a capitalised word or a number from the
question, and the run's inputs say it is a demo, not a model's reading.
`out["question checks"]` lists each check's matches and reason.
"""

from __future__ import annotations

import stk.extensions as ext
from sqlalchemy import select

from sealbook.agents import read, tasks
from sealbook.agents.models import AgentTask, AgentWake
from sealbook.ledger.models import Blob, Run
from sealbook.monitor.study import step
from sealbook.runs.worker import process


async def _check(st, task, wakes) -> None:
    run = await tasks.queue(st.session, task, wakes, model=read.FIXED)
    await st.session.commit()
    await process(ext.async_session_factory, run.id)
    await st.session.refresh(run)
    if run.output_hash is None:
        raise RuntimeError(f"the standing question's check failed: {run.error}")
    out = read.read_output(await st.session.get(Blob, run.output_hash))
    st.out.setdefault("question checks", []).append(
        {
            "woken by": len(wakes),
            "screened": out["screened"],
            "matches": out["outcome"]["proposed"],
            "reason": out["outcome"]["reason"],
        }
    )


@step("question")
async def question(st, act, args) -> None:
    task = AgentTask(
        user_id=st.owner.id,
        investigation=st.inv,
        kind="standing",
        spec={"question": args["question"].strip()},
        every_seconds=None,
    )
    st.session.add(task)
    await st.session.flush()
    st.others["question"] = task.id
    await _check(st, task, [])


@step("wake")
async def wake(st, act, args) -> None:
    task_id = st.others.get("question")
    if task_id is None:
        return  # no question asked yet: nothing is watching
    task = await st.session.get(AgentTask, task_id)
    arrived = set(act["sources"])
    run_ids = {
        found.run_id
        for c in st.spec["claims"]
        if (found := st.written.get(c["key"])) is not None
        and found.run_id is not None
        and (st.records.get(c["record"]) or {}).get("source", c["record"]) in arrived
    }
    for run_id in sorted(run_ids):
        await tasks.evidence(st.session, await st.session.get(Run, run_id))
    await st.session.flush()
    pending = list(
        await st.session.scalars(
            select(AgentWake)
            .where(AgentWake.task_id == task.id, AgentWake.run_id.is_(None))
            .order_by(AgentWake.created_at, AgentWake.id)
        )
    )
    if pending:
        await _check(st, task, pending)
