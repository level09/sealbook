"""The `extract_sheet` worker tool: read the rows a person released from one sheet
version and propose claims about the person each row describes. The model quotes;
the tool pins every quote to its cell and decides dates from the date table. Every
claim is an unconfirmed agent proposal that goes through normal review."""

from __future__ import annotations

import uuid

import orjson

from sealbook import blobs
from sealbook.agents import read
from sealbook.discovery import service as discovery
from sealbook.ledger import chain, write
from sealbook.ledger.contracts import (
    EVENT_KINDS,
    EvidenceKind,
    Prop,
    Refused,
    StatementIn,
)
from sealbook.ledger.models import Blob
from sealbook.monitor.data import LOCATED
from sealbook.places import resolve
from sealbook.runs import routes
from sealbook.settings import Config
from sealbook.sheets import dates, parse
from sealbook.sheets.mapping import FIELDS
from sealbook.watch import matcher

# ponytail: a 12B local model takes about 40 s per row on a laptop and two rows
# already passed the 120 s route timeout; raise this on a faster server.
ROWS_PER_CALL = 1
PROPS = (
    Prop.NAME,
    Prop.SEX,
    Prop.AGE,
    Prop.OCCUPATION,
    Prop.FATHER_NAME,
    Prop.MOTHER_NAME,
    Prop.BIRTH_DATE,
    Prop.PLACE_OF_BIRTH,
    Prop.ARRESTED_AT,
    Prop.DETAINED_AT,
    Prop.DETENTION_BRANCH,
    Prop.TRANSFERRED_TO,
    Prop.SEEN_AT,
    Prop.LAST_SEEN,
    Prop.EVENT,
)
DATED = LOCATED | {Prop.EVENT}
LABELS = {f.key: f.label for f in FIELDS}

SYSTEM = (
    "You read rows of a spreadsheet. Each row describes one person. For each row, "
    "return the facts its cells state about that person, and nothing the cells do "
    "not state. For each fact give the property, the column number of the cell it "
    "comes from, and the text copied exactly from that cell (part of the cell, never "
    "rewritten, translated or completed), with its language code. "
    "name is the person's name as written; leave out sex, age and job words around "
    "it. sex needs norm male or female. age needs norm as whole years in Western "
    "digits (عشرين is 20, خمسين is 50), quote the age words. occupation is the job, "
    "trade, rank or study. "
    "arrested_at, detained_at, detention_branch, transferred_to, seen_at and "
    "last_seen quote the place or facility: the most specific place stated, from the "
    "text or from the place column, and only when the row says the person was "
    "arrested, held, moved or seen there; seen_at is not a default for the place "
    "column. event is a life event with kind killed, missing, "
    "displaced, released, wounded or defected; quote the words that say it. "
    "Only those six kinds are events; an arrest or detention is a position fact, not "
    "an event. For every position and event fact, date is the date copied exactly as "
    "written in the row, usually the whole date column cell, or null when the row has "
    "none. norm, kind and date are null where they do not apply. " + read.DATA_ONLY
)

SCHEMA = {
    "name": "sheet_rows",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["rows"],
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["row", "facts"],
                    "properties": {
                        "row": {"type": "integer"},
                        "facts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "prop",
                                    "column",
                                    "quote",
                                    "lang",
                                    "norm",
                                    "kind",
                                    "date",
                                ],
                                "properties": {
                                    "prop": {
                                        "type": "string",
                                        "enum": [str(p) for p in PROPS],
                                    },
                                    "column": {"type": "integer"},
                                    "quote": {"type": "string"},
                                    "lang": {"type": "string"},
                                    "norm": {"type": ["string", "null"]},
                                    "kind": {
                                        "type": ["string", "null"],
                                        "enum": [*EVENT_KINDS, None],
                                    },
                                    "date": {"type": ["string", "null"]},
                                },
                            },
                        },
                    },
                },
            }
        },
    },
}


def prompt_rows(sheet: parse.Sheet, rows: list[int], mapping: dict) -> list[dict]:
    """The cells the model sees: non-empty, not the key, not marked `ignore`."""
    header = sheet.head
    out = []
    for n in rows:
        cells = [
            {
                "column": i + 1,
                "header": h,
                "field": LABELS[mapping[h]],
                "text": text,
            }
            for i, (h, text) in enumerate(zip(header, sheet.rows[n - 1], strict=True))
            if text.strip() and mapping.get(h, "ignore") not in ("ignore", "key")
        ]
        out.append({"row": n, "cells": cells})
    return out


def pin(
    sheet: parse.Sheet, sheet_no: int, n: int, column: int, quote: str
) -> dict | None:
    """The span of `quote` in its cell, or in another cell of the same row when the
    model named the wrong column. None when the row does not contain it."""
    row = sheet.rows[n - 1]
    order = [column - 1] if 1 <= column <= len(row) else []
    order += [i for i in range(len(row)) if i != column - 1]
    for i in order:
        start = row[i].find(quote) if quote else -1
        if start >= 0:
            span = {"sheet": sheet_no, "row": n, "column": i + 1}
            if (start, len(quote)) != (0, len(row[i])):
                span |= {"start": start, "end": start + len(quote)}
            return span
    return None


