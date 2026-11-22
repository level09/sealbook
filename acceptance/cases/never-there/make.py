"""Generate the case study "The witness who was never there" into this directory.

A fictional fire at a joinery workshop in the invented town of Larkhaven. Forty-one
statements and twelve other items; one alibi fails on a photograph's recorded
place and time and a voice note's time. Every person, place, business and
coordinate is invented; the coordinates lie in open ocean so no real place is the
scene. The planted truth is scored by acceptance/eval_agents.py.

    uv run python acceptance/cases/never-there/make.py            # case.json
    uv run python acceptance/cases/never-there/make.py --photo P  # tag picture P
    uv run python acceptance/cases/never-there/make.py --voice    # speak the note
    uv run python acceptance/cases/never-there/make.py --scans    # render the scans
    uv run python acceptance/cases/never-there/make.py --record   # record the reading

case.json is byte-stable. `--photo` copies an edited picture to files/IMG_4471.jpg
and writes its EXIF time and GPS with exiftool. `--voice` speaks the voice note
through `make_case.speak` (OpenAI gpt-audio on OpenRouter, OPENROUTER_API_KEY) into
files/voice_note.ogg and its sentence times into files/voice_note.json. Both media
steps run once; their outputs are committed.

The case is read by the app, not planted: every source is marked `read`, so the
seeder runs the worker's own OCR, speech to text, extraction and first pass on it,
replaying each model answer from `recordings.json`. Four papers (Mara's and Jo's
statements and the two exhibit sheets) are scanned pages rendered by `--scans`;
the rest stay typed text. `--record` reads the case once on the configured routes
(cloud-zdr, OPENROUTER_API_KEY, and the local speech model) in a throwaway
database and writes `recordings.json`; never edit that file by hand. The planted
claims stay the truth `acceptance/eval_agents.py` scores.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
FILES = HERE / "files"
INVESTIGATION = "case-never-there"
TODAY = "2026-04-01"  # the gap finder's "now", two weeks after the arrest
DAY = "2026-03-14"  # the night of the fire

STORY = {
    "title": "The witness who was never there",
    "kind": "private crime scene",
    "puzzle": (
        "Hollin Joinery on Tanner's Row in Larkhaven burned on the night of 14 March "
        "2026, and forty-one people gave statements. The bookkeeper's alibi is one "
        "of the plainest: she was at her sister's house in Brindle Cross, 34 km "
        "away, from six until past midnight, and her sister agrees she came."
    ),
    "twist": (
        "The photograph she handed over to show an earlier break-in was taken at "
        "the workshop gate at 21:52 that night, and the voice note she sent her "
        "sister at 22:07 says she is still at the yard. The contradiction hunter "
        "puts her two places on one day side by side; a person reads the times and "
        "decides."
    ),
    "rests_on": ["contradictions", "evidence location", "speech to text"],
    "fictional": True,
}

# ---- places, as an analyst confirmed them --------------------------------------
# Invented coordinates in the South Atlantic. Brindle Cross is 34 km east of
# Larkhaven; the Larkhaven places sit inside the town's 3 km precision.

PLACES = {  # key: name, lon, lat, precision in metres
    "larkhaven": ("Larkhaven", -27.48, -31.62, 3000),
    "tanners_row": ("Tanner's Row, Larkhaven", -27.47652, -31.62154, 150),
    "market": ("Larkhaven market", -27.48295, -31.61702, 100),
    "crown": ("The Crown and Anchor, Larkhaven", -27.48, -31.62407, 50),
    "quay": ("Larkhaven quay", -27.47367, -31.61548, 200),
    "depot": ("Ferry Road bus depot, Larkhaven", -27.48738, -31.62271, 100),
    "brindle": ("Brindle Cross", -27.12133, -31.62, 2500),
    "brindle_bus": ("Brindle Cross bus station", -27.11974, -31.61891, 80),
}
# Where the photograph's GPS puts the phone: the workshop's back gate.
PHOTO_GPS = (-27.47641, -31.62162)
PHOTO_TIME = "2026:03:14 21:52:07"
PHOTO_OFFSET = "-02:00"

# ---- the planted people ----------------------------------------------------------
# Each source is (title, document type, sensitivity, date, text). Claims follow
# with every value quoted from its source's text, as extraction would.

SOURCES: dict = {}
RECORDS: dict = {}
CLAIMS: list = []


def source(
    key, title, doc_type, date, text, sensitivity="ordinary", file=None, transcript=None
):
    SOURCES[key] = (
        {
            "title": title,
            "doc_type": doc_type,
            "sensitivity": sensitivity,
            "date": date,
            "text": text,
        }
        | ({"file": file} if file else {})
        | ({"transcript": transcript} if transcript else {})
    )


def claim(
    key,
    record,
    prop,
    value,
    start=None,
    end=None,
    place=None,
    tier=None,
    after=None,
    time=None,
):
    """`after`: text the quote follows, when the value first appears about
    someone or somewhere else. `time`: the time of day or range the source gives
    for a place ("21:52", "18:00-24:00")."""
    CLAIMS.append(
        {
            "key": key,
            "record": record,
            "prop": prop,
            "value": value,
            "from": start,
            "to": end or start,
            "place": place,
            "tier": tier or "unconfirmed",
        }
        | ({"time": time} if time else {})
        | ({"pin": {"text": value, "after": after}} if after else {})
    )


def record(key, src, person):
    RECORDS[key] = {"source": src, "person": person}


# The bookkeeper and her alibi.
source(
    "mara",
    "Statement of Mara Quill: with her sister in Brindle Cross from 18:00 to past "
    "midnight on 14 March",
    "witness_statement",
    "2026-03-16",
    "Statement of Mara Quill, bookkeeper at Hollin Joinery, taken on 16 March 2026.\n\n"
    "My name is Mara Quill. I was born in 1981. I keep the books for Hollin Joinery. "
    "On Friday 13 March 2026, early in the morning, I found the back gate on "
    "Tanner's Row forced and took a photograph of it on my phone, which I have "
    "handed over. On Saturday 14 March 2026 I drove to my sister Jo's house in "
    "Brindle Cross. I was there from about six in the evening until well past "
    "midnight and stayed the night. I heard about the fire on Sunday morning.\n",
)
record("mara_stmt", "mara", "mara")
claim("mara_stmt.name", "mara_stmt", "name", "Mara Quill", tier="accepted")
claim("mara_stmt.born", "mara_stmt", "birth_date", "1981")
claim(
    "mara_stmt.gate",
    "mara_stmt",
    "seen_at",
    "Tanner's Row",
    "2026-03-13",
    place="tanners_row",
)
claim(
    "mara_stmt.at",
    "mara_stmt",
    "seen_at",
    "Brindle Cross",
    DAY,
    place="brindle",
    tier="accepted",
    time="18:00-24:00",
)

source(
    "jo",
    "Statement of Jo Quill, sister of Mara Quill",
    "witness_statement",
    "2026-03-17",
    "Statement of Jo Quill, taken on 17 March 2026.\n\n"
    "My name is Jo Quill. I was born in 1978. On Saturday 14 March 2026 I was at "
    "home in Brindle Cross all evening. My sister Mara Quill came to dinner at my "
    "house in Brindle Cross that night. She got here late, about half past "
    "eleven; she said the traffic was bad. She stayed the night.\n",
)
record("jo_stmt", "jo", "jo")
record("mara_jo", "jo", "mara")
claim("jo_stmt.name", "jo_stmt", "name", "Jo Quill")
claim("jo_stmt.born", "jo_stmt", "birth_date", "1978")
claim(
    "jo_stmt.at",
    "jo_stmt",
    "seen_at",
    "Brindle Cross",
    DAY,
    place="brindle",
    time="18:00-24:00",
)
claim("mara_jo.name", "mara_jo", "name", "Mara Quill")
claim(
    "mara_jo.at",
    "mara_jo",
    "seen_at",
    "Brindle Cross",
    DAY,
    place="brindle",
    after="My sister Mara Quill",
    time="23:30",
)

# Her phone: the exhibit sheets the hunter reads, and the media they describe.
source(
    "mq3",
    "Exhibit MQ/3: photograph from Mara Quill's phone, taken 14 March 2026 at 21:52",
    "exhibit_sheet",
    "2026-03-20",
    "Exhibit MQ/3. Photograph IMG_4471.jpg, handed over by Mara Quill on 16 March "
    "2026 from her phone. She said she took it early on 13 March.\n\n"
    "Embedded metadata: taken 14 March 2026 at 21:52:07 (UTC-2); GPS position "
    "with a stated error of 8 metres at the back gate of Hollin Joinery. The "
    "metadata places the phone, and so Mara Quill, on Tanner's Row, Larkhaven on "
    "14 March 2026. The picture shows the back gate at night, lit by the phone's "
    "flash.\n",
)
record("mara_mq3", "mq3", "mara")
claim("mara_mq3.name", "mara_mq3", "name", "Mara Quill")
claim(
    "mara_mq3.at",
    "mara_mq3",
    "seen_at",
    "Tanner's Row, Larkhaven",
    DAY,
    place="tanners_row",
    time="21:52",
)
source(
    "mq4",
    "Exhibit MQ/4: voice note from Mara Quill's phone, sent 14 March 2026 at 22:07",
    "exhibit_sheet",
    "2026-03-20",
    "Exhibit MQ/4. Voice note sent from Mara Quill's phone to Jo Quill on 14 March "
    "2026 at 22:07 (UTC-2), 11 seconds, recovered from the message history.\n\n"
    "Transcript: \"Jo, it's me. Start without me, I'm still at the yard on "
    "Tanner's Row. Something's come up with the books. I'll be with you by half "
    'eleven."\n\n'
    "On 14 March 2026 Mara Quill was at the yard on Tanner's Row when she sent "
    "it.\n",
)
record("mara_mq4", "mq4", "mara")
claim("mara_mq4.name", "mara_mq4", "name", "Mara Quill")
claim(
    "mara_mq4.at",
    "mara_mq4",
    "seen_at",
    "the yard on Tanner's Row",
    DAY,
    place="tanners_row",
    time="22:07",
)
source(
    "photo",
    "IMG_4471.jpg, the back gate of Hollin Joinery",
    "photograph",
    DAY,
    None,
    file="IMG_4471.jpg",
)
source(
    "voice",
    "Voice note from Mara Quill to Jo Quill",
    "voice_note",
    DAY,
    None,
    file="voice_note.ogg",
    transcript="voice_note.json",
)

# Jo at the market that morning: two places on one day that both hold.
source(
    "market_log",
    "Larkhaven market stall 12 takings book, morning of 14 March 2026",
    "takings_book",
    DAY,
    "Larkhaven market, stall 12 (cut flowers). Takings book, Saturday 14 March "
    "2026, morning.\n\n"
    "09:40 card sale, tulips, to Jo Quill, a regular. Seen at Larkhaven market on "
    "14 March 2026.\n",
)
record("jo_market", "market_log", "jo")
claim("jo_market.name", "jo_market", "name", "Jo Quill")
claim(
    "jo_market.at",
    "jo_market",
    "seen_at",
    "Larkhaven market",
    DAY,
    place="market",
    after="to Jo Quill",
    time="09:40",
)

# The night watchman: one place in two spellings, and a birth year that disagrees.
source(
    "ivo",
    "Statement of Ivo Brandt, night watchman",
    "witness_statement",
    "2026-03-15",
    "Statement of Ivo Brandt, taken on 15 March 2026.\n\n"
    "My name is Ivo Brandt. I was born in 1958. I am the night watchman for "
    "Larkhaven Security. On 14 March 2026 I walked my rounds on Tanner's Row from "
    "ten at night. I saw nobody at the yard. At about four minutes past eleven I "
    "saw flames at the back of Hollin Joinery and called the fire service.\n",
)
source(
    "patrol_log",
    "Larkhaven Security night patrol log, 14 March 2026",
    "patrol_log",
    DAY,
    "Larkhaven Security. Night patrol log, 14 March 2026. Officer: Ivo Brandt.\n\n"
    "22:00 start, Tanners Row. 22:30 Mill Lane. 23:04 fire seen at Hollin "
    "Joinery, Tanners Row, fire service called. Ivo Brandt was on Tanners Row on "
    "14 March 2026.\n",
)
source(
    "staff_list",
    "Larkhaven Security staff list, January 2026",
    "staff_list",
    "2026-01-05",
    "Larkhaven Security. Staff list, January 2026.\n\n"
    "Ivo Brandt, night officer, date of birth 1965-02-11.\n"
    "Sana Whitlock, day officer, date of birth 1993-07-30.\n",
)
record("ivo_stmt", "ivo", "ivo")
record("ivo_patrol", "patrol_log", "ivo")
record("ivo_staff", "staff_list", "ivo")
record("sana_staff", "staff_list", "sana")
claim("ivo_stmt.name", "ivo_stmt", "name", "Ivo Brandt")
claim("ivo_stmt.born", "ivo_stmt", "birth_date", "1958")
claim("ivo_stmt.at", "ivo_stmt", "seen_at", "Tanner's Row", DAY, place="tanners_row")
claim("ivo_patrol.name", "ivo_patrol", "name", "Ivo Brandt")
claim(
    "ivo_patrol.at",
    "ivo_patrol",
    "seen_at",
    "Tanners Row",
    DAY,
    place="tanners_row",
    after="Ivo Brandt was on",
)
claim("ivo_staff.name", "ivo_staff", "name", "Ivo Brandt")
claim("ivo_staff.born", "ivo_staff", "birth_date", "1965-02-11")
claim("sana_staff.name", "sana_staff", "name", "Sana Whitlock")
claim("sana_staff.born", "sana_staff", "birth_date", "1993-07-30")

# The apprentice: two places of birth.
source(
    "kofi",
    "Statement of Kofi Mensah, apprentice joiner",
    "witness_statement",
    "2026-03-16",
    "Statement of Kofi Mensah, taken on 16 March 2026.\n\n"
    "My name is Kofi Mensah. I was born in Pellow Bridge in 2006. I am an "
    "apprentice at Hollin Joinery. On 14 March 2026 I was at the Crown and Anchor "
    "with friends from eight until closing.\n",
)
source(
    "apprentice_record",
    "Apprenticeship record for Kofi Mensah",
    "apprenticeship_record",
    "2024-09-02",
    "Larkhaven College of Building. Apprenticeship record.\n\n"
    "Apprentice: Kofi Mensah. Place of birth: Saltmarch. Employer: Hollin "
    "Joinery, Tanner's Row. Start: 2 September 2024.\n",
)
record("kofi_stmt", "kofi", "kofi")
record("kofi_record", "apprentice_record", "kofi")
claim("kofi_stmt.name", "kofi_stmt", "name", "Kofi Mensah")
claim("kofi_stmt.born_in", "kofi_stmt", "place_of_birth", "Pellow Bridge")
claim("kofi_stmt.born", "kofi_stmt", "birth_date", "2006")
claim(
    "kofi_stmt.at", "kofi_stmt", "seen_at", "the Crown and Anchor", DAY, place="crown"
)
claim("kofi_record.name", "kofi_record", "name", "Kofi Mensah")
claim("kofi_record.born_in", "kofi_record", "place_of_birth", "Saltmarch")

# The owner: a birth date at two precisions.
source(
    "ruth",
    "Statement of Ruth Hollin, owner of Hollin Joinery",
    "witness_statement",
    "2026-03-15",
    "Statement of Ruth Hollin, taken on 15 March 2026.\n\n"
    "My name is Ruth Hollin. I was born in 1962. I own Hollin Joinery. On 14 March "
    "2026 I was at home on Larkhaven quay all evening. The fire service rang me "
    "at twenty past eleven. Our bookkeeper had told me last week that the "
    "accounts would be ready on Monday.\n",
)
source(
    "register",
    "Company register extract, Hollin Joinery Ltd",
    "company_register",
    "2026-03-18",
    "Company register extract. Hollin Joinery Ltd, Tanner's Row, Larkhaven.\n\n"
    "Director: Ruth Hollin, date of birth 1962-08-19. Company secretary: none.\n",
)
record("ruth_stmt", "ruth", "ruth")
record("ruth_register", "register", "ruth")
claim("ruth_stmt.name", "ruth_stmt", "name", "Ruth Hollin")
claim("ruth_stmt.born", "ruth_stmt", "birth_date", "1962")
claim("ruth_stmt.at", "ruth_stmt", "seen_at", "Larkhaven quay", DAY, place="quay")
claim("ruth_register.name", "ruth_register", "name", "Ruth Hollin")
claim("ruth_register.born", "ruth_register", "birth_date", "1962-08-19")

# A neighbour at home in town and at the fire: a street inside its town.
source(
    "pia",
    "Statement of Pia Lund, neighbour",
    "witness_statement",
    "2026-03-15",
    "Statement of Pia Lund, taken on 15 March 2026.\n\n"
    "My name is Pia Lund. I was born in 1969. On 14 March 2026 I was at home in "
    "Larkhaven. I went out when I heard the sirens.\n",
)
source(
    "arjun",
    "Statement of Arjun Pillai, neighbour",
    "witness_statement",
    "2026-03-15",
    "Statement of Arjun Pillai, taken on 15 March 2026.\n\n"
    "My name is Arjun Pillai. I was born in 1975. On 14 March 2026 I was at home "
    "on Tanner's Row and saw smoke at about eleven. My neighbour Pia Lund was "
    "standing on Tanner's Row, Larkhaven, watching the fire with me.\n",
)
record("pia_stmt", "pia", "pia")
record("pia_arjun", "arjun", "pia")
record("arjun_stmt", "arjun", "arjun")
claim("pia_stmt.name", "pia_stmt", "name", "Pia Lund")
claim("pia_stmt.born", "pia_stmt", "birth_date", "1969")
claim("pia_stmt.at", "pia_stmt", "seen_at", "Larkhaven", DAY, place="larkhaven")
claim("pia_arjun.name", "pia_arjun", "name", "Pia Lund")
claim(
    "pia_arjun.at",
    "pia_arjun",
    "seen_at",
    "Tanner's Row, Larkhaven",
    DAY,
    place="tanners_row",
)
claim("arjun_stmt.name", "arjun_stmt", "name", "Arjun Pillai")
claim("arjun_stmt.born", "arjun_stmt", "birth_date", "1975")
claim(
    "arjun_stmt.at", "arjun_stmt", "seen_at", "Tanner's Row", DAY, place="tanners_row"
)

# The timber driver: a day inside a month.
source(
    "tomas",
    "Statement of Tomas Ferreira, delivery driver",
    "witness_statement",
    "2026-03-17",
    "Statement of Tomas Ferreira, taken on 17 March 2026.\n\n"
    "My name is Tomas Ferreira. I was born in 1988. On 14 March 2026 I delivered "
    "oak boards to Hollin Joinery on Tanner's Row at about two in the afternoon. "
    "The back gate was shut and locked.\n",
)
source(
    "letter",
    "Employer's letter for Tomas Ferreira",
    "employment_letter",
    "2026-03-19",
    "Saltmarch Timber, Brindle Cross depot.\n\n"
    "To whom it may concern. Tomas Ferreira drives for us and was based at "
    "Brindle Cross throughout March 2026, with deliveries to Larkhaven on "
    "Saturdays.\n",
)
record("tomas_stmt", "tomas", "tomas")
record("tomas_letter", "letter", "tomas")
claim("tomas_stmt.name", "tomas_stmt", "name", "Tomas Ferreira")
claim("tomas_stmt.born", "tomas_stmt", "birth_date", "1988")
claim(
    "tomas_stmt.at", "tomas_stmt", "seen_at", "Tanner's Row", DAY, place="tanners_row"
)
claim("tomas_letter.name", "tomas_letter", "name", "Tomas Ferreira")
claim(
    "tomas_letter.at",
    "tomas_letter",
    "seen_at",
    "Brindle Cross",
    "2026-03-01",
    "2026-03-31",
    place="brindle",
)

# The first suspect: last seen at the pub and arrested 34 km away on one day.
source(
    "dale",
    "Interview of Dale Corrin, former employee",
    "interview_record",
    "2026-03-16",
    "Record of interview with Dale Corrin, 16 March 2026.\n\n"
    "My name is Dale Corrin. I was born in 1990. I worked at Hollin Joinery until "
    "January, when Ruth Hollin let me go. On 14 March 2026 I was at the Crown and "
    "Anchor from eight until closing. I did not go near the yard.\n",
)
source(
    "nell",
    "Statement of Nell Achebe, bar manager at the Crown and Anchor",
    "witness_statement",
    "2026-03-16",
    "Statement of Nell Achebe, taken on 16 March 2026.\n\n"
    "My name is Nell Achebe. I was born in 1985. I manage the Crown and Anchor. "
    "On 14 March 2026 I worked behind the bar at the Crown and Anchor until "
    "closing. Dale Corrin drank at the Crown and Anchor on 14 March 2026 until "
    "closing. He was last seen at the Crown and Anchor at noon on 15 March 2026, "
    "when he left with a holdall.\n",
)
source(
    "custody",
    "Custody record for Dale Corrin, 15 March 2026",
    "custody_record",
    "2026-03-15",
    "Larkhaven police. Custody record.\n\n"
    "Dale Corrin was arrested at Brindle Cross bus station on 15 March 2026 at "
    "16:20 on suspicion of arson. Released without charge on 17 March 2026.\n",
    sensitivity="sensitive",
)
record("dale_stmt", "dale", "dale")
record("dale_nell", "nell", "dale")
record("dale_custody", "custody", "dale")
record("nell_stmt", "nell", "nell")
claim("dale_stmt.name", "dale_stmt", "name", "Dale Corrin")
claim("dale_stmt.born", "dale_stmt", "birth_date", "1990")
claim(
    "dale_stmt.at",
    "dale_stmt",
    "seen_at",
    "the Crown and Anchor",
    DAY,
    place="crown",
)
claim("dale_nell.name", "dale_nell", "name", "Dale Corrin")
claim(
    "dale_nell.at",
    "dale_nell",
    "seen_at",
    "the Crown and Anchor",
    DAY,
    place="crown",
    after="Dale Corrin drank at",
)
claim(
    "dale_nell.last_seen",
    "dale_nell",
    "last_seen",
    "the Crown and Anchor",
    "2026-03-15",
    place="crown",
    after="He was last seen at",
)
claim("dale_custody.name", "dale_custody", "name", "Dale Corrin")
claim(
    "dale_custody.arrested",
    "dale_custody",
    "arrested_at",
    "Brindle Cross bus station",
    "2026-03-15",
    place="brindle_bus",
)
claim("nell_stmt.name", "nell_stmt", "name", "Nell Achebe")
claim("nell_stmt.born", "nell_stmt", "birth_date", "1985")
claim(
    "nell_stmt.at",
    "nell_stmt",
    "seen_at",
    "the Crown and Anchor",
    DAY,
    place="crown",
    after="behind the bar at",
)

source(
    "fire_report",
    "Fire service incident report, Hollin Joinery, 14 March 2026",
    "incident_report",
    "2026-03-15",
    "Larkhaven fire service. Incident report.\n\n"
    "Call received 23:04 on 14 March 2026 from a night watchman. Fire in the "
    "timber store at the back of Hollin Joinery, Tanner's Row. Two crews. Fire "
    "out at 01:10. Rear gate found open, padlock missing. Seat of fire near the "
    "office. Cause under investigation.\n",
)

# ---- the rest of the forty-one statements ----------------------------------------
# key: name, birth year, place key, the words naming it, what they did that night.
# One record each, consistent; they are the pile the twist hides in.

WITNESSES = [
    (
        "amara",
        "Amara Osei",
        "1984",
        "tanners_row",
        "Tanner's Row",
        "I was at home on Tanner's Row and smelled smoke at about eleven.",
    ),
    (
        "bram",
        "Bram Kessler",
        "1971",
        "depot",
        "the Ferry Road bus depot",
        "I waited for fares at the taxi rank by the Ferry Road bus depot until midnight.",
    ),
    (
        "celia",
        "Celia Duarte",
        "1993",
        "crown",
        "the Crown and Anchor",
        "I was at the Crown and Anchor for a birthday until half eleven.",
    ),
    (
        "dmitri",
        "Dmitri Volkov",
        "1966",
        "quay",
        "Larkhaven quay",
        "I walked my dog along Larkhaven quay at ten and saw nothing unusual.",
    ),
    (
        "esme",
        "Esme Hartley",
        "2001",
        "larkhaven",
        "Larkhaven",
        "I was at my flat in Larkhaven studying all evening.",
    ),
    (
        "farid",
        "Farid Haddad",
        "1979",
        "tanners_row",
        "Tanner's Row",
        "I closed my bakery on Tanner's Row at six and went upstairs to bed early.",
    ),
    (
        "greta",
        "Greta Lindqvist",
        "1958",
        "tanners_row",
        "Tanner's Row",
        "I heard glass break on Tanner's Row at about a quarter to eleven.",
    ),
    (
        "hiro",
        "Hiro Tanaka",
        "1995",
        "depot",
        "the Ferry Road bus depot",
        "I drove the last bus into the Ferry Road bus depot at 23:15.",
    ),
    (
        "ines",
        "Ines Moreau",
        "1987",
        "market",
        "Larkhaven market",
        "I packed up my stall at Larkhaven market at one in the afternoon.",
    ),
    (
        "jonas",
        "Jonas Weber",
        "1973",
        "crown",
        "the Crown and Anchor",
        "I played darts at the Crown and Anchor until about eleven.",
    ),
    (
        "kezia",
        "Kezia Banda",
        "1999",
        "larkhaven",
        "Larkhaven",
        "I was babysitting in Larkhaven and saw the glow from the window.",
    ),
    (
        "liam",
        "Liam Byrne",
        "1982",
        "tanners_row",
        "Tanner's Row",
        "I parked my van on Tanner's Row at nine and did not move it.",
    ),
    (
        "marisol",
        "Marisol Vega",
        "1976",
        "quay",
        "Larkhaven quay",
        "I ran the late shift at the chip shop on Larkhaven quay.",
    ),
    (
        "nadim",
        "Nadim Rahman",
        "1968",
        "larkhaven",
        "Larkhaven",
        "I was at evening prayers and then at home in Larkhaven.",
    ),
    (
        "olu",
        "Olu Adeyemi",
        "1991",
        "tanners_row",
        "Tanner's Row",
        "I cycled home along Tanner's Row at about half ten and saw a light in the "
        "joinery office.",
    ),
    (
        "petra",
        "Petra Novak",
        "1980",
        "market",
        "Larkhaven market",
        "I set up the Saturday craft table at Larkhaven market in the morning.",
    ),
    (
        "quentin",
        "Quentin Ashby",
        "1963",
        "crown",
        "the Crown and Anchor",
        "I ate supper at the Crown and Anchor and left at ten.",
    ),
    (
        "rosa",
        "Rosa Lemaire",
        "1994",
        "larkhaven",
        "Larkhaven",
        "I was at a friend's flat in Larkhaven watching a film.",
    ),
    (
        "soren",
        "Soren Dahl",
        "1977",
        "tanners_row",
        "Tanner's Row",
        "I heard a car door slam on Tanner's Row shortly before ten.",
    ),
    (
        "tala",
        "Tala Fonoti",
        "2003",
        "depot",
        "the Ferry Road bus depot",
        "I cleaned buses at the Ferry Road bus depot from eight until two.",
    ),
    (
        "ugo",
        "Ugo Bianchi",
        "1960",
        "quay",
        "Larkhaven quay",
        "I checked the moorings on Larkhaven quay at eleven.",
    ),
    (
        "vera",
        "Vera Sokolova",
        "1986",
        "larkhaven",
        "Larkhaven",
        "I drove home through Larkhaven at about eleven and saw the fire engines.",
    ),
    (
        "wen",
        "Wen Li",
        "1992",
        "tanners_row",
        "Tanner's Row",
        "I closed the laundrette on Tanner's Row at nine.",
    ),
    (
        "xavier",
        "Xavier Ortiz",
        "1985",
        "crown",
        "the Crown and Anchor",
        "I played in the band at the Crown and Anchor from nine until midnight.",
    ),
    (
        "yasmin",
        "Yasmin Karimi",
        "1997",
        "larkhaven",
        "Larkhaven",
        "I was at home in Larkhaven and heard the sirens at about ten past eleven.",
    ),
    (
        "zane",
        "Zane Whitaker",
        "1974",
        "tanners_row",
        "Tanner's Row",
        "I walked past Hollin Joinery on Tanner's Row at nine and the gate was shut.",
    ),
    (
        "abebe",
        "Abebe Tesfaye",
        "1983",
        "market",
        "Larkhaven market",
        "I delivered bread to Larkhaven market at seven in the morning.",
    ),
    (
        "brigid",
        "Brigid Nolan",
        "1957",
        "quay",
        "Larkhaven quay",
        "I sat on my balcony over Larkhaven quay until late and saw smoke to the "
        "south.",
    ),
    (
        "caio",
        "Caio Mendes",
        "1989",
        "crown",
        "the Crown and Anchor",
        "I collected glasses at the Crown and Anchor from seven.",
    ),
    (
        "dina",
        "Dina Shapiro",
        "1972",
        "larkhaven",
        "Larkhaven",
        "I was at home in Larkhaven and went to bed at ten.",
    ),
    (
        "emeka",
        "Emeka Nwosu",
        "1996",
        "tanners_row",
        "Tanner's Row",
        "I saw a small car parked by the joinery's back gate on Tanner's Row at about "
        "ten to ten.",
    ),
]

for i, (key, name, born, place, words, did) in enumerate(WITNESSES):
    taken = f"2026-03-{15 + i % 4:02d}"
    source(
        key,
        f"Statement of {name}",
        "witness_statement",
        taken,
        f"Statement of {name}, taken on {int(taken[-2:])} March 2026.\n\n"
        f"My name is {name}. I was born in {born}. On 14 March 2026 {did}\n",
    )
    record(f"{key}_stmt", key, key)
    claim(f"{key}_stmt.name", f"{key}_stmt", "name", name)
    claim(f"{key}_stmt.born", f"{key}_stmt", "birth_date", born)
    claim(f"{key}_stmt.at", f"{key}_stmt", "seen_at", words, DAY, place=place)

# Same-person decisions already accepted in the case: one cluster each.
CLUSTERS = [
    ["mara_stmt", "mara_jo", "mara_mq3", "mara_mq4"],
    ["jo_stmt", "jo_market"],
    ["ivo_stmt", "ivo_patrol", "ivo_staff"],
    ["kofi_stmt", "kofi_record"],
    ["ruth_stmt", "ruth_register"],
    ["pia_stmt", "pia_arjun"],
    ["tomas_stmt", "tomas_letter"],
    ["dale_stmt", "dale_nell", "dale_custody"],
]

# ---- planted truth -----------------------------------------------------------

CONTRADICTIONS = [
    (
        ["mara_stmt.at", "mara_mq3.at"],
        "the alibi in Brindle Cross against the photograph at the workshop gate, "
        "21:52 on the same night",
    ),
    (
        ["mara_stmt.at", "mara_mq4.at"],
        "the alibi in Brindle Cross against the voice note sent from the yard at 22:07",
    ),
    (["ivo_stmt.born", "ivo_staff.born"], "birth year 1958 against 1965"),
    (["kofi_stmt.born_in", "kofi_record.born_in"], "Pellow Bridge against Saltmarch"),
]
NOT_CONTRADICTIONS = [
    (["ivo_stmt.at", "ivo_patrol.at"], "one street in two spellings"),
    (["ruth_stmt.born", "ruth_register.born"], "a year against a full date in it"),
    (
        ["dale_nell.last_seen", "dale_custody.arrested"],
        "last seen at noon and arrested at 16:20 on the same day",
    ),
    (["pia_stmt.at", "pia_arjun.at"], "a street inside its town"),
    (["tomas_stmt.at", "tomas_letter.at"], "a day inside a month"),
]
TRAP_CONTRADICTIONS = [
    (
        ["jo_stmt.at", "jo_market.at"],
        "the market at 09:40 and home in Brindle Cross that evening",
    ),
]
# What the two exhibit sheets support, once a person accepts them. Her own
# statement's Brindle Cross claim is what they contradict, so it is not cited.
CONCLUSION = {
    "text": "Mara Quill was at the yard on Tanner's Row on the evening of 14 March "
    "2026, not in Brindle Cross from six as her statement says.",
    "standard": "reasonable_grounds",
    "rests_on": ["mara_mq3.at", "mara_mq4.at"],
    "alternatives": [
        {
            "text": "She was at her sister's house in Brindle Cross from about six",
            "why": "Her sister says she arrived late, about half past eleven, and the "
            "voice note sent from her phone at 22:07 says she is still at the yard.",
        },
        {
            "text": "The photograph is the one she took of the forced gate early on "
            "13 March, as she said",
            "why": "Its embedded metadata dates it 14 March 2026 at 21:52 and places "
            "it at the back gate.",
        },
    ],
    "not_established": [
        "Who started the fire.",
        "Why she was at the yard that evening.",
    ],
    "peer_review": True,
}
BATCH = ["mq3", "mq4"]  # the phone exhibits arrive last; the brief reads them


def truth():
    return {
        "same_person": [],
        "twins": [],
        "gaps": [],
        "not_gaps": [],
        "contradictions": [{"claims": c, "why": w} for c, w in CONTRADICTIONS],
        "not_contradictions": [{"claims": c, "why": w} for c, w in NOT_CONTRADICTIONS],
        "hard_same_person": [],
        "traps": [],
        "hard_contradictions": [],
        "trap_contradictions": [
            {"claims": c, "why": w} for c, w in TRAP_CONTRADICTIONS
        ],
        "canary": {"pair": [], "contradiction": []},
    }


# ---- scanned pages ---------------------------------------------------------------
# Four papers arrive as scans; the app reads them through OCR. The typed text stays
# in case.json as the truth the scans were rendered from.

SCANNED = ("mara", "jo", "mq3", "mq4")


def scan_name(key: str) -> str:
    return f"{key}_scan.jpg"


def scan_html(key: str) -> str:
    src = SOURCES[key]
    head, *paras = src["text"].strip().split("\n\n")
    body = "".join(f"<p>{p}</p>" for p in paras)
    office = (
        "LARKHAVEN POLICE - WITNESS STATEMENT"
        if src["doc_type"] == "witness_statement"
        else "LARKHAVEN POLICE - DIGITAL EXHIBIT SHEET"
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><style>
body{{margin:0;background:#fff;font-family:"Courier New",monospace;color:#222}}
.page{{width:1240px;height:1754px;padding:110px;box-sizing:border-box;position:relative}}
h1{{font-family:Arial;font-size:28px;letter-spacing:3px;border-bottom:3px solid #222;padding-bottom:10px}}
.head{{font-size:24px;margin:28px 0 36px}}
p{{font-size:25px;line-height:1.55;margin:0 0 24px}}
.syn{{position:absolute;bottom:40px;left:0;right:0;text-align:center;font-size:16px;color:#888;font-family:Arial}}
</style><body><div class="page"><h1>{office}</h1><div class="head">{head}</div>{body}
<div class="syn">Fictional. Every name, business and place in this case is invented.</div>
</div></body></html>"""


