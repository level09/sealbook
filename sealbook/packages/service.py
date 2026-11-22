"""Briefs, redaction profiles and packages: build a verifiable offline package
(sealbook.ledger.verify_package layout v1), collect two human attestations over its
manifest hash, then finalize and export."""

from __future__ import annotations

import asyncio
import io
import shutil
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import orjson
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sealbook import blobs
from sealbook.ledger import attest, chain
from sealbook.ledger.contracts import PolicyRow, Prop, Refused, StatementIn, Tier
from sealbook.ledger.gate import resolve
from sealbook.ledger.models import (
    Acquisition,
    Blob,
    Chain,
    Payload,
    Policy,
    Principal,
    Run,
    Statement,
    StatementTier,
)
from sealbook.ledger.write import append, current_clause, principal_for_user
from sealbook.monitor.filter import source, statement_blob
from sealbook.packages.models import Brief, Package, Sentence

FACTUAL = (Tier.ACCEPTED, Tier.CORROBORATED)
# Records about use or about packages, never facts a sentence can rest on.
NOT_CITABLE = (
    Prop.REVIEW,
    Prop.RETRACTION,
    Prop.ACCESS,
    Prop.ATTEST,
    Prop.PACKAGE,
    Prop.ERASURE,
    Prop.REVIEW_REQUEST,
    Prop.REVIEW_COMMENT,
    Prop.FINGERPRINT,
    Prop.MEDIA_MATCH,
    Prop.CONCLUSION,
    Prop.CASE_STATUS,
)