def value_of(prop: Prop, fact: dict) -> dict | None:
    """The statement value, or None when a required normalisation is missing."""
    value = {"text": fact["quote"], "lang": fact["lang"]}
    norm = (fact.get("norm") or "").strip().lower()
    if prop is Prop.SEX:
        if norm not in ("male", "female"):
            return None
        value["norm"] = norm
    elif prop is Prop.AGE:
        digits = norm.translate(dates._DIGITS)
        if digits.isdigit() and 0 < int(digits) < 120:
            value["norm"] = int(digits)
    elif prop is Prop.EVENT:
        if fact.get("kind") not in EVENT_KINDS:
            return None
        value["kind"] = fact["kind"]
    return value


async def extract_sheet(c) -> str | None:
    blob = await c.s.get(Blob, c.run.inputs["blobs"][0])
    sheet_no = c.run.inputs["sheet"]
    sheet = parse.read(blobs.read(blob, Config.BLOB_DIR), blob.mime)[sheet_no - 1]
    mapping, rows = c.run.inputs["mapping"], c.run.inputs["rows"]
    await c.emit("input_read", rows=len(rows), columns=len(sheet.head))
    c.run.prompt_hash = chain.sha256(chain.canon([SYSTEM, SCHEMA]))
    raws, counts = [], {"proposed": 0, "written": 0, "dropped": 0, "undated": 0}
    subjects, names, places = {}, [], []
    for i in range(0, len(rows), ROWS_PER_CALL):
        part = rows[i : i + ROWS_PER_CALL]
        served = await c.call(
            {
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {
                        "role": "user",
                        "content": orjson.dumps(
                            prompt_rows(sheet, part, mapping)
                        ).decode(),
                    },
                ],
                "response_format": {"type": "json_schema", "json_schema": SCHEMA},
                "temperature": 0,
                # Local thinking models spend the whole context reasoning and answer
                # nothing; the answer here is a quote table, not a deliberation.
                **({"reasoning_effort": "none"} if c.run.route == "local" else {}),
            }
        )
        raws.append(c.run.raw_output_ref)
        for out in orjson.loads(routes.content(served))["rows"]:
            n = out["row"]
            if n not in part:
                counts["dropped"] += len(out["facts"])
                await c.emit("dropped", drop_reason="row_not_requested")
                continue
            for fact in out["facts"]:
                counts["proposed"] += 1
                st = await _write(c, blob, sheet, sheet_no, n, fact, subjects, counts)
                if st is not None and st.prop == Prop.NAME:
                    names.append(st.id)
                elif st is not None and st.prop in LOCATED:
                    places.append(st.id)
    located = await resolve.propose(
        c.s, c.run.id, c.run.principal_id, c.sign, only=places
    )
    await c.emit(
        "statements_written", subjects=len(subjects), located=located, **counts
    )
    for subject in set(subjects.values()):
        await discovery.queue_actor(c.s, subject, c.acq)
    if names:
        agent = await matcher.matcher_agent(c.s)
        c.s.add(
            matcher.match_run(agent, c.run.inputs["acquisition"], blob.sha256, names)
        )
    manifest = await blobs.put(
        c.s, orjson.dumps({"raw": raws}), "application/json", Config.BLOB_DIR
    )
    c.run.raw_output_ref = manifest.sha256
    return manifest.sha256


async def _write(c, blob, sheet, sheet_no, n, fact, subjects, counts):
    """Write one fact as a proposal; the statement when it is pinned to a cell."""
    prop = Prop(fact["prop"])
    value = value_of(prop, fact)
    if value is None:
        counts["dropped"] += 1
        await c.emit("dropped", drop_reason=f"{prop}_not_normalised")
        return None
    span = pin(sheet, sheet_no, n, fact["column"], fact["quote"])
    valid_from = valid_to = None
    if prop in DATED and (said := (fact.get("date") or "").strip()):
        # A date the row does not contain is the model's, not the source's.
        if any(said in cell for cell in sheet.rows[n - 1]):
            value["date_text"] = said
            found = dates.interval(said)
            if found:
                valid_from, valid_to = found
            elif not dates.unknown(said):
                counts["undated"] += 1
    subject = subjects.setdefault(n, f"person:{uuid.uuid4()}")
    try:
        st = await write.append(
            c.s,
            StatementIn(
                subject=subject,
                prop=prop,
                value=value,
                author_id=c.run.principal_id,
                run_id=c.run.id,
                span_blob=blob.sha256,
                # Without a span the gate rejects it mechanically; the row stays as audit.
                span=span,
                valid_from=valid_from,
                valid_to=valid_to,
                evidence=(EvidenceKind.PRINTED_TEXT,),
            ),
            sign=c.sign,
        )
    except Refused as exc:
        counts["dropped"] += 1
        await c.emit("dropped", drop_reason=f"refused: {exc.reason}")
        return None
    counts["written"] += 1
    if span is None:
        counts["dropped"] += 1
        await c.emit("dropped", drop_reason="quote_not_in_row")
        return None
    return st