def render_scans() -> None:
    import random

    sys.path.insert(0, str(HERE.parents[2]))
    from PIL import Image
    from playwright.sync_api import sync_playwright

    from acceptance.make_case import scan_realism

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for n, key in enumerate(SCANNED):
            page = browser.new_page(viewport={"width": 1240, "height": 1754})
            page.set_content(scan_html(key))
            page.evaluate("document.fonts.ready")
            raw = FILES / f"{key}.raw.png"
            page.screenshot(path=raw)
            page.close()
            img, _, _ = scan_realism(Image.open(raw), {}, random.Random(1400 + n))
            raw.unlink()
            img.save(FILES / scan_name(key), quality=82)
        browser.close()


# ---- the investigation, act by act ----------------------------------------------
# What happens, in order, and what each act shows (docs/case-studies.md). The app
# reads every source itself; nothing below plants a claim.

LATER = ("photo", "voice", "mq3", "mq4")


def acts() -> list[dict]:
    first = sorted(
        k for k, s in SOURCES.items() if s["date"] <= "2026-03-15" and k not in LATER
    )
    rest = sorted(
        k for k, s in SOURCES.items() if s["date"] > "2026-03-15" and k not in LATER
    )
    return [
        {
            "title": "The fire and the first statements",
            "day": "2026-03-15",
            "story": "Hollin Joinery burns on the night of 14 March. The fire report, "
            "the first statements and the records the police already hold arrive; "
            "the app reads each one, proposes its people, places and dates and a "
            "first pass, and the analyst confirms the fire report's first pass.",
            "shows": ["reading", "first pass"],
            "sources": first,
            "steps": [
                "read",
                "places",
                {"do": "first pass", "sources": ["fire_report"]},
            ],
            "see": ["/evidence/{source:fire_report}", "/sources"],
        },
        {
            "title": "Mara Quill's statement, on paper",
            "day": "2026-03-16",
            "story": "More statements come in, two of them as scanned pages: the "
            "bookkeeper Mara Quill says she was at her sister's in Brindle Cross from "
            "six, and her sister Jo says she came. The app reads the scans through OCR.",
            "shows": ["reading"],
            "sources": rest,
            "steps": ["read", "places"],
            "see": ["/evidence/{source:mara}", "/evidence/{source:jo}"],
        },
        {
            "title": "The photograph of the gate",
            "day": "2026-03-16",
            "story": "Mara hands over a photograph she says shows an earlier "
            "break-in. The app reads where and when the phone took it from the file "
            "itself, says what the picture shows, and makes it findable by that.",
            "shows": ["reading", "search"],
            "sources": ["photo"],
            "steps": ["read"],
            "see": ["/evidence/{source:photo}", "/search?q=gate"],
        },
        {
            "title": "The phone exhibits and the voice note",
            "day": "2026-03-20",
            "story": "An examiner writes up the phone: exhibit sheets for the "
            "photograph and for a voice note Mara sent her sister. Speech to text "
            "transcribes the note, and extraction reads the dated places from the "
            "sheets' own words.",
            "shows": ["reading"],
            "sources": ["mq3", "mq4", "voice"],
            "steps": ["read", "places", "brief"],
            "see": ["/evidence/{source:voice}", "/evidence/{source:mq3}"],
        },
        {
            "title": "One woman in four papers",
            "day": "2026-03-21",
            "story": "The linker proposes that the Mara Quill of the two exhibit "
            "sheets is one person, and the analyst accepts it. Her own statement and "
            "her sister's name her with nothing else in common with the sheets, so "
            "no rule proposes them; the analyst joins the four papers herself, as "
            "the desk joins each witness's papers.",
            "shows": ["same person"],
            "sources": [],
            "steps": [
                {"do": "agent", "agent": "linker"},
                "links",
                {
                    "do": "join",
                    "clusters": "planted",
                    "why": "the same witness in her own statement and in the papers "
                    "about her, joined by the desk that took them",
                },
            ],
            "see": ["/people/{person:mara_stmt}"],
        },
        {
            "title": "Two places on one night",
            "day": "2026-03-21",
            "story": "Extraction read where Mara says she was that evening, in "
            "Brindle Cross from six until past midnight, and where her sister says "
            "she arrived, at about half past eleven. The contradiction hunter sets "
            "her places on 14 March side by side by the clock: her own evening in "
            "Brindle Cross overlaps 21:52 and 22:07 on Tanner's Row in the exhibit "
            "sheets. It proposes; nothing is decided.",
            "shows": ["contradiction"],
            "sources": [],
            "steps": [
                "places",
                "accept",
                {"do": "agent", "agent": "contradictions"},
            ],
            "see": ["/people/{person:mara_stmt}"],
        },
        {
            "title": "A colleague checks the lead",
            "day": "2026-03-23",
            "story": "The analyst assigns the two exhibit sheets to a colleague and "
            "asks her to check the voice note's place against the sheet. She answers "
            "on the claim and marks her work updated.",
            "shows": ["assignment", "peer review"],
            "sources": [],
            "steps": [
                {"do": "assign", "sources": ["mq3", "mq4"], "days": 2},
                {
                    "do": "ask",
                    "claim": "mara_mq4.at",
                    "why": "Does the recording say the yard on Tanner's Row, as the "
                    "sheet has it?",
                },
                {
                    "do": "reply",
                    "claim": "mara_mq4.at",
                    "text": "Yes. The transcript reads 'I'm still at the yard on "
                    "Tanner's Row', and the sheet gives 22:07 for the message.",
                },
                {"do": "mark", "sources": ["mq3", "mq4"], "status": "done"},
            ],
            "see": ["/todo", "/evidence/{source:mq4}"],
        },
        {
            "title": "The conclusion",
            "day": "2026-03-25",
            "story": "The analyst writes what the case shows, on what it rests, the "
            "alternatives set aside and what is not established, and signs it; a "
            "reviewer agrees.",
            "shows": ["conclusion", "peer review"],
            "sources": [],
            "steps": ["conclusion"],
            "see": ["/cases/overview"],
        },
    ]


