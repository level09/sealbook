"""Generate the agent evaluation case and its planted truth into acceptance/agents/.

Everything here is invented. P1 and P2 come from acceptance/case.json; the rest are
new synthetic people. The case is `case-agent-eval`, kept apart from the demo cases
and never seeded. Run: uv run python acceptance/make_agent_case.py
"""

import json
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "agents"
INVESTIGATION = "case-agent-eval"
TODAY = "2016-01-01"  # the gap finder's "now" for open-ended silences
CASE = json.loads((ROOT / "case.json").read_text(encoding="utf-8"))
P1, P2 = CASE["persons"]["P1"], CASE["persons"]["P2"]

# ---- places, as an analyst confirmed them --------------------------------------

PLACES = {  # key: English, Arabic, lon, lat, precision in metres
    "harasta": ("Harasta", "حرستا", 36.365, 33.558, 2000),
    "douma": ("Douma", "دوما", 36.402, 33.571, 3000),
    "qaboun": ("Qaboun", "القابون", 36.330, 33.540, 1500),
    "jdeideh": ("Jdeideh", "حي الجديدة", 36.300, 33.520, 1000),
    "homs": ("Homs", "حمص", 36.720, 34.730, 5000),
    # Fictional facilities, at invented coordinates.
    "branch999": ("Branch 999", "الفرع 999", 36.270, 33.510, 200),
    "prisonk": ("Central Prison K", "سجن ك المركزي", 36.200, 33.450, 300),
}

# ---- sources -----------------------------------------------------------------

SOURCES = {  # key: title, document type, sensitivity, document date
    "reg14": (
        "Branch 999 register, page 14",
        "detention_register",
        "sensitive",
        "2013-05-03",
    ),
    "post": (
        "Missing person post for Yousef",
        "social_media_post",
        "ordinary",
        "2013-05-10",
    ),
    "family": ("Al-Ali family statement", "testimony", "ordinary", "2014-01-20"),
    "list": (
        "Branch 999 detainee list, February 2014",
        "detainee_list",
        "sensitive",
        "2014-03-01",
    ),
    "note": ("Casework note on Branch 999", "casework_note", "ordinary", "2014-03-05"),
    "intake": (
        "Branch 999 intake sheet, February 2014",
        "intake_sheet",
        "sensitive",
        "2014-02-21",
    ),
    "school": (
        "School record for Lina Al-Najjar",
        "school_record",
        "ordinary",
        "2014-03-10",
    ),
    "roster": ("Dormitory 4 roster, July 2013", "roster", "sensitive", "2013-07-31"),
    "testimony": (
        "Testimony of a former detainee",
        "testimony",
        "sensitive",
        "2014-05-01",
    ),
    "transfer": ("Transfer order 4471", "transfer_order", "sensitive", "2013-09-17"),
    "appeal": ("Family appeal for Omar Said", "appeal", "ordinary", "2015-01-01"),
    "harasta_reg": (
        "Harasta police register",
        "detention_register",
        "sensitive",
        "2014-01-11",
    ),
    "witness": (
        "Witness statement on Central Prison K",
        "testimony",
        "ordinary",
        "2013-09-01",
    ),
    "release": (
        "Release certificates, 2013 to 2014",
        "release_certificate",
        "ordinary",
        "2014-04-02",
    ),
    "huda_post": (
        "Missing person post for Huda",
        "social_media_post",
        "ordinary",
        "2014-01-12",
    ),
    "reg31": (
        "Branch 999 register, page 31",
        "detention_register",
        "sensitive",
        "2013-06-02",
    ),
    "witness2": (
        "Witness statement on Branch 999",
        "testimony",
        "ordinary",
        "2014-04-01",
    ),
    "homslog": ("Homs checkpoint log", "checkpoint_log", "sensitive", "2014-01-02"),
    "visitors": (
        "Visitor log, Central Prison K",
        "visitor_log",
        "ordinary",
        "2014-02-02",
    ),
    "maya_appeal": (
        "Family appeal for Maya Hamdan",
        "appeal",
        "ordinary",
        "2015-02-05",
    ),
    "witness3": ("Witness statement, June 2015", "testimony", "ordinary", "2015-06-20"),
}

