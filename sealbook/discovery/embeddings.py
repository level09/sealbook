"""One embedding space is versioned and checked against non-sensitive inputs."""

import asyncio
import base64
import io
import math
import wave

import httpx
import numpy as np
from PIL import Image
from sqlalchemy import select, text

from sealbook.discovery.compute import vector
from sealbook.discovery.models import DIMENSIONS, EmbeddingSpace
from sealbook.ledger import chain
from sealbook.runs import routes
from sealbook.settings import Config

TEXT_PROBES = ["A blue door beside a stone building.", "باب أزرق بجانب مبنى حجري."]
# EmbeddingGemma input formats from its model card.
QUERY_PREFIX = "task: search result | query: "
DOCUMENT_PREFIX = "title: none | text: "


def content(kind, data):
    # Ollama /api/embed media item: {"image": base64} or {"audio": base64}.
    return {kind: base64.b64encode(data).decode()}


def probes():
    values = [QUERY_PREFIX + q for q in TEXT_PROBES]
    for color in ("red", "blue"):
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), color).save(buffer, "PNG")
        values.append(content("image", buffer.getvalue()))
    for hz in (440, 880):
        samples = (
            np.sin(np.arange(32000) * (2 * math.pi * hz / 16000)) * 12000
        ).astype("<i2")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(samples.tobytes())
        values.append(content("audio", buffer.getvalue()))
    return values


def recipe():
    return {
        "dimensions": DIMENSIONS,
        "model": Config.EMBEDDING_MODEL,
        "revision": Config.EMBEDDING_REVISION,
        "endpoint": Config.EMBEDDING_URL
        if Config.EMBEDDING_ROUTE == "local"
        else "openrouter",
        "preprocessing": "gemma2-v1;chunks1800;title-none-document;png1024;mono16k;audio30-hop25;frame3s",
    }


def space_key(model=None):
    settings = recipe()
    if model:
        settings["model"] = model
    return chain.sha256(chain.canon(settings))


def active_spaces(model=None):
    return select(EmbeddingSpace.id).where(
        EmbeddingSpace.active.is_(True), EmbeddingSpace.key == space_key(model)
    )


async def bind_run(c, space):
    reference = await c.s.get(EmbeddingSpace, space)
    c.run.inputs = {
        **c.run.inputs,
        "embedding_space": str(space),
        "embedding_recipe": reference.recipe,
        "embedding_reference_hash": chain.sha256(
            chain.canon([[float(v).hex() for v in row] for row in reference.reference])
        ),
    }


# Gemini Embedding 2 on OpenRouter takes OpenAI-style input: a string, or a
# content part with a data URL (checked 2026-10-25).
PARALLEL = 8


def _part(item):
    if isinstance(item, str):
        return item
    ((kind, data),) = item.items()
    if kind == "image":
        mime = (
            "image/png"
            if base64.b64decode(data[:12]).startswith(b"\x89PNG")
            else "image/jpeg"
        )
        return {
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}"},
                }
            ]
        }
    return {
        "content": [
            {
                "type": "input_audio",
                "input_audio": {
                    "data": f"data:audio/wav;base64,{data}",
                    "format": "wav",
                },
            }
        ]
    }


async def _post_hosted(inputs, references, model, transport):
    """One item per call: a batch is routed to endpoints without zero retention."""
    endpoint = f"{routes.OPENROUTER}/embeddings"
    key = routes._env("OPENROUTER_API_KEY")
    gate = asyncio.Semaphore(PARALLEL)
    async with httpx.AsyncClient(transport=transport, timeout=routes.TIMEOUT) as client:

        async def one(item):
            async with gate:
                response = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "input": [_part(item)],
                        "dimensions": DIMENSIONS,
                        "provider": routes.zdr_provider(),
                    },
                )
                response.raise_for_status()
                return response

        responses = await asyncio.gather(*(one(x) for x in inputs + references))
    bodies = [r.json() for r in responses]
    for body in bodies:
        routes.check_pinned(body.get("provider"))
    served = {b.get("model") for b in bodies}
    if len(served) != 1 or None in served:
        raise ValueError("embedding responses name no single served model")
    values = [vector(b["data"][0]["embedding"], DIMENSIONS) for b in bodies]
    raw = httpx.Response(
        200, content=b"[" + b",".join(r.content for r in responses) + b"]"
    )
    return values[: len(inputs)], values[len(inputs) :], served.pop(), endpoint, raw


