"""A case's chronology as a spreadsheet to take away: every accepted or corroborated
claim, in date order, each row linking back to its source in the app. A claim from a
sensitive source the reader has not revealed in this session is listed with its value
withheld, so the download shows no more than the pages do. The download writes one
access record naming every claim whose value it carried."""

from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font

from sealbook.agents import brief, read
from sealbook.discovery.evidence import granted
from sealbook.ledger.contracts import Tier

STANDING = (Tier.ACCEPTED, Tier.CORROBORATED)
HEAD = ("When", "Time", "Who", "What", "Value", "Place", "Source", "Standing", "Claim")
WIDTHS = (20, 12, 26, 18, 48, 26, 40, 13, 38)
WITHHELD = "withheld: reveal the source in the app to include it"


async def workbook(db, user, investigation: str, name: str, base: str) -> bytes:
    case = await read.case(db, investigation)
    claims = [c for c in case.claims if c.tier in STANDING]
    claims.sort(
        key=lambda c: (
            c.lo or c.hi or date.max,
            c.hi or c.lo or date.max,
            c.time or "",
            str(c.id),
        )
    )
    book = Workbook()
    dated = book.active
    dated.title = "Chronology"
    undated = book.create_sheet("Undated")
    shown = []
    # A name is taken only from what this reader may see, as on the pages.
    visible = [c for c in case.claims if not c.sensitive or granted(c.source)]
    for sheet in (dated, undated):
        sheet.append(HEAD)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
    for c in claims:
        hidden = c.sensitive and not granted(c.source)
        if not hidden:
            shown.append(str(c.id))
        place = case.places.get(c.id)
        sheet = dated if c.lo or c.hi else undated
        sheet.append(
            (
                read.when(c.lo, c.hi) or "",
                c.time or "",
                (case.name(c.person, visible) or "unnamed")
                if c.person
                else "the document",
                read.label(c.prop),
                WITHHELD if hidden else c.text,
                "" if hidden or place is None else read.place_name(place),
                case.docs[c.source]["title"],
                c.tier,
                str(c.id),
            )
        )
        link = sheet.cell(row=sheet.max_row, column=7)
        link.hyperlink = f"{base}evidence/{c.source}"
        link.font = Font(color="1F4E8C", underline="single")
    for sheet in (dated, undated):
        for i, w in enumerate(WIDTHS):
            sheet.column_dimensions[chr(65 + i)].width = w
    book.properties.title = f"{name}: chronology"
    if shown:
        await brief.log_view(
            db, user, None, shown, subject=f"chronology:{investigation}"
        )
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
