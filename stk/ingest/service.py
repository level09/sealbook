"""Intake of bytes into the ledger: content-addressed blob store, acquisitions,
and the deterministic runs (hash, C2PA verification, URL liveness) that follow."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import orjson
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stk import netguard
from stk.blobs import blob_path, chunked, storage_ref, store
from stk.discovery import service as discovery
from stk.ledger import chain, write
from stk.ledger.contracts import (
    PrincipalKind,
    Prop,
    Refused,
    Route,
    StatementIn,
    Tier,
)
from stk.ledger.gate import resolve
from stk.ledger.models import Acquisition, Blob, Principal, Run, StatementTier
from stk.settings import Config
from stk.sheets import parse as sheets

AGENT_NAME = "ingest"


# --- origins -------------------------------------------------------------------


def normalise_url(url: str) -> str:
    """One page, one origin: lowercase scheme and host, drop default port,
    fragment and userinfo."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an http(s) url: {url!r}")
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if parts.port and parts.port != {"http": 80, "https": 443}[scheme]:
        host = f"{host}:{parts.port}"
    return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))


def make_origin(kind: str, value: str) -> str:
    if kind == "url":
        return f"url:{normalise_url(value)}"
    if kind not in ("device", "channel") or not value.strip():
        raise ValueError(f"bad origin {kind}:{value!r}")
    return f"{kind}:{value.strip()}"


# --- agent runs ----------------------------------------------------------------


async def system_agent(session: AsyncSession, name: str = AGENT_NAME) -> Principal:
    # ponytail: find-or-create without a unique key; two first-ever ingests racing
    # make two agent rows (harmless), add a unique index if it matters.
    found = (
        await session.execute(
            select(Principal).where(
                Principal.kind == PrincipalKind.AGENT,
                Principal.name == name,
                Principal.user_id.is_(None),
            )
        )
    ).scalar()
    if found:
        return found
    agent = Principal(kind=PrincipalKind.AGENT, name=name)
    session.add(agent)
    await session.flush()
    return agent


async def agent_run(
    session: AsyncSession,
    tool: str,
    inputs: dict,
    values: list[tuple[str, Prop, object]],
) -> list[dict]:
    """One sealed deterministic run: fresh ed25519 key (private half never leaves
    this call), one signed statement per (subject, prop, value), then seal.
    Deterministic tools are auto-accepted by policy; anything else is a bug."""
    agent = await system_agent(session)
    key = Ed25519PrivateKey.generate()
    run = Run(
        principal_id=agent.id,
        tool=tool,
        route="local",
        inputs=inputs,
        status="running",
        pubkey=key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
    )
    session.add(run)
    await session.flush()

    def sign(h: str) -> dict:
        return {"kind": "ed25519", "sig": key.sign(h.encode()).hex()}

    out = []
    for subject, prop, value in values:
        st = await write.append(
            session,
            StatementIn(
                subject=subject,
                prop=prop,
                value=value,
                author_id=agent.id,
                run_id=run.id,
            ),
            sign=sign,
        )
        tier = await session.get(StatementTier, st.id)
        if tier.tier != Tier.ACCEPTED:
            raise Refused(f"{tool} statement not auto-accepted: {tier.reason}")
        out.append(
            {"id": str(st.id), "prop": str(prop), "value": value, "tier": tier.tier}
        )
    await write.seal_run(
        session, run, output_hash=chain.sha256(chain.canon([o["id"] for o in out]))
    )
    return out


# --- C2PA ----------------------------------------------------------------------


class C2paError(RuntimeError):
    """c2patool could not run on this file, as opposed to finding no manifest."""


