"""What stk.ledger.span.validate needs to know about a blob, read from its bytes.
Content addressed, so a sha's meta never changes and is cached for the process."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from stk import blobs
from stk.ledger.models import Blob
from stk.ledger.span import validate
from stk.settings import Config
from stk.sheets import parse as sheets


@lru_cache(maxsize=4096)
def _meta(sha256: str, mime: str, storage_ref: str, blob_dir: str) -> dict:
    data = blobs.read(Blob(sha256=sha256, storage_ref=storage_ref), blob_dir)
    meta = {"pages": None, "text_len": None, "duration_ms": None, "sheets": None}
    if mime in sheets.MIMES:
        meta["sheets"] = sheets.lengths(data, mime)
    if mime.startswith("text/"):
        meta["text_len"] = len(data.decode())
    elif mime.startswith("image/"):
        with Image.open(BytesIO(data)) as im:
            pages = []
            for i in range(getattr(im, "n_frames", 1)):
                im.seek(i)
                pages.append(im.size)
            meta["pages"] = pages
    # ponytail: no PDF page sizes or audio durations (no installed lib reads them);
    # spans on those blobs fail as *_required until intake records them.
    return meta


def span_check(blob: Blob | None, span: dict | None) -> tuple[bool, str]:
    """validate() against the blob's real bytes. Unreadable or tampered bytes mean
    the span cannot be shown to exist in the source."""
    if blob is None or span is None:
        return False, "no span"
    try:
        meta = _meta(blob.sha256, blob.mime, blob.storage_ref, Config.BLOB_DIR)
    # ValueError covers undecodable text and unreadable sheets.
    except (OSError, RuntimeError, UnidentifiedImageError, ValueError) as exc:
        return False, f"source bytes unreadable: {exc}"
    return validate(span, meta)
