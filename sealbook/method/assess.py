"""The first pass of the analyst's method on one item: triage, an analyst title, a
fact-only description and alleged labels, each resting on words quoted from the item.
The worker's assess tool sends SYSTEM and SCHEMA and writes what `items` keeps."""

from sealbook.agents.read import DATA_ONLY
from sealbook.ledger.contracts import Prop
from sealbook.method import CONTENT, RELIABILITY, alleged_labels

MAX_LINES = 6

SYSTEM = (
    "You do the first pass of a human-rights analyst's method on one item of "
    "documentation: a post, report, document, interview, or the transcript of a video "
    "or recording. Use only what the item itself says or shows, never outside "
    "knowledge. Return: "
    "lang, the item's language code; "
    "content, what the item is: incident (it shows or records an attack or violation "
    "as it happens), post_incident_recent (its aftermath with signs it just "
    "happened), post_incident (its aftermath), non_incident (no incident: daily life, "
    "a demonstration, a speech), interview, report, media (coverage by a news "
    "outlet), document (an official or administrative paper) or promotional; "
    "reliability, how far the item's own content supports that a war crime or a "
    "human-rights violation was committed, whatever its source claims: no_evidence, "
    "some_evidence (it might, more is needed) or strong_evidence; "
    "triage_quote, the words in the item that most support that judgment; why, one "
    "sentence; "
    "title, a short title in English of what actually occurred, like 'Shelling hits "
    "buildings' or 'Security forces arrest three men', never the source's headline, "
    "with title_quote, the words it rests on; "
    f"description, at most {MAX_LINES} lines in English, each one fact the item states "
    "or shows, with no judgment or guess, each with quote, the words it rests on; "
    "labels, alleged labels from the list that the item states and at least partly "
    "supports, each with quote; a bare claim with no support gets no label. "
    "Every quote is copied character for character from the item. " + DATA_ONLY
)

_QUOTED = {"type": "string"}
SCHEMA = {
    "name": "first_pass",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "lang",
            "content",
            "reliability",
            "triage_quote",
            "why",
            "title",
            "title_quote",
            "description",
            "labels",
        ],
        "properties": {
            "lang": {"type": "string"},
            "content": {"enum": list(CONTENT)},
            "reliability": {"enum": list(RELIABILITY)},
            "triage_quote": _QUOTED,
            "why": {"type": "string"},
            "title": {"type": "string"},
            "title_quote": _QUOTED,
            "description": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["line", "quote"],
                    "properties": {"line": {"type": "string"}, "quote": _QUOTED},
                },
            },
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["label", "quote"],
                    "properties": {
                        "label": {"enum": list(alleged_labels())},
                        "quote": _QUOTED,
                    },
                },
            },
        },
    },
}


def items(answer: dict, text: str) -> tuple[list[tuple[Prop, dict, dict]], int]:
    """(prop, value, span) for each part of the answer whose quote is in `text`,
    and how many parts were dropped because it is not."""
    lang = answer["lang"]
    parts = [
        (
            Prop.TRIAGE,
            {k: answer[k] for k in ("content", "reliability", "why")},
            answer["triage_quote"],
        ),
        (Prop.TITLE, {"title": answer["title"].strip()}, answer["title_quote"]),
    ]
    parts += [
        (Prop.DESCRIPTION, {"line": d["line"].strip()}, d["quote"])
        for d in answer["description"][:MAX_LINES]
    ]
    seen = set()
    for lb in answer["labels"]:
        if lb["label"] not in seen:
            seen.add(lb["label"])
            parts.append((Prop.LABEL, {"label": lb["label"]}, lb["quote"]))
    out, dropped = [], 0
    for prop, value, quote in parts:
        start = text.find(quote) if quote.strip() else -1
        if start < 0:
            dropped += 1
            continue
        span = {"start": start, "end": start + len(quote)}
        out.append((prop, value | {"text": quote, "lang": lang}, span))
    return out, dropped
