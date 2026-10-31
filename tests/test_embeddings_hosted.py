import os
import unittest
from unittest import mock

import httpx2
import orjson

from sealbook.discovery import embeddings
from sealbook.settings import Config


class HostedEmbeddingTest(unittest.IsolatedAsyncioTestCase):
    """The default route: one item per OpenRouter call, zero retention, in order."""

    async def test_each_item_is_its_own_zero_retention_call(self):
        seen = []

        def handle(request):
            body = orjson.loads(request.content)
            seen.append(body)
            (item,) = body["input"]
            index = len(seen) - 1
            vector = [0.0] * 768
            vector[index] = 1.0
            return httpx2.Response(
                200,
                json={"model": "gemini-embedding-2", "data": [{"embedding": vector}]},
            )

        self.enterContext(mock.patch.object(Config, "EMBEDDING_ROUTE", "cloud-zdr"))
        self.enterContext(mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}))
        self.enterContext(mock.patch.object(embeddings, "PARALLEL", 1))
        values, refs, served, endpoint, raw = await embeddings._post(
            ["words"],
            embeddings.probes(),
            "google/gemini-embedding-2",
            httpx2.MockTransport(handle),
        )
        self.assertEqual(len(seen), 7)
        self.assertTrue(all(b["provider"] == {"zdr": True} for b in seen))
        self.assertEqual(seen[0]["input"], ["words"])
        image = seen[3]["input"][0]["content"][0]
        self.assertTrue(image["image_url"]["url"].startswith("data:image/png;base64,"))
        audio = seen[5]["input"][0]["content"][0]
        self.assertTrue(
            audio["input_audio"]["data"].startswith("data:audio/wav;base64,")
        )
        self.assertEqual(values[0][0], 1.0)
        self.assertEqual(len(refs), 6)
        self.assertEqual(served, "gemini-embedding-2")
        self.assertTrue(endpoint.endswith("/embeddings"))
        self.assertEqual(len(orjson.loads(raw.content)), 7)