# ponytail: profiles are a module constant chosen without partner or legal input;
# move them to a table when someone needs to edit one.
# "withhold": props whose values stay out of manifest "values" (the commitment
# still verifies). "blobs": whether source bytes ship; without them spans are
# references only.
PROFILES = {
    "family": {
        "withhold": {Prop.REVIEW, Prop.SAME_AS, Prop.NOT_SAME},
        "blobs": False,
    },
    "partner": {"withhold": {Prop.REVIEW}, "blobs": True},
    "court": {"withhold": set(), "blobs": True},
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _access(db: AsyncSession, user, subject: str, value: dict) -> None:
    who = await principal_for_user(db, user.id, user.name or user.email)
    await append(
        db,
        StatementIn(subject=subject, prop=Prop.ACCESS, value=value, author_id=who.id),
    )


# --- briefs ---------------------------------------------------------------------


def factual_clause(allow=()):
    """Accepted or corroborated, current, and a fact; `allow` names props of
    NOT_CITABLE that may stand anyway (a conclusion may rest on a media match)."""
    return (
        StatementTier.tier.in_([str(t) for t in FACTUAL]),
        Statement.prop.not_in([str(p) for p in NOT_CITABLE if p not in allow]),
        current_clause(),
    )


async def require_factual(db: AsyncSession, ids: list[uuid.UUID], allow=()) -> None:
    found = set(
        (
            await db.execute(
                select(Statement.id)
                .join(StatementTier, StatementTier.statement_id == Statement.id)
                .where(Statement.id.in_(ids), *factual_clause(allow))
            )
        ).scalars()
    )
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise Refused(f"not in the factual projection: {', '.join(missing)}")


async def save_sentence(
    db: AsyncSession,
    brief: Brief,
    user,
    text: str,
    statement_ids: list[str],
    sentence_id: uuid.UUID | None = None,
) -> Sentence:
    """Every sentence rests on statements; saving cites them, which moves them up
    the review queue."""
    if not text.strip():
        raise Refused("a sentence needs text")
    if not statement_ids:
        raise Refused("a sentence must cite at least one statement")
    ids = list(dict.fromkeys(uuid.UUID(i) for i in statement_ids))
    await require_factual(db, ids)
    if sentence_id:
        sentence = await db.get(Sentence, sentence_id)
        if sentence is None or sentence.brief_id != brief.id:
            raise Refused("no such sentence in this brief")
        sentence.text, sentence.statement_ids = text, ids
    else:
        last = (
            await db.execute(
                select(func.max(Sentence.ord)).where(Sentence.brief_id == brief.id)
            )
        ).scalar()
        sentence = Sentence(
            brief_id=brief.id, ord=(last or 0) + 1, text=text, statement_ids=ids
        )
        db.add(sentence)
    await db.flush()
    await _access(
        db,
        user,
        f"brief:{brief.id}",
        {"action": "cite", "at": _now(), "statements": [str(i) for i in ids]},
    )
    return sentence


# --- package build --------------------------------------------------------------


def _plain(row: dict) -> dict:
    return orjson.loads(chain.canon(row))


def _row(obj, kind: str) -> dict:
    row = {name: getattr(obj, name) for name in chain.CHAINED_FIELDS[kind]}
    return _plain(row) | {"hash": chain.row_hash(kind, row)}


def _cited_ids(brief: Brief) -> list[uuid.UUID]:
    ids = [i for s in brief.sentences for i in s.statement_ids]
    if not ids:
        raise Refused("the brief has no cited sentences")
    return list(dict.fromkeys(ids))


async def cases_of(db: AsyncSession, brief: Brief) -> list[str]:
    """The cases a brief belongs to: those whose sources hold a statement it cites."""
    ids = [i for s in brief.sentences for i in s.statement_ids]
    if not ids:
        return []
    found = await db.scalars(
        select(Acquisition.context["investigation"].astext)
        .join(Statement, Acquisition.blob_sha256 == source(statement_blob()))
        .where(Statement.id.in_(ids))
        .distinct()
    )
    return sorted(c for c in found if c)


async def case_conclusions(db: AsyncSession, brief: Brief, user=None) -> list[dict]:
    """What a brief opens with: each of its cases, where it stands and its standing
    conclusions, each saying whether it still stands on what it cited."""
    from sealbook.cases import conclusions
    from sealbook.cases.models import Case

    out = []
    for slug in await cases_of(db, brief):
        found = await conclusions.listing(db, slug, user)
        name = await db.scalar(select(Case.name).where(Case.slug == slug))
        out.append(
            {
                "case": slug,
                "name": name or slug,
                "status": found["status"],
                "conclusions": [
                    c for c in found["conclusions"] if c["state"] == "standing"
                ],
            }
        )
    return out


def _sealed(found: list[dict]) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """The statements a package carries for its cases' conclusions: each conclusion
    and the status it left the case in, and the claims it contradicts (what was
    said, accepted or not), then what the conclusions rest on. One that no longer
    stands is refused: it is revised or withdrawn before it is sealed."""
    own, rests = [], []
    for case in found:
        for c in case["conclusions"]:
            if not c["stands"]:
                raise Refused(
                    "a conclusion of this case no longer stands on what it cited; "
                    "revise or withdraw it before building"
                )
            own.append(uuid.UUID(c["id"]))
            own += [uuid.UUID(i) for i in c.get("contradicts") or []]
            rests += [uuid.UUID(i) for i in c["rests_on"]]
        if case["conclusions"] and case["status"].get("id"):
            own.append(uuid.UUID(case["status"]["id"]))
    return own, rests


async def collect(
    db: AsyncSession,
    brief: Brief,
    opening: list | None = None,
    decisions: list[uuid.UUID] = (),
) -> dict:
    """The rows a brief's package discloses: cited statements plus the reviews and
    attestations about them, the DERIVED_FROM links from their text to the
    acquired files, those files' acquisitions and the runs. `opening` is the brief's
    case conclusions (case_conclusions), carried with what they rest on;
    `decisions` the signed choices to include sensitive files (disclosures)."""
    own, rests = _sealed(opening or [])
    cites = _cited_ids(brief)
    # A conclusion and the case status are records about the case, never a fact a
    # sentence cites; what they rest on must be factual like any citation, and may
    # be an accepted match between two files.
    await require_factual(db, cites)
    await require_factual(db, rests, allow={Prop.MEDIA_MATCH})
    facts = list(dict.fromkeys([*rests, *cites]))
    cited = list(dict.fromkeys([*own, *facts, *decisions]))
    about = [f"statement:{i}" for i in cited]
    statements = list(
        (
            await db.execute(
                select(Statement)
                .where(
                    Statement.id.in_(cited)
                    | (
                        Statement.subject.in_(about)
                        & Statement.prop.in_([Prop.REVIEW, Prop.ATTEST])
                    )
                )
                .order_by(Statement.recorded_at)
            )
        ).scalars()
    )
    shas = {s.span_blob for s in statements if s.span_blob} | {
        s.subject.removeprefix("blob:")
        for s in statements
        if s.subject.startswith("blob:") and s.id in cited
    }
    # A match joins two files: the second one ships with the first.
    if matched := [
        s.commitment for s in statements if s.prop == Prop.MEDIA_MATCH and s.id in cited
    ]:
        shas |= set(
            await db.scalars(
                select(Payload.value["blob"].astext).where(
                    Payload.commitment.in_(matched)
                )
            )
        )
    # Text read from a PDF, scan or recording has no acquisition of its own: ship
    # the DERIVED_FROM statements back to the acquired file.
    frontier, seen = set(shas), {s.id for s in statements}
    while frontier:
        derived = (
            await db.execute(
                select(Statement, Payload.value["blob"].astext)
                .outerjoin(Payload, Payload.commitment == Statement.commitment)
                .where(
                    Statement.subject.in_([f"blob:{sha}" for sha in frontier]),
                    Statement.prop == Prop.DERIVED_FROM,
                )
            )
        ).all()
        statements += [s for s, _ in derived if s.id not in seen]
        seen |= {s.id for s, _ in derived}
        frontier = {src for _, src in derived if src} - shas
        shas |= frontier
    statements.sort(key=lambda s: s.recorded_at)
    acquisitions = list(
        (
            await db.execute(
                select(Acquisition)
                .where(Acquisition.blob_sha256.in_(shas))
                .order_by(Acquisition.at)
            )
        ).scalars()
    )
    run_ids = {s.run_id for s in statements if s.run_id}
    runs = list((await db.execute(select(Run).where(Run.id.in_(run_ids)))).scalars())
    if open_runs := [str(r.id) for r in runs if r.finished_at is None]:
        raise Refused(f"runs not sealed yet: {', '.join(open_runs)}")
    refs = (
        [("statement", str(s.id)) for s in statements]
        + [("acquisition", str(a.id)) for a in acquisitions]
        + [("run", str(r.id)) for r in runs]
    )
    entries = list(
        (
            await db.execute(
                select(Chain)
                .where(
                    func.concat(Chain.kind, ":", Chain.ref).in_(
                        [f"{k}:{r}" for k, r in refs]
                    )
                )
                .order_by(Chain.seq)
            )
        ).scalars()
    )
    if len(entries) != len(refs):
        raise Refused("some exported rows are missing from the chain")
    blob_rows = list(
        (await db.execute(select(Blob).where(Blob.sha256.in_(shas)))).scalars()
    )
    return {
        "cited": cited,
        "statements": statements,
        "acquisitions": acquisitions,
        "runs": runs,
        "chain": entries,
        "blobs": blob_rows,
    }


# --- sensitive files ------------------------------------------------------------
# A package never ships a sensitive source's bytes silently. By default the file is
# withheld: its hash stays in the chained acquisition row, so a recipient holding a
# copy can still check it. Including it takes a passkey-signed decision by the
# person building, for this brief and its audience, written like a reveal (an
# access statement on the file) and carried in the package.

DISCLOSE = "disclose"


async def decisions(db: AsyncSession, brief: Brief, user) -> dict[str, dict]:
    """The signed decisions of `user` to include sensitive files in this brief's
    package for its audience, by acquired file."""
    who = await principal_for_user(db, user.id, user.name or user.email)
    rows = await db.execute(
        select(Statement, Payload.value)
        .join(Payload, Payload.commitment == Statement.commitment)
        .where(
            Statement.prop == Prop.ACCESS,
            Statement.author_id == who.id,
            Statement.sig["kind"].astext == "webauthn",
            Payload.value["action"].astext == DISCLOSE,
            Payload.value["brief"].astext == str(brief.id),
            Payload.value["audience"].astext == brief.audience,
        )
        .order_by(Statement.recorded_at)
    )
    return {
        st.subject.removeprefix("blob:"): {
            "id": str(st.id),
            "by": who.name,
            "at": st.recorded_at.isoformat(),
        }
        for st, _ in rows
    }


async def files(db: AsyncSession, brief: Brief, rows: dict, user) -> list[dict]:
    """Each acquired file the package's rows reach, with the derived text read from
    it: its title, whether it is sensitive, the builder's signed decision to include
    it, and whether this audience's package ships its bytes."""
    from sealbook.monitor.data import Context

    ctx = await Context.load(db, blobs=[b.sha256 for b in rows["blobs"]])
    decided = await decisions(db, brief, user)
    ships = PROFILES[brief.audience]["blobs"]
    out: dict[str, dict] = {}
    for b in rows["blobs"]:
        doc = ctx.doc(b.sha256)
        src = doc["source_sha256"]
        f = out.setdefault(
            src,
            {
                "sha256": src,
                "title": ctx.doc(src)["title"],
                "sensitive": ctx.doc(src)["sensitive"],
                "decision": decided.get(src),
                "blobs": [],
            },
        )
        f["blobs"].append(b.sha256)
    for f in out.values():
        f["ships"] = ships and (not f["sensitive"] or f["decision"] is not None)
    return sorted(out.values(), key=lambda f: (not f["sensitive"], f["title"]))


async def withheld(db: AsyncSession, package: Package, blob_dir: str) -> list[dict]:
    """The acquired files a built package withholds, read from its manifest: an
    acquisition whose bytes it does not ship."""
    from sealbook.monitor.data import Context

    manifest = await _manifest(db, package, blob_dir)
    shipped = {b["sha256"] for b in manifest["blobs"]}
    shas = sorted({a["blob_sha256"] for a in manifest["acquisitions"]} - shipped)
    ctx = await Context.load(db, blobs=shas)
    return [{"sha256": sha, "title": ctx.doc(sha)["title"]} for sha in shas]


async def plan_disclose(db: AsyncSession, brief: Brief, user, chosen) -> list:
    """The access statements one passkey gesture signs to include these sensitive
    files in the brief's package. Refused for a file the package does not reach, one
    that is not sensitive, or an audience that receives no files."""
    if not PROFILES[brief.audience]["blobs"]:
        raise Refused(f"a {brief.audience} package carries no source files")
    if not isinstance(chosen, list) or not chosen:
        raise Refused("choose the sensitive files to include")
    rows = await collect(db, brief, await case_conclusions(db, brief))
    reached = {f["sha256"]: f for f in await files(db, brief, rows, user)}
    who = await principal_for_user(db, user.id, user.name or user.email)
    out = []
    for sha in dict.fromkeys(map(str, chosen)):
        f = reached.get(sha)
        if f is None or not f["sensitive"]:
            raise Refused("only a sensitive file this package carries can be included")
        out.append(
            StatementIn(
                subject=f"blob:{sha}",
                prop=Prop.ACCESS,
                value={
                    "action": DISCLOSE,
                    "at": _now(),
                    "audience": brief.audience,
                    "brief": str(brief.id),
                    "title": f["title"],
                },
                author_id=who.id,
            )
        )
    return out


async def _require_anchored(db: AsyncSession, seqs: list[int]) -> None:
    from sealbook.anchor.service import anchored

    if not await anchored(db, seqs):
        raise Refused("some rows are not anchored yet; wait for the next anchor")


async def write_manifest(
    db: AsyncSession,
    rows: dict,
    audience: str,
    directory: Path,
    blob_dir: str,
    withheld: set[str] = frozenset(),
) -> bytes:
    """Write DIR/blobs and DIR/anchors and return the manifest.json bytes; every
    other file already at the DIR root is listed in its documents. A `withheld`
    blob ships no bytes; its acquisition row still carries its hash."""
    from sealbook.anchor.service import proofs

    profile = PROFILES[audience]
    seqs = [e.seq for e in rows["chain"]]
    await _require_anchored(db, seqs)
    anchoring = await proofs(db, seqs)
    (directory / "anchors").mkdir()
    for anchor in anchoring["anchors"]:
        (directory / "anchors" / anchor["file"]).write_bytes(anchor["bytes"])
    blob_rows = []
    if profile["blobs"]:
        (directory / "blobs").mkdir()
        for blob in rows["blobs"]:
            if blob.sha256 in withheld:
                continue
            (directory / "blobs" / blob.sha256).write_bytes(blobs.read(blob, blob_dir))
            blob_rows.append(
                {"sha256": blob.sha256, "md5": blob.md5, "file": blob.sha256}
            )
    disclosed = [
        s.commitment for s in rows["statements"] if s.prop not in profile["withhold"]
    ]
    payloads = (
        await db.execute(select(Payload).where(Payload.commitment.in_(disclosed)))
    ).scalars()
    manifest = {
        "version": 1,
        "statements": [_row(s, "statement") for s in rows["statements"]],
        "acquisitions": [_row(a, "acquisition") for a in rows["acquisitions"]],
        "runs": [_row(r, "run") for r in rows["runs"]],
        "chain": [
            {k: getattr(e, k) for k in ("seq", "kind", "ref", "row_hash", "prev_hash")}
            | {"hash": e.hash}
            for e in rows["chain"]
        ],
        "batches": anchoring["batches"],
        "proofs": anchoring["proofs"],
        "anchors": [
            {k: a[k] for k in ("root", "kind", "file")} for a in anchoring["anchors"]
        ],
        "blobs": blob_rows,
        "values": [
            _plain({"commitment": p.commitment, "salt": p.salt, "value": p.value})
            for p in payloads
        ],
        "signatures": [
            {"hash": s.hash, "sig": s.sig} for s in rows["statements"] if s.sig
        ],
        "documents": [
            {"file": p.name, "sha256": chain.sha256(p.read_bytes())}
            for p in sorted(directory.iterdir())
            if p.is_file()
        ],
    }
    return orjson.dumps(manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)


async def methodology(db: AsyncSession, rows: dict, audience: str) -> str:
    """Berkeley Protocol paras 25, 143(c), 197: tools, models served, prompts,
    measured error rates and reviewers, generated from the exported rows."""
    policies = [
        PolicyRow(
            capability=p.capability,
            doc_type=p.doc_type,
            sensitivity=p.sensitivity,
            calibration=p.calibration,
        )
        for p in (await db.execute(select(Policy))).scalars()
    ]
    acq_by_blob = {}
    for a in rows["acquisitions"]:
        acq_by_blob.setdefault(a.blob_sha256, a)
    lines = [
        "# Methodology",
        "",
        f"Audience: {audience}.",
        f"Generated from the {len(rows['runs'])} runs and "
        f"{len(rows['statements'])} statements in this package.",
        "",
        "## Runs",
        "",
    ]
    if not rows["runs"]:
        lines.append("No automated runs produced the exported statements.")
    for r in rows["runs"]:
        first = (r.inputs or {}).get("blobs", [None])[0]
        acq = acq_by_blob.get(first)
        policy = resolve(
            policies,
            r.tool,
            acq.doc_type if acq else "none",
            acq.sensitivity if acq else "sensitive",
        )
        rate = (
            orjson.dumps(policy.calibration, option=orjson.OPT_SORT_KEYS).decode()
            if policy.calibration
            else "not measured"
        )
        lines += [
            f"### Run `{r.id}`",
            "",
            f"- Tool: {r.tool}",
            f"- Route: {r.route or 'none'} ({r.endpoint or 'no endpoint'})",
            f"- Model requested: {r.model_requested or 'none'}",
            f"- Model served: {r.model_served or 'none'}",
            f"- Provider served: {r.provider_served or 'none'}",
            f"- Prompt hash: {r.prompt_hash or 'none'}",
            f"- Output hash: {r.output_hash or 'none'}",
            f"- Finished: {r.finished_at.isoformat()}",
            f"- Measured error rate: {rate}",
            "",
        ]
    counts = {}
    for s in rows["statements"]:
        if s.sig and s.sig.get("kind") == "webauthn":
            counts[s.author_id] = counts.get(s.author_id, 0) + 1
    names = {
        p.id: p.name
        for p in (
            await db.execute(select(Principal).where(Principal.id.in_(counts)))
        ).scalars()
    }
    lines += ["## Reviewers", ""]
    lines += [
        f"- {names[pid]}: attested {n} statement{'s' if n != 1 else ''}"
        for pid, n in sorted(counts.items(), key=lambda kv: names[kv[0]])
    ] or ["No human attestations."]
    lines += [
        "",
        "Attestations are passkey assertions over ledger hashes: attribution to an",
        "account's key, not an eIDAS advanced or qualified electronic signature.",
        "",
    ]
    return "\n".join(lines)


_COVER = """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="120">
<rect width="640" height="120" fill="#fff"/>
<text x="16" y="40" font-family="monospace" font-size="16">{title}</text>
<text x="16" y="80" font-family="monospace" font-size="11">see manifest.json</text>
</svg>
"""


async def c2pa_sign(directory: Path, title: str, documents: dict, cert: str, key: str):
    """Sign DIR/package.svg, a cover asset whose C2PA claim asserts the document
    hashes: `c2patool package.svg` checks it offline. It cannot assert the
    manifest hash, because the manifest lists the cover's own hash."""
    if not cert or not key:
        raise RuntimeError("C2PA_SIGN_CERT and C2PA_SIGN_KEY must be set to package")
    with tempfile.TemporaryDirectory() as tmp:
        cover = Path(tmp) / "cover.svg"
        safe = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        cover.write_text(_COVER.format(title=safe))
        definition = Path(tmp) / "manifest.json"
        definition.write_bytes(
            orjson.dumps(
                {
                    "alg": "es256",
                    "private_key": str(Path(key).resolve()),
                    "sign_cert": str(Path(cert).resolve()),
                    "claim_generator_info": [{"name": "sealbook"}],
                    "assertions": [
                        {
                            "label": "org.sealbook.package",
                            "data": {"documents": documents},
                        }
                    ],
                }
            )
        )
        proc = await asyncio.create_subprocess_exec(
            "c2patool",
            str(cover),
            "-m",
            str(definition),
            "-o",
            str(directory / "package.svg"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"c2patool sign failed: {err.decode()[:500]}")


def _zip(directory: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(directory).as_posix())
    return buf.getvalue()


async def build(db: AsyncSession, brief: Brief, user, config) -> Package:
    """Build the package directory, sign it and keep manifest and zip as blobs."""
    if brief.audience not in PROFILES:
        raise Refused(f"unknown audience {brief.audience}")
    # Said before any work, as the user's refusal: signing needs the server's key.
    if not config.get("C2PA_SIGN_CERT") or not config.get("C2PA_SIGN_KEY"):
        raise Refused(
            "packages are off on this server: no signing certificate is set "
            "(C2PA_SIGN_CERT and C2PA_SIGN_KEY)"
        )
    opening = await case_conclusions(db, brief)
    listed = await files(db, brief, await collect(db, brief, opening), user)
    sensitive = [f for f in listed if f["sensitive"]]
    # The signed decisions travel with the package, so their signatures verify.
    rows = await collect(
        db,
        brief,
        opening,
        [uuid.UUID(f["decision"]["id"]) for f in sensitive if f["decision"]],
    )
    withheld = {b for f in listed if not f["ships"] for b in f["blobs"]}
    blob_dir = config["BLOB_DIR"]
    directory = Path(tempfile.mkdtemp(prefix="package-"))
    try:
        (directory / "brief.json").write_bytes(
            orjson.dumps(
                {
                    "title": brief.title,
                    "audience": brief.audience,
                    # The brief opens with its cases' signed conclusions.
                    "conclusions": [
                        {
                            "case": case["case"],
                            "name": case["name"],
                            "status": case["status"]["status"],
                            "status_statement": case["status"].get("id"),
                            "conclusions": [
                                {
                                    k: c.get(k)
                                    for k in (
                                        "id",
                                        "text",
                                        "standard",
                                        "rests_on",
                                        "contradicts",
                                        "alternatives",
                                        "not_established",
                                        "by",
                                        "at",
                                    )
                                }
                                | {"peer_review": c["peer"]}
                                for c in case["conclusions"]
                            ],
                        }
                        for case in opening
                    ],
                    "sentences": [
                        {
                            "ord": s.ord,
                            "text": s.text,
                            "statements": [str(i) for i in s.statement_ids],
                        }
                        for s in brief.sentences
                    ],
                    # What this reader receives of each sensitive file, and on
                    # whose signed decision; withheld files verify by hash alone.
                    "files": {
                        "withheld": [
                            {"sha256": f["sha256"], "title": f["title"]}
                            for f in listed
                            if not f["ships"]
                        ],
                        "included_sensitive": [
                            {
                                "sha256": f["sha256"],
                                "title": f["title"],
                                "by": f["decision"]["by"],
                                "at": f["decision"]["at"],
                                "decision": f["decision"]["id"],
                            }
                            for f in sensitive
                            if f["ships"]
                        ],
                    },
                },
                option=orjson.OPT_INDENT_2,
            )
        )
        (directory / "methodology.md").write_text(
            await methodology(db, rows, brief.audience)
        )
        await c2pa_sign(
            directory,
            brief.title,
            {
                name: chain.sha256((directory / name).read_bytes())
                for name in ("brief.json", "methodology.md")
            },
            config.get("C2PA_SIGN_CERT"),
            config.get("C2PA_SIGN_KEY"),
        )
        manifest = await write_manifest(
            db, rows, brief.audience, directory, blob_dir, withheld
        )
        (directory / "manifest.json").write_bytes(manifest)
        # The manifest hashes every document, so equal hashes mean equal content.
        sha = chain.sha256(manifest)
        existing = (
            await db.execute(select(Package).where(Package.manifest_sha256 == sha))
        ).scalar()
        if existing:
            return existing
        await blobs.put(db, manifest, "application/json", blob_dir)
        packed = await blobs.put(db, _zip(directory), "application/zip", blob_dir)
    finally:
        shutil.rmtree(directory)
    package = Package(
        brief_id=brief.id,
        audience=brief.audience,
        manifest_sha256=sha,
        zip_sha256=packed.sha256,
        statements=len(rows["statements"]),
        statement_ids=[s.id for s in rows["statements"]],
        created_by=user.id,
    )
    db.add(package)
    await db.flush()
    return package


# --- two-person export -----------------------------------------------------------


async def _attestations(db: AsyncSession, package: Package) -> list:
    q = (
        select(Principal.id, Principal.name, Statement.recorded_at, Statement.sig)
        .join(Principal, Principal.id == Statement.author_id)
        .where(
            Statement.subject == f"blob:{package.manifest_sha256}",
            Statement.prop == Prop.ATTEST,
            Principal.kind == "human",
            Statement.sig["kind"].astext == "webauthn",
        )
        .order_by(Statement.recorded_at)
    )
    return list((await db.execute(q)).all())


async def attesters(db: AsyncSession, package: Package) -> list[dict]:
    return [
        {"principal": str(pid), "name": name, "at": at.isoformat()}
        for pid, name, at, _ in await _attestations(db, package)
    ]


async def _lock(db: AsyncSession, package: Package) -> None:
    """Serialize attest and finalize per package until the transaction ends."""
    key = int.from_bytes(package.id.bytes[:8], "big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(key)))


async def attest_options(db: AsyncSession, user, package: Package) -> dict:
    return await attest.options(db, user, package.manifest_sha256)


async def add_attestation(
    db: AsyncSession, user, package: Package, credential: dict
) -> Statement:
    _live(package)
    who = await principal_for_user(db, user.id, user.name or user.email)
    await _lock(db, package)
    if any(a["principal"] == str(who.id) for a in await attesters(db, package)):
        raise Refused("you already attested this package; a second person must")
    sha = package.manifest_sha256
    sig = await attest.verify(db, user, sha, credential)
    return await append(
        db,
        StatementIn(
            subject=f"blob:{sha}",
            prop=Prop.ATTEST,
            value={"manifest": sha, "at": _now()},
            author_id=who.id,
        ),
        sign=lambda _hash: sig,
    )


async def _manifest(db: AsyncSession, package: Package, blob_dir: str) -> dict:
    blob = await db.get(Blob, package.manifest_sha256)
    return orjson.loads(blobs.read(blob, blob_dir))


def _live(package: Package) -> None:
    if package.revoked_at:
        raise Refused(
            "package revoked: it discloses statements erased since it was built"
        )


async def finalize(db: AsyncSession, user, package: Package, blob_dir: str) -> None:
    """Freeze the export: the built zip plus attestations.json, the passkey
    assertions over the manifest hash, which the manifest cannot contain."""
    _live(package)
    await _lock(db, package)
    await db.refresh(package)
    if package.finalized_at:
        return
    rows = await _attestations(db, package)
    people = {pid for pid, *_ in rows}
    keys = {sig["public_key"] for *_, sig in rows}
    if len(people) < 2 or len(keys) < 2:
        raise Refused("two different people must attest the manifest hash")
    manifest = await _manifest(db, package, blob_dir)
    await _require_anchored(db, [e["seq"] for e in manifest["chain"]])
    who = await principal_for_user(db, user.id, user.name or user.email)
    await append(
        db,
        StatementIn(
            subject=f"blob:{package.manifest_sha256}",
            prop=Prop.PACKAGE,
            value={"audience": package.audience, "statements": package.statements},
            author_id=who.id,
        ),
    )
    signed = orjson.dumps(
        [
            {"name": name, "credential_id": sig["credential_id"], "sig": sig}
            for _, name, _, sig in rows
        ],
        option=orjson.OPT_INDENT_2,
    )
    buf = io.BytesIO(blobs.read(await db.get(Blob, package.zip_sha256), blob_dir))
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("attestations.json", signed)
    exported = await blobs.put(db, buf.getvalue(), "application/zip", blob_dir)
    package.export_sha256 = exported.sha256
    package.finalized_at = datetime.now(UTC)
    await db.flush()


async def export(db: AsyncSession, user, package: Package, blob_dir: str) -> bytes:
    """The zip bytes, logged as an export of every statement in the manifest."""
    _live(package)
    if not package.finalized_at:
        raise Refused("package is not finalized")
    manifest = await _manifest(db, package, blob_dir)
    await _access(
        db,
        user,
        f"blob:{package.manifest_sha256}",
        {
            "action": "export",
            "at": _now(),
            "statements": [s["id"] for s in manifest["statements"]],
        },
    )
    return blobs.read(await db.get(Blob, package.export_sha256), blob_dir)
