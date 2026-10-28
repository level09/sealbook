import unittest
from unittest import mock

import httpx
import orjson

from sealbook.agents import gaps, read
from sealbook.ledger.contracts import Route
from sealbook.runs import routes

BODY = {
    "model": "claude-opus-5-5",
    "messages": [
        {"role": "system", "content": "Answer as JSON."},
        {"role": "user", "content": '{"claims": [{"h": "c1"}]}'},
    ],
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "gaps",
            "strict": True,
            "schema": read.cite_only(gaps.SCHEMA, ["c1"]),
        },
    },
    "temperature": 0,
    "reasoning_effort": "none",
}


def reply(text='{"items": []}', stop="end_turn"):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "s"},
            {"type": "text", "text": text},
        ],
        "stop_reason": stop,
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


def keys(schema):
    if isinstance(schema, dict):
        return set(schema) | {k for v in schema.values() for k in keys(v)}
    if isinstance(schema, list):
        return {k for v in schema for k in keys(v)}
    return set()


class AnthropicRouteTest(unittest.IsolatedAsyncioTestCase):
    """The native Messages API behind the OpenAI-shaped body agents send."""

    def setUp(self):
        self.enterContext(mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}))
        self.sent = []

    def transport(self, data):
        def handle(request):
            self.sent.append(request)
            return httpx.Response(200, json=data)

        return httpx.MockTransport(handle)

    async def test_request_and_response_translate(self):
        served = await routes.chat(Route.ANTHROPIC, BODY, self.transport(reply()))
        (request,) = self.sent
        self.assertEqual(str(request.url), "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.headers["x-api-key"], "k")
        self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
        body = orjson.loads(request.content)
        self.assertEqual(body["system"], "Answer as JSON.")
        self.assertEqual([m["role"] for m in body["messages"]], ["user"])
        self.assertNotIn("temperature", body)
        self.assertEqual(body["max_tokens"], routes.ANTHROPIC_MAX_TOKENS)
        self.assertEqual(body["output_config"]["effort"], "low")
        fmt = body["output_config"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        schema = fmt["schema"]
        self.assertFalse(keys(schema) & routes.UNSUPPORTED)
        self.assertEqual(
            schema["properties"]["items"]["items"]["properties"]["claims"],
            {"type": "array", "items": {"enum": ["c1"]}},
        )
        self.assertIn("maxLength", keys(gaps.SCHEMA))  # the caller's copy is kept

        self.assertEqual(routes.content(served), '{"items": []}')
        self.assertEqual(
            (served.model, served.provider), ("claude-opus-5-5", "Anthropic")
        )
        self.assertEqual(
            served.data["usage"], {"prompt_tokens": 120, "completion_tokens": 30}
        )
        self.assertEqual(orjson.loads(served.raw)["stop_reason"], "end_turn")

    async def test_early_stops_fail_the_call(self):
        for stop, shown in (("max_tokens", "length"), ("refusal", "refusal")):
            served = await routes.chat(
                Route.ANTHROPIC, BODY, self.transport(reply('{"ga', stop))
            )
            with self.assertRaisesRegex(routes.RouteError, f"stopped early: {shown}"):
                routes.content(served)

    async def test_missing_key_fails_visibly(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaisesRegex(routes.RouteError, "ANTHROPIC_API_KEY"):
                await routes.chat(Route.ANTHROPIC, BODY, self.transport(reply()))
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
