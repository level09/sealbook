"""Sample rows. `stk db seed` adds them; `stk smoke` renders every page with them."""

from quart import current_app


async def run(session):
    from sealbook.monitor.seed import seed
    from sealbook.monitor.seed_full import seed_full

    await seed(session)
    await seed_full(session, current_app)