def case() -> dict:
    keys = {c["key"] for c in CLAIMS}
    assert len(keys) == len(CLAIMS), "claim keys repeat"
    for item in CONTRADICTIONS + NOT_CONTRADICTIONS + TRAP_CONTRADICTIONS:
        assert set(item[0]) <= keys, item
    for c in CLAIMS:
        text = SOURCES[RECORDS[c["record"]]["source"]]["text"]
        assert c["value"] in text, c["key"]
    for key in SCANNED:
        SOURCES[key]["file"] = scan_name(key)
    for s in SOURCES.values():
        s["read"] = True
    statements = [k for k, s in SOURCES.items() if s["doc_type"] == "witness_statement"]
    assert len(statements) == 40, len(statements)  # and one interview record
    return {
        "about": "Planted truth for the case study The witness who was never there, "
        "by acceptance/cases/never-there/make.py. All content is fictional.",
        "story": STORY,
        "investigation": INVESTIGATION,
        "today": TODAY,
        "places": {
            k: {"name_en": n, "name_ar": None, "lon": lon, "lat": lat, "precision_m": m}
            | {"fictional": True}
            for k, (n, lon, lat, m) in PLACES.items()
        },
        "sources": SOURCES,
        "records": RECORDS,
        "clusters": CLUSTERS,
        "claims": CLAIMS,
        "batch": BATCH,
        "truth": truth(),
        "conclusion": CONCLUSION,
        "acts": acts(),
        "media": {
            "IMG_4471.jpg": {
                "exif_time": PHOTO_TIME,
                "exif_offset": PHOTO_OFFSET,
                "gps": {"lon": PHOTO_GPS[0], "lat": PHOTO_GPS[1], "error_m": 8},
                "subject_of": "mq3",
            },
            "voice_note.ogg": {
                "sent": "2026-03-14T22:07:00-02:00",
                "transcript": "voice_note.json",
                "subject_of": "mq4",
            },
        },
    }


