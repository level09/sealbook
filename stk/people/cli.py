"""`stk erase`: the erasure procedure for one person."""

import click
from quart.cli import ScriptInfo
from sqlalchemy import select

import stk.extensions as ext
from stk.cli.base import console, run_async
from stk.ledger.write import principal_for_user
from stk.user.models import User


@click.command("erase")
@click.argument("ref")
@click.option("--reason", required=True, help="Why: recorded on the tombstone.")
@click.option("--by", "email", required=True, help="Email of the user erasing.")
def erase_cmd(ref, reason, email):
    """Delete every value recorded about a person; statements and chain stay."""
    from stk.people.data import erase

    click.get_current_context().ensure_object(ScriptInfo).load_app()

    async def _run():
        async with ext.async_session_factory() as session, session.begin():
            user = (
                await session.execute(select(User).where(User.email == email))
            ).scalar()
            if user is None:
                raise click.ClickException(f"no user {email}")
            who = await principal_for_user(session, user.id, user.name or user.email)
            try:
                return await erase(session, ref, who.id, reason)
            except (ValueError, LookupError) as exc:
                raise click.ClickException(str(exc)) from exc

    result = run_async(_run())
    console.print(
        f"Erased person:{result['person']}: {result['commitments']} values deleted "
        f"across {len(result['members'])} mentions, tombstone {result['tombstone']}. "
        "Source blob bytes are untouched."
    )