# ---- records: one person as one source names them ------------------------------
# key: (source, real person). Two records of one real person in different clusters
# are a pair the linker should find; namesakes below must never be joined.

RECORDS = {
    "yousef_reg": ("reg14", "P1"),
    "twin_reg": ("reg14", "P2"),
    "yousef_post": ("post", "P1"),
    "yousef_family": ("family", "P1"),
    "lina_list": ("list", "lina"),
    "lina_note": ("note", "lina"),
    "lina_school": ("school", "lina_twin"),
    "khatib_list": ("list", "khatib"),
    "khatib_note": ("note", "khatib"),
    "khatib_intake": ("intake", "khatib_twin"),
    "bassel_roster": ("roster", "bassel"),
    "bassel_testimony": ("testimony", "bassel"),
    "omar_transfer": ("transfer", "omar"),
    "omar_appeal": ("appeal", "omar"),
    "rami_reg": ("harasta_reg", "rami"),
    "rami_witness": ("witness", "rami"),
    "rami_release": ("release", "rami"),
    "huda_post": ("huda_post", "huda"),
    "huda_reg": ("harasta_reg", "huda"),
    "huda_note": ("note", "huda"),
    "nour_reg": ("reg31", "nour"),
    "nour_witness": ("witness2", "nour"),
    "nour_log": ("homslog", "nour"),
    "nour_release": ("release", "nour"),
    "sami_visitors": ("visitors", "sami"),
    "maya_appeal": ("maya_appeal", "maya"),
    "maya_witness": ("witness3", "maya"),
}
NAMESAKES = [("P1", "P2"), ("lina", "lina_twin"), ("khatib", "khatib_twin")]
# Same-person decisions already accepted in the case: one cluster each.
CLUSTERS = [
    ["rami_reg", "rami_witness", "rami_release"],
    ["huda_post", "huda_reg", "huda_note"],
    ["nour_reg", "nour_witness", "nour_log", "nour_release"],
    ["maya_appeal", "maya_witness"],
]

CLAIMS = []


def claim(
    key,
    record,
    prop,
    value,
    start=None,
    end=None,
    place=None,
    tier="unconfirmed",
    event=None,
):
    CLAIMS.append(
        {
            "key": key,
            "record": record,
            "prop": prop,
            "value": value,
            "from": start,
            "to": end or start,
            "place": place,
            "tier": tier,
        }
        | ({"event": event} if event else {})
    )


# The linker set: cross-script spellings, a cross-script parent, and namesakes.
claim("yousef_reg.name", "yousef_reg", "name", P1["spellings"][0])
claim("yousef_reg.father", "yousef_reg", "father_name", P1["father_name"])
claim("yousef_reg.mother", "yousef_reg", "mother_name", P1["mother_name"])
claim("yousef_reg.born", "yousef_reg", "birth_date", P1["birth_date"][:4])
claim(
    "yousef_reg.arrested",
    "yousef_reg",
    "arrested_at",
    "حرستا",
    "2013-05-02",
    place="harasta",
)
claim(
    "yousef_reg.branch",
    "yousef_reg",
    "detention_branch",
    "الفرع 999",
    place="branch999",
)
claim("twin_reg.name", "twin_reg", "name", P2["spellings"][0])
claim("twin_reg.father", "twin_reg", "father_name", P2["father_name"])
claim("twin_reg.mother", "twin_reg", "mother_name", P2["mother_name"])
claim("twin_reg.born", "twin_reg", "birth_date", P2["birth_date"][:4])
claim(
    "twin_reg.arrested",
    "twin_reg",
    "arrested_at",
    "القابون",
    "2013-05-02",
    place="qaboun",
)
claim("yousef_post.name", "yousef_post", "name", P1["spellings"][1])
claim(
    "yousef_post.last_seen",
    "yousef_post",
    "last_seen",
    "دوما",
    "2013-05-02",
    place="douma",
)
claim("yousef_family.name", "yousef_family", "name", P1["spellings"][2])
claim("yousef_family.born", "yousef_family", "birth_date", P1["birth_date"])
claim("yousef_family.father", "yousef_family", "father_name", "Khaled")
claim("yousef_family.mother", "yousef_family", "mother_name", "Fatima")
claim(
    "yousef_family.last_seen",
    "yousef_family",
    "last_seen",
    "Douma",
    "2013-05-02",
    place="douma",
)

