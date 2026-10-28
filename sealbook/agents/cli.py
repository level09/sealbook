"""agent-agreement command."""

import click
import stk.extensions as ext
from quart.cli import ScriptInfo
from rich.table import Table
from stk.cli.base import console, run_async


@click.command("agent-agreement")
@click.option("--investigation", help="Limit the table to one case.")
def agent_agreement(investigation):
    """Analyst decisions on agent proposals by case, agent, prompt version and
    served model, all time. Counts only."""
    from sealbook.agents.overview import agreement

    click.get_current_context().ensure_object(ScriptInfo).load_app()

    async def main():
        async with ext.async_session_factory() as db:
            return await agreement(db, investigation)

    table = Table("case", "agent", "version", "model", "accepted", "rejected", "open")
    for row in run_async(main()):
        table.add_row(*(str(v) for v in row.values()))
    console.print(table)
