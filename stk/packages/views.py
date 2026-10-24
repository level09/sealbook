"""Briefs and packages: compose cited sentences, build, attest twice, export."""

import uuid

import orjson
from quart import Blueprint, Response, abort, current_app, g, render_template, request
from quart_security import auth_required, current_user
from sqlalchemy import select

from stk.ledger.contracts import Refused
from stk.ledger.models import Statement
from stk.monitor import data
from stk.monitor.filter import statement_clause
from stk.packages import service
from stk.packages.models import Brief, Package

packages = Blueprint("packages", __name__)

ROLES = ("analyst", "admin")


@packages.before_request
@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


@packages.after_request
async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


async def _item() -> dict:
    return ((await request.get_json()) or {}).get("item") or {}


async def _get(model, key):
    found = await g.db_session.get(model, key)
    if found is None:
        abort(404)
    return found


def _package(p: Package, attesters: list[dict]) -> dict:
    return {
        "id": str(p.id),
        "audience": p.audience,
        "manifest_sha256": p.manifest_sha256,
        "statements": p.statements,
        "created_at": p.created_at.isoformat(),
        "finalized_at": p.finalized_at.isoformat() if p.finalized_at else None,
        "revoked_at": p.revoked_at.isoformat() if p.revoked_at else None,
        "attesters": attesters,
    }


async def _brief(b: Brief) -> dict:
    db = g.db_session
    pkgs = (
        await db.execute(
            select(Package)
            .where(Package.brief_id == b.id)
            .order_by(Package.created_at.desc())
        )
    ).scalars()
    return {
        "id": str(b.id),
        "title": b.title,
        "audience": b.audience,
        "created_at": b.created_at.isoformat(),
        "sentences": [
            {
                "id": str(s.id),
                "ord": s.ord,
                "text": s.text,
                "statement_ids": [str(i) for i in s.statement_ids],
            }
            for s in b.sentences
        ],
        "packages": [_package(p, await service.attesters(db, p)) for p in pkgs],
    }


async def _refusing(coro):
    """Run a service call; a refusal is the user's 400, never a 500."""
    try:
        return await coro
    except Refused as exc:
        abort(400, description=exc.reason)


@packages.get("/packages")
async def index():
    return await render_template(
        "packages/index.html", boot={"audiences": list(service.PROFILES)}
    )


@packages.get("/api/packages/briefs")
async def briefs():
    """Briefs, newest first. With an investigation, those citing a statement from
    its sources, plus briefs with no sentence yet (they belong to no case until
    they cite something, and a new brief must not vanish)."""
    db = g.db_session
    found = list(
        (await db.execute(select(Brief).order_by(Brief.created_at.desc()))).scalars()
    )
    investigation = request.args.get("investigation") or None
    if investigation:
        cited = {i for b in found for s in b.sentences for i in s.statement_ids}
        here = set(
            await db.scalars(
                select(Statement.id).where(
                    Statement.id.in_(cited),
                    statement_clause({"investigation": investigation, "time": None}),
                )
            )
        )
        found = [
            b
            for b in found
            if not b.sentences
            or any(i in here for s in b.sentences for i in s.statement_ids)
        ]
    return _json(
        {
            "items": [
                {
                    "id": str(b.id),
                    "title": b.title,
                    "audience": b.audience,
                    "sentences": len(b.sentences),
                    "created_at": b.created_at.isoformat(),
                }
                for b in found
            ]
        }
    )


@packages.get("/packages/brief/<uuid:bid>")
async def brief_page(bid):
    boot = {"brief": await _brief(await _get(Brief, bid))}
    return await render_template("packages/brief.html", boot=boot)


@packages.post("/api/packages/briefs")
async def create_brief():
    item = await _item()
    title, audience = (item.get("title") or "").strip(), item.get("audience")
    if not title or audience not in service.PROFILES:
        abort(400, description="a brief needs a title and a known audience")
    brief = Brief(title=title, audience=audience, author_id=current_user.id)
    g.db_session.add(brief)
    await g.db_session.commit()
    return _json({"id": str(brief.id)})


@packages.get("/api/packages/factual")
async def factual():
    rows = await data.statement_rows(g.db_session, *service.factual_clause())
    return _json({"items": rows})


@packages.post("/api/packages/briefs/<uuid:bid>/sentences")
async def save_sentence(bid):
    brief = await _get(Brief, bid)
    item = await _item()
    try:
        sid = uuid.UUID(item["id"]) if item.get("id") else None
        ids = [str(uuid.UUID(i)) for i in item.get("statement_ids") or []]
    except (ValueError, TypeError):
        abort(400, description="statement ids must be uuids")
    await _refusing(
        service.save_sentence(
            g.db_session, brief, current_user, item.get("text") or "", ids, sid
        )
    )
    await g.db_session.commit()
    return _json(await _brief(await _get(Brief, bid)))


@packages.post("/api/packages/briefs/<uuid:bid>/build")
async def build(bid):
    brief = await _get(Brief, bid)
    package = await _refusing(
        service.build(g.db_session, brief, current_user, current_app.config)
    )
    await g.db_session.commit()
    return _json({"id": str(package.id), "manifest_sha256": package.manifest_sha256})


@packages.post("/api/packages/<uuid:pid>/attest/options")
async def attest_options(pid):
    package = await _get(Package, pid)
    return _json(
        await _refusing(service.attest_options(g.db_session, current_user, package))
    )


@packages.post("/api/packages/<uuid:pid>/attest")
async def attest(pid):
    package = await _get(Package, pid)
    credential = await _item()
    await _refusing(
        service.add_attestation(g.db_session, current_user, package, credential)
    )
    await g.db_session.commit()
    return _json(_package(package, await service.attesters(g.db_session, package)))


@packages.post("/api/packages/<uuid:pid>/finalize")
async def finalize(pid):
    package = await _get(Package, pid)
    await _refusing(
        service.finalize(
            g.db_session, current_user, package, current_app.config["BLOB_DIR"]
        )
    )
    await g.db_session.commit()
    return _json(_package(package, await service.attesters(g.db_session, package)))


@packages.get("/packages/<uuid:pid>/download")
async def download(pid):
    package = await _get(Package, pid)
    body = await _refusing(
        service.export(
            g.db_session, current_user, package, current_app.config["BLOB_DIR"]
        )
    )
    await g.db_session.commit()
    return Response(
        body,
        content_type="application/zip",
        headers={
            "Content-Disposition": "attachment; "
            f'filename="package-{package.manifest_sha256[:12]}.zip"'
        },
    )
