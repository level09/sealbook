"""Generate the case study "One man, four names" into acceptance/cases/four-names/.

Fictional throughout: the people, the companies and Port Ondrel are invented. The
sheets and case.json are byte-stable; the scans need Playwright and are rendered
only with --scans, which also rewrites files/boxes.json (where each pinned value
sits on its scan).

  uv run python acceptance/cases/four-names/make.py            # sheets and case.json
  uv run python acceptance/cases/four-names/make.py --scans    # also the five scans
"""

import csv
import io
import json
import random
import sys
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from sealbook.search.arabic import candidates as name_match  # noqa: E402

FILES = HERE / "files"
INVESTIGATION = "case-four-names"
TODAY = "2024-06-30"
SEED = 4404

# ---- the man and his namesakes -------------------------------------------------
# One man, born 2 March 1974 in Velmora, son of Elias. Each desk met him under its
# own spelling and joined its own records; nobody joined them across desks.
BORN, TOWN, FATHER = "1974-03-02", "Velmora", "Elias"
ROLES = {  # cluster: (desk, records)
    "supplier": ("procurement", ["vendors/V031", "vendor_form/owner"]),
    "consultant": ("finance", ["consultants/C014", "agreement/consultant"]),
    "guarantor": (
        "credit",
        ["guarantees/G022", "guarantee_deed/guarantor", "passport/holder"],
    ),
    "agent": ("port", ["agents/A027", "licence/holder"]),
}
# Who each record really is. The two namesakes are other people.
MAN = {r for _, rs in ROLES.values() for r in rs}
N1 = "crew/K018"  # Adrian Kassar, deck cadet, born 1988
N2 = "surveyors/S009"  # Adrian Kasar, marine surveyor, born August 1974 in Torvane

GIVEN = """Marta Jonas Elena Tobias Ines Pavel Sofia Lukas Nadia Oskar Clara Milan
Hanna Teodor Livia Rafael Irena Bruno Selma Greta Emil Dora Felix Lena Marco Petra
Simon Vera Anton Julia Hugo Nora Ivan Ruth Karl Alma Leon Mira Otto Paula Stefan
Boris Edith Mateo Ilse Gideon Rosa Tomas Agnes Viktor Bianca Cyril Freya Lionel
Magda Piers Sabine Ulric Wanda Bertil Corinne""".split()
FAMILY = """Brandt Novak Ferrante Lindqvist Okafor Haldane Moreau Petrov Castell
Varga Szabo Brennan Dalca Rosen Mertens Kovac Lorne Amsel Teller Quist Fenwick
Marsh Iles Corvin Belmont Hadley Sorel Vance Pryor Galt Renner Holm Ostrander
Wexler Calloway Duarte Esposito Mendel Farrow Kessler Aalto Bjorn Cardell Dempsey
Ebner Falk Gorman Hallett Ingram Jessup Kaplan Lucan Maddox Nyberg Orloff Pelham
Rook Strand Thorne Ulmann Voss Wilde Yates Zoller""".split()
TOWNS = ["Velmora", "Torvane", "Halbrook", "Fennick", "Lantry", "Brisca", "Evrin"]

rng = random.Random(SEED)


def people(n, taken):
    """`n` invented full names, none name-matching any other name in the case."""
    out = []
    while len(out) < n:
        name = f"{rng.choice(GIVEN)} {rng.choice(FAMILY)}"
        if any(name_match(name, t) for t in taken):
            continue
        taken.append(name)
        out.append(name)
    return out


def day(lo, hi):
    """A date between two years, as ISO text."""
    y = rng.randint(lo, hi)
    return f"{y}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def write_csv(name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    (FILES / name).write_text(buf.getvalue(), encoding="utf-8")
    return buf.getvalue()


# ---- the sheets --------------------------------------------------------------------

PLANTED = [
    "Adrian Kassar",
    "Adrian Qassar",
    "A. Qassar",
    "Adrian Khassar",
    "Adrian Kasar",
    "أدريان قصار",
]
TAKEN = list(PLANTED)
RECORDS = {}  # record key: (source, person)
CLAIMS = []


