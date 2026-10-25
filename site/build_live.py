"""Site data from the app itself: `stk demo-seed --full` on a throwaway Postgres
database, read back through the same HTTP endpoints the analyst pages call, signed
gestures checked with the offline verifier, then the database is dropped.

Called by build_data.py. Needs a local Postgres role with CREATEDB.
"""

import asyncio
import os
import tempfile
import time
import uuid
from datetime import datetime
from unittest import mock

import stk.extensions as ext
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from stk.migrations import build_alembic_config, throwaway_database_url

from sealbook.app import app as sealbook_app
from sealbook.app import create_app
from sealbook.ledger import offline_sigs
from sealbook.ledger.models import Acquisition, Payload, Statement
from sealbook.settings import Config

CASE = "case-demo-02"
PASSWORD = "SiteBuild-Throwaway-1!"  # throwaway database, dropped after the build
QUERIES = ["Branch 999", "يوسف خالد العلي", "Harasta", "تسعمية"]


def live() -> dict:
    with (
        throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True) as url,
        tempfile.TemporaryDirectory() as blob_dir,
        # No model calls: the demo's three fixed-output runs stand in, and they are
        # labelled as such on the page.
        mock.patch.object(Config, "EMBEDDING_MODEL", None),
        mock.patch.object(Config, "RUN_MODELS", {}),
        mock.patch.object(Config, "SPEECH_MODEL", ""),
        # Agents are off except where _watch runs them on the fixed answer, so the
        # page never depends on a live model's answer.
        mock.patch.object(Config, "AGENT_MODEL", ""),
        # Research queries are only proposed here; nothing is sent to a provider.
        mock.patch.object(Config, "BRAVE_API_KEY", ""),
        mock.patch.object(Config, "BLOB_DIR", blob_dir),
    ):
        cfg = build_alembic_config(sealbook_app, url, quiet=True)
        command.upgrade(cfg, "heads")
        db_url = (
            make_url(url)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False)
        )

        class SiteConfig(Config):
            SQLALCHEMY_DATABASE_URI = db_url
            TESTING = True
            SESSION_TYPE = None
            STK_ENV = "testing"

        app = create_app(SiteConfig)
        return asyncio.run(_run(app))


async def _run(app) -> dict:
    try:
        return await _read(app)
    finally:
        await ext.engine.dispose()


