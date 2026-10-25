"""Queue bounded enrichment of existing evidence without reading remote stores."""

import click
import stk.extensions as ext
from quart.cli import ScriptInfo
from sqlalchemy import select, tuple_
from stk.cli.base import run_async

from sealbook.discovery import service
from sealbook.ingest.service import queue_transcribe
from sealbook.ledger.models import Acquisition, Blob, Run, Statement
from sealbook.monitor import filter as mfilter


@click.command("enrich")
@click.option("--investigation", help="Limit processing to one investigation.")
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option(
    "--after",
    type=click.UUID,
    help="Resume after the acquisition cursor printed by the previous batch.",
)
@click.option(
    "--rebuild",
    is_flag=True,
    help="Rebuild fingerprints and vectors, including changed model aliases.",
)
def enrich(investigation, limit, after, rebuild):
    """Queue local text, actor, picture and sound indexing, and transcription of
    recordings not yet transcribed, for existing acquisitions."""
    click.get_current_context().ensure_object(ScriptInfo).load_app()

    async def queue():
        async with ext.async_session_factory() as db, db.begin():
            q = select(Acquisition, Blob).join(
                Blob, Blob.sha256 == Acquisition.blob_sha256
            )
            if investigation:
                q = q.where(
                    Acquisition.context["investigation"].astext == investigation
                )
            if after:
                anchor = await db.get(Acquisition, after)
                if anchor is None or (
                    investigation
                    and anchor.context.get("investigation") != investigation
                ):
                    raise click.ClickException(
                        "cursor does not belong to this acquisition filter"
                    )
                q = q.where(
                    tuple_(Acquisition.at, Acquisition.id)
                    > tuple_(anchor.at, anchor.id)
                )
            found = (
                await db.execute(
                    q.order_by(Acquisition.at, Acquisition.id).limit(limit + 1)
                )
            ).all()
            rows = found[:limit]
            for acq, blob in rows:
                await service.queue(db, acq, blob.mime, rebuild=rebuild)
                if blob.mime.startswith(("audio/", "video/")):
                    await queue_transcribe(db, acq)
                subjects = list(
                    (
                        await db.execute(
                            select(Statement.subject)
                            .where(
                                Statement.subject.startswith("person:"),
                                mfilter.source(Statement.span_blob) == blob.sha256,
                            )
                            .distinct()
                        )
                    ).scalars()
                )
                for subject in subjects:
                    await service.queue_actor(db, subject, acq, rebuild=rebuild)
            return len(rows), str(rows[-1][0].id) if len(found) > limit else None

    count, cursor = run_async(queue())
    click.echo(
        f"Scheduled discovery for {count} acquisitions. Run stk worker to process them."
    )
    if cursor:
        click.echo(f"Next batch: --after {cursor}")


@click.command("discover")
@click.option("--investigation", required=True, help="Investigation to search.")
def discover(investigation):
    """Queue a bounded run proposing shared presence and candidate incidents."""
    click.get_current_context().ensure_object(ScriptInfo).load_app()
    from sealbook.ingest.service import system_agent

    async def queue():
        async with ext.async_session_factory() as db, db.begin():
            known = await db.scalar(
                select(
                    select(Acquisition.id)
                    .where(Acquisition.context["investigation"].astext == investigation)
                    .exists()
                )
            )
            if not known:
                raise click.ClickException("no acquisitions in that investigation")
            agent = await system_agent(db, "discover")
            run = Run(
                principal_id=agent.id,
                tool="discover",
                route="local",
                inputs={"investigation": investigation},
            )
            db.add(run)
            await db.flush()
            return run.id

    click.echo(
        f"Queued discover run {run_async(queue())}. Run stk worker to process it."
    )