async def c2pa_verify(path: Path) -> dict:
    """Run c2patool on a file. No manifest is a result ("absent"), a missing
    binary or any other tool failure is an error."""
    proc = await asyncio.create_subprocess_exec(
        "c2patool",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        if b"No claim found" in err:
            return {"status": "absent", "issuer": None, "codes": []}
        if b"Unsupported file type" in err:
            # Containers c2patool cannot read (webm) carry no readable manifest.
            return {
                "status": "absent",
                "issuer": None,
                "codes": ["container_unsupported"],
            }
        raise C2paError(f"c2patool failed ({proc.returncode}): {err.decode()[:500]}")
    report = orjson.loads(out)
    active = report["manifests"][report["active_manifest"]]
    return {
        # validation_state is Invalid, Valid (untrusted signer) or Trusted
        "status": "invalid" if report["validation_state"] == "Invalid" else "valid",
        "issuer": active.get("signature_info", {}).get("issuer"),
        "codes": [c["code"] for c in report.get("validation_status", [])],
    }


# --- intake --------------------------------------------------------------------


async def ingest(
    session: AsyncSession,
    chunks: AsyncIterator[bytes],
    *,
    blob_dir: str,
    mime: str,
    origin: str,
    collector_id: uuid.UUID,
    doc_type: str = "unknown",
    sensitivity: str = "sensitive",
    partner: str | None = None,
    context: dict | None = None,
) -> dict:
    """Store bytes, record where they came from, and on first sight run hash
    (and C2PA for images and video). Re-ingest only adds an acquisition."""
    sha, md5, size = await store(chunks, blob_dir)
    new = await session.get(Blob, sha) is None
    if new:
        session.add(
            Blob(
                sha256=sha,
                md5=md5,
                size=size,
                mime=mime,
                storage_ref=storage_ref(sha),
            )
        )
        await session.flush()
    acq = await write.record_acquisition(
        session,
        blob_sha256=sha,
        origin=origin,
        collector_id=collector_id,
        at=datetime.now(UTC),
        context=context or {},
        sensitivity=sensitivity,
        doc_type=doc_type,
        partner=partner,
    )
    statements, processing, enrichments = [], None, []
    if new:
        subject = f"blob:{sha}"
        statements += await agent_run(
            session,
            "hash",
            {"blobs": [sha]},
            [(subject, Prop.SHA256, sha), (subject, Prop.MD5, md5)],
        )
        if mime.startswith(("image/", "video/")):
            result = await c2pa_verify(blob_path(blob_dir, sha))
            statements += await agent_run(
                session,
                "c2pa_verify",
                {"blobs": [sha]},
                [(subject, Prop.C2PA_VERIFY, result)],
            )
        processing = await queue_processing(session, acq, mime)
        enrichments = await discovery.queue(session, acq, mime)
    return {
        "sha256": sha,
        "md5": md5,
        "new_blob": new,
        "acquisition_id": str(acq.id),
        "statements": statements,
        "processing": processing,
        "discovery": enrichments,
    }


def _tool(mime: str) -> str | None:
    if mime in sheets.MIMES or mime == "application/pdf":
        # A person maps a sheet's columns first (stk.sheets); the pdf run reads a
        # PDF's text layer and queues extraction per page.
        return None
    if mime.startswith("image/"):
        return "ocr"
    if mime.startswith("text/"):
        return "extract"
    if mime.startswith(("audio/", "video/")):
        return "transcribe"
    return None


READS = {"ocr": "reading text in images", "extract": "reading claims from text"}


def _model_setting(route: str) -> str:
    return f"MODEL_{route.upper().replace('-', '_')}"


async def _route(
    session: AsyncSession, tool: str, acq: Acquisition
) -> tuple[Route | None, str]:
    """The most private route the policy allows that has a model configured, or None
    and the reason in words. Nothing allowed and configured is reported, never
    rerouted."""
    policy = resolve(await write.policies(session), tool, acq.doc_type, acq.sensitivity)
    route = next(
        (r for r in Route if r in policy.routes and r in Config.RUN_MODELS), None
    )
    if route or not policy.routes:
        return route, (
            f"The policy for {acq.sensitivity} sources allows no model for "
            f"{READS.get(tool, tool)}."
        )
    settings = " or ".join(_model_setting(r) for r in policy.routes)
    return None, (
        f"No model is configured for {READS.get(tool, tool)} on the routes this "
        f"{acq.sensitivity} source may use ({', '.join(policy.routes)}). Set "
        f"{settings} in settings."
    )


async def extract_target(session: AsyncSession, acq: Acquisition) -> dict:
    """Where claims are read from text derived later (OCR, transcript), or nothing
    when no allowed route has a model."""
    route, _ = await _route(session, "extract", acq)
    if route is None:
        return {}
    return {"extract_route": str(route), "extract_model": Config.RUN_MODELS[route]}


async def queue_transcribe(session: AsyncSession, acq: Acquisition) -> dict:
    """Speech to text runs in the worker process, so it is always the local route and
    sensitive recordings qualify. Claims are then read from the transcript on the
    route extraction is allowed, if any. One transcription per recording."""
    if not Config.SPEECH_MODEL:
        return {
            "queued": None,
            "reason": "Speech to text is off. Set SPEECH_MODEL in settings.",
        }
    known = await session.scalar(
        select(Run.id)
        .where(
            Run.tool == "transcribe",
            Run.inputs["blobs"][0].astext == acq.blob_sha256,
        )
        .limit(1)
    )
    if known:
        return {"queued": None, "reason": "The speech is already read or queued."}
    policy = resolve(
        await write.policies(session), "transcribe", acq.doc_type, acq.sensitivity
    )
    if Route.LOCAL not in policy.routes:
        return {
            "queued": None,
            "reason": "The policy does not allow local speech to text for this source.",
        }
    run = Run(
        principal_id=(await system_agent(session, "transcriber")).id,
        tool="transcribe",
        route=str(Route.LOCAL),
        model_requested=Config.SPEECH_MODEL,
        inputs={"blobs": [acq.blob_sha256], "acquisition": str(acq.id)}
        | await extract_target(session, acq),
    )
    session.add(run)
    await session.flush()
    return {"queued": "transcribe", "run": str(run.id), "route": str(Route.LOCAL)}


async def queue_read(
    session: AsyncSession, acq: Acquisition, tool: str, sha: str
) -> dict:
    """Queue OCR or extraction of `sha` (the source, or text derived from it) once;
    a failed run may be queued again."""
    known = await session.scalar(
        select(Run.id)
        .where(
            Run.tool == tool,
            Run.inputs["blobs"][0].astext == sha,
            Run.status != "failed",
        )
        .limit(1)
    )
    if known:
        return {
            "queued": None,
            "why": "already_read",
            "reason": f"{READS[tool].capitalize()} is already done or queued.",
        }
    route, reason = await _route(session, tool, acq)
    if route is None:
        return {"queued": None, "why": "no_model", "reason": reason}
    inputs = {"blobs": [sha], "acquisition": str(acq.id)}
    if tool == "ocr":
        inputs |= await extract_target(session, acq)
    run = Run(
        principal_id=(await system_agent(session, "extractor")).id,
        tool=tool,
        route=str(route),
        model_requested=Config.RUN_MODELS[route],
        inputs=inputs,
    )
    session.add(run)
    await session.flush()
    return {"queued": tool, "run": str(run.id), "route": str(route)}


async def queue_processing(session: AsyncSession, acq: Acquisition, mime: str) -> dict:
    """Queue OCR, extraction or transcription for new bytes, so the first human
    contact is a review card. The policy picks the routes."""
    tool = _tool(mime)
    if tool is None:
        reason = (
            "A person maps the sheet's columns first."
            if mime in sheets.MIMES
            else "PDF pages are read by the PDF run, then claims page by page."
            if mime == "application/pdf"
            else f"Nothing reads {mime} files automatically."
        )
        return {"queued": None, "reason": reason}
    if tool == "transcribe":
        return await queue_transcribe(session, acq)
    return await queue_read(session, acq, tool, acq.blob_sha256)


async def ingest_bytes(session: AsyncSession, data: bytes, **kw) -> dict:
    return await ingest(session, chunked(data), **kw)


# --- URLs ----------------------------------------------------------------------


async def capture_url(
    session: AsyncSession,
    url: str,
    *,
    blob_dir: str,
    collector_id: uuid.UUID,
    transport: httpx.AsyncBaseTransport | None = None,
    context: dict | None = None,
    **kw,
) -> dict:
    target = normalise_url(url)
    async with netguard.stream(target, transport=transport) as (resp, final_url):
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return await ingest(
            session,
            resp.aiter_bytes(),
            blob_dir=blob_dir,
            mime=content_type.split(";")[0].strip().lower(),
            origin=f"url:{target}",
            collector_id=collector_id,
            context={
                "final_url": final_url,
                "status": resp.status_code,
                "content_type": content_type,
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            | (context or {}),
            **kw,
        )


async def recheck_url(
    session: AsyncSession,
    acquisition_id: uuid.UUID,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Re-fetch a captured URL and record whether it is unchanged, edited or gone.
    Only 404 and 410 mean gone; other failures raise rather than guess."""
    acq = await session.get(Acquisition, acquisition_id)
    if acq is None or not acq.origin.startswith("url:"):
        raise ValueError(f"acquisition {acquisition_id} is not a url capture")
    url = acq.origin.removeprefix("url:")
    async with netguard.stream(url, transport=transport) as (resp, _):
        if resp.status_code in (404, 410):
            value = {"status": "gone", "http_status": resp.status_code}
        else:
            resp.raise_for_status()
            digest = hashlib.sha256()
            async for chunk in resp.aiter_bytes():
                digest.update(chunk)
            now = digest.hexdigest()
            status = "unchanged" if now == acq.blob_sha256 else "edited"
            value = {"status": status, "sha256": now}
    statements = await agent_run(
        session,
        "url_check",
        {"blobs": [acq.blob_sha256], "acquisition": str(acq.id)},
        [(f"blob:{acq.blob_sha256}", Prop.URL_LIVENESS, value)],
    )
    return {"sha256": acq.blob_sha256, "statements": statements}