async def _post(inputs, references, model, transport):
    if Config.EMBEDDING_ROUTE == "cloud-zdr":
        return await _post_hosted(inputs, references, model, transport)
    if not Config.EMBEDDING_URL:
        raise ValueError("EMBEDDING_URL is not configured")
    endpoint = f"{Config.EMBEDDING_URL.rstrip('/')}/api/embed"
    async with httpx.AsyncClient(transport=transport, timeout=routes.TIMEOUT) as client:
        response = await client.post(
            endpoint,
            json={
                "model": model,
                "input": inputs + references,
                "dimensions": DIMENSIONS,
            },
        )
        response.raise_for_status()
    body = response.json()
    served = body.get("model")
    if not served:
        raise ValueError("embedding response names no served model")
    rows = body["embeddings"]
    if len(rows) != len(inputs) + len(references):
        raise ValueError(
            "embedding response does not match inputs and reference probes"
        )
    values = [vector(row, DIMENSIONS) for row in rows]
    return values[: len(inputs)], values[len(inputs) :], served, endpoint, response


async def query(db, words, model, transport=None):
    """Embed one search query against the active space.

    Image and audio probes cost about 2.5 s per call and test inputs a text query
    never sends, so a query re-checks only the text probes for drift. A missing
    space falls back to the full call, which creates it."""
    space = await db.scalar(
        select(EmbeddingSpace).where(
            EmbeddingSpace.key == space_key(model), EmbeddingSpace.active.is_(True)
        )
    )
    if space is None:
        values, *_, created = await call(db, [QUERY_PREFIX + words], model, transport)
        return values[0], created
    texts = probes()[: len(TEXT_PROBES)]
    values, measured, served, _, _ = await _post(
        [QUERY_PREFIX + words], texts, model, transport
    )
    if served != space.model_served or any(
        sum(a * b for a, b in zip(before, after, strict=True)) < 0.999
        for before, after in zip(
            space.reference[: len(measured)], measured, strict=True
        )
    ):
        raise ValueError(
            "embedding reference drift detected; inspect the model and explicitly rebuild"
        )
    return values[0], space.id


async def call(db, inputs, model, transport=None, *, rebuild=False):
    settings = recipe()
    settings["model"] = model
    values, measured, served, endpoint, response = await _post(
        inputs, probes(), model, transport
    )
    if any(
        np.allclose(measured[a], measured[b], atol=1e-7, rtol=0)
        for a, b in ((2, 3), (4, 5))
    ):
        raise ValueError("encoder ignored distinct image or audio reference inputs")
    key_hash = chain.sha256(chain.canon(settings))
    await db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key_hash},
    )
    old = await db.scalar(
        select(EmbeddingSpace).where(
            EmbeddingSpace.key == key_hash, EmbeddingSpace.active.is_(True)
        )
    )
    drift = old is not None and (
        old.model_served != served
        or len(old.reference) != len(measured)
        or any(
            sum(a * b for a, b in zip(before, after, strict=True)) < 0.999
            for before, after in zip(old.reference, measured, strict=True)
        )
    )
    if drift and not rebuild:
        raise ValueError(
            "embedding reference drift detected; inspect the model and explicitly rebuild"
        )
    if drift:
        old.active = False
        await db.flush()
    if old is None or drift:
        old = EmbeddingSpace(
            key=key_hash,
            active=True,
            model_requested=model,
            model_served=served,
            dimensions=settings["dimensions"],
            recipe=settings,
            reference=measured,
        )
        db.add(old)
        await db.flush()
    return values, served, endpoint, response.content, old.id
