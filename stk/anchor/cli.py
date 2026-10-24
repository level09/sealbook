"""anchor and verify-anchors commands."""

import asyncio

import click
import httpx
from quart.cli import ScriptInfo

import stk.extensions as ext
from stk.cli.base import console, run_async
from stk.settings import Config


@click.command("anchor")
@click.option("--loop", is_flag=True, help="Repeat every ANCHOR_INTERVAL_SECONDS.")
def anchor(loop):
    """Close a Merkle batch over new chain entries and anchor pending batches."""
    from stk.anchor.service import run_once

    click.get_current_context().ensure_object(ScriptInfo).load_app()

    async def main():
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                batch, errors = await run_once(ext.async_session_factory, client)
                if batch:
                    console.print(
                        f"batch {batch.first_seq}-{batch.last_seq} root {batch.root}"
                    )
                for error in errors:
                    console.print(f"[red]{error}[/red]")
                if not loop:
                    return errors
                # ponytail: sleep loop in one process; move to cron or the task
                # runner if anchoring must survive restarts on schedule.
                await asyncio.sleep(Config.ANCHOR_INTERVAL_SECONDS)

    if run_async(main()):
        raise SystemExit(1)


@click.command("verify-anchors")
def verify_anchors_cmd():
    """Check batches, OTS, TSA and partner copies against the chain."""
    from stk.anchor.service import verify_anchors

    click.get_current_context().ensure_object(ScriptInfo).load_app()

    async def main():
        async with ext.async_session_factory() as s:
            return await verify_anchors(s)

    problems, notes = run_async(main())
    for note in notes:
        console.print(note)
    for p in problems:
        console.print(f"[red]{p}[/red]")
    if problems:
        raise SystemExit(1)
    console.print("anchors ok")
