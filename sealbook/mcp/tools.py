"""The three MCP tools. Each call is one sealed run by the agent principal,
signed with an ed25519 key that exists only for the call. No tool accepts,
deletes or exports anything, and none ever will."""

from __future__ import annotations

import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from jsonschema import Draft202012Validator
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sealbook.ledger import chain, write
from sealbook.ledger.contracts import (
    EXTRACTED_PROPS,
    PRIVATE_PROPS,
    EvidenceKind,
    Prop,
    Refused,
    StatementIn,
    Tier,
)
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Chain,
    Payload,
    Principal,
    Run,
    Statement,
    StatementTier,
)
from sealbook.mcp.oauth import Caller

UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
SHA256 = "[0-9a-f]{64}"
DOCUMENT_PROPS = frozenset({Prop.DOCUMENT_TYPE, Prop.DOCUMENT_DATE})
FACT_PROPS = sorted(str(p) for p in EXTRACTED_PROPS)
EVIDENCE = {
    "type": "array",
    "items": {"enum": [str(e) for e in EvidenceKind]},
    "minItems": 1,
    "uniqueItems": True,
    "description": "What in the source carries this, never a confidence score.",
}
HEAD = {
    "type": ["string", "null"],
    "pattern": f"^{SHA256}$",
    "description": "The subject head that read returned when you looked "
    "(history or fetch), or null if the subject had none. Review uses it to "
    "show proposals that went stale.",
}
COUNT = {"type": "integer", "minimum": 0}

