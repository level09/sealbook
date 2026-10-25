"""Run worker command."""

import click
import stk.extensions as ext
from quart.cli import ScriptInfo
from stk.cli.base import run_async


@click.command("worker")
@click.option("--tool", "tools", multiple=True, help="Only claim runs of this tool.")
def worker(tools):
    """Claim and execute queued runs until interrupted."""
    from sealbook.runs.worker import serve

    click.get_current_context().ensure_object(ScriptInfo).load_app()
    run_async(serve(ext.async_session_factory, list(tools)))