# ---- media -----------------------------------------------------------------------


def tag_photo(src: Path) -> None:
    """The edited picture with its phone metadata: time, offset and GPS only."""
    out = FILES / "IMG_4471.jpg"
    shutil.copyfile(src, out)
    lon, lat = PHOTO_GPS
    subprocess.run(
        [
            "exiftool",
            "-q",
            "-overwrite_original",
            "-all=",
            f"-DateTimeOriginal={PHOTO_TIME}",
            f"-CreateDate={PHOTO_TIME}",
            f"-OffsetTimeOriginal={PHOTO_OFFSET}",
            f"-GPSLatitude={abs(lat)}",
            f"-GPSLatitudeRef={'S' if lat < 0 else 'N'}",
            f"-GPSLongitude={abs(lon)}",
            f"-GPSLongitudeRef={'W' if lon < 0 else 'E'}",
            "-GPSHPositioningError=8",
            "-GPSImgDirection=200",
            "-GPSImgDirectionRef=T",
            str(out),
        ],
        check=True,
    )


VOICE_LINES = [
    "Jo, it's me.",
    "Start without me, I'm still at the yard on Tanner's Row.",
    "Something's come up with the books.",
    "I'll be with you by half eleven.",
]
ACTOR = (
    "You are a voice actor recording a hurried, quiet voice message. Read the "
    "user's text aloud exactly as written, word for word, in English, and say "
    "nothing else. Do not add, describe, translate, or comment."
)