claim("lina_list.name", "lina_list", "name", "لينا عمر النجار")
claim("lina_list.born", "lina_list", "birth_date", "1994")
claim(
    "lina_list.held",
    "lina_list",
    "detained_at",
    "الفرع 999",
    "2014-02-11",
    place="branch999",
)
claim("lina_note.name", "lina_note", "name", "Lina Al-Najjar")
claim("lina_note.born", "lina_note", "birth_date", "1994")
claim(
    "lina_note.last_seen",
    "lina_note",
    "last_seen",
    "Jdeideh",
    "2014-02-11",
    place="jdeideh",
)
claim("lina_school.name", "lina_school", "name", "Lina Al-Najjar")
claim("lina_school.born", "lina_school", "birth_date", "2001-09-05")
claim("lina_school.mother", "lina_school", "mother_name", "Huda")
claim(
    "lina_school.last_seen",
    "lina_school",
    "last_seen",
    "Jdeideh",
    "2014-02-11",
    place="jdeideh",
)

claim("khatib_list.name", "khatib_list", "name", "محمد احمد الخطيب")
claim("khatib_list.born_in", "khatib_list", "place_of_birth", "دوما")
claim(
    "khatib_list.held",
    "khatib_list",
    "detained_at",
    "الفرع 999",
    "2014-02-01",
    "2014-02-28",
    place="branch999",
)
claim("khatib_note.name", "khatib_note", "name", "Mohamad al Khatib")
claim(
    "khatib_note.held",
    "khatib_note",
    "detained_at",
    "Branch 999",
    "2014-02-15",
    place="branch999",
)
claim("khatib_intake.name", "khatib_intake", "name", "Mohammed Ahmed Al-Khatib")
claim("khatib_intake.born_in", "khatib_intake", "place_of_birth", "Aleppo")
claim(
    "khatib_intake.held",
    "khatib_intake",
    "detained_at",
    "Branch 999",
    "2014-02-20",
    place="branch999",
)

claim("bassel_roster.name", "bassel_roster", "name", "Bassel Nader")
claim(
    "bassel_roster.held",
    "bassel_roster",
    "detained_at",
    "Branch 999",
    "2013-07-01",
    "2013-07-31",
    place="branch999",
)
claim("bassel_testimony.name", "bassel_testimony", "name", "باسل نادر")
claim(
    "bassel_testimony.held",
    "bassel_testimony",
    "detained_at",
    "الفرع 999",
    "2013-07-01",
    "2013-07-31",
    place="branch999",
)

claim("omar_transfer.name", "omar_transfer", "name", "عمر سعيد")
claim("omar_transfer.born", "omar_transfer", "birth_date", "1980")
claim(
    "omar_transfer.moved",
    "omar_transfer",
    "transferred_to",
    "سجن ك المركزي",
    "2013-09-17",
    place="prisonk",
)
claim("omar_appeal.name", "omar_appeal", "name", "Omar Said")
claim("omar_appeal.born", "omar_appeal", "birth_date", "1980-06-01")