async def _read(app) -> dict:
    from quart_security import hash_password
    from stk.user.models import Role, User

    from sealbook.monitor.seed import seed
    from sealbook.monitor.seed_full import L1, R1, seed_full
    from sealbook.runs.worker import work_once

    async with ext.async_session_factory() as s, s.begin():
        owner = User(
            email="owner@example.com",
            name="owner",
            password=hash_password(PASSWORD, app=app),
            active=True,
            confirmed_at=datetime.now(),
        )
        owner.roles.append(Role(name="admin"))
        s.add(owner)
    async with ext.async_session_factory() as s:
        await seed(s)
        await seed_full(s, app)
    # Text indexing only, so the grouped search has passages to find.
    while await work_once(ext.async_session_factory, ["index"]):
        pass

    client = app.test_client()
    await client.get("/login")
    async with client.session_transaction() as cookie:
        csrf = cookie["_csrf_token"]
    r = await client.post(
        "/login",
        form={"email": "owner@example.com", "password": PASSWORD, "csrf_token": csrf},
    )
    assert r.status_code == 302, r.status_code

    async def get(path, **args):
        r = await client.get(path, query_string=args)
        assert r.status_code == 200, (path, r.status_code, await r.get_data(True))
        return await r.get_json()

    await client.get("/discover")
    async with client.session_transaction() as cookie:
        token = cookie["_csrf_token"]

    async def post(path, body=None):
        r = await client.post(path, json=body or {}, headers={"X-CSRFToken": token})
        assert r.status_code == 200, (path, r.status_code, await r.get_data(True))
        return await r.get_json()

    revealed = set()

    async def reveal(sha):
        if sha and sha not in revealed:
            await post(f"/api/evidence/{sha}/reveal")
            revealed.add(sha)

    async with ext.async_session_factory() as s:
        files = dict(
            (
                await s.execute(
                    select(
                        Acquisition.blob_sha256, Acquisition.context["file"].astext
                    ).where(Acquisition.context["investigation"].astext == CASE)
                )
            ).all()
        )
        access_before = await _count_access(s)

    queue = (await get("/api/discover/findings", investigation=CASE))["items"]
    for f in queue:
        for m in f["members"]:
            await reveal(m["source"])
    findings = [await get(f"/api/discover/findings/{f['id']}") for f in queue]
    incidents = (await get("/api/discover/incidents", investigation=CASE))["items"]
    async with ext.async_session_factory() as s:
        # Reveals and finding views: what reading the findings left on the ledger.
        access_findings = await _count_access(s) - access_before
        finding_reveals = len(revealed)

    ref = R1.removeprefix("person:")
    story = await get("/api/stories", lens="person", ref=ref, investigation=CASE)

    async with ext.async_session_factory() as s:
        gestures = await _gestures(s)
        places = await _places(s, {p["place_id"] for p in story["points"]})
        sheet_acq = await s.scalar(
            select(Acquisition).where(
                Acquisition.context["investigation"].astext == CASE,
                Acquisition.context["file"].astext == "messy_people.csv",
            )
        )
        sheet_sha, sheet_id = sheet_acq.blob_sha256, sheet_acq.id

    await reveal(sheet_sha)
    sheet = await get(f"/api/evidence/{sheet_sha}/sheet")
    proposals = await get(f"/api/sheets/{sheet_id}/proposals")

    search = []
    for q in QUERIES:
        body = {"item": {"query": q, "filter": {"investigation": CASE}, "limit": 4}}
        # Second pass is the one timed, as in acceptance/eval_search.py.
        await post("/api/search", body)
        t0 = time.perf_counter()
        found = await post("/api/search", body)
        search.append(
            {
                "query": q,
                "ms": round((time.perf_counter() - t0) * 1000),
                "result": found,
            }
        )

    async with ext.async_session_factory() as s:
        names = await _names(s)
        versions = await _versions(s)
        batches = await _batches(s)
        link_runs = await _link_runs(s)

    # The intake brief for each batch, as its reader sees it after the reveals above.
    # The register stays unrevealed here, so a line resting on it reads as locked.
    for b in batches:
        for sha in b["sources"]:
            await reveal(sha)
    briefs = [await get("/api/agents/brief", batch=b["batch"]) for b in batches]
    async with ext.async_session_factory() as s:
        for b in briefs:
            b["cited"] = await _cited(s, b["version"]["id"])
    linked = [
        await get(f"/api/review/claim/{w['statement']}") | {"kind": w["kind"]}
        for r in link_runs
        for w in r["written"]
    ]

    # Disagreements and counts as the People list, the person page and search give them.
    roster = await get("/api/people", investigation=CASE)
    people = []
    for row in roster["items"]:
        page = await get(f"/api/people/{row['person']}")
        facts = [f for fs in page["facts"].values() for f in fs]
        people.append(
            {
                "row": row,
                "forks": page["forks"],
                "page_facts": len(facts),
                "page_documents": len(
                    {f["doc"]["sha256"] for f in facts if f.get("doc")}
                ),
            }
        )
    for p in people[:12]:
        if not p["row"]["name"]:
            continue
        found = await post(
            "/api/search",
            {
                "item": {
                    "query": p["row"]["name"],
                    "types": ["people"],
                    "semantic": False,
                    "filter": {"investigation": CASE, "time": None},
                }
            },
        )
        p["search"] = next(
            (
                i.get("subtitle")
                for i in found["groups"]["people"]["items"]
                if i["id"] == f"person:{p['row']['person']}"
            ),
            None,
        )

    places_table = await get("/api/places/table", investigation=CASE, per_page=50)
    branch = next(p for p in places_table["items"] if p["name_en"] == "Branch 999")
    place_page = await get(f"/api/places/{branch['id']}", investigation=CASE)
    incident_id = incidents[0]["id"].removeprefix("incident:")
    incident_page = await get(f"/api/incidents/{incident_id}", investigation=CASE)

    async def send(method, path, item, status):
        r = await getattr(client, method)(
            path, json={"item": item}, headers={"X-CSRFToken": token}
        )
        assert r.status_code == status, (path, r.status_code, await r.get_data(True))
        return await r.get_json()

    # Before research marks the case sensitive below.
    watch = await _watch(get, send)
    casemap = await _casemap(get)

    research = {
        "person": L1.removeprefix("person:"),
        "queries": await post("/api/research/queries", {"item": {"scope": L1}}),
        "page": await get(f"/api/people/{L1.removeprefix('person:')}"),
    }
    # The same case marked sensitive: the guardrail answers before anything is sent.
    await post(
        "/api/research/case", {"item": {"investigation": CASE, "sensitive": True}}
    )
    research["refused"] = (await get("/api/research/status", investigation=CASE))[
        "refusal"
    ]
    research["refused_queries"] = await post(
        "/api/research/queries", {"item": {"scope": L1}}
    )

    return {
        "watch": watch,
        "casemap": casemap,
        "batches": batches,
        "briefs": briefs,
        "link_runs": link_runs,
        "linked": linked,
        "people": people,
        "places_table": places_table,
        "place_page": place_page,
        "incident_page": incident_page,
        "research": research,
        "files": files,
        "queue": queue,
        "findings": findings,
        "incidents": incidents,
        "gestures": gestures,
        "story": story,
        "places": places,
        "sheet": {
            "sha": sheet_sha,
            "table": sheet,
            "proposals": proposals,
            "versions": versions,
        },
        "names": names,
        "search": search,
        "reveals": finding_reveals,
        "access_records": access_findings,
    }