def speak_note() -> None:
    sys.path.insert(0, str(HERE.parents[1]))
    import make_case
    import make_recordings

    make_case.ACTOR, make_case.VOICE = ACTOR, "shimmer"
    make_case.VOICE_DIR = HERE.parents[2] / "instance" / "never-there-voice"
    make_case.VOICE_DIR.mkdir(parents=True, exist_ok=True)
    frames, sentences, t = [], [], 0
    for i, text in enumerate(VOICE_LINES):
        for attempt in range(4):
            try:
                data = make_case.speak(f"note_{i}", text)
                break
            except AssertionError as said:
                print(f"line {i}: retry {attempt + 1}: {said}")
        else:
            raise SystemExit(f"line {i} was never read as written")
        ms = len(data) * 1000 // (2 * make_case.RATE)
        sentences.append({"text": text, "start_ms": t, "end_ms": t + ms})
        gap = 350
        frames += [data, b"\0" * (2 * make_case.RATE * gap // 1000)]
        t += ms + gap
    make_recordings.ogg(FILES / "voice_note.ogg", b"".join(frames))
    (FILES / "voice_note.json").write_text(
        json.dumps(
            {"duration_ms": t - gap, "sentences": sentences},
            ensure_ascii=False,
            indent=1,
        )
        + "\n"
    )


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--photo", type=Path, help="edited picture to tag as IMG_4471.jpg")
    p.add_argument("--voice", action="store_true", help="speak the voice note")
    p.add_argument("--scans", action="store_true", help="render the four scans")
    p.add_argument(
        "--record", action="store_true", help="read the case on the live routes"
    )
    args = p.parse_args()
    FILES.mkdir(exist_ok=True)
    if args.scans:
        render_scans()
    if args.photo:
        tag_photo(args.photo)
    if args.voice:
        speak_note()
    (HERE / "case.json").write_text(
        json.dumps(case(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.record:
        record()


def record() -> None:
    """Read the case once on the live routes, in a throwaway database, and write
    recordings.json: about 110 chat calls on cloud-zdr (OCR on the four scans, one
    extraction and one first pass per text, the photo's first pass) and the local
    speech model once. Needs OPENROUTER_API_KEY and the speech weights
    (SPEECH_MODEL_DIR, `stk speech-model`)."""
    import os
    import secrets
    import tempfile

    sys.path.insert(0, str(HERE.parents[2]))
    from alembic import command
    from sqlalchemy.engine import make_url
    from stk.migrations import build_alembic_config, throwaway_database_url

    from sealbook.settings import Config

    with (
        throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True) as url,
        tempfile.TemporaryDirectory() as blob_dir,
    ):
        command.upgrade(build_alembic_config("sealbook", url, quiet=True), "heads")
        env = os.environ | {
            "SQLALCHEMY_DATABASE_URI": make_url(url)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False),
            "BLOB_DIR": blob_dir,
        }
        stk = ["uv", "run", "stk"]
        password = secrets.token_hex(16) + "Aa1!"
        install = [*stk, "install", "-e", "owner@example.com", "-p", password]
        subprocess.run(install, env=env, check=True)
        seed = [*stk, "demo-seed", "--case", HERE.name, "--record"]
        subprocess.run(seed, env=env, check=True)


if __name__ == "__main__":
    main()
