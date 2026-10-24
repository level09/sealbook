"""The monitor's shared filter: {"investigation": str | null, "time": [from, to] | null},
kept in the URL as JSON. Parsing and SQL compilation live here only, so the filter
AST compiler (stk/search/filter.py) can replace this module whole.

Every panel row hangs off a blob (acquisitions, run inputs, statement spans), so the
filter compiles to one predicate over a blob sha256. A statement with its own valid
time is placed by it; one without inherits its document's dated statements."""

from __future__ import annotations

from datetime import date

import orjson
from sqlalchemy import and_, exists, func, or_, select, true
from sqlalchemy.orm import aliased

from stk.ledger.contracts import Prop, Tier
from stk.ledger.models import Acquisition, Payload, Statement, StatementTier

EMPTY = {"investigation": None, "time": None}


def parse(raw: str | None) -> dict:
    """Validate the URL filter. Raises ValueError with a reason for the caller."""
    if not raw:
        return dict(EMPTY)
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise ValueError("filter is not JSON") from exc
    if not isinstance(data, dict) or set(data) - set(EMPTY):
        raise ValueError("filter takes only investigation and time")
    investigation, window = data.get("investigation"), data.get("time")
    if investigation is not None and not isinstance(investigation, str):
        raise ValueError("investigation must be a string")
    if window is not None:
        if not isinstance(window, list) or len(window) != 2:
            raise ValueError("time must be [from, to]")
        try:
            window = [date.fromisoformat(d) for d in window]
        except (TypeError, ValueError) as exc:
            raise ValueError("time bounds must be ISO dates") from exc
        if window[0] > window[1]:
            raise ValueError("time window ends before it starts")
    return {"investigation": investigation, "time": window}


def dump(f: dict) -> dict:
    return {
        "investigation": f["investigation"],
        "time": [d.isoformat() for d in f["time"]] if f["time"] else None,
    }


def _overlaps(st, window):
    lo, hi = window
    return and_(
        st.valid_from.is_not(None),
        st.valid_from <= hi,
        func.coalesce(st.valid_to, st.valid_from) >= lo,
    )


def source(sha):
    """The acquired blob behind `sha`: the scan an OCR text was derived from, else
    `sha` itself. Derived blobs have no acquisition, their source does."""
    # ponytail: correlated lookup per row; store the source on the blob row if
    # panels slow down.
    derived = aliased(Statement)
    found = (
        select(Payload.value["blob"].astext)
        .select_from(derived)
        .join(Payload, Payload.commitment == derived.commitment)
        .where(
            derived.subject == func.concat("blob:", sha),
            derived.prop == Prop.DERIVED_FROM,
        )
        .limit(1)
        .correlate_except(derived, Payload)
        .scalar_subquery()
    )
    return func.coalesce(found, sha)


def statement_blob(st=Statement):
    """The blob a statement hangs off: its span, or the blob it is about."""
    return func.coalesce(
        st.span_blob,
        func.substring(st.subject, r"^blob:([0-9a-f]{64})$"),
    )


def blob_clause(f: dict, sha) -> object:
    """SQL predicate: the blob `sha` (a column or expression) matches the filter."""
    conds = []
    sha = source(sha)
    if f["investigation"]:
        conds.append(
            exists().where(
                Acquisition.blob_sha256 == sha,
                Acquisition.context["investigation"].astext == f["investigation"],
            )
        )
    if f["time"]:
        dated = aliased(Statement)
        conds.append(
            exists()
            .where(source(statement_blob(dated)) == sha, _overlaps(dated, f["time"]))
            .where(
                ~exists().where(
                    StatementTier.statement_id == dated.id,
                    StatementTier.tier == Tier.REJECTED,
                )
            )
        )
    return and_(true(), *conds)


def statement_clause(f: dict, st=Statement) -> object:
    blob = statement_blob(st)
    clause = blob_clause({"investigation": f["investigation"], "time": None}, blob)
    if f["time"]:
        clause = and_(
            clause,
            or_(
                _overlaps(st, f["time"]),
                and_(
                    st.valid_from.is_(None),
                    blob_clause({"investigation": None, "time": f["time"]}, blob),
                ),
            ),
        )
    return clause


def run_clause(f: dict, inputs) -> object:
    """A run matches when any of its input blobs (run.inputs["blobs"]) does."""
    if f == EMPTY:
        return true()
    blobs = func.jsonb_array_elements_text(inputs["blobs"]).table_valued("value")
    return exists().select_from(blobs).where(blob_clause(f, blobs.c.value))