async def _watch(get, send) -> dict:
    """The four agents that keep checking, run by the worker's own code on the demo's
    fixed answer, then read back through the endpoints the Agents page, Desk and
    People call, as their owner reads them."""
    from sealbook.agents import tasks
    from sealbook.agents.read import FIXED
    from sealbook.runs.worker import work_once

    with (
        mock.patch.object(Config, "AGENT_MODEL", FIXED),
        # Never called: the fixed answer stands in for the local model.
        mock.patch.dict(
            os.environ, {"ROUTE_LOCAL_BASE_URL": "http://fixed-output.invalid/v1"}
        ),
    ):
        ready = await get("/api/agents/overview", investigation=CASE)
        made = {}
        for kind in ("contradictions", "gaps", "noticer"):
            item = {"kind": kind, "investigation": CASE}
            made[kind] = (await send("post", "/api/agents/tasks", item, 201))["id"]
        await tasks.tick(ext.async_session_factory)
        tools = ["standing", "contradictions", "gaps", "notice"]
        while await work_once(ext.async_session_factory, tools):
            pass
        watching = await get("/api/agents/overview", investigation=CASE)
        lists = {
            kind: (await get("/api/agents/tasks", investigation=CASE, kind=kind))[
                "items"
            ]
            for kind in tasks.KINDS
        }
        question = await get(f"/api/agents/tasks/{lists['standing'][0]['id']}")
        reports = {
            "contradictions": await get(
                "/api/agents/contradictions", investigation=CASE
            ),
            "gaps": await get("/api/agents/gaps", investigation=CASE),
            "noticer": await get("/api/agents/notice", investigation=CASE),
        }
        # The keep checking switch, off and on again.
        stopped = {}
        made["standing"] = lists["standing"][0]["id"]
        for kind, tid in made.items():
            await send("patch", f"/api/agents/tasks/{tid}", {"enabled": False}, 200)
            stopped[kind] = await get("/api/agents/overview", investigation=CASE)
            await send("patch", f"/api/agents/tasks/{tid}", {"enabled": True}, 200)
        # The person's click: one resemblance sent to Review as a proposed link.
        pick = next(
            (i for i in reports["noticer"]["items"] if i.get("link") == "can"), None
        )
        proposed = None
        if pick:
            item = {"run": pick["run"], "key": pick["key"]}
            sid = (await send("post", "/api/agents/notice/link", item, 201))[
                "statement"
            ]
            proposed = {
                "key": pick["key"],
                "claim": await get(f"/api/review/claim/{sid}"),
            }
    return {
        "ready": ready,
        "watching": watching,
        "stopped": stopped,
        "tasks": lists,
        "question": question,
        "reports": reports,
        "proposed": proposed,
    }


async def _casemap(get) -> dict:
    """The case map, and the path answer from every person on it to every other."""
    view = await get("/api/case-map", investigation=CASE)
    keys = [r["key"] for r in view["rows"]]
    paths = []
    for a in keys:
        for b in keys:
            if a != b:
                args = {"investigation": CASE, "from": a, "to": b}
                found = await get("/api/case-map/path", **args)
                paths.append({"from": a, "to": b} | found)
    return {"view": view, "paths": paths}


async def _cited(s, run_id: str) -> dict:
    """Per brief line, the claims it cites: prop and the source it was read from."""
    from sealbook.agents.read import read_output
    from sealbook.ledger.models import Blob, Run
    from sealbook.monitor import filter as mfilter

    run = await s.get(Run, uuid.UUID(run_id))
    sections = read_output(await s.get(Blob, run.output_hash))["sections"]
    ids = {
        uuid.UUID(i) for ls in sections.values() for line in ls for i in line["claims"]
    }
    rows = (
        await s.execute(
            select(
                Statement.id,
                Statement.prop,
                mfilter.source(mfilter.statement_blob()),
            ).where(Statement.id.in_(ids))
        )
    ).all()
    shas = {sha for *_, sha in rows}
    titles = dict(
        (
            await s.execute(
                select(
                    Acquisition.blob_sha256,
                    Acquisition.context["title"].astext,
                ).where(Acquisition.blob_sha256.in_(shas))
            )
        ).all()
    )
    by = {str(i): {"prop": prop, "source": titles.get(sha)} for i, prop, sha in rows}
    return {
        name: [[by.get(i) for i in line["claims"]] for line in ls]
        for name, ls in sections.items()
    }


