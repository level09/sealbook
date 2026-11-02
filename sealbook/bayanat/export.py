"""Read a Bayanat export: the JSON file its export writes (`{"bulletins": [...]}`), or
the zip that holds it next to the media files it named. Pure: bytes in, bulletins
out, and each bulletin rendered as the text that becomes its item."""

from __future__ import annotations

import html.parser
import io
import zipfile
from dataclasses import dataclass, field

import orjson

MIMES = frozenset(
    {"application/json", "application/zip", "application/x-zip-compressed"}
)
# Statuses in which a person had worked the bulletin; anything else (Machine
# Created, Human Created, no status) still gets Sealbook's own first pass.
WORKED = frozenset(
    {
        "Assigned",
        "Updated",
        "Peer Review Assigned",
        "Peer Reviewed",
        "Revisited",
        "Senior Updated",
        "Senior Reviewed",
        "Finalized",
    }
)


@dataclass
class Export:
    bulletins: list[dict]
    # filename -> zip member, for the media the zip carries
    media: dict[str, str] = field(default_factory=dict)
    archive: zipfile.ZipFile | None = None

    def read_media(self, filename: str) -> bytes:
        return self.archive.read(self.media[filename])


def _bulletins(data: bytes) -> list[dict]:
    try:
        doc = orjson.loads(data)
    except orjson.JSONDecodeError as exc:
        raise ValueError("this file is not JSON") from exc
    rows = doc.get("bulletins") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise ValueError(
            "not a Bayanat bulletin export: it has no bulletins list"
            + (
                "; actor and incident exports are not imported yet"
                if isinstance(doc, dict) and ({"actors", "incidents"} & doc.keys())
                else ""
            )
        )
    if not all(isinstance(b, dict) and isinstance(b.get("id"), int) for b in rows):
        raise ValueError("not a Bayanat bulletin export: a bulletin has no id")
    return rows


def read(data: bytes) -> Export:
    """The bulletins of a JSON export, or of the JSON inside an export zip."""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        return Export(_bulletins(data))
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in archive.namelist() if not n.endswith("/")]
    found = [n for n in names if n.lower().endswith(".json")]
    if len(found) != 1:
        raise ValueError("a Bayanat export zip holds exactly one JSON file")
    media = {n.rsplit("/", 1)[-1]: n for n in names if n != found[0]}
    return Export(_bulletins(archive.read(found[0])), media, archive)


class _Text(html.parser.HTMLParser):
    BREAKS = frozenset({"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BREAKS:
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def plain(markup: str | None) -> str:
    """Bayanat descriptions are rich-text HTML; keep the words and line breaks."""
    if not markup:
        return ""
    parser = _Text()
    parser.feed(markup)
    lines = (" ".join(line.split()) for line in "".join(parser.out).splitlines())
    return "\n".join(line for line in lines if line)


def label_name(label: dict) -> str:
    """Bayanat's label path and title in the method list's form: "Parent, Child"."""
    path = [p.strip() for p in (label.get("path") or "").split(" > ") if p.strip()]
    return ", ".join([*path, (label.get("title") or "").strip()])


def _place(loc: dict | None) -> str:
    return ((loc or {}).get("full_string") or (loc or {}).get("title") or "").strip()


def _event(e: dict) -> str:
    kind = ((e.get("eventtype") or {}).get("title") or "").strip()
    words = [w for w in (kind, (e.get("title") or "").strip()) if w]
    if place := _place(e.get("location")):
        words.append(f"at {place}")
    if e.get("from_date") or e.get("to_date"):
        words.append(f"from {e.get('from_date') or '?'} to {e.get('to_date') or '?'}")
    return " ".join(words)


@dataclass
class Rendered:
    text: str
    # (part, value) -> {"start", "end"} in text: ("title", None), ("label", name),
    # ("observed", name)
    spans: dict[tuple[str, str | None], dict]


def render(b: dict) -> Rendered:
    """The bulletin as the text of its item: what it says and what its analysts
    decided, one field per line, so each decision can be pinned to its words."""
    lines: list[str] = []
    spans: dict = {}
    at = 0

    def line(label: str, value: str, key=None):
        nonlocal at
        if not value:
            return
        head = f"{label}: "
        if key is not None:
            spans[key] = {"start": at + len(head), "end": at + len(head) + len(value)}
        lines.append(head + value)
        at += len(head) + len(value) + 1

    def listed(label: str, names: list[str], kind: str):
        nonlocal at
        names = list(dict.fromkeys(n for n in names if n))
        if not names:
            return
        start = at + len(label) + 2
        for n in names:
            spans[(kind, n)] = {"start": start, "end": start + len(n)}
            start += len(n) + 2
        lines.append(f"{label}: " + "; ".join(names))
        at += len(lines[-1]) + 1

    line("Bayanat bulletin", str(b["id"]))
    line("Original title", (b.get("title") or "").strip())
    line("Original title (Arabic)", (b.get("title_ar") or "").strip())
    line("Analyst title", (b.get("sjac_title") or "").strip(), ("title", None))
    line(
        "Analyst title (Arabic)",
        (b.get("sjac_title_ar") or "").strip(),
        ("title_ar", None),
    )
    line("Source link", (b.get("source_link") or "").strip())
    line("Origin id", str(b.get("originid") or "").strip())
    line("Published", b.get("publish_date") or "")
    line("Documented", b.get("documentation_date") or "")
    line("Sources", "; ".join(s.get("title") or "" for s in b.get("sources") or []))
    line("Locations", "; ".join(_place(loc) for loc in b.get("locations") or []))
    for e in b.get("events") or []:
        line("Event", _event(e))
    listed("Labels", [label_name(lb) for lb in b.get("labels") or []], "label")
    listed(
        "Verified labels",
        [label_name(lb) for lb in b.get("verLabels") or []],
        "observed",
    )
    if description := plain(b.get("description")):
        lines.append("Description:")
        lines.append(description)
    return Rendered("\n".join(lines) + "\n", spans)


def worked(b: dict) -> bool:
    return b.get("status") in WORKED


def people(b: dict) -> dict:
    """Who worked it in Bayanat, as the export names them."""
    return {
        role: name
        for role, who in (
            ("analyst", b.get("assigned_to")),
            ("reviewer", b.get("first_peer_reviewer")),
        )
        if (name := ((who or {}).get("name") or "").strip())
    }
