"""Generate the case study "The name nobody wrote down" into this directory.

A fictional missing person case: Ostry Coaches, an invented bus company in the
invented town of Wenmere. Writes case.json (the planted case and its truth, in the
shape of acceptance/agents/case.json plus a `story`), the log sheets and statements
under files/, and two scanned pages. The two pictures in files/ are not made here;
files/ATTRIBUTION.md says where each came from.

  uv run python acceptance/cases/unwritten-name/make.py              # everything
  uv run python acceptance/cases/unwritten-name/make.py --no-scans   # skip the browser

case.json, the sheets and the statements are byte-stable. The scans go through a
browser and a scan filter, so their bytes may differ between machines.
"""

import argparse
import csv
import io
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
FILES = HERE / "files"
INVESTIGATION = "case-unwritten-name"
TODAY = "2026-07-01"  # the gap finder's "now" for open silences
START, END = date(2026, 2, 2), date(2026, 6, 30)
SEED = 1403
FICTIONAL = "Fictional. Every name, company and place in this case is invented."

# ---- places, as an analyst confirmed them ----------------------------------------
# Wenmere and Holt Cross are invented. Their points lie at sea so a map can never
# put the story in a real town. Place.name_ar is required, so it repeats the name.

PLACES = {  # key: name, lon, lat, precision in metres
    "wenmere": ("Wenmere", -24.500, 47.300, 4000),
    "station": ("Wenmere coach station and depot", -24.505, 47.302, 150),
    "holt_cross": ("Holt Cross depot", -24.290, 47.360, 300),
}

# ---- the drivers -----------------------------------------------------------------
# key: full name, gate log form, fuel card form, dispatch form, route, bus, shift,
# rest weekdays (0 is Monday), days with no named record (first, last).

DRIVERS = {
    "aron": (
        "Aron Vesk",
        "A. Vesk",
        "VESK A",
        "Vesk",
        "9",
        "14",
        "late",
        (4, 6),
        ("2026-03-13", "2026-05-25"),
    ),
    "mara": (
        "Mara Lind",
        "M. Lind",
        "LIND M",
        "Lind",
        "9",
        "11",
        "early",
        (2, 6),
        None,
    ),
    "ivo": (
        "Ivo Sand",
        "I. Sand",
        "SAND I",
        "Sand",
        "9",
        "12",
        "early",
        (5, 6),
        ("2026-02-20", "2026-05-31"),
    ),
    "nils": (
        "Nils Harrow",
        "N. Harrow",
        "HARROW N",
        "Harrow",
        "4",
        "7",
        "early",
        (2, 6),
        ("2026-04-01", "2026-05-29"),
    ),
    "leon": (
        "Leon Abbot",
        "L. Abbot",
        "ABBOT L",
        "Abbot",
        "4",
        "8",
        "late",
        (0, 6),
        ("2026-04-19", "2026-06-29"),
    ),
    "petra": (
        "Petra Coyle",
        "P. Coyle",
        "COYLE P",
        "Coyle",
        "12",
        "21",
        "early",
        (3, 6),
        ("2026-04-05", "2026-06-12"),
    ),
}
SHIFTS = {"early": ("05:40", "14:10"), "late": ("14:30", "23:50")}
# The night: Aron was called in for the 22:40 only, and the gate line for it was
# left without a driver. The 22:40 is the last departure of route 9.
NIGHT = date(2026, 3, 14)
ROSTER_LEAVE = {"petra": ("2026-04-06", "2026-06-12")}


def days():
    d = START
    while d <= END:
        yield d
        d += timedelta(days=1)


def off(key, d):
    gone = DRIVERS[key][8]
    return bool(gone) and date.fromisoformat(gone[0]) <= d <= date.fromisoformat(
        gone[1]
    )


def shifts():
    """Each named working day, (date, driver key). A few days off sick at random,
    never next to a planted edge."""
    rng = random.Random(SEED)
    edges = {
        date.fromisoformat(x) + timedelta(days=k)
        for _, *rest in DRIVERS.values()
        if rest[7]
        for x in rest[7]
        for k in (-3, -2, -1, 0, 1, 2, 3)
    }
    out = []
    for d in days():
        for key, row in DRIVERS.items():
            if d.weekday() in row[7] or off(key, d):
                continue
            if d not in edges and rng.random() < 0.03:
                continue
            out.append((d, key))
    return out


