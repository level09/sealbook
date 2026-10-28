"""Shared types for the ledger. Frozen contract: modules import from here, only
the integrator edits it. Changing a chained field list breaks every existing
chain, so those tuples never change after the first anchored root.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import date


class Tier(enum.StrEnum):
    UNCONFIRMED = "unconfirmed"
    CORROBORATED = "corroborated"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PrincipalKind(enum.StrEnum):
    HUMAN = "human"
    AGENT = "agent"


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Route(enum.StrEnum):
    LOCAL = "local"
    CLOUD_ZDR = "cloud-zdr"
    CLOUD = "cloud"
    ANTHROPIC = "anthropic"


class Sensitivity(enum.StrEnum):
    SENSITIVE = "sensitive"  # default at intake
    ORDINARY = "ordinary"


class EvidenceKind(enum.StrEnum):
    SIGNATURE_BLOCK = "signature_block"
    REGISTRY_STAMP = "registry_stamp"
    LETTERHEAD = "letterhead"
    HANDWRITTEN_ENTRY = "handwritten_entry"
    PRINTED_TEXT = "printed_text"
    SPOKEN = "spoken"
    PHOTO_CONTENT = "photo_content"
    C2PA_MANIFEST = "c2pa_manifest"
    BYTE_EQUALITY = "byte_equality"
    CORROBORATING_FIELD = "corroborating_field"


class Prop(enum.StrEnum):
    # Facts about a person mention (subject "person:<uuid>")
    NAME = "name"
    BIRTH_DATE = "birth_date"
    PLACE_OF_BIRTH = "place_of_birth"
    FATHER_NAME = "father_name"
    MOTHER_NAME = "mother_name"
    ARRESTED_AT = "arrested_at"
    DETAINED_AT = "detained_at"
    DETENTION_BRANCH = "detention_branch"
    TRANSFERRED_TO = "transferred_to"
    SEEN_AT = "seen_at"
    LAST_SEEN = "last_seen"
    # Person fields read from intake sheets and text. SEX and AGE carry a "norm"
    # ("male"/"female"; whole years) next to the quoted text.
    SEX = "sex"
    AGE = "age"
    OCCUPATION = "occupation"
    # A life event, value {"text", "lang", "kind": one of EVENT_KINDS}, dated by
    # valid_from/valid_to.
    EVENT = "event"
    # Facts about a document (subject "blob:<sha256>")
    DOCUMENT_TYPE = "document_type"
    DOCUMENT_DATE = "document_date"
    SHA256 = "sha256"
    MD5 = "md5"
    C2PA_VERIFY = "c2pa_verify"
    URL_LIVENESS = "url_liveness"
    DERIVED_FROM = "derived_from"
    # Identity (subject "person:<uuid>", value {"person": "<uuid>"})
    SAME_AS = "same_as"
    NOT_SAME = "not_same"
    # About another statement (subject "statement:<uuid>")
    # value {"verdict": "accept" | "reject", "reason": str}, optional "at" (iso
    # time of the gesture) and "acknowledged_head" (the subject head a reviewer
    # accepted a stale proposal against)
    REVIEW = "review"
    RETRACTION = "retraction"
    REVIEW_REQUEST = "review_request"
    REVIEW_COMMENT = "review_comment"
    FINGERPRINT = "fingerprint"
    MEDIA_MATCH = "media_match"
    # Access log (subject is whatever was viewed, searched or exported). Value
    # {"action": "view"|"search"|"reveal"|"cite"|"export", "at": iso time,
    #  "statements": [ids]}; "statements" lists what the use touched, and is what
    # orders the review queue (a statement in use jumps ahead of untouched ones).
    ACCESS = "access"
    # A package (subject "blob:<manifest sha256>", value {"audience": str,
    # "statements": int}); ATTEST is one human's passkey over that manifest hash.
    PACKAGE = "package"
    ATTEST = "attest"
    # Tombstone written by the erasure procedure (subject "person:<uuid>", value
    # {"reason": str, "commitments": int}); the payloads it names are deleted.
    ERASURE = "erasure"
    # Incident layer (subject "incident:<uuid>", except LOCATED_AT). INCIDENT value
    # {"title": text, "kind": str}; OCCURRED_AT {"place": "place:<uuid>"} with
    # valid_from/valid_to; INVOLVED {"person": "person:<uuid>", "role":
    # victim|witness|alleged_perpetrator|other|unspecified}, and an agent may only
    # write "unspecified"; SUPPORTED_BY {"statement": "<uuid>"}.
    INCIDENT = "incident"
    OCCURRED_AT = "occurred_at"
    INVOLVED = "involved"
    SUPPORTED_BY = "supported_by"
    # Discovery finding (subject "finding:<key>"): value {"kind":
    # shared_presence|candidate_incident, "members": [statement ids], "people": [...],
    # "places": [...], "window": [iso, iso], "features": {...}, "key": str}.
    FINDING = "finding"
    # Subject "statement:<uuid>", value {"place": "place:<uuid>", "name": norm}.
    LOCATED_AT = "located_at"
    # Where a photo was taken or a video filmed (subject "blob:<sha256>"), value
    # {"geometry": {"type": "Point", "coordinates": [lon, lat] as decimal strings},
    #  "precision_m": int, "method": "exif" | "c2pa" | "analyst", optional
    #  "bearing" (degrees from north) and "landmarks" (texts an analyst matched)}.
    # "exif" also covers a video container's ISO 6709 location tag.
    PHOTOGRAPHED_AT = "photographed_at"
    FILMED_AT = "filmed_at"
    # Two people or two documents related. Subject "person:<uuid>" with value
    # {"person": "<uuid>", "kind": family|cellmate|command}, or "blob:<sha256>" with
    # {"blob": "<sha256>", "kind": contains|contained_in|related}; both carry
    # "basis" (statement refs) and optionally "why". Proposed by an agent, decided
    # by a person; never collapses source origins.
    RELATION = "relation"


# Props whose value is read out of document bytes: an agent-authored one without a
# span is rejected mechanically.
PRIVATE_PROPS = frozenset({Prop.REVIEW_REQUEST, Prop.REVIEW_COMMENT})

EXTRACTED_PROPS = frozenset(
    {
        Prop.NAME,
        Prop.BIRTH_DATE,
        Prop.PLACE_OF_BIRTH,
        Prop.FATHER_NAME,
        Prop.MOTHER_NAME,
        Prop.ARRESTED_AT,
        Prop.DETAINED_AT,
        Prop.DETENTION_BRANCH,
        Prop.TRANSFERRED_TO,
        Prop.SEEN_AT,
        Prop.LAST_SEEN,
        Prop.SEX,
        Prop.AGE,
        Prop.OCCUPATION,
        Prop.EVENT,
        Prop.DOCUMENT_TYPE,
        Prop.DOCUMENT_DATE,
    }
)

EVENT_KINDS = ("killed", "missing", "displaced", "released", "wounded", "defected")
PERSON_RELATIONS = ("family", "cellmate", "command")
DOCUMENT_RELATIONS = ("contains", "contained_in", "related")

# Fields hashed into each chained row. Order is irrelevant (canonical JSON sorts
# keys); membership is the contract.
CHAINED_FIELDS = {
    "statement": (
        "id",
        "subject",
        "prop",
        "commitment",
        "span_blob",
        "span",
        "author_id",
        "run_id",
        "valid_from",
        "valid_to",
        "supersedes",
        "evidence",
        "based_on",
        "recorded_at",
    ),
    "acquisition": (
        "id",
        "blob_sha256",
        "origin",
        "collector_id",
        "at",
        "context",
        "sensitivity",
        "doc_type",
        "partner",
    ),
    "run": (
        "id",
        "principal_id",
        "tool",
        "route",
        "endpoint",
        "model_requested",
        "model_served",
        "provider_served",
        "prompt_hash",
        "inputs",
        "output_hash",
        "pubkey",
        "finished_at",
    ),
}


@dataclass(frozen=True)
class StatementIn:
    """A statement as an author proposes it. `value` is canonical JSON: str, int,
    bool, None, lists and dicts of those. No floats. Text values keep the original
    script: {"text": "...", "lang": "ar", "norm": "..."}.
    `span` is {"page": int, "bbox": [x0, y0, x1, y1]} in integer pixels, or
    {"start": int, "end": int} in characters, or {"ms": [start, end]} for media, or
    {"sheet": int, "row": int, "column": int} for a spreadsheet cell (all from 1),
    optionally with "start" and "end" for characters inside that cell.
    """

    subject: str
    prop: Prop
    value: object
    author_id: uuid.UUID
    run_id: uuid.UUID | None = None
    span_blob: str | None = None
    span: dict | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    supersedes: uuid.UUID | None = None
    evidence: tuple[EvidenceKind, ...] = ()
    based_on: str | None = None  # subject chain head the author saw


@dataclass(frozen=True)
class GateInput:
    """Everything gate() may look at. Built by the projection from the database;
    gate() itself does no I/O."""

    prop: Prop
    author_kind: PrincipalKind
    has_span: bool
    span_valid: bool  # span exists in the source bytes
    attested: bool  # carries a verified human passkey assertion
    verdict: str | None  # latest attested human review: "accept", "reject", None
    retracted: bool
    distinct_origins: int  # counted from acquisitions, never uploads


@dataclass(frozen=True)
class PolicyRow:
    capability: str  # run.tool, "human" for human authors, or "*"
    doc_type: str  # or "*"
    sensitivity: str  # or "*"
    routes: tuple[Route, ...] = ()
    auto_accept: bool = False  # only deterministic capabilities
    min_origins: int | None = None  # set only with a calibration artifact
    calibration: dict | None = None


@dataclass(frozen=True)
class Event:
    """Run event envelope. Drives the live UI and doubles as audit. Text in
    `counts` and `drop_reason` is masked upstream for sensitive input."""

    run_id: uuid.UUID
    seq: int
    stage: str
    counts: dict = field(default_factory=dict)
    drop_reason: str | None = None
    causation_id: uuid.UUID | None = None
    correlation_id: uuid.UUID | None = None
    # Set by the server only after a passkey assertion; never from a payload,
    # never on MCP or agent paths.
    human_gesture: bool = False


class Refused(Exception):
    """The write path refused a statement; `reason` goes back to the author."""

    def __init__(self, reason: str, existing: uuid.UUID | None = None):
        super().__init__(reason)
        self.reason = reason
        self.existing = existing
