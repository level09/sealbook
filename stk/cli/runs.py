"""Run worker command."""

import click
from quart.cli import ScriptInfo

import stk.extensions as ext
from stk.cli.base import run_async


@click.command("worker")
@click.option("--tool", "tools", multiple=True, help="Only claim runs of this tool.")
def worker(tools):
    """Claim and execute queued runs until interrupted."""
    from stk.runs.worker import serve

    click.get_current_context().ensure_object(ScriptInfo).load_app()
    run_async(serve(ext.async_session_factory, list(tools)))