# ---- the sheets ------------------------------------------------------------------


def minutes(hhmm, jitter):
    h, m = map(int, hhmm.split(":"))
    t = h * 60 + m + jitter
    return f"{t // 60 % 24:02d}:{t % 60:02d}"


def gate_rows(worked):
    rng = random.Random(SEED + 1)
    rows = []
    for d, key in worked:
        name, gate, _, _, route, bus, shift, *_ = DRIVERS[key]
        out, back = SHIFTS[shift]
        rows.append(
            [
                d.isoformat(),
                bus,
                route,
                gate,
                minutes(out, rng.randint(-6, 8)),
                minutes(back, rng.randint(-10, 15)),
                "",
            ]
        )
    # Aron's late duty went out every working day of his silence with no name. On
    # the night a relief ran the late duty on a spare bus until the 22:40.
    for d in days():
        if d != NIGHT and off("aron", d) and d.weekday() not in DRIVERS["aron"][7]:
            rows.append(
                [
                    d.isoformat(),
                    "14",
                    "9",
                    "relief",
                    minutes("14:30", rng.randint(-6, 8)),
                    minutes("23:50", rng.randint(-10, 15)),
                    "",
                ]
            )
    rows.append([NIGHT.isoformat(), "15", "9", "relief", "14:34", "22:18", ""])
    rows.append([NIGHT.isoformat(), "14", "9", "", "22:41", "00:52", "22:40 only"])
    rows.sort(key=lambda r: (r[0], r[4], r[1]))
    return rows


def fuel_rows(worked):
    rng = random.Random(SEED + 2)
    rows = []
    for d, key in worked:
        if rng.random() < 0.45:
            _, _, card, _, _, bus, shift, *_ = DRIVERS[key]
            back = SHIFTS[shift][1]
            rows.append(
                [
                    d.isoformat(),
                    minutes(back, rng.randint(-40, -5)),
                    "P2",
                    bus,
                    f"{rng.uniform(95, 180):.1f}",
                    card,
                ]
            )
    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    return rows


def dispatch_rows(worked):
    """March only: one row per duty, signed out by the driver."""
    rows = []
    for d, key in worked:
        if d.month != 3:
            continue
        _, _, _, sign, route, bus, shift, *_ = DRIVERS[key]
        rows.append([d.isoformat(), route, shift, bus, sign])
    for d in days():
        if (
            d.month == 3
            and d != NIGHT
            and off("aron", d)
            and d.weekday() not in DRIVERS["aron"][7]
        ):
            rows.append([d.isoformat(), "9", "late", "14", "relief"])
    rows.append([NIGHT.isoformat(), "9", "late to 22:20", "15", "relief"])
    rows.append([NIGHT.isoformat(), "9", "22:40 only", "14", ""])
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return rows


def roster_rows():
    """The plan, week by week: E early, L late, R rest, LV leave. A plan is not a
    sighting, so the roster makes no claims."""
    rows = []
    monday = START
    while monday <= END:
        for key, (name, _, _, _, route, _, shift, rest, _) in DRIVERS.items():
            cells = []
            for k in range(7):
                d = monday + timedelta(days=k)
                leave = ROSTER_LEAVE.get(key)
                if leave and leave[0] <= d.isoformat() <= leave[1]:
                    cells.append("LV")
                elif d.weekday() in rest:
                    cells.append("R")
                else:
                    cells.append(f"{'E' if shift == 'early' else 'L'}{route}")
            rows.append([monday.isoformat(), name, *cells])
        monday += timedelta(days=7)
    return rows


