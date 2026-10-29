"""The extraction scorer counts an outlet filed as a person as invented, and a
planted person and town as found, on a scripted answer."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from acceptance import eval_extract
from sealbook.ledger.contracts import Route


def item(mention, prop, text):
    return {
        "mention": mention,
        "prop": prop,
        "value": {"text": text, "lang": "en"},
        "occurrence": 1,
        "evidence": ["printed_text"],
        "valid": None,
    }


class ScorerTest(unittest.TestCase):
    def test_outlet_is_invented_and_planted_facts_are_found(self):
        post = {
            "id": "x",
            "text": "Daraa 24 Network: Mahmoud Fanish was shot near Nawa.",
            "people": ["Mahmoud Fanish"],
            "towns": ["Nawa"],
        }
        answer = {
            "statements": [
                item("Mahmoud Fanish", "name", "Mahmoud Fanish"),
                item("Daraa 24 Network", "name", "Daraa 24 Network"),
            ],
            "events": [
                {
                    "what": "was shot",
                    "lang": "en",
                    "kind": "killing",
                    "place": "near Nawa",
                    "town": "Nawa",
                    "date": None,
                }
            ],
        }
        served = SimpleNamespace(
            data={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(answer)},
                    }
                ]
            }
        )
        with mock.patch.object(
            eval_extract.routes, "chat", mock.AsyncMock(return_value=served)
        ):
            got = asyncio.run(eval_extract.trial(Route.ANTHROPIC, "m", [post]))
        self.assertEqual(got["invented_people"], 1)
        self.assertEqual(got["people_recall"], 1.0)
        self.assertEqual(got["town_recall"], 1.0)
        self.assertEqual(got["invented_events"], 0)