TOOLS = [
    {
        "name": "read",
        "title": "Read the evidence ledger",
        "description": (
            "Read evidence under your user's permissions. Every call is recorded "
            "on the ledger as an access by you on behalf of your user.\n"
            "target=search: text match on statement values (names, places, "
            "branches, dates); needs query. Returns statements with subject, "
            "tier and neighbour ids (blob, run, author, related statements).\n"
            "target=fetch: one item by ref (statement:<uuid>, blob:<sha256> or "
            "person:<uuid>) with its neighbours and its subject head.\n"
            "target=history: every statement on a subject (ref) in ledger order, "
            "with the subject head to pass as based_on when you propose.\n"
            "target=whoami: you, the human you act for, and your scopes.\n"
            "Tiers: unconfirmed is a draft, never a fact; corroborated has "
            "independent origins; accepted is human-attested or deterministic; "
            "rejected was refused by a human. Say which tier a value has "
            "whenever you report it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"enum": ["search", "fetch", "history", "whoami"]},
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "ref": {
                    "type": "string",
                    "pattern": f"^(statement:{UUID}|person:{UUID}|blob:{SHA256})$",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["target"],
            "additionalProperties": False,
            "allOf": [
                {
                    "if": {"properties": {"target": {"const": "search"}}},
                    "then": {"required": ["query"]},
                },
                {
                    "if": {"properties": {"target": {"enum": ["fetch", "history"]}}},
                    "then": {"required": ["ref"]},
                },
            ],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "propose_statements",
        "title": "Propose facts for human review",
        "description": (
            "Propose facts read out of source documents. Each item becomes an "
            "unconfirmed statement authored by you on behalf of your user; "
            "nothing is a fact until a human accepts it. Rules: copy the value "
            "exactly as written, in its original script, with its language "
            "(add norm only for a normalised spelling); give the span in the "
            "source bytes it was read from; list the evidence kinds that carry "
            "it; set based_on to the subject head you read. Facts about a person "
            "mention use subject person:<uuid> (a new uuid for a new mention); "
            "document_type and document_date use blob:<sha256>. A value a human "
            "rejected for that subject, or one already pending review, is "
            "refused with the reason. Retrying an item returns the statement it "
            "already created."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject": {
                                "type": "string",
                                "pattern": f"^(person:{UUID}|blob:{SHA256})$",
                            },
                            "prop": {"enum": FACT_PROPS},
                            "value": {
                                "type": "object",
                                "properties": {
                                    "text": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 2000,
                                    },
                                    "lang": {
                                        "type": "string",
                                        "pattern": "^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$",
                                    },
                                    "norm": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 2000,
                                    },
                                },
                                "required": ["text", "lang"],
                                "additionalProperties": False,
                            },
                            "span": {
                                "type": "object",
                                "properties": {
                                    "blob": {
                                        "type": "string",
                                        "pattern": f"^{SHA256}$",
                                    },
                                    "span": {
                                        "oneOf": [
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "page": {
                                                        "type": "integer",
                                                        "minimum": 1,
                                                    },
                                                    "bbox": {
                                                        "type": "array",
                                                        "items": COUNT,
                                                        "minItems": 4,
                                                        "maxItems": 4,
                                                    },
                                                },
                                                "required": ["page", "bbox"],
                                                "additionalProperties": False,
                                            },
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "start": COUNT,
                                                    "end": COUNT,
                                                },
                                                "required": ["start", "end"],
                                                "additionalProperties": False,
                                            },
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "ms": {
                                                        "type": "array",
                                                        "items": COUNT,
                                                        "minItems": 2,
                                                        "maxItems": 2,
                                                    }
                                                },
                                                "required": ["ms"],
                                                "additionalProperties": False,
                                            },
                                        ],
                                        "description": "{page, bbox} in integer "
                                        "pixels, {start, end} in characters, or "
                                        "{ms: [start, end]} for audio and video.",
                                    },
                                },
                                "required": ["blob", "span"],
                                "additionalProperties": False,
                            },
                            "evidence": EVIDENCE,
                            "based_on": HEAD,
                        },
                        "required": [
                            "subject",
                            "prop",
                            "value",
                            "span",
                            "evidence",
                            "based_on",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "propose_identity",
        "title": "Propose whether two person mentions are one person",
        "description": (
            "Propose same_as or not_same between two person mentions, for human "
            "review. Nothing merges until a human accepts it. Matching "
            "procedure, fail closed:\n"
            "1. Names alone never match. Exact-name twins are common and "
            "transliterations collide; a shared name is not evidence.\n"
            "2. Propose same_as only with one strong signal (an identifier unique "
            "to one person, such as a registry or ID number, on both mentions) "
            "or two independent corroborating fields (for example birth date and "
            "father's name, or birth date and detention branch at overlapping "
            "dates).\n"
            "3. Any contradicting field (a different birth date or mother's "
            "name) rules out same_as; propose not_same only when the "
            "contradiction is clear.\n"
            "4. When unsure, propose nothing.\n"
            "basis lists the statements carrying the matching or contradicting "
            "fields: at least one on each mention and at least one that is not a "
            "name. based_on is the head of person."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "relation": {"enum": ["same_as", "not_same"]},
                "person": {"type": "string", "pattern": f"^person:{UUID}$"},
                "other": {"type": "string", "pattern": f"^person:{UUID}$"},
                "basis": {
                    "type": "array",
                    "items": {"type": "string", "pattern": f"^statement:{UUID}$"},
                    "minItems": 2,
                    "maxItems": 20,
                    "uniqueItems": True,
                },
                "evidence": EVIDENCE,
                "based_on": HEAD,
            },
            "required": [
                "relation",
                "person",
                "other",
                "basis",
                "evidence",
                "based_on",
            ],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]
VALIDATORS = {t["name"]: Draft202012Validator(t["inputSchema"]) for t in TOOLS}
RELATED_CAP = 20
LIST_CAP = 50


class ToolError(Exception):
    """A bad call; the message goes back to the agent so it can correct itself."""


async def call(db: AsyncSession, caller: Caller, name: str, arguments: dict) -> dict:
    error = next(iter(VALIDATORS[name].iter_errors(arguments)), None)
    if error is not None:
        where = "/".join(str(p) for p in error.absolute_path) or "arguments"
        raise ToolError(f"{where}: {error.message}")
    handler = {
        "read": read,
        "propose_statements": propose_statements,
        "propose_identity": propose_identity,
    }[name]
    return await handler(db, caller, arguments)


# --- runs ----------------------------------------------------------------------


async def _open_run(db: AsyncSession, caller: Caller, inputs: dict):
    key = Ed25519PrivateKey.generate()
    run = Run(
        principal_id=caller.agent.id,
        tool="mcp",
        status="running",
        inputs={"client": caller.grant.client_id, **inputs},
        pubkey=key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
    )
    db.add(run)
    await db.flush()

    def sign(h: str) -> dict:
        return {"kind": "ed25519", "sig": key.sign(h.encode()).hex()}

    return run, sign