def write_csv(name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    (FILES / name).write_text(buf.getvalue(), encoding="utf-8")


# ---- statements and records ------------------------------------------------------

SUPERVISOR = """WENMERE POLICE - WITNESS STATEMENT

Name: Edda Morrow
Occupation: Night supervisor, Wenmere coach station (Ostry Coaches)
Taken: Monday 16 March 2026, Wenmere police station

I was on duty at Wenmere coach station on Saturday 14 March 2026 from 18:00.
At about 22:35 a young woman in a green coat asked me which stand the 22:40 to
Holt Cross left from. I showed her stand 3. I have been shown a photograph of
Dana Kovar and I recognise her as that woman.

The 22:40 to Holt Cross is the last departure on route 9. That night it left on
time from stand 3, driven by Aron Vesk. He had been called in for that one run.
I saw the woman get on. That was the last I saw of either of them.

I closed the station at 23:10. I did not see bus 14 come back.

Signed: E. Morrow
"""

MANAGER = """OSTRY COACHES - NOTE FOR WENMERE POLICE

From: Colm Ferrier, depot manager, Wenmere
Date: Wednesday 18 March 2026

You asked who drives route 9. The route 9 lates are Aron Vesk's duty, bus 14.
Mara Lind drives the route 9 earlies on bus 11.

Ivo Sand drove route 9 earlies until February. I last saw him at the depot on
Friday 20 February, when he asked to move to our Holt Cross depot.

Rosters for March to June are attached. I have no record of anyone on route 9
being on leave in March.

C. Ferrier
"""

FLATMATE = """WENMERE POLICE - WITNESS STATEMENT

Name: Joan Teller
Taken: Sunday 15 March 2026, Wenmere police station

I share a flat in Wenmere with Dana Kovar. Dana is 24 and works as a nurse at
the clinic in Holt Cross. On Saturday 14 March she had a night shift starting at
23:30. She left the flat at about 22:10 to catch the 22:40 bus from the coach
station. She was wearing her green coat.

At 07:00 on Sunday the clinic called me to ask why she had not come in. Her phone
goes straight to voicemail. This is not like her.

Signed: J. Teller
"""

NOTICE = """WENMERE POLICE - MISSING PERSON

Dana Kovar, female, 24.
Missing since Saturday 14 March 2026.
Last seen at Wenmere coach station at about 22:35, boarding the 22:40 bus to
Holt Cross. Wearing a dark green wool coat.

Anyone who has seen her is asked to call Wenmere police.
Issued Monday 16 March 2026.
"""

CHARGES = """WENMERE POLICE - CHARGE SHEET EXTRACT, SPRING 2026

Wednesday 1 April 2026. Nils Harrow, bus driver. Arrested in Wenmere town centre
at 21:15, driving a private car over the limit. Charged. Licence suspended
pending a hearing.

Monday 20 April 2026. Leon Abbot, bus driver. Arrested in Wenmere at 23:40 after
a fight outside a bar. Charged with affray.
"""

BAIL = """WENMERE MAGISTRATES - BAIL RECORD

Leon Abbot. Released on bail on Tuesday 21 April 2026, 10:30, on condition
that he lives at his home address and does not drive for work until the
hearing.
"""

STAFF = """OSTRY COACHES - HOLT CROSS DEPOT STAFF LIST 2026

Drivers based at Holt Cross during 2026:
  Ivo Sand
  Rosa Penhale
  Tobias Quill
  Wen Achter
"""

TEXTS = {
    "manager_note.txt": MANAGER,
    "flatmate_statement.txt": FLATMATE,
    "missing_notice.txt": NOTICE,
    "charge_sheet.txt": CHARGES,
    "bail_record.txt": BAIL,
    "holt_cross_staff_2026.txt": STAFF,
    "supervisor_statement.txt": SUPERVISOR,
}

SOURCES = {  # key: title, document type, sensitivity, document date, file
    "gate": (
        "Wenmere depot gate log, February to June 2026",
        "gate_log",
        "ordinary",
        "2026-06-30",
        "gate_log.csv",
    ),
    "fuel": (
        "Wenmere depot fuel receipts, February to June 2026",
        "fuel_receipts",
        "ordinary",
        "2026-06-30",
        "fuel_receipts.csv",
    ),
    "dispatch": (
        "Route dispatch sheet, March 2026",
        "dispatch_sheet",
        "ordinary",
        "2026-03-31",
        "dispatch_march.csv",
    ),
    "roster": (
        "Shift roster, February to June 2026",
        "roster",
        "ordinary",
        "2026-01-26",
        "shift_roster.csv",
    ),
    "gatebook": (
        "Wenmere gate book, page for 14 March 2026",
        "gate_log",
        "ordinary",
        "2026-03-14",
        "gate_book_14_march.jpg",
    ),
    "supervisor": (
        "Statement of the station night supervisor",
        "testimony",
        "ordinary",
        "2026-03-16",
        "supervisor_statement.jpg",
    ),
    "manager": (
        "Depot manager's note to the police",
        "casework_note",
        "ordinary",
        "2026-03-18",
        "manager_note.txt",
    ),
    "flatmate": (
        "Statement of Dana Kovar's flatmate",
        "testimony",
        "ordinary",
        "2026-03-15",
        "flatmate_statement.txt",
    ),
    "notice": (
        "Missing person notice for Dana Kovar",
        "appeal",
        "ordinary",
        "2026-03-16",
        "missing_notice.txt",
    ),
    "photo": (
        "Photograph of Dana Kovar given to the police",
        "photo",
        "ordinary",
        "2026-03-15",
        "dana_kovar.jpg",
    ),
    "depot": (
        "Wenmere depot, the yard from the gate",
        "photo",
        "ordinary",
        None,
        "wenmere_depot.jpg",
    ),
    "charges": (
        "Wenmere police charge sheet extract, spring 2026",
        "charge_sheet",
        "sensitive",
        "2026-04-21",
        "charge_sheet.txt",
    ),
    "bail": (
        "Bail record for Leon Abbot",
        "release_certificate",
        "sensitive",
        "2026-04-21",
        "bail_record.txt",
    ),
    "staff": (
        "Holt Cross depot staff list 2026",
        "staff_list",
        "ordinary",
        "2026-01-05",
        "holt_cross_staff_2026.txt",
    ),
}

# The text of a scanned page, for a reader that does not run OCR. The gate book
# page is the 14 March part of gate_log.csv.
TRANSCRIPTS = {"supervisor": "supervisor_statement.txt"}

CLAIMS = []
RECORDS = {}


def claim(key, record, prop, value, start=None, end=None, place=None, event=None):
    CLAIMS.append(
        {
            "key": key,
            "record": record,
            "prop": prop,
            "value": value,
            "from": start,
            "to": end or start,
            "place": place,
            "tier": "unconfirmed",
        }
        | ({"event": event} if event else {})
    )


def record(key, source, person, name):
    RECORDS[key] = {"source": source, "person": person}
    if name:
        claim(f"{key}.name", key, "name", name)


def build(gate, fuel, dispatch):
    """One claim per log line that names a driver, on the line's day."""
    forms = {
        (src, DRIVERS[k][i]): k
        for k in DRIVERS
        for src, i in (("gate", 1), ("fuel", 2), ("dispatch", 3))
    }
    for src, rows, col, value in (
        ("gate", gate, 3, "Wenmere depot gate"),
        ("fuel", fuel, 5, "Wenmere depot pump P2"),
        ("dispatch", dispatch, 4, "Wenmere coach station"),
    ):
        for row in rows:
            key = forms.get((src, row[col]))
            if key is None:  # relief or blank: nobody named
                continue
            rec = f"{key}_{src}"
            if rec not in RECORDS:
                record(rec, src, key, row[col])
            claim(f"{rec}.{row[0]}", rec, "seen_at", value, row[0], place="station")

    record("dana_flatmate", "flatmate", "dana", "Dana Kovar")
    claim("dana_flatmate.age", "dana_flatmate", "age", "24")
    claim("dana_flatmate.occupation", "dana_flatmate", "occupation", "nurse")
    claim(
        "dana_flatmate.last_seen",
        "dana_flatmate",
        "last_seen",
        "the flat in Wenmere",
        "2026-03-14",
        place="wenmere",
    )
    record("dana_notice", "notice", "dana", "Dana Kovar")
    claim("dana_notice.sex", "dana_notice", "sex", "female")
    claim("dana_notice.age", "dana_notice", "age", "24")
    claim(
        "dana_notice.last_seen",
        "dana_notice",
        "last_seen",
        "Wenmere coach station",
        "2026-03-14",
        place="station",
    )
    claim(
        "dana_notice.missing",
        "dana_notice",
        "event",
        "Missing since Saturday 14 March 2026",
        "2026-03-14",
        place="station",
        event="missing",
    )
    record("dana_supervisor", "supervisor", "dana", "Dana Kovar")
    claim(
        "dana_supervisor.last_seen",
        "dana_supervisor",
        "last_seen",
        "stand 3, Wenmere coach station",
        "2026-03-14",
        place="station",
    )
    record("dana_photo", "photo", "dana", "Dana Kovar")

    record("aron_supervisor", "supervisor", "aron", "Aron Vesk")
    claim(
        "aron_supervisor.last_seen",
        "aron_supervisor",
        "last_seen",
        "stand 3, Wenmere coach station",
        "2026-03-14",
        place="station",
    )
    record("aron_manager", "manager", "aron", "Aron Vesk")
    claim(
        "aron_manager.occupation",
        "aron_manager",
        "occupation",
        "bus driver, route 9 lates",
    )
    record("ivo_manager", "manager", "ivo", "Ivo Sand")
    claim(
        "ivo_manager.last_seen",
        "ivo_manager",
        "last_seen",
        "the depot",
        "2026-02-20",
        place="station",
    )
    record("ivo_staff", "staff", "ivo", "Ivo Sand")
    claim(
        "ivo_staff.based",
        "ivo_staff",
        "seen_at",
        "Holt Cross depot",
        "2026-01-01",
        "2026-12-31",
        place="holt_cross",
    )
    record("nils_charges", "charges", "nils", "Nils Harrow")
    claim(
        "nils_charges.arrested",
        "nils_charges",
        "arrested_at",
        "Wenmere town centre",
        "2026-04-01",
        place="wenmere",
    )
    record("leon_charges", "charges", "leon", "Leon Abbot")
    claim(
        "leon_charges.arrested",
        "leon_charges",
        "arrested_at",
        "Wenmere",
        "2026-04-20",
        place="wenmere",
    )
    record("leon_bail", "bail", "leon", "Leon Abbot")
    claim(
        "leon_bail.released",
        "leon_bail",
        "event",
        "Released on bail",
        "2026-04-21",
        event="released",
    )


# ---- planted truth ---------------------------------------------------------------

GAPS = [  # record of the person, silence start, end (None: no record since)
    (
        "aron_gate",
        "2026-03-14",
        "2026-05-26",
        "last seen driving the 22:40 the night Dana vanished, then in no log for 73 days",
    ),
    ("dana_notice", "2026-03-14", None, "last seen boarding the 22:40, nothing since"),
]
NOT_GAPS = [
    (
        "nils_charges",
        "2026-04-01",
        "2026-05-30",
        "arrested, back on the gate log 59 days later: under the 60-day threshold",
    ),
    (
        "leon_charges",
        "2026-04-21",
        "2026-06-30",
        "off the logs 70 days, but a release on bail closes the silence",
    ),
    (
        "ivo_manager",
        "2026-02-20",
        "2026-06-01",
        "gone from the Wenmere logs 101 days, but a year-only record of him at Holt "
        "Cross ends on 31 December, so it may fall inside the silence",
    ),
    (
        "petra_gate",
        "2026-04-04",
        "2026-06-13",
        "70 days on recorded leave; no last-seen, arrest or missing account starts a "
        "watch on her, so the finder never measures this silence",
    ),
]
BATCH = ["supervisor", "manager", "flatmate"]  # the intake brief's batch

STORY = {
    "title": "The name nobody wrote down",
    "kind": "missing person",
    "puzzle": (
        "Dana Kovar, a nurse, boarded the last bus out of Wenmere on Saturday 14 March "
        "2026 and never reached her night shift in Holt Cross. The bus company's gate "
        "log, fuel receipts and dispatch sheet run to several hundred lines over five "
        "months, and nobody reading them saw anything wrong."
    ),
    "twist": (
        "Aron Vesk, who drove her bus, is named in every log until that night and in "
        "none for the next 73 days, though the roster kept him on route 9 and the gate "
        "line for his last run was left blank. The gap finder sets his silence beside "
        "the day Dana was last seen; a person decides what it means."
    ),
    "rests_on": ["gap finder", "timeline"],
    "fictional": True,
}


def truth():
    def rows(items):
        return [{"record": r, "from": a, "to": b, "why": w} for r, a, b, w in items]

    return {
        "same_person": [],
        "twins": [],
        "gaps": rows(GAPS),
        "not_gaps": rows(NOT_GAPS),
        "contradictions": [],
        "not_contradictions": [],
        "hard_same_person": [],
        "traps": [],
        "hard_contradictions": [],
        "trap_contradictions": [],
        "canary": {"pair": [], "contradiction": []},
    }


def check():
    keys = {c["key"] for c in CLAIMS}
    assert len(keys) == len(CLAIMS), "claim keys repeat"
    for item in GAPS + NOT_GAPS:
        assert item[0] in RECORDS, item
    days_ = {
        r: (date.fromisoformat(b) - date.fromisoformat(a)).days
        for r, a, b, _ in GAPS + NOT_GAPS
        if b
    }
    assert days_["aron_gate"] == 73 and days_["nils_charges"] == 59, days_


# ---- scanned pages ---------------------------------------------------------------

PAGE = {"width": 1240, "height": 1754}  # A4 at 150 dpi
HAND = '"Bradley Hand", "Segoe Script", cursive'


def gatebook_html(gate):
    rows = [r for r in gate if r[0] == NIGHT.isoformat()]
    body = "".join(
        f"<tr><td>{r[1]}</td><td>{r[2]}</td><td class=h>{r[3]}</td>"
        f"<td class=h>{r[4]}</td><td class=h>{r[5]}</td><td class=h>{r[6]}</td></tr>"
        for r in rows
    )
    blank = "<tr><td></td><td></td><td></td><td></td><td></td><td></td></tr>" * (
        18 - len(rows)
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><style>
body{{margin:0;background:#fbfaf5;font-family:Georgia,serif;color:#1d1d1d}}
.page{{width:1240px;height:1754px;padding:90px;box-sizing:border-box;position:relative}}
h1{{font-size:34px;margin:0 0 6px;letter-spacing:2px}} .sub{{font-size:22px;margin-bottom:34px}}
table{{width:100%;border-collapse:collapse;font-size:24px}}
th,td{{border:1.5px solid #555;height:62px;padding:0 10px;text-align:left}}
th{{background:#ecebe4;font-size:20px;text-transform:uppercase;letter-spacing:1px}}
.h{{font-family:{HAND};font-size:30px;color:#1f2f7a}}
.date{{font-family:{HAND};font-size:34px;color:#1f2f7a}}
.foot{{position:absolute;bottom:90px;left:90px;right:90px;font-size:20px;color:#444}}
.syn{{position:absolute;bottom:40px;left:0;right:0;text-align:center;font-size:16px;color:#888;font-family:Arial}}
</style><body><div class="page">
<h1>OSTRY COACHES · WENMERE DEPOT</h1>
<div class="sub">Gate book. Every vehicle in or out is entered with its driver.
Date: <span class="date">Sat 14 March 2026</span></div>
<table><tr><th>Bus</th><th>Route</th><th>Driver</th><th>Out</th><th>In</th><th>Note</th></tr>
{body}{blank}</table>
<div class="foot">Gate officer to sign each page. Page 73.</div>
<div class="syn">{FICTIONAL}</div>
</div></body></html>"""


def statement_html():
    paras = SUPERVISOR.strip().split("\n\n")
    head, rest = paras[0], paras[1:-1]
    lines = "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paras[1:2])
    text = "".join(f"<p>{p.replace(chr(10), ' ')}</p>" for p in rest[1:])
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><style>
body{{margin:0;background:#fff;font-family:"Courier New",monospace;color:#222}}
.page{{width:1240px;height:1754px;padding:110px;box-sizing:border-box;position:relative}}
h1{{font-family:Arial;font-size:30px;letter-spacing:3px;border-bottom:3px solid #222;padding-bottom:10px}}
.meta p,.body p{{font-size:25px;line-height:1.55;margin:0 0 22px}}
.body{{margin-top:40px}}
.sig{{margin-top:70px;font-size:25px}} .sig span{{font-family:{HAND};font-size:44px;color:#183a8a;margin-left:20px}}
.syn{{position:absolute;bottom:40px;left:0;right:0;text-align:center;font-size:16px;color:#888;font-family:Arial}}
</style><body><div class="page">
<h1>{head}</h1><div class="meta">{lines}</div>
<div class="body">{text}</div>
<div class="sig">Signed:<span>E. Morrow</span></div>
<div class="syn">{FICTIONAL}</div>
</div></body></html>"""


def render_scans(gate):
    sys.path.insert(0, str(HERE.parents[2]))
    from PIL import Image
    from playwright.sync_api import sync_playwright

    from acceptance.make_case import scan_realism

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, html, seed in (
            ("gate_book_14_march.jpg", gatebook_html(gate), SEED + 3),
            ("supervisor_statement.jpg", statement_html(), SEED + 4),
        ):
            page = browser.new_page(viewport=PAGE)
            page.set_content(html)
            page.evaluate("document.fonts.ready")
            raw = FILES / f"{name}.raw.png"
            page.screenshot(path=raw)
            page.close()
            img, _, _ = scan_realism(Image.open(raw), {}, random.Random(seed))
            raw.unlink()
            img.save(FILES / name, quality=82)
        browser.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-scans", action="store_true", help="skip the two scans")
    args = ap.parse_args()
    FILES.mkdir(exist_ok=True)
    worked = shifts()
    gate, fuel, dispatch = gate_rows(worked), fuel_rows(worked), dispatch_rows(worked)
    write_csv(
        "gate_log.csv", ["date", "bus", "route", "driver", "out", "in", "note"], gate
    )
    write_csv(
        "fuel_receipts.csv", ["date", "time", "pump", "bus", "litres", "card"], fuel
    )
    write_csv(
        "dispatch_march.csv",
        ["date", "route", "duty", "bus", "signed out by"],
        dispatch,
    )
    write_csv(
        "shift_roster.csv",
        ["week of", "driver", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        roster_rows(),
    )
    for name, text in TEXTS.items():
        (FILES / name).write_text(text + "\n" + FICTIONAL + "\n", encoding="utf-8")
    build(gate, fuel, dispatch)
    check()
    clusters = [
        sorted(r for r, v in RECORDS.items() if v["person"] == p)
        for p in sorted({v["person"] for v in RECORDS.values()})
    ]
    case = {
        "about": "Planted case and truth for the case study 'The name nobody wrote "
        "down', by acceptance/cases/unwritten-name/make.py. All content is fictional.",
        "story": STORY,
        "investigation": INVESTIGATION,
        "today": TODAY,
        "places": {
            k: {"name_en": n, "name_ar": n, "lon": lon, "lat": lat, "precision_m": m}
            for k, (n, lon, lat, m) in PLACES.items()
        },
        "sources": {
            k: {"title": t, "doc_type": d, "sensitivity": s, "date": dt, "file": f}
            | ({"transcript": TRANSCRIPTS[k]} if k in TRANSCRIPTS else {})
            for k, (t, d, s, dt, f) in SOURCES.items()
        },
        "records": RECORDS,
        "clusters": [c for c in clusters if len(c) > 1],
        "claims": CLAIMS,
        "batch": BATCH,
        "truth": truth(),
    }
    (HERE / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    if not args.no_scans:
        render_scans(gate)
    print(
        f"{len(CLAIMS)} claims, {len(RECORDS)} records, {len(gate)} gate lines, "
        f"{len(fuel)} fuel receipts, {len(dispatch)} dispatch rows"
    )


if __name__ == "__main__":
    main()
