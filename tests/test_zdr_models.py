"""Every hosted model the app is configured with has a strict zero-retention endpoint.

An account set to "ZDR only" refuses endpoints that keep prompts for safety review
("Safety Retention"), so a model whose only zero-retention endpoints carry it
answers 404 to every call (Google's, from 18 October 2026). OpenRouter's list of
zero-retention endpoints is public; reading it costs nothing. Runs only where a key
is set, as the other live checks do."""

import os
import unittest

import httpx2

from sealbook.settings import Config

ZDR = "https://openrouter.ai/api/v1/endpoints/zdr"


def hosted_models() -> dict[str, str]:
    models = {
        "OCR_MODEL": Config.OCR_MODEL,
        "ASSESS_MODEL": Config.ASSESS_MODEL,
        "MODEL_CLOUD_ZDR": Config.RUN_MODELS.get("cloud-zdr"),
        "TRANSCRIBE_MODEL": Config.TRANSCRIBE_MODEL,
    }
    if Config.AGENT_ROUTE == "cloud-zdr":
        models["AGENT_MODEL"] = Config.AGENT_MODEL
    if Config.EMBEDDING_ROUTE == "cloud-zdr":
        models["EMBEDDING_MODEL"] = Config.EMBEDDING_MODEL
    return {k: v for k, v in models.items() if v}


@unittest.skipUnless(os.environ.get("OPENROUTER_API_KEY"), "no OPENROUTER_API_KEY")
class StrictZeroRetentionTest(unittest.TestCase):
    def test_each_hosted_model_has_an_endpoint_without_safety_retention(self):
        endpoints = httpx2.get(ZDR, timeout=30).raise_for_status().json()["data"]
        strict = {e["model_id"] for e in endpoints if e["safety_retention"] is False}
        missing = {k: v for k, v in hosted_models().items() if v not in strict}
        self.assertEqual(missing, {}, "no strict zero-retention endpoint")
