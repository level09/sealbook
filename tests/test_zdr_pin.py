import os
import unittest
from unittest import mock

import httpx2
import orjson

from sealbook.discovery import embeddings
from sealbook.ledger.contracts import Route
from sealbook.runs import routes
from sealbook.settings import Config


def served_by(provider, seen):
    def handle(request):
        seen.append(orjson.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "model": "m",
                "provider": provider,
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "data": [{"embedding": [1.0] + [0.0] * 767}],
            },
        )

    return httpx2.MockTransport(handle)


class ZdrPinTest(unittest.IsolatedAsyncioTestCase):
    """ROUTE_CLOUD_ZDR_PROVIDERS narrows cloud-zdr to named providers, checked twice."""

    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}))

    def pin(self, *slugs):
        self.enterContext(
            mock.patch.object(Config, "ROUTE_CLOUD_ZDR_PROVIDERS", list(slugs))
        )

    async def chat(self, provider, route=Route.CLOUD_ZDR):
        seen = []
        served = await routes.chat(route, {"model": "m"}, served_by(provider, seen))
        return served, seen[0]

    async def test_unpinned_sends_zdr_only(self):
        served, body = await self.chat("Anyone")
        self.assertEqual(body["provider"], {"zdr": True})
        self.assertEqual(served.provider, "Anyone")

    async def test_pinned_display_name_matches_slug(self):
        self.pin("deepinfra/turbo", "google-ai-studio")
        for name in ("DeepInfra", "Google AI Studio"):
            served, body = await self.chat(name)
            self.assertEqual(
                body["provider"],
                {"zdr": True, "only": ["deepinfra/turbo", "google-ai-studio"]},
            )
            self.assertEqual(served.provider, name)

    async def test_other_provider_fails_the_call(self):
        self.pin("deepinfra")
        with self.assertRaisesRegex(routes.RouteError, "served by Together"):
            await self.chat("Together")
        # A display name that only shares a prefix with the slug does not pass.
        self.pin("google-vertex")
        with self.assertRaisesRegex(routes.RouteError, "not a pinned provider"):
            await self.chat("Google")

    async def test_pin_leaves_the_plain_cloud_route_alone(self):
        self.pin("deepinfra")
        served, body = await self.chat("Together", route=Route.CLOUD)
        self.assertNotIn("provider", body)

    async def test_embeddings_send_and_check_the_pin(self):
        self.pin("google-vertex")
        self.enterContext(mock.patch.object(Config, "EMBEDDING_ROUTE", "cloud-zdr"))
        seen = []
        await embeddings._post(["w"], [], "m", served_by("Google Vertex", seen))
        self.assertEqual(seen[0]["provider"], {"zdr": True, "only": ["google-vertex"]})
        for provider in ("Together", None):
            with self.assertRaises(routes.RouteError):
                await embeddings._post(["w"], [], "m", served_by(provider, []))