def claim(record, prop, value, pin=None, when=None, place=None):
    CLAIMS.append(
        {
            "key": f"{record}.{prop}",
            "record": record,
            "prop": prop,
            "value": value,
            "from": when,
            "to": when,
            "place": place,
            "tier": "unconfirmed",
        }
        | ({"pin": pin} if pin else {})
    )


def row_claims(source, row_id, person, fields):
    """One person record per sheet row, each value pinned to its cell. Rows count
    from 1 with the header as row 1, so row id V031 is row 32."""
    record = f"{source}/{row_id}"
    RECORDS[record] = (source, person)
    row = int(row_id[1:]) + 1
    for prop, value, column in fields:
        claim(record, prop, value, {"sheet": 1, "row": row, "column": column})
    return record


def companies(n):
    stems = """Harbour Anchor Tideline Northgate Bluewater Keel Mooring Saltway Quayside
    Larkspur Breakwater Gull Ironside Seabright Dockhand Lantern Compass Riverbend
    Shoreline Westerly Beacon Capstan Driftwood Halyard Mariner Portside Rigging
    Sextant Starboard Windward""".split()
    kinds = [
        "Provisions",
        "Marine Services",
        "Rope and Chain",
        "Fuel Supply",
        "Ship Repair",
        "Electrical",
        "Coatings",
        "Logistics",
        "Catering",
        "Safety Equipment",
        "Hydraulics",
    ]
    seen = set()
    while len(seen) < n:
        seen.add(f"{rng.choice(stems)} {rng.choice(kinds)} Ltd")
    return sorted(seen)