# The gap and contradiction set: people whose records are already joined.
claim("rami_reg.name", "rami_reg", "name", "رامي حداد", tier="accepted")
claim("rami_reg.born", "rami_reg", "birth_date", "1979", tier="accepted")
claim("rami_reg.mother", "rami_reg", "mother_name", "فاطمة", tier="accepted")
claim(
    "rami_reg.arrested",
    "rami_reg",
    "arrested_at",
    "حرستا",
    "2013-03-01",
    place="harasta",
    tier="accepted",
)
claim("rami_witness.name", "rami_witness", "name", "Rami Haddad")
claim("rami_witness.born", "rami_witness", "birth_date", "1983")
claim("rami_witness.mother", "rami_witness", "mother_name", "Fatima")
claim(
    "rami_witness.arrested",
    "rami_witness",
    "arrested_at",
    "Harasta",
    "2013-03-01",
    place="harasta",
)
claim(
    "rami_witness.seen",
    "rami_witness",
    "seen_at",
    "Central Prison K",
    "2013-08-15",
    place="prisonk",
)
claim(
    "rami_release.released",
    "rami_release",
    "event",
    "Released",
    "2013-09-01",
    event="released",
)

claim("huda_post.name", "huda_post", "name", "Huda Saleh")
claim("huda_post.mother", "huda_post", "mother_name", "Amal")
claim(
    "huda_post.last_seen",
    "huda_post",
    "last_seen",
    "Douma",
    "2014-01-10",
    place="douma",
)
claim("huda_reg.name", "huda_reg", "name", "هدى صالح")
claim(
    "huda_reg.arrested",
    "huda_reg",
    "arrested_at",
    "حرستا",
    "2014-01-10",
    place="harasta",
)
claim("huda_note.name", "huda_note", "name", "Huda Saleh")
claim("huda_note.mother", "huda_note", "mother_name", "Samira")

claim("nour_reg.name", "nour_reg", "name", "نور قاسم", tier="accepted")
claim("nour_reg.born", "nour_reg", "birth_date", "1990", tier="accepted")
claim(
    "nour_reg.held",
    "nour_reg",
    "detained_at",
    "الفرع 999",
    "2013-06-01",
    place="branch999",
)
claim("nour_witness.name", "nour_witness", "name", "Nour Kassem")
claim("nour_witness.born", "nour_witness", "birth_date", "1990-04-02")
claim(
    "nour_witness.seen",
    "nour_witness",
    "seen_at",
    "Branch 999",
    "2013-07-30",
    place="branch999",
)
claim(
    "nour_witness.arrested",
    "nour_witness",
    "arrested_at",
    "Harasta",
    "2014-01-01",
    place="harasta",
)
claim(
    "nour_witness.seen_k",
    "nour_witness",
    "seen_at",
    "Central Prison K",
    "2014-03-15",
    place="prisonk",
)
claim("nour_log.name", "nour_log", "name", "Nour Kassem")
claim(
    "nour_log.arrested", "nour_log", "arrested_at", "Homs", "2014-01-01", place="homs"
)
claim(
    "nour_release.released",
    "nour_release",
    "event",
    "Released",
    "2014-04-01",
    event="released",
)

claim("sami_visitors.name", "sami_visitors", "name", "Sami Yassin")
claim(
    "sami_visitors.held",
    "sami_visitors",
    "detained_at",
    "Branch 999",
    "2013-01-01",
    "2013-12-31",
    place="branch999",
)
claim(
    "sami_visitors.seen",
    "sami_visitors",
    "seen_at",
    "Central Prison K",
    "2014-02-01",
    place="prisonk",
)

claim("maya_appeal.name", "maya_appeal", "name", "Maya Hamdan")
claim(
    "maya_appeal.missing",
    "maya_appeal",
    "event",
    "Missing",
    "2015-02-01",
    event="missing",
)
claim("maya_witness.name", "maya_witness", "name", "Maya Hamdan")
claim(
    "maya_witness.seen",
    "maya_witness",
    "seen_at",
    "Central Prison K",
    "2015-06-10",
    place="prisonk",
)

# ---- planted truth -----------------------------------------------------------

