import os
import unittest
from unittest import mock

import httpx2
import orjson

from sealbook.discovery import embeddings
from sealbook.settings import Config


def served_by(model, seen):
    def handle(request):
        body = orjson.loads(request.content)
        seen.append(body)
        vector = [0.0] * 768
        vector[len(seen) - 1] = 1.0
        return httpx2.Response(
            200, json={"model": model, "data": [{"embedding": vector}]}
        )

    return httpx2.MockTransport(handle)


class HostedEmbeddingTest(unittest.IsolatedAsyncioTestCase):
    """The default route: one item per OpenRouter call, zero retention, in order."""

    def setUp(self):
        self.enterContext(mock.patch.object(Config, "EMBEDDING_ROUTE", "cloud-zdr"))
        self.enterContext(mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}))
        self.enterContext(mock.patch.object(embeddings, "PARALLEL", 1))

    async def test_each_item_is_its_own_zero_retention_call(self):
        seen = []
        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_MEDIA", ("audio", "image"))
        )
        values, refs, served, endpoint, raw = await embeddings._post(
            ["words"],
            embeddings.probes(),
            "example/multimodal-embedding",
            served_by("multimodal-embedding", seen),
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
        self.assertEqual(served, "multimodal-embedding")
        self.assertTrue(endpoint.endswith("/embeddings"))
        self.assertEqual(len(orjson.loads(raw.content)), 7)

    async def test_a_text_model_gets_text_probes_and_no_media(self):
        """The default model reads text only: no image or audio leaves, the recipe
        says so, and a photo, PDF, video or recording is not embedded itself."""
        self.assertEqual(Config.EMBEDDING_MODEL, "openai/text-embedding-3-large")
        self.assertEqual(Config.EMBEDDING_MEDIA, ())
        seen = []
        *_, served, _, _ = await embeddings._post(
            ["words"],
            embeddings.probes(),
            Config.EMBEDDING_MODEL,
            served_by("text-embedding-3-large", seen),
        )
        self.assertEqual(len(seen), 1 + len(embeddings.TEXT_PROBES))
        self.assertTrue(all(isinstance(b["input"][0], str) for b in seen))
        self.assertEqual(
            embeddings.recipe()["preprocessing"],
            "gemma2-v1;chunks1800;title-none-document",
        )
        for mime in ("image/jpeg", "application/pdf", "video/mp4", "audio/ogg"):
            self.assertFalse(embeddings.embeds(mime), mime)

    def test_a_model_reading_media_keeps_its_space(self):
        """EmbeddingGemma 2 on a private server reads image and audio; its recipe,
        and so its space key, is the one it had before EMBEDDING_MEDIA existed."""
        self.enterContext(
            mock.patch.object(Config, "EMBEDDING_MEDIA", ("audio", "image"))
        )
        self.assertEqual(
            embeddings.recipe()["preprocessing"],
            "gemma2-v1;chunks1800;title-none-document;png1024;mono16k;audio30-hop25;frame3s",
        )
        self.assertTrue(embeddings.embeds("video/mp4"))
        self.assertEqual(len(embeddings.probes()), 6)