def sheets():
    # Vendor master: 60 suppliers and their contact person.
    names = people(59, TAKEN)
    firms = companies(59)
    rows, vendors = [], []
    for i in range(1, 61):
        rid = f"V{i:03d}"
        if rid == "V031":
            firm, contact = "Kassar Marine Supply", "Adrian Kassar"
        else:
            firm, contact = firms.pop(), names.pop()
        vendors.append(firm)
        rows.append(
            [rid, firm, contact, day(2009, 2021), rng.choice(["30", "45", "60"])]
        )
        if rid != "V031":
            row_claims("vendors", rid, f"vendor-{rid}", [("name", contact, 3)])
    write_csv(
        "vendor_master.csv",
        [
            "Vendor ID",
            "Company",
            "Contact person",
            "Registered",
            "Payment terms (days)",
        ],
        rows,
    )
    row_claims("vendors", "V031", "man", [("name", "Adrian Kassar", 3)])

    # Purchase ledger and payments: the bulk a reader would have to go through.
    ledger = []
    for i in range(1, 421):
        firm = "Kassar Marine Supply" if i % 9 == 4 else rng.choice(vendors)
        ledger.append(
            [
                f"PO-23-{i:04d}",
                day(2023, 2023),
                firm,
                rng.choice(["Provisions", "Spares", "Services", "Fuel", "Repairs"]),
                f"{rng.randint(4, 380) * 125:.2f}",
                rng.choice(["JM", "RT", "KL", "SD", "BW"]),
            ]
        )
    ledger.sort(key=lambda r: (r[1], r[0]))
    write_csv(
        "purchase_ledger_2023.csv",
        ["PO", "Date", "Vendor", "Category", "Amount", "Approved by"],
        ledger,
    )
    payments = []
    for i, po in enumerate(ledger, 1):
        payments.append([f"PAY-{i:05d}", po[1], po[2], po[4], po[0]])
    for i in range(len(ledger) + 1, len(ledger) + 201):
        payments.append(
            [
                f"PAY-{i:05d}",
                day(2023, 2023),
                rng.choice(["Port Ondrel Authority", "Harbour dues", "Payroll"]),
                f"{rng.randint(10, 900) * 50:.2f}",
                "",
            ]
        )
    payments.sort(key=lambda r: (r[1], r[0]))
    write_csv(
        "payments_2023.csv",
        ["Payment", "Date", "Payee", "Amount", "Against PO"],
        payments,
    )

    # Approved consultants: 25, with birth year and home town.
    names = people(24, TAKEN)
    rows = []
    for i in range(1, 26):
        rid = f"C{i:03d}"
        if rid == "C014":
            name, born, field = "Adrian Qassar", "1974", "Port logistics"
        else:
            name, born = names.pop(), str(rng.randint(1955, 1992))
            field = rng.choice(["Customs", "Insurance", "Port logistics", "Audit"])
        rows.append([rid, name, born, field, f"{rng.randint(6, 14) * 50}"])
        if rid != "C014":
            row_claims(
                "consultants",
                rid,
                f"consultant-{rid}",
                [("name", name, 2), ("birth_date", born, 3)],
            )
    write_csv(
        "consultants_register.csv",
        ["Ref", "Consultant", "Born", "Field", "Day rate"],
        rows,
    )
    row_claims(
        "consultants",
        "C014",
        "man",
        [("name", "Adrian Qassar", 2), ("birth_date", "1974", 3)],
    )
    invoices = []
    consultants = [r[1] for r in rows]
    for i in range(1, 181):
        who = "A. Qassar" if i % 7 == 3 else rng.choice(consultants)
        invoices.append(
            [f"CI-23-{i:04d}", day(2023, 2023), who, rng.randint(2, 15), ""]
        )
    for inv in invoices:
        inv[4] = f"{inv[3] * rng.choice([400, 450, 500, 650]):.2f}"
    invoices.sort(key=lambda r: (r[1], r[0]))
    write_csv(
        "consultancy_invoices_2023.csv",
        ["Invoice", "Date", "Consultant", "Days", "Amount"],
        invoices,
    )

    # Personal guarantees held by the credit desk: name, father, birth date.
    # One guarantor per family name, so no two noise rows read as relatives.
    names = []
    while len(names) < 29:
        names += [
            n
            for n in people(1, TAKEN)
            if n.split()[-1] not in {m.split()[-1] for m in names}
        ]
    rows = []
    for i in range(1, 31):
        rid = f"G{i:03d}"
        if rid == "G022":
            name, father, born = "Adrian Khassar", FATHER, BORN
            line = "Ondrel Logistics credit line"
        else:
            name, father, born = names.pop(), rng.choice(GIVEN), day(1950, 1990)
            line = f"{rng.choice(vendors)} trade credit"
        rows.append([rid, name, father, born, f"G-{400 + i:04d}", line])
        if rid != "G022":
            row_claims(
                "guarantees",
                rid,
                f"guarantor-{rid}",
                [
                    ("name", name, 2),
                    ("father_name", father, 3),
                    ("birth_date", born, 4),
                ],
            )
    write_csv(
        "guarantees_register.csv",
        ["Ref", "Guarantor", "Father", "Born", "Guarantee", "Secures"],
        rows,
    )
    row_claims(
        "guarantees",
        "G022",
        "man",
        [
            ("name", "Adrian Khassar", 2),
            ("father_name", FATHER, 3),
            ("birth_date", BORN, 4),
        ],
    )

    # Licensed ship agents at Port Ondrel: name and licence only.
    names = people(39, TAKEN)
    rows = []
    for i in range(1, 41):
        rid = f"A{i:03d}"
        name = "Adrian Kasar" if rid == "A027" else names.pop()
        rows.append([rid, name, f"PA-{2264 + i}", day(2015, 2023)])
        if rid != "A027":
            row_claims("agents", rid, f"agent-{rid}", [("name", name, 2)])
    write_csv(
        "ship_agents_2023.csv",
        ["Ref", "Agent", "Licence", "Licensed since"],
        rows,
    )
    row_claims("agents", "A027", "man", [("name", "Adrian Kasar", 2)])

    # The namesakes' own sheets. A deck cadet with the same name, and a marine
    # surveyor born the same year whose name the port list spells the same way.
    names = people(49, TAKEN)
    rows = []
    for i in range(1, 51):
        rid = f"K{i:03d}"
        if rid == "K018":
            name, born, rank = "Adrian Kassar", "1988-11-20", "Deck cadet"
        else:
            name, born = names.pop(), day(1960, 2003)
            rank = rng.choice(["AB", "Oiler", "Cook", "Bosun", "Third officer"])
        rows.append([rid, name, rank, born])
        row_claims(
            "crew",
            rid,
            "namesake-1" if rid == "K018" else f"crew-{rid}",
            [("name", name, 2), ("birth_date", born, 4)],
        )
    write_csv(
        "crew_list_ondrel_star.csv",
        ["Ref", "Name", "Rank", "Born"],
        rows,
    )
    names = people(19, TAKEN)
    rows = []
    for i in range(1, 21):
        rid = f"S{i:03d}"
        if rid == "S009":
            name, born, town = "Adrian Kasar", "1974-08-19", "Torvane"
        else:
            name, born, town = names.pop(), day(1955, 1990), rng.choice(TOWNS)
        rows.append([rid, name, born, town])
        row_claims(
            "surveyors",
            rid,
            "namesake-2" if rid == "S009" else f"surveyor-{rid}",
            [("name", name, 2), ("birth_date", born, 3), ("place_of_birth", town, 4)],
        )
    write_csv(
        "marine_surveyors_register.csv",
        ["Ref", "Surveyor", "Born", "Place of birth"],
        rows,
    )


