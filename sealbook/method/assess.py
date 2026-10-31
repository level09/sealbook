"""The first pass of the analyst's method on one item: triage, an analyst title, a
fact-only description and alleged labels, each resting on words quoted from the item.
The worker's assess tool sends SYSTEM and SCHEMA and writes what `items` keeps; for a
photo it sends SEE_SYSTEM and SEE_SCHEMA, and each part rests on a box in the picture
(`seen`), with observed labels instead of alleged ones."""

from sealbook.agents.read import DATA_ONLY
from sealbook.ledger.contracts import Prop
from sealbook.method import CONTENT, RELIABILITY, alleged_labels, observed_labels

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


def specific(labels: list[str]) -> set[str]:
    """Labels with no more specific label of their own also chosen."""
    return {lb for lb in labels if not any(o.startswith(lb + ", ") for o in labels)}


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
    seen, keep = set(), specific([lb["label"] for lb in answer["labels"]])
    for lb in answer["labels"]:
        if lb["label"] in keep and lb["label"] not in seen:
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


SEE_SYSTEM = (
    "You do the first pass of a human-rights analyst's method on one photo. Report "
    "only what is visible, never what anyone claims and never outside knowledge. "
    "Return: content (as for any item: incident, post_incident_recent, "
    "post_incident, non_incident, interview, report, media, document or "
    "promotional), reliability (no_evidence, some_evidence or strong_evidence that "
    "a war crime or human-rights violation was committed, from what is visible), "
    "triage_box and why; a short English title of what the photo shows, like "
    "'Damaged residential street', with title_box; "
    f"description, at most {MAX_LINES} lines, each one visible fact with its box; "
    "labels, observed labels from the list that the photo clearly shows, each with "
    "its box. A box is [x0, y0, x1, y1] on a 0 to 1000 scale of the photo's width "
    "and height, around the part that shows it."
)
_BOX = {"type": "array", "items": {"type": "integer"}}
SEE_SCHEMA = {
    "name": "first_pass_seen",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "content",
            "reliability",
            "triage_box",
            "why",
            "title",
            "title_box",
            "description",
            "labels",
        ],
        "properties": {
            "content": {"enum": list(CONTENT)},
            "reliability": {"enum": list(RELIABILITY)},
            "triage_box": _BOX,
            "why": {"type": "string"},
            "title": {"type": "string"},
            "title_box": _BOX,
            "description": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["line", "box"],
                    "properties": {"line": {"type": "string"}, "box": _BOX},
                },
            },
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["label", "box"],
                    "properties": {
                        "label": {"enum": list(observed_labels())},
                        "box": _BOX,
                    },
                },
            },
        },
    },
}


def region(box, width: int, height: int) -> dict | None:
    """A 0 to 1000 box as a pixel span on page 1 of the image, or None if unusable."""
    if not (
        isinstance(box, list) and len(box) == 4 and all(isinstance(n, int) for n in box)
    ):
        return None
    x0, y0, x1, y1 = (max(0, min(1000, n)) for n in box)
    px = [
        x0 * width // 1000,
        y0 * height // 1000,
        x1 * width // 1000,
        y1 * height // 1000,
    ]
    if px[0] >= px[2] or px[1] >= px[3]:
        return None
    return {"page": 1, "bbox": px}


def seen(
    answer: dict, width: int, height: int
) -> tuple[list[tuple[Prop, dict, dict]], int]:
    """(prop, value, span) for each part of a photo's answer with a usable box."""
    parts = [
        (
            Prop.TRIAGE,
            {k: answer[k] for k in ("content", "reliability", "why")},
            answer["triage_box"],
        ),
        (Prop.TITLE, {"title": answer["title"].strip()}, answer["title_box"]),
    ]
    parts += [
        (Prop.DESCRIPTION, {"line": d["line"].strip()}, d["box"])
        for d in answer["description"][:MAX_LINES]
    ]
    labels, keep = {}, specific([lb["label"] for lb in answer["labels"]])
    for lb in answer["labels"]:
        if lb["label"] in keep:
            labels.setdefault(lb["label"], lb["box"])
    parts += [
        (Prop.LABEL, {"label": label, "basis": "observed"}, box)
        for label, box in labels.items()
    ]
    out, dropped = [], 0
    for prop, value, box in parts:
        span = region(box, width, height)
        if span is None:
            dropped += 1
            continue
        out.append((prop, value | {"lang": "en"}, span))
    return out, dropped


assert specific(["A", "A, B", "C"]) == {"A, B", "C"}
assert region([0, 0, 500, 1000], 200, 100) == {"page": 1, "bbox": [0, 0, 100, 100]}
assert region([10, 10, 10, 20], 200, 100) is None
