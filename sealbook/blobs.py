"""Content-addressed blob bytes on local disk: `<BLOB_DIR>/<sha[:2]>/<sha>`.
The only place that writes or reads evidence bytes.

# ponytail: plain files, no encryption at rest; per-blob keys for crypto-shredding
# arrive with erasure.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from sealbook.ledger.models import Blob

CHUNK = 1 << 16


async def chunked(data: bytes) -> AsyncIterator[bytes]:
    for i in range(0, len(data), CHUNK):
        yield data[i : i + CHUNK]


async def store(chunks: AsyncIterator[bytes], blob_dir: str) -> tuple[str, str, int]:
    """Stream chunks to `<blob_dir>/<sha[:2]>/<sha>`, hashing as they pass.
    The temp file lives in blob_dir so the final rename is atomic."""
    root = Path(blob_dir)
    root.mkdir(parents=True, exist_ok=True)
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in chunks:
                sha.update(chunk)
                md5.update(chunk)
                size += len(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        dest = blob_path(blob_dir, sha.hexdigest())
        dest.parent.mkdir(exist_ok=True)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return sha.hexdigest(), md5.hexdigest(), size


def blob_path(blob_dir: str, sha256: str) -> Path:
    return Path(blob_dir) / storage_ref(sha256)


def storage_ref(sha256: str) -> str:
    """Relative to BLOB_DIR, so the store can move without rewriting rows."""
    return f"{sha256[:2]}/{sha256}"


async def put(session: AsyncSession, data: bytes, mime: str, blob_dir: str) -> Blob:
    """Store bytes produced inside the system (raw model output, OCR text)."""
    sha, md5, size = await store(chunked(data), blob_dir)
    found = await session.get(Blob, sha)
    if found:
        return found
    blob = Blob(sha256=sha, md5=md5, size=size, mime=mime, storage_ref=storage_ref(sha))
    session.add(blob)
    await session.flush()
    return blob


class MissingBytes(LookupError):
    """A blob row whose bytes are not in the store: a synthetic filler record
    (storage_ref "synthetic") or a file that is gone. Answered as 404 no_file."""


SYNTHETIC = "synthetic"


def stored(blob: Blob, blob_dir: str) -> bool:
    return (
        blob.storage_ref != SYNTHETIC and (Path(blob_dir) / blob.storage_ref).is_file()
    )


def _path(blob: Blob, blob_dir: str) -> Path:
    if not stored(blob, blob_dir):
        raise MissingBytes(f"no file is stored for {blob.sha256}")
    return Path(blob_dir) / blob.storage_ref


def read(blob: Blob, blob_dir: str) -> bytes:
    data = _path(blob, blob_dir).read_bytes()
    if hashlib.sha256(data).hexdigest() != blob.sha256:
        raise RuntimeError(f"blob {blob.sha256} bytes do not match their hash")
    return data


def verified_path(blob: Blob, blob_dir: str) -> Path:
    """Verify bytes without loading a video into memory before decoding or playback."""
    path = _path(blob, blob_dir)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != blob.sha256:
        raise RuntimeError(f"blob {blob.sha256} bytes do not match their hash")
    return path