# ---- the scans ---------------------------------------------------------------------
# Each is a typed form, photographed or scanned at the desk that holds it. The ids
# are the claims pinned on it; boxes.json records where they landed.

SCANS = {
    "vendor_form": (
        "vendor_form_kassar.jpg",
        "Vendor registration form",
        "Ondrel Freight Lines, procurement",
        [
            ("Company", "Kassar Marine Supply", None),
            ("Owner", "Adrian Kassar", "name"),
            ("Owner's father", FATHER, "father_name"),
            ("Owner's date of birth", BORN, "birth_date"),
            ("Owner's place of birth", TOWN, "place_of_birth"),
            ("Registered office", "Quay Road 14, Port Ondrel", None),
            ("Vendor ID", "V031", None),
            (
                "Signed in person at",
                "Ondrel Freight Lines, Port Ondrel, 14 June 2021",
                "seen_at",
                "Ondrel Freight Lines, Port Ondrel",
            ),
        ],
    ),
    "agreement": (
        "consultancy_agreement_qassar.jpg",
        "Consultancy agreement CA-2023-117",
        "Ondrel Freight Lines, finance",
        [
            ("Consultant", "A. Qassar", "name"),
            ("Born", "1974", "birth_date"),
            ("Place of birth", TOWN, "place_of_birth"),
            ("Scope", "Review of port logistics and agency costs", None),
            ("Term", "1 February 2023 to 31 January 2024", None),
            ("Fee", "650.00 per day, invoiced monthly", None),
            (
                "Signed in person at",
                "Ondrel Freight Lines, Port Ondrel, 30 January 2023",
                "seen_at",
                "Ondrel Freight Lines, Port Ondrel",
            ),
        ],
    ),
    "guarantee_deed": (
        "guarantee_deed_khassar.jpg",
        "Personal guarantee G-0422",
        "Ondrel Freight Lines, credit desk",
        [
            ("Guarantor", "Adrian Khassar", "name"),
            ("Son of", FATHER, "father_name"),
            ("Date of birth", BORN, "birth_date"),
            ("Secures", "Ondrel Logistics credit line, up to 1,200,000.00", None),
            (
                "Witnessed at",
                "Ondrel Freight Lines credit desk, Port Ondrel, 9 March 2023",
                "seen_at",
                "Ondrel Freight Lines credit desk, Port Ondrel",
            ),
        ],
    ),
    "passport": (
        "passport_copy.jpg",
        "Passport copy attached to guarantee G-0422",
        "Copy certified by the credit desk",
        [
            ("Surname / Given names", "KASSAR / ADRIAN", "name", "Adrian Kassar"),
            ("Sex", "M", "sex", "male"),
            ("Date of birth", BORN, "birth_date"),
            ("Place of birth", TOWN, "place_of_birth"),
            (
                "Original seen in person at",
                "Ondrel Freight Lines credit desk, 9 March 2023",
                "seen_at",
                "Ondrel Freight Lines credit desk",
            ),
        ],
    ),
    "licence": (
        "ship_agent_licence.jpg",
        "Ship agent licence PA-2291",
        "Port Ondrel Authority",
        [
            ("Name / الاسم", "أدريان قصار", "name"),
            ("Father / اسم الأب", "إلياس", "father_name"),
            ("Date of birth", BORN, "birth_date"),
            ("Licence", "PA-2291", None),
            ("Valid until", "31 December 2024", None),
            (
                "Issued in person at",
                "Harbour House, Port Ondrel, 5 January 2023",
                "seen_at",
                "Harbour House, Port Ondrel",
            ),
        ],
    ),
}
# Where and when each form puts the man in person: one trail across the four
# desks, each read off its own form.
TRAIL = {
    "vendor_form": ("2021-06-14", "freight_lines"),
    "licence": ("2023-01-05", "harbour_house"),
    "agreement": ("2023-01-30", "freight_lines"),
    "guarantee_deed": ("2023-03-09", "freight_lines"),
    "passport": ("2023-03-09", "freight_lines"),
}
# Invented places, in open sea so no map puts the story in a real town.
PLACES = {  # key: name, lon, lat, precision in metres
    "freight_lines": ("Ondrel Freight Lines, Port Ondrel", -26.104, 44.302, 100),
    "harbour_house": ("Harbour House, Port Ondrel", -26.092, 44.296, 100),
}
SCAN_PERSON = {
    "vendor_form": "vendor_form/owner",
    "agreement": "agreement/consultant",
    "guarantee_deed": "guarantee_deed/guarantor",
    "passport": "passport/holder",
    "licence": "licence/holder",
}


