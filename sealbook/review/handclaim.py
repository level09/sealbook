"""A claim a person reads themselves: they select the words in a source's text, say
whom or what they are about and which field they give, and sign. The claim is the
person's own statement, pinned to the characters they selected, so it is checked and
shown like any read claim; no model is involved."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select

from sealbook import blobs
from sealbook.discovery import evidence
from sealbook.discovery.models import Segment
from sealbook.ledger.contracts import EXTRACTED_PROPS, Prop, StatementIn
from sealbook.ledger.models import Blob, Statement
from sealbook.settings import Config

# What a person may read off the text by hand: every field the reader proposes, but
# a reported event, which needs its kind and place words as well.
PROPS = frozenset(EXTRACTED_PROPS - {Prop.REPORTS})
DOCUMENT = frozenset({Prop.DOCUMENT_TYPE, Prop.DOCUMENT_DATE})
LONGEST = 500
ARABIC = re.compile(r"[؀-ۿ]")
VALID = re.compile(r"^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$")


async def text_of(db, sha: str, text_blob: str) -> str:
    """The text the page shows for `sha` under `text_blob`: the source's own text,
    or text read from it (a page's text layer, a picture's text, a transcript)."""
    if text_blob == sha:
        blob = await db.get(Blob, sha)
        if not blob.mime.startswith("text/"):
            raise ValueError(
                "this source has no text of its own; select in its read text"
            )
        return blobs.read(blob, Config.BLOB_DIR).decode()
    read = await db.scalar(
        select(Segment.text_sha256).where(
            Segment.source_sha256 == sha,
            Segment.text_sha256 == text_blob,
            Segment.kind != "description",
        )
    )
    if read is None:
        raise ValueError("those words are not from this source's text")
    return blobs.read(await db.get(Blob, text_blob), Config.BLOB_DIR).decode()


async def plan(db, user, item: dict, author) -> tuple[str, list[StatementIn]]:
    sha, text_blob = item.get("sha"), item.get("blob")
    if not all(isinstance(x, str) and len(x) == 64 for x in (sha, text_blob)):
        raise ValueError("send the source and the text the words are in")
    if await evidence.source_blob(db, sha) is None:
        raise LookupError("this source is not available")
    if not await evidence.viewable(db, sha, user):
        raise PermissionError("reveal this source before reading claims from it")
    try:
        prop = Prop(item.get("prop"))
        start, end = int(item["start"]), int(item["end"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("choose a field and select the words") from None
    if prop not in PROPS:
        raise ValueError("that field is not read from text")
    text = await text_of(db, sha, text_blob)
    quote = text[start:end] if 0 <= start < end <= len(text) else ""
    if not quote.strip() or quote != item.get("quote") or len(quote) > LONGEST:
        raise ValueError(
            f"select 1 to {LONGEST} characters of the text, then try again"
        )
    if prop in DOCUMENT:
        subject = f"blob:{text_blob}"
    elif item.get("person") == "new":
        subject = f"person:{uuid.uuid4()}"
    else:
        subject = item.get("person")
        family = await evidence.family(db, sha)
        known = isinstance(subject, str) and await db.scalar(
            select(Statement.id)
            .where(Statement.subject == subject, Statement.span_blob.in_(family))
            .limit(1)
        )
        if not known:
            raise ValueError("choose a person this source names, or someone new")
    valid = item.get("valid") or None
    if valid is not None and not (isinstance(valid, str) and VALID.match(valid)):
        raise ValueError("write the date as 2013, 2013-09 or 2013-09-17")
    from sealbook.runs.worker import valid_range

    try:
        valid_from, valid_to = valid_range(valid)
    except ValueError:
        raise ValueError("that date does not exist") from None
    statement = StatementIn(
        subject=subject,
        prop=prop,
        value={"text": quote, "lang": "ar" if ARABIC.search(quote) else "und"},
        author_id=author,
        span_blob=text_blob,
        span={"start": start, "end": end},
        valid_from=valid_from,
        valid_to=valid_to,
    )
    return str(uuid.uuid4()), [statement]
