"""The analyst's method as data: triage by content and reliability, the priority they
set, the roles people play in an incident, and the label and violation vocabularies.
labels.csv, potential_violation.csv and claimed_violation.csv are Bayanat's (AGPL-3.0,
https://github.com/sjacorg/bayanat, docs/methodology), reused so analysts trained on
it find their own terms; verified labels are what the analyst observes in the item,
unverified ones what the source alleges and the item partly supports."""

import csv
from functools import cache
from pathlib import Path

from sealbook.ledger.contracts import Prop

HERE = Path(__file__).parent

# What the item is. "post_incident_recent" is the method's Post-incident with signs
# of a recent incident.
CONTENT = {
    "incident": "Incident",
    "post_incident_recent": "Post-incident, signs of a recent incident",
    "post_incident": "Post-incident",
    "non_incident": "Non-incident",
    "interview": "Interview",
    "report": "Report",
    "media": "Media",
    "document": "Document",
    "promotional": "Promotional",
}
# How far the item itself supports a violation, whatever its source claims.
RELIABILITY = {
    "no_evidence": "No evidence",
    "some_evidence": "Some evidence",
    "strong_evidence": "Strong evidence",
}
# High priority pairs from the method's priority-based analysis; every other pair is
# low. High priority items get related items and titles as well as the basic fields.
HIGH = {
    ("post_incident_recent", "some_evidence"),
    ("post_incident_recent", "strong_evidence"),
    ("post_incident", "strong_evidence"),
    ("incident", "some_evidence"),
    ("incident", "strong_evidence"),
}


def priority(content: str, reliability: str) -> str:
    return "high" if (content, reliability) in HIGH else "low"


# Roles in an incident, in the method's terms. "victim" is the word Sealbook used
# before; statements that carry it stay valid and read as Injured party.
ROLES = (
    "unspecified",
    "injured_party",
    "witness",
    "alleged_perpetrator",
    "appeared",
    "participant",
    "other",
)
ROLE_WORDS = {
    "unspecified": "Role not set",
    "injured_party": "Injured party",
    "victim": "Injured party",
    "witness": "Witness",
    "alleged_perpetrator": "Alleged perpetrator",
    "appeared": "Appeared",
    "participant": "Participant",
    "other": "Other",
}


def words(prop: str, value: dict) -> str:
    """A first-pass value as the analyst reads it; its quote is the evidence."""
    if prop == Prop.TRIAGE:
        c, r = value["content"], value["reliability"]
        return f"{CONTENT[c]}, {RELIABILITY[r].lower()}, {priority(c, r)} priority"
    if prop == Prop.LABEL:
        seen = value.get("basis") == "observed"
        return f"{'Seen' if seen else 'Alleged'}: {value['label']}"
    return value[{Prop.TITLE: "title", Prop.LABEL: "label"}.get(prop, "line")]


def _rows(name: str) -> list[dict]:
    with open(HERE / name, newline="") as f:
        return list(csv.DictReader(f))


# Verified label groups that triage and the workflow already carry, so the first pass
# does not propose them again as labels.
TRIAGED = ("Content", "Reliability", "Priority", "Meta")


@cache
def observed_labels() -> tuple[str, ...]:
    """Verified labels an item may carry: what the analyst sees in it."""
    return tuple(
        r["title"].strip()
        for r in _rows("labels.csv")
        if r["verified"] == "1"
        and r["for_item"] == "1"
        and not r["title"].startswith(TRIAGED)
    )


@cache
def alleged_labels() -> tuple[str, ...]:
    """Unverified labels an item may carry: the source alleges it and the item
    partly supports it."""
    return tuple(
        r["title"].strip()
        for r in _rows("labels.csv")
        if r["verified"] != "1" and r["for_item"] == "1"
    )


@cache
def violations() -> dict[str, tuple[str, ...]]:
    """The possible category of an incident's violation, and the violations claimed."""
    return {
        kind: tuple(r["title"].strip() for r in _rows(f"{kind}_violation.csv"))
        for kind in ("potential", "claimed")
    }


assert priority("incident", "some_evidence") == "high"
assert priority("post_incident", "some_evidence") == "low"