def scan_html(key):
    _, title, desk, rows = SCANS[key]
    photo = (FILES / "id_photo.jpg").resolve().as_uri()
    pic = (
        f'<img src="{photo}" style="width:240px;float:right;margin:0 0 20px 30px;'
        'border:1px solid #888">'
        if key in ("passport", "licence")
        else ""
    )
    trs = "".join(
        f'<tr><th>{label}</th><td><span id="{prop or f"x{i}"}">{shown}</span></td></tr>'
        for i, (label, shown, prop, *_) in enumerate(rows)
    )
    return f"""<!doctype html><html><meta charset="utf-8"><style>
body{{margin:0;background:#fff;font-family:"Times New Roman",serif;color:#1d1d1d}}
.page{{width:1240px;height:1754px;padding:110px 120px;box-sizing:border-box}}
.desk{{font-size:24px;letter-spacing:2px;text-transform:uppercase;color:#444}}
h1{{font-size:46px;margin:24px 0 60px;border-bottom:3px solid #222;padding-bottom:18px}}
table{{border-collapse:collapse;font-size:30px;width:100%}}
th{{text-align:left;font-weight:normal;color:#555;padding:18px 30px 18px 0;width:42%;vertical-align:top}}
td{{padding:18px 0;border-bottom:1px solid #bbb;font-family:"Courier New",monospace;font-size:32px}}
td span[id^="name"],td span[id^="father"]{{font-family:"Geeza Pro","Courier New",monospace}}
.foot{{margin-top:120px;font-size:22px;color:#555}}
.sig{{margin-top:90px;font-size:26px;border-top:1px solid #222;width:420px;padding-top:8px}}
</style><div class="page"><div class="desk">{desk}</div><h1>{title}</h1>{pic}
<table>{trs}</table>
<div class="sig">Signature</div>
<div class="foot">Fictional document for the Sealbook case study "One man, four names".</div>
</div></html>"""