async def _seal(db: AsyncSession, run: Run, result: dict) -> None:
    await write.seal_run(db, run, chain.sha256(chain.canon(result)))


# --- read ------------------------------------------------------------------------


def _ref(kind: str, ident) -> str | None:
    return f"{kind}:{ident}" if ident is not None else None


def _brief(st: Statement, value, tier) -> dict:
    return {
        "id": _ref("statement", st.id),
        "subject": st.subject,
        "prop": st.prop,
        "value": value,
        "tier": tier,
    }


def _rows():
    """Statements with value and tier. Access logs are never shown to agents."""
    return (
        select(Statement, Payload.value, StatementTier.tier)
        .outerjoin(Payload, Payload.commitment == Statement.commitment)
        .outerjoin(StatementTier, StatementTier.statement_id == Statement.id)
        .where(Statement.prop.not_in(PRIVATE_PROPS | {Prop.ACCESS}))
    )


async def _related(db: AsyncSession, subjects: set[str]) -> dict[str, list[str]]:
    rows = await db.execute(
        select(Statement.subject, Statement.id)
        .where(
            Statement.subject.in_(subjects),
            Statement.prop.not_in(PRIVATE_PROPS | {Prop.ACCESS}),
        )
        .order_by(Statement.recorded_at)
    )
    related: dict[str, list[str]] = {}
    for subject, sid in rows.all():
        related.setdefault(subject, []).append(_ref("statement", sid))
    return related


async def _search(db: AsyncSession, query: str, limit: int) -> dict:
    # ponytail: substring match over norm-or-text, newest first. The real
    # search (pg_trgm, pgvector, Arabic name normaliser, ranking) replaces this.
    text_value = func.coalesce(
        Payload.value["norm"].astext, Payload.value["text"].astext
    )
    rows = (
        await db.execute(
            _rows()
            .where(
                Statement.prop.in_(FACT_PROPS),
                StatementTier.tier != Tier.REJECTED,
                text_value.icontains(query, autoescape=True),
            )
            .order_by(Statement.recorded_at.desc())
            .limit(limit)
        )
    ).all()
    related = await _related(db, {st.subject for st, _, _ in rows})
    hits = []
    for st, value, tier in rows:
        own = _ref("statement", st.id)
        others = [r for r in related.get(st.subject, []) if r != own]
        hits.append(
            _brief(st, value, tier)
            | {
                "neighbours": {
                    "blob": _ref("blob", st.span_blob),
                    "run": _ref("run", st.run_id),
                    "author": _ref("principal", st.author_id),
                    "statements": others[:RELATED_CAP],
                }
            }
        )
    return {"target": "search", "query": query, "hits": hits}


async def _principal(db: AsyncSession, pid) -> dict:
    p = await db.get(Principal, pid)
    return {
        "id": _ref("principal", p.id),
        "kind": p.kind,
        "name": p.name,
        "on_behalf_of": _ref("principal", p.on_behalf_of),
    }