GAPS = [  # record of the person, silence start, end (None: no record since)
    ("rami_reg", "2013-03-01", "2013-08-15", "arrested, next seen at Central Prison K"),
    (
        "huda_reg",
        "2014-01-10",
        None,
        "arrested the day she was last seen, nothing since",
    ),
    ("nour_reg", "2014-01-01", "2014-03-15", "re-arrested, next seen 73 days later"),
    (
        "maya_appeal",
        "2015-02-01",
        "2015-06-10",
        "reported missing, seen 129 days later",
    ),
]
NOT_GAPS = [
    ("nour_reg", "2013-06-01", "2013-07-30", "59 days, under the 60-day threshold"),
    (
        "sami_visitors",
        "2013-12-31",
        "2014-02-01",
        "a year-only detention ends at its latest date",
    ),
    ("rami_reg", "2013-09-01", None, "a release closes the record"),
    ("nour_reg", "2014-04-01", None, "a release closes the record"),
]
CONTRADICTIONS = [
    (["rami_reg.born", "rami_witness.born"], "birth year 1979 against 1983"),
    (["huda_post.mother", "huda_note.mother"], "mother Amal against Samira"),
    (
        ["nour_witness.arrested", "nour_log.arrested"],
        "arrested in Harasta and in Homs on one day",
    ),
]
NOT_CONTRADICTIONS = [
    (["rami_reg.arrested", "rami_witness.arrested"], "one place in two scripts"),
    (
        ["huda_post.last_seen", "huda_reg.arrested"],
        "last seen and arrested on the same day",
    ),
    (["nour_reg.born", "nour_witness.born"], "a year against a full date in that year"),
    (["rami_reg.mother", "rami_witness.mother"], "one name in two scripts"),
]
BATCH = ["witness", "witness2"]  # the intake brief's batch


def truth():
    cluster = {r: min(c) for c in CLUSTERS for r in c}
    pairs = [
        sorted((a, b))
        for a, b in combinations(RECORDS, 2)
        if cluster.get(a, a) != cluster.get(b, b)
    ]
    who = {r: person for r, (_, person) in RECORDS.items()}
    twins = {frozenset(n) for n in NAMESAKES}
    return {
        "same_person": [p for p in pairs if who[p[0]] == who[p[1]]],
        "twins": [p for p in pairs if frozenset((who[p[0]], who[p[1]])) in twins],
        "gaps": [{"record": r, "from": a, "to": b, "why": w} for r, a, b, w in GAPS],
        "not_gaps": [
            {"record": r, "from": a, "to": b, "why": w} for r, a, b, w in NOT_GAPS
        ],
        "contradictions": [{"claims": c, "why": w} for c, w in CONTRADICTIONS],
        "not_contradictions": [{"claims": c, "why": w} for c, w in NOT_CONTRADICTIONS],
    }


def main():
    OUT.mkdir(exist_ok=True)
    keys = {c["key"] for c in CLAIMS}
    assert len(keys) == len(CLAIMS), "claim keys repeat"
    for item in CONTRADICTIONS + NOT_CONTRADICTIONS:
        assert set(item[0]) <= keys, item
    case = {
        "about": "Planted truth for acceptance/eval_agents.py, by acceptance/make_agent_case.py. All content is synthetic.",
        "investigation": INVESTIGATION,
        "today": TODAY,
        "places": {
            k: {"name_en": en, "name_ar": ar, "lon": lon, "lat": lat, "precision_m": m}
            for k, (en, ar, lon, lat, m) in PLACES.items()
        },
        "sources": {
            k: {"title": t, "doc_type": d, "sensitivity": s, "date": dt}
            for k, (t, d, s, dt) in SOURCES.items()
        },
        "records": {k: {"source": s, "person": p} for k, (s, p) in RECORDS.items()},
        "clusters": CLUSTERS,
        "claims": CLAIMS,
        "batch": BATCH,
        "truth": truth(),
    }
    (OUT / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