def render_scans():
    from PIL import Image
    from playwright.sync_api import sync_playwright

    from acceptance.make_case import scan_realism
    from acceptance.make_demo_docs import BOX_JS, PAGE

    boxes = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for n, key in enumerate(SCANS):
            file, *_, rows = SCANS[key]
            page = browser.new_page(viewport=PAGE)
            raw = FILES / f"{key}.raw.html"
            raw.write_text(scan_html(key), encoding="utf-8")
            page.goto(raw.as_uri())
            page.evaluate("document.fonts.ready")
            found = {
                prop: page.locator(f"#{prop}").evaluate(BOX_JS)
                for _, _, prop, *_ in rows
                if prop
            }
            shot = FILES / f"{key}.raw.png"
            page.screenshot(path=shot)
            page.close()
            raw.unlink()
            img, moved, _ = scan_realism(
                Image.open(shot), found, random.Random(SEED + n)
            )
            shot.unlink()
            w, h = img.size
            img.save(FILES / file, quality=80)
            boxes[key] = {
                k: [
                    round(max(0, a)),
                    round(max(0, b)),
                    round(min(w, c)),
                    round(min(h, d)),
                ]
                for k, (a, b, c, d) in moved.items()
            }
        browser.close()
    (FILES / "boxes.json").write_text(json.dumps(boxes, indent=2) + "\n")


def scan_claims():
    boxes = json.loads((FILES / "boxes.json").read_text())
    for key, (_, _, _, rows) in SCANS.items():
        record = SCAN_PERSON[key]
        RECORDS[record] = (key, "man")
        for _, shown, prop, *read in rows:
            # A value read off the page in another form ("M" as male) is pinned to
            # the field it was read from.
            if prop:
                value = read[0] if read else shown
                when, place = TRAIL[key] if prop == "seen_at" else (None, None)
                pin = {"page": 1, "bbox": boxes[key][prop]}
                claim(record, prop, value, pin, when, place)


# ---- sources, truth, case.json ---------------------------------------------------------

SOURCES = {  # key: title, document type, sensitivity, date, file
    "vendors": (
        "Vendor master list, procurement",
        "vendor_list",
        "ordinary",
        "2023-12-31",
        "vendor_master.csv",
    ),
    "ledger": (
        "Purchase ledger 2023",
        "ledger",
        "ordinary",
        "2023-12-31",
        "purchase_ledger_2023.csv",
    ),
    "payments": (
        "Outgoing payments 2023",
        "ledger",
        "ordinary",
        "2023-12-31",
        "payments_2023.csv",
    ),
    "vendor_form": (
        "Vendor registration form, Kassar Marine Supply",
        "registration_form",
        "ordinary",
        "2021-06-14",
        "vendor_form_kassar.jpg",
    ),
    "consultants": (
        "Approved consultants register",
        "register",
        "ordinary",
        "2023-01-15",
        "consultants_register.csv",
    ),
    "invoices": (
        "Consultancy invoices 2023",
        "ledger",
        "ordinary",
        "2023-12-31",
        "consultancy_invoices_2023.csv",
    ),
    "agreement": (
        "Consultancy agreement CA-2023-117",
        "contract",
        "ordinary",
        "2023-01-30",
        "consultancy_agreement_qassar.jpg",
    ),
    "guarantees": (
        "Personal guarantees register, credit desk",
        "register",
        "ordinary",
        "2023-12-31",
        "guarantees_register.csv",
    ),
    "guarantee_deed": (
        "Personal guarantee G-0422",
        "guarantee",
        "ordinary",
        "2023-03-09",
        "guarantee_deed_khassar.jpg",
    ),
    "passport": (
        "Passport copy attached to guarantee G-0422",
        "id_document",
        "sensitive",
        "2023-03-09",
        "passport_copy.jpg",
    ),
    "agents": (
        "Licensed ship agents, Port Ondrel, 2023",
        "register",
        "ordinary",
        "2023-12-31",
        "ship_agents_2023.csv",
    ),
    "licence": (
        "Ship agent licence PA-2291",
        "licence",
        "ordinary",
        "2023-01-05",
        "ship_agent_licence.jpg",
    ),
    "crew": (
        "Crew list, MV Ondrel Star, May 2023",
        "crew_list",
        "ordinary",
        "2023-05-02",
        "crew_list_ondrel_star.csv",
    ),
    "surveyors": (
        "Marine surveyors register",
        "register",
        "ordinary",
        "2023-09-30",
        "marine_surveyors_register.csv",
    ),
}
# One accepted cluster per desk: what each desk already knew.
CLUSTERS = [rs for _, rs in ROLES.values()]
# The truth is read per cluster: one record stands for each desk's cluster.
FACE = {role: rs[-1] for role, (_, rs) in ROLES.items()}