async def _batches(s) -> list[dict]:
    """The case's intake batches, oldest first, with their sources."""
    rows = await s.execute(
        select(Acquisition.context["batch"].astext, Acquisition.blob_sha256)
        .where(
            Acquisition.context["investigation"].astext == CASE,
            Acquisition.context["batch"].is_not(None),
        )
        .order_by(Acquisition.at)
    )
    out: dict[str, set] = {}
    for batch, sha in rows:
        out.setdefault(batch, set()).add(sha)
    return [{"batch": b, "sources": sorted(v)} for b, v in out.items()]


async def _link_runs(s) -> list[dict]:
    from sealbook.agents import linker
    from sealbook.agents.read import read_output
    from sealbook.ledger.models import Blob, Run

    runs = (
        await s.scalars(
            select(Run).where(Run.tool == linker.TOOL).order_by(Run.created_at)
        )
    ).all()
    out = []
    for r in runs:
        o = read_output(await s.get(Blob, r.output_hash))
        out.append(
            {
                "model": r.model_served,
                "demo": r.inputs.get("demo"),
                "written": o.get("written", []),
                "candidates": o.get("candidates"),
                "already_known": o.get("already_known"),
                "valid": o.get("valid"),
            }
        )
    return out


async def _count_access(s) -> int:
    return await s.scalar(select(func.count()).where(Statement.prop == "access"))


async def _gestures(s) -> list[dict]:
    """Every multi-row signed gesture: its rows, and whether each row verifies with
    the offline verifier the package checker uses."""
    rows = (
        await s.execute(
            select(Statement, Payload.value)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(Statement.sig["batch"].is_not(None))
            .order_by(Statement.recorded_at)
        )
    ).all()
    by_batch: dict[str, dict] = {}
    for st, value in rows:
        key = st.sig["challenge"]
        g = by_batch.setdefault(key, {"sig": dict(st.sig), "rows": []})
        offline_sigs.verify_statement_signature(st.hash, st.sig)  # raises if not
        g["rows"].append(
            {
                "hash": st.hash,
                "subject": st.subject,
                "prop": st.prop,
                "value": value,
                "valid": [
                    str(st.valid_from) if st.valid_from else None,
                    str(st.valid_to) if st.valid_to else None,
                ],
                "verified": True,
            }
        )
    return list(by_batch.values())


async def _places(s, ids) -> dict:
    from sealbook.places.models import AdminArea, Place

    out = {}
    for p in (await s.scalars(select(Place).where(Place.id.in_(ids)))).all():
        admin2 = await s.get(AdminArea, p.admin2_id) if p.admin2_id else None
        out[str(p.id)] = {
            "ar": p.name_ar,
            "en": p.name_en,
            "kind": p.kind,
            "lonlat": [float(p.lon), float(p.lat)],
            "precision_m": p.precision_m,
            "geo_source": p.geo_source,
            "source": p.source,
            "geonames_id": p.geonames_id,
            "admin2": admin2 and {"name": admin2.name_en, "pcode": admin2.pcode},
        }
    return out


async def _names(s) -> dict:
    """Every name written for a person or place subject in the case, as recorded."""
    from sealbook.places.models import Place

    out: dict[str, list] = {}
    rows = await s.execute(
        select(Statement.subject, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(Statement.prop == "name", Statement.subject.like("person:%"))
        .order_by(Statement.recorded_at)
    )
    for subject, value in rows:
        text = value.get("text") if isinstance(value, dict) else None
        if text and text not in out.setdefault(subject, []):
            out[subject].append(text)
    for p in (await s.scalars(select(Place))).all():
        out[f"place:{p.id}"] = [x for x in (p.name_en, p.name_ar) if x]
    return out


async def _versions(s) -> list[dict]:
    from sealbook.sheets.models import Dataset, DatasetVersion

    rows = await s.execute(
        select(DatasetVersion, Dataset.name, Acquisition.context["file"].astext)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .join(Acquisition, Acquisition.id == DatasetVersion.acquisition_id)
        .order_by(DatasetVersion.number)
    )
    return [
        {
            "dataset": name,
            "number": v.number,
            "file": file,
            "key_column": v.key_column,
            "mapping": v.mapping,
            "diff": v.diff,
        }
        for v, name, file in rows
    ]