async def _fetch_statement(db: AsyncSession, sid: uuid.UUID) -> dict:
    row = (await db.execute(_rows().where(Statement.id == sid))).first()
    if row is None:
        raise ToolError(f"statement:{sid} not found")
    st, value, tier = row
    run = await db.get(Run, st.run_id) if st.run_id else None
    about = (
        await db.execute(_rows().where(Statement.subject == f"statement:{sid}"))
    ).all()
    related = (await _related(db, {st.subject})).get(st.subject, [])
    reason = await db.scalar(
        select(StatementTier.reason).where(StatementTier.statement_id == sid)
    )
    return _brief(st, value, tier) | {
        "tier_reason": reason,
        "span": {"blob": _ref("blob", st.span_blob), "span": st.span}
        if st.span_blob
        else None,
        "evidence": st.evidence,
        "based_on": st.based_on,
        "supersedes": _ref("statement", st.supersedes),
        "recorded_at": st.recorded_at.isoformat(),
        "hash": st.hash,
        "author": await _principal(db, st.author_id),
        "run": {
            "id": _ref("run", run.id),
            "tool": run.tool,
            "model_served": run.model_served,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
        if run
        else None,
        "about": [_brief(*r) for r in about],
        "related": [r for r in related if r != _ref("statement", sid)][:RELATED_CAP],
        "head": await write.subject_head(db, st.subject),
    }


async def _fetch_blob(db: AsyncSession, sha: str) -> dict:
    blob = await db.get(Blob, sha)
    if blob is None:
        raise ToolError(f"blob:{sha} not found")
    acquisitions = (
        await db.execute(
            select(Acquisition)
            .where(Acquisition.blob_sha256 == sha)
            .order_by(Acquisition.at)
        )
    ).scalars()
    spans = await db.execute(
        _rows()
        .where(Statement.span_blob == sha)
        .order_by(Statement.recorded_at)
        .limit(LIST_CAP)
    )
    about = await db.execute(
        _rows()
        .where(Statement.subject == f"blob:{sha}")
        .order_by(Statement.recorded_at)
    )
    return {
        "id": _ref("blob", sha),
        "md5": blob.md5,
        "size": blob.size,
        "mime": blob.mime,
        "acquisitions": [
            {
                "origin": a.origin,
                "at": a.at.isoformat(),
                "doc_type": a.doc_type,
                "sensitivity": a.sensitivity,
            }
            for a in acquisitions
        ],
        "statements": [_brief(*r) for r in spans.all()],
        "about": [_brief(*r) for r in about.all()],
        "head": await write.subject_head(db, f"blob:{sha}"),
    }


async def _fetch_person(db: AsyncSession, ref: str) -> dict:
    rows = (
        await db.execute(
            _rows().where(Statement.subject == ref).order_by(Statement.recorded_at)
        )
    ).all()
    if not rows:
        raise ToolError(f"{ref} has no statements")
    person = ref.removeprefix("person:")
    links = await db.execute(
        _rows().where(
            Statement.prop.in_([Prop.SAME_AS, Prop.NOT_SAME]),
            or_(Statement.subject == ref, Payload.value["person"].astext == person),
        )
    )
    return {
        "id": ref,
        "statements": [_brief(*r) for r in rows],
        "identity": [_brief(*r) for r in links.all()],
        "blobs": sorted(
            {_ref("blob", st.span_blob) for st, _, _ in rows if st.span_blob}
        ),
        "head": await write.subject_head(db, ref),
    }


async def _history(db: AsyncSession, ref: str) -> dict:
    rows = await db.execute(
        select(Statement, Payload.value, StatementTier.tier, Chain.seq)
        .join(
            Chain, (Chain.kind == "statement") & (Chain.ref == cast(Statement.id, Text))
        )
        .outerjoin(Payload, Payload.commitment == Statement.commitment)
        .outerjoin(StatementTier, StatementTier.statement_id == Statement.id)
        .where(
            Statement.subject == ref,
            Statement.prop.not_in(PRIVATE_PROPS | {Prop.ACCESS}),
        )
        .order_by(Chain.seq)
    )
    return {
        "id": ref,
        "statements": [
            _brief(st, value, tier)
            | {
                "seq": seq,
                "author": _ref("principal", st.author_id),
                "based_on": st.based_on,
                "recorded_at": st.recorded_at.isoformat(),
            }
            for st, value, tier, seq in rows.all()
        ],
        "head": await write.subject_head(db, ref),
    }


async def _whoami(db: AsyncSession, caller: Caller) -> dict:
    return {
        "agent": await _principal(db, caller.agent.id),
        "on_behalf_of": await _principal(db, caller.agent.on_behalf_of),
        "roles": sorted(r.name for r in caller.user.roles),
        "granted": sorted(caller.grant.scope.split()),
        "scopes": sorted(caller.scopes),
    }


async def read(db: AsyncSession, caller: Caller, args: dict) -> dict:
    target, ref = args["target"], args.get("ref")
    if target == "search":
        result = await _search(db, args["query"], args.get("limit", 20))
    elif target == "whoami":
        result = await _whoami(db, caller)
    elif target == "history":
        result = await _history(db, ref)
    elif ref.startswith("statement:"):
        result = await _fetch_statement(db, uuid.UUID(ref.removeprefix("statement:")))
    elif ref.startswith("blob:"):
        result = await _fetch_blob(db, ref.removeprefix("blob:"))
    else:
        result = await _fetch_person(db, ref)
    run, sign = await _open_run(db, caller, {"mcp_tool": "read", "target": target})
    # The run id keeps each access value distinct: the write path refuses a value
    # identical to one still unconfirmed on the same subject.
    seen = {"target": target, "run": str(run.id)}
    if target == "search":
        seen |= {"query": args["query"], "results": [h["id"] for h in result["hits"]]}
    await write.append(
        db,
        StatementIn(
            subject=ref
            if target in ("fetch", "history")
            else _ref("principal", caller.agent.id),
            prop=Prop.ACCESS,
            value=seen,
            author_id=caller.agent.id,
            run_id=run.id,
        ),
        sign=sign,
    )
    await _seal(db, run, result)
    return result


# --- propose ---------------------------------------------------------------------


async def _propose(db: AsyncSession, caller: Caller, s: StatementIn, sign) -> dict:
    try:
        st = await write.append(db, s, sign=sign)
    except Refused as refused:
        return {
            "refused": refused.reason,
            "existing": _ref("statement", refused.existing),
        }
    tier = await db.scalar(
        select(StatementTier.tier).where(StatementTier.statement_id == st.id)
    )
    return {"statement": _ref("statement", st.id), "tier": tier}


async def propose_statements(db: AsyncSession, caller: Caller, args: dict) -> dict:
    items = args["items"]
    for i, item in enumerate(items):
        is_doc = Prop(item["prop"]) in DOCUMENT_PROPS
        if is_doc != item["subject"].startswith("blob:"):
            want = "blob:<sha256>" if is_doc else "person:<uuid>"
            raise ToolError(f"items/{i}: {item['prop']} needs a {want} subject")
        shas = {item["span"]["blob"]}
        if is_doc:
            shas.add(item["subject"].removeprefix("blob:"))
        for sha in shas:
            if await db.get(Blob, sha) is None:
                raise ToolError(f"items/{i}: blob:{sha} not found")
    run, sign = await _open_run(
        db, caller, {"mcp_tool": "propose_statements", "items": len(items)}
    )
    results = []
    for item in items:
        statement = StatementIn(
            subject=item["subject"],
            prop=Prop(item["prop"]),
            value=item["value"],
            author_id=caller.agent.id,
            run_id=run.id,
            span_blob=item["span"]["blob"],
            span=item["span"]["span"],
            evidence=tuple(EvidenceKind(e) for e in item["evidence"]),
            based_on=item["based_on"],
        )
        results.append(await _propose(db, caller, statement, sign))
    result = {"run": _ref("run", run.id), "results": results}
    await _seal(db, run, result)
    return result


async def propose_identity(db: AsyncSession, caller: Caller, args: dict) -> dict:
    person, other = args["person"], args["other"]
    if person == other:
        raise ToolError("person and other are the same mention")
    ids = [uuid.UUID(b.removeprefix("statement:")) for b in args["basis"]]
    basis = (
        await db.execute(
            select(Statement.id, Statement.subject, Statement.prop, StatementTier.tier)
            .outerjoin(StatementTier, StatementTier.statement_id == Statement.id)
            .where(Statement.id.in_(ids))
        )
    ).all()
    if len(basis) != len(ids):
        raise ToolError("basis: a statement was not found")
    if any(prop not in FACT_PROPS for _, _, prop, _ in basis):
        raise ToolError("basis: only facts about the two mentions count")
    if any(tier == Tier.REJECTED for *_, tier in basis):
        raise ToolError("basis: a statement was rejected by a human")
    if {subject for _, subject, _, _ in basis} != {person, other}:
        raise ToolError("basis: needs statements on both mentions and no others")
    if all(prop == Prop.NAME for _, _, prop, _ in basis):
        raise ToolError("basis: names alone never establish identity")
    run, sign = await _open_run(
        db,
        caller,
        {"mcp_tool": "propose_identity", "basis": sorted(args["basis"])},
    )
    statement = StatementIn(
        subject=person,
        prop=Prop(args["relation"]),
        value={"person": other.removeprefix("person:")},
        author_id=caller.agent.id,
        run_id=run.id,
        evidence=tuple(EvidenceKind(e) for e in args["evidence"]),
        based_on=args["based_on"],
    )
    result = {"run": _ref("run", run.id)} | await _propose(db, caller, statement, sign)
    await _seal(db, run, result)
    return result