def truth():
    same = [sorted(p) for p in combinations(FACE.values(), 2)]
    twins = [sorted((n, r)) for n in (N1, N2) for r in FACE.values()]
    return {
        "same_person": same,
        "twins": twins,
        "why": {
            "same_person": "one man, born 1974-03-02 in Velmora, son of Elias, under "
            "four spellings and one Arabic-script entry",
            "twins": f"{N1} is a deck cadet born 1988; {N2} is a marine surveyor "
            "born 1974-08-19 in Torvane",
        },
        "gaps": [],
        "not_gaps": [],
        "contradictions": [],
        "not_contradictions": [],
        "hard_same_person": [],
        "traps": [],
        "hard_contradictions": [],
        "trap_contradictions": [],
    }


def main():
    FILES.mkdir(exist_ok=True)
    if "--scans" in sys.argv:
        render_scans()
    sheets()
    scan_claims()
    keys = [c["key"] for c in CLAIMS]
    assert len(keys) == len(set(keys)), "claim keys repeat"
    assert {RECORDS[r][1] for r in MAN} == {"man"}
    assert all(r in RECORDS for p in truth()["twins"] for r in p)
    # No two noise names match: only the planted names can make a candidate.
    noise = sorted({c["value"] for c in CLAIMS if c["prop"] == "name"} - set(PLANTED))
    assert not [p for p in combinations(noise, 2) if name_match(*p)]
    rows = sum(
        len((FILES / s[4]).read_text().splitlines()) - 1
        for s in SOURCES.values()
        if s[4].endswith(".csv")
    )
    case = {
        "about": "Planted truth for the case study One man, four names, by "
        "acceptance/cases/four-names/make.py. All content is fictional.",
        "story": {
            "title": "One man, four names",
            "kind": "fraud",
            "puzzle": "A freight company at Port Ondrel buys from a marine supplier, "
            "pays a logistics consultant, holds a personal guarantee for a credit "
            "line and works through a licensed ship agent. Each desk checked its own "
            f"file and found nothing wrong. The files hold {rows:,} sheet rows and "
            "five scans.",
            "twist": "The supplier Adrian Kassar, the consultant A. Qassar, the "
            "guarantor Adrian Khassar and the ship agent أدريان قصار are one man, "
            "born on 2 March 1974. A marine surveyor named Adrian Kasar, born the "
            "same year, is someone else, and the rules cannot tell them apart.",
            "rests_on": ["linker", "cross-script name keys", "human review"],
            "fictional": True,
        },
        "investigation": INVESTIGATION,
        "today": TODAY,
        "places": {
            k: {"name_en": n, "name_ar": None, "lon": lon, "lat": lat, "precision_m": m}
            for k, (n, lon, lat, m) in PLACES.items()
        },
        "sources": {
            k: {"title": t, "doc_type": d, "sensitivity": s, "date": dt}
            | {"file": f"files/{f}"}
            for k, (t, d, s, dt, f) in SOURCES.items()
        },
        "records": {k: {"source": s, "person": p} for k, (s, p) in RECORDS.items()},
        "clusters": CLUSTERS,
        "claims": CLAIMS,
        "batch": ["guarantee_deed", "passport"],
        "truth": truth(),
    }
    (HERE / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
