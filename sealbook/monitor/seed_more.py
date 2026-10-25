"""`stk demo-seed --more`: four more investigations, so the case switcher, Sources,
Discover and Stories look like a deployment and not a single story. Every person is
invented and every document is a short synthetic text written here; the photographs
are the openly licensed Commons files in acceptance/public, attached as sources with
no claim about them. Places are the GeoNames fixture plus a few more in
seed_places.tsv (Raqqa and invented facilities). Same write path as `--full`."""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import click
from sqlalchemy import select

from sealbook.cases.service import ensure as ensure_case
from sealbook.discovery import service as discovery
from sealbook.discovery.models import FindingKey
from sealbook.ingest.service import system_agent
from sealbook.ledger.contracts import EvidenceKind as E
from sealbook.ledger.contracts import Prop, Tier
from sealbook.ledger.models import Payload, Statement, StatementTier
from sealbook.ledger.write import principal_for_user
from sealbook.monitor import seed_flows as flows
from sealbook.monitor.seed import Seeder, seeded
from sealbook.monitor.seed_full import COMMONS, ROOT, intake, users
from sealbook.places import cli as places
from sealbook.review import findings

ANALYSTS = ("analyst-2", "analyst-3", "analyst-4", "analyst-5")
AT, DT, SA, LS, TR = (
    Prop.ARRESTED_AT,
    Prop.DETAINED_AT,
    Prop.SEEN_AT,
    Prop.LAST_SEEN,
    Prop.TRANSFERRED_TO,
)
LABEL_AR = {
    AT: "مكان التوقيف",
    DT: "مكان الاحتجاز",
    SA: "شوهد في",
    LS: "آخر ظهور في",
    TR: "نُقل إلى",
}
LABEL_EN = {
    AT: "arrested in",
    DT: "held at",
    SA: "seen at",
    LS: "last seen in",
    TR: "transferred to",
}
# place key: (Arabic, English), exactly as the gazetteer spells them
PLACE = {
    "jadida": ("حي الجديدة", "Al Jadida Quarter"),
    "aleppo": ("حلب", "Aleppo"),
    "homs": ("حمص", "Homs"),
    "babaamr": ("بابا عمرو", "Baba Amr"),
    "talbiseh": ("تلبيسة", "Talbiseh"),
    "rastan": ("الرستن", "Ar Rastan"),
    "raqqa": ("الرقة", "Raqqa"),
    "tabqa": ("الطبقة", "Tabqa"),
    "manbij": ("منبج", "Manbij"),
    "camp": ("مخيم الزيتون", "Zaytoun Camp"),
    "b418": ("الفرع 418", "Branch 418"),
    "block": ("مركز احتجاز الكتلة ج", "Block C Detention Centre"),
}
# kind: (Arabic, English); the kinds the sheet reader uses
EVENT = {
    "disp": ("displaced", "نزوح من المنزل", "displaced from home"),
    "wound": ("wounded", "إصابة بشظايا القصف", "wounded by shelling"),
    "kill": ("killed", "مقتل في القصف", "killed in the shelling"),
    "miss": ("missing", "فقدان الاتصال", "reported missing"),
    "rel": ("released", "إخلاء سبيل", "released"),
}


def R(who, spelling, place, prop, d1, d2=None, event=None):
    """One row of a document: a person written one of their spellings, a dated
    position claim at a place, an event, or both."""
    return who, spelling, place, prop, d1, d2, EVENT[event] if event else None


# A document: (file, language, heading, date, rows, doc_type, sensitivity, accepted)
# People are (Arabic, English, English variant) per key.
CASES = {
    "case-demo-03": {
        "title": "Aleppo, Al Jadida quarter under siege, 2016",
        "people": {
            "a1": ("حسام وليد الحلبي", "Hussam Walid al-Halabi", "Husam Waleed Halabi"),
            "a2": ("منى فواز الدباغ", "Mona Fawwaz al-Dabbagh", "Muna Fawaz Dabbagh"),
            "a3": ("خليل سمير القاضي", "Khalil Samir al-Qadi", "Khaleel Sameer Alqadi"),
            "a4": (
                "رندة يوسف الصباغ",
                "Randa Yousef al-Sabbagh",
                "Randah Yusuf Sabbagh",
            ),
            "a5": ("أيهم نبيل شعبان", "Ayham Nabil Shaaban", "Aiham Nabeel Shaban"),
            "a6": ("هالة جميل مراد", "Hala Jamil Murad", "Halah Jameel Murad"),
            "a7": ("سامي عادل دركزللي", "Sami Adel Darkazalli", "Sammy Adil Darkazli"),
            "a8": ("نجلاء فارس العمر", "Najla Fares al-Omar", "Najlaa Faris Alomar"),
            "a9": ("بسام كمال عيسى", "Bassam Kamal Issa", "Bassam Kamel Eissa"),
            "a10": ("ديمة سليم الأخرس", "Dima Salim al-Akhras", "Deema Saleem Akhras"),
        },
        "docs": [
            (
                "shelter_roster_2016-07-24.txt",
                "ar",
                "كشف المقيمين في ملجأ حي الجديدة",
                "2016-07-24",
                "register",
                "sensitive",
                True,
                [
                    R("a1", 0, "jadida", SA, "2016-07-20", "2016-07-24"),
                    R("a2", 0, "jadida", SA, "2016-07-20", "2016-07-24"),
                    R("a3", 0, "jadida", SA, "2016-07-20", "2016-07-24"),
                    R("a4", 0, "jadida", SA, "2016-07-20", "2016-07-24"),
                    R("a9", 0, "jadida", SA, "2016-07-22", "2016-07-24"),
                ],
            ),
            (
                "clinic_log_2016-08.txt",
                "en",
                "Field clinic log, Al Jadida Quarter",
                "2016-08-31",
                "report",
                "sensitive",
                True,
                [
                    R("a2", 1, "jadida", SA, "2016-08-02", "2016-08-09"),
                    R("a5", 1, "jadida", SA, "2016-08-02", "2016-08-05"),
                    R("a6", 1, "jadida", SA, "2016-08-03", "2016-08-09"),
                    R("a7", 1, "jadida", SA, "2016-08-14", None, "wound"),
                    R("a8", 1, "jadida", SA, "2016-08-14", None, "wound"),
                ],
            ),
            (
                "bakery_queue_report_2016-08-14.txt",
                "en",
                "Witness report: shelling of a bakery queue",
                "2016-08-15",
                "report",
                "sensitive",
                True,
                [
                    R("a5", 2, "jadida", SA, "2016-08-14", None, "wound"),
                    R("a6", 2, "jadida", SA, "2016-08-14", None, "wound"),
                    R("a7", 2, "jadida", SA, "2016-08-14", None, "wound"),
                    R("a10", 2, "jadida", SA, "2016-08-14", None, "kill"),
                ],
            ),
            (
                "evacuation_list_2016-09-03.txt",
                "ar",
                "قائمة الإجلاء من حي الجديدة",
                "2016-09-03",
                "register",
                "sensitive",
                False,
                [
                    R("a1", 0, "jadida", LS, "2016-09-02"),
                    R("a2", 0, "jadida", LS, "2016-09-02"),
                    R("a1", 0, "aleppo", SA, "2016-09-03", None, "disp"),
                    R("a2", 0, "aleppo", SA, "2016-09-03", None, "disp"),
                    R("a3", 0, "aleppo", SA, "2016-09-03", None, "disp"),
                    R("a4", 0, "aleppo", SA, "2016-09-03", None, "disp"),
                    R("a9", 0, "aleppo", SA, "2016-09-03", None, "disp"),
                ],
            ),
            (
                "missing_post_2016-09-21.html",
                "ar",
                "مفقودة: نجلاء فارس العمر",
                "2016-09-21",
                "web_post",
                "ordinary",
                False,
                [
                    R("a8", 0, "jadida", LS, "2016-09-20", None, "miss"),
                    R("a6", 0, "jadida", LS, "2016-09-20"),
                ],
            ),
            (
                "relief_distribution_2016-09.txt",
                "en",
                "Relief distribution record",
                "2016-09-16",
                "report",
                "ordinary",
                False,
                [
                    R("a3", 1, "jadida", SA, "2016-09-10", "2016-09-15"),
                    R("a4", 1, "jadida", SA, "2016-09-10", "2016-09-15"),
                    R("a9", 1, "jadida", SA, "2016-09-10", "2016-09-15"),
                    R("a1", 1, "jadida", SA, "2016-09-10", "2016-09-15"),
                ],
            ),
            (
                "hospital_intake_2016-10.txt",
                "ar",
                "سجل قبول مستشفى في حلب",
                "2016-10-04",
                "register",
                "sensitive",
                True,
                [
                    R("a5", 0, "aleppo", SA, "2016-10-02", "2016-10-04", "wound"),
                    R("a6", 0, "aleppo", SA, "2016-10-02", "2016-10-04"),
                    R("a7", 0, "aleppo", SA, "2016-10-03", "2016-10-04"),
                ],
            ),
            (
                "evacuee_testimony_2016-09-30.txt",
                "en",
                "Evacuee testimony",
                "2016-09-30",
                "witness_statement",
                "sensitive",
                False,
                [
                    R("a9", 2, "aleppo", SA, "2016-09-03", "2016-09-05"),
                    R("a1", 2, "aleppo", SA, "2016-09-03", "2016-09-05"),
                ],
            ),
        ],
        "photos": {"aleppo_building.jpg": "Immeuble bombardé à Alep.jpg"},
        # the one wrong place the reviewer rejected: (file, row number, reason)
        "reject": [("clinic_log_2016-08.txt", 2, "dates belong to the next row")],
        "decide": {
            "accept": (
                "candidate_incident",
                "2016-08",
                "Shelling of a bakery queue, Al Jadida, 14 August 2016",
            ),
            "reject": (
                "shared_presence",
                "2016-07",
                "One roster only: all five names come from one list, not from "
                "independent sources.",
            ),
        },
        # (analyst, files, days from today, priority); analyst 0 is the owner
        "assign": [
            (
                1,
                ["evacuation_list_2016-09-03.txt", "relief_distribution_2016-09.txt"],
                4,
                "high",
            ),
            (2, ["missing_post_2016-09-21.html"], -2, "high"),
            (3, ["evacuee_testimony_2016-09-30.txt"], 6, "normal"),
            (0, ["hospital_intake_2016-10.txt"], 9, "low"),
        ],
    },
    "case-demo-04": {
        "title": "Displacement from Raqqa, 2017",
        "people": {
            "b1": (
                "عبد الرحمن محمود الجاسم",
                "Abdulrahman Mahmoud al-Jassem",
                "Abd al-Rahman Mahmud Jasem",
            ),
            "b2": (
                "زينب حسين الخلف",
                "Zainab Hussein al-Khalaf",
                "Zaynab Husayn Khalaf",
            ),
            "b3": (
                "إبراهيم ياسر الحمدان",
                "Ibrahim Yasser al-Hamdan",
                "Ebrahim Yaser Hamdan",
            ),
            "b4": (
                "سلمى عبد الله المطر",
                "Salma Abdullah al-Matar",
                "Selma Abdallah Mattar",
            ),
            "b5": (
                "محمد جمعة الشلاش",
                "Mohammad Jumaa al-Shallash",
                "Muhammad Juma Shalash",
            ),
            "b6": ("هدى صالح الفرحان", "Huda Saleh al-Farhan", "Houda Salih Farhan"),
            "b7": ("عمار خالد الدهمان", "Ammar Khaled al-Dahman", "Amar Khalid Dahman"),
            "b8": (
                "ليلى نايف الرمضان",
                "Layla Nayef al-Ramadan",
                "Leila Nayif Ramadan",
            ),
            "b9": ("رياض عيسى الحسن", "Riyad Issa al-Hasan", "Riyadh Isa Hassan"),
            "b10": ("أمل ماجد الفياض", "Amal Majed al-Fayyad", "Amal Majid Fayyad"),
        },
        "docs": [
            (
                "household_registration_raqqa_2017-08.txt",
                "ar",
                "سجل الأسر في الحي المتضرر بالرقة",
                "2017-08-10",
                "register",
                "sensitive",
                True,
                [
                    R("b1", 0, "raqqa", LS, "2017-08-08", None, "disp"),
                    R("b2", 0, "raqqa", LS, "2017-08-08", None, "disp"),
                    R("b3", 0, "raqqa", LS, "2017-08-08"),
                    R("b4", 0, "raqqa", LS, "2017-08-09"),
                    R("b5", 0, "raqqa", LS, "2017-08-09"),
                ],
            ),
            (
                "tabqa_transit_log_2017-09.txt",
                "en",
                "Transit log, Tabqa checkpoint",
                "2017-09-02",
                "report",
                "sensitive",
                True,
                [
                    R("b1", 1, "tabqa", SA, "2017-09-01"),
                    R("b2", 1, "tabqa", SA, "2017-09-01"),
                    R("b3", 1, "tabqa", SA, "2017-09-01"),
                    R("b6", 1, "tabqa", SA, "2017-09-01"),
                ],
            ),
            (
                "manbij_reception_2017-09.txt",
                "ar",
                "سجل استقبال النازحين في منبج",
                "2017-09-18",
                "register",
                "sensitive",
                False,
                [
                    R("b1", 0, "manbij", SA, "2017-09-15", "2017-09-18"),
                    R("b3", 0, "manbij", SA, "2017-09-15", "2017-09-18"),
                    R("b4", 0, "manbij", SA, "2017-09-16", "2017-09-18"),
                    R("b7", 0, "manbij", SA, "2017-09-16", "2017-09-18"),
                    R("b8", 0, "manbij", SA, "2017-09-16", "2017-09-18"),
                ],
            ),
            (
                "zaytoun_camp_intake_2017-10.txt",
                "en",
                "Zaytoun Camp intake register",
                "2017-10-12",
                "register",
                "sensitive",
                True,
                [
                    R("b1", 2, "camp", SA, "2017-10-05", "2017-10-12", "disp"),
                    R("b2", 2, "camp", SA, "2017-10-05", "2017-10-12", "disp"),
                    R("b4", 2, "camp", SA, "2017-10-06", "2017-10-12", "disp"),
                    R("b7", 2, "camp", SA, "2017-10-06", "2017-10-12", "disp"),
                    R("b9", 2, "camp", SA, "2017-10-07", "2017-10-12", "disp"),
                ],
            ),
            (
                "camp_aid_list_2017-10.txt",
                "ar",
                "قائمة المساعدات في مخيم الزيتون",
                "2017-10-16",
                "report",
                "ordinary",
                True,
                [
                    R("b1", 0, "camp", SA, "2017-10-08", "2017-10-15"),
                    R("b2", 0, "camp", SA, "2017-10-08", "2017-10-15"),
                    R("b5", 0, "camp", SA, "2017-10-09", "2017-10-15"),
                    R("b7", 0, "camp", SA, "2017-10-09", "2017-10-15"),
                ],
            ),
            (
                "looking_for_relatives_2017-09.html",
                "en",
                "Looking for relatives: the Fayyad family",
                "2017-09-12",
                "web_post",
                "ordinary",
                False,
                [
                    R("b10", 1, "raqqa", LS, "2017-08-09", None, "miss"),
                    R("b3", 1, "tabqa", LS, "2017-09-01"),
                ],
            ),
            (
                "witness_interview_2017-11.txt",
                "ar",
                "مقابلة مع شاهدة",
                "2017-11-03",
                "witness_statement",
                "sensitive",
                False,
                [
                    R("b6", 0, "manbij", SA, "2017-09-20", "2017-09-25"),
                    R("b8", 0, "manbij", SA, "2017-09-20", "2017-09-25"),
                ],
            ),
            (
                "neighbourhood_damage_note_2017-08.txt",
                "en",
                "Neighbourhood damage note",
                "2017-08-21",
                "report",
                "ordinary",
                False,
                [
                    R("b9", 2, "raqqa", LS, "2017-08-08", None, "kill"),
                    R("b5", 2, "raqqa", LS, "2017-08-08"),
                ],
            ),
        ],
        "photos": {
            "raqqa_destruction_2017.jpg": "Raqqa destruction 2017.jpg",
            "raqqa_clock_square_2017.jpg": "Place de l'Horloge Raqqa 2017.jpg",
        },
        "reject": [
            ("tabqa_transit_log_2017-09.txt", 3, "the checkpoint wrote the wrong day")
        ],
        "decide": None,
        "assign": [
            (
                2,
                ["manbij_reception_2017-09.txt", "witness_interview_2017-11.txt"],
                5,
                "normal",
            ),
            (3, ["looking_for_relatives_2017-09.html"], 2, "high"),
            (4, ["neighbourhood_damage_note_2017-08.txt"], -4, "normal"),
        ],
    },
    "case-demo-05": {
        "title": "Detention and disappearance, Branch 418 near Homs, 2012 to 2014",
        "people": {
            "c1": ("طارق منير الشامي", "Tarek Munir al-Shami", "Tariq Mounir Shami"),
            "c2": ("وائل سعيد الدرويش", "Wael Said al-Darwish", "Wa'el Saeed Darwish"),
            "c3": ("غسان فؤاد الحمصي", "Ghassan Fouad al-Homsi", "Ghassan Fuad Homsi"),
            "c4": ("رائد عمر السباعي", "Raed Omar al-Sibai", "Rayed Omar Sebai"),
            "c5": ("نبيل أحمد الصالح", "Nabil Ahmad al-Saleh", "Nabeel Ahmed Salih"),
            "c6": ("ماهر يحيى العيسى", "Maher Yahya al-Issa", "Mahir Yehia Eissa"),
            "c7": ("أنس رضوان الزعبي", "Anas Radwan al-Zoubi", "Anas Radwan Zuabi"),
            "c8": ("جمال حسن المصري", "Jamal Hassan al-Masri", "Jamal Hasan Masri"),
            "c9": (
                "سليمان راشد البكري",
                "Suleiman Rashed al-Bakri",
                "Sulaiman Rashid Bakri",
            ),
            "c10": ("كمال يونس الشعار", "Kamal Younes al-Shaar", "Kamal Yunis Shaar"),
        },
        "docs": [
            (
                "raid_log_baba_amr_2012-03.txt",
                "ar",
                "سجل مداهمات بابا عمرو",
                "2012-03-12",
                "detention_register",
                "sensitive",
                True,
                [
                    R("c1", 0, "babaamr", AT, "2012-03-09"),
                    R("c2", 0, "babaamr", AT, "2012-03-09"),
                    R("c3", 0, "babaamr", AT, "2012-03-10"),
                    R("c4", 0, "babaamr", AT, "2012-03-10"),
                ],
            ),
            (
                "branch_418_intake_2012-03.txt",
                "ar",
                "سجل استلام الفرع 418",
                "2012-03-14",
                "detention_register",
                "sensitive",
                True,
                [
                    R("c1", 0, "b418", DT, "2012-03-10", "2012-03-14"),
                    R("c2", 0, "b418", DT, "2012-03-10", "2012-03-14"),
                    R("c3", 0, "b418", DT, "2012-03-11", "2012-03-14"),
                    R("c4", 0, "b418", DT, "2012-03-11", "2012-03-14"),
                    R("c5", 0, "homs", AT, "2012-03-12"),
                ],
            ),
            (
                "family_complaint_2012-04.txt",
                "en",
                "Family complaint to a monitoring group",
                "2012-04-02",
                "report",
                "sensitive",
                True,
                [
                    R("c1", 1, "b418", DT, "2012-03-20", "2012-03-31"),
                    R("c3", 1, "b418", DT, "2012-03-20", "2012-03-31"),
                    R("c6", 1, "b418", DT, "2012-03-20", "2012-03-31"),
                ],
            ),
            (
                "block_c_transfer_order_2012-09.txt",
                "ar",
                "أمر نقل من الفرع 418 إلى مركز الكتلة ج",
                "2012-09-10",
                "transfer_order",
                "sensitive",
                True,
                [
                    R("c1", 0, "block", TR, "2012-09-10"),
                    R("c2", 0, "block", TR, "2012-09-10"),
                    R("c5", 0, "block", TR, "2012-09-10"),
                ],
            ),
            (
                "released_detainee_statement_2013-01.txt",
                "en",
                "Statement of a released detainee",
                "2013-01-20",
                "witness_statement",
                "sensitive",
                False,
                [
                    R("c4", 2, "b418", DT, "2012-06-01", "2012-09-30", "rel"),
                    R("c6", 2, "b418", DT, "2012-06-01", "2012-09-30"),
                    R("c7", 2, "b418", DT, "2012-06-01", "2012-09-30"),
                ],
            ),
            (
                "raid_log_ar_rastan_2013-06.txt",
                "ar",
                "سجل توقيف الرستن",
                "2013-06-05",
                "detention_register",
                "sensitive",
                False,
                [
                    R("c8", 0, "rastan", AT, "2013-06-03"),
                    R("c9", 0, "rastan", AT, "2013-06-03"),
                    R("c10", 0, "rastan", AT, "2013-06-04"),
                ],
            ),
            (
                "missing_appeal_post_2013-05.html",
                "ar",
                "نداء: جمال حسن المصري",
                "2013-05-20",
                "web_post",
                "ordinary",
                False,
                [
                    R("c8", 0, "talbiseh", LS, "2013-05-02", None, "miss"),
                ],
            ),
            (
                "military_hospital_note_2014-02.txt",
                "en",
                "Military hospital intake note",
                "2014-02-11",
                "report",
                "sensitive",
                False,
                [
                    R("c2", 1, "homs", SA, "2014-02-09", "2014-02-11"),
                ],
            ),
            (
                "block_c_roster_2014-03.txt",
                "ar",
                "قائمة نزلاء مركز الكتلة ج",
                "2014-03-08",
                "detention_register",
                "sensitive",
                True,
                [
                    R("c9", 0, "block", DT, "2014-03-01", "2014-03-08"),
                    R("c10", 0, "block", DT, "2014-03-01", "2014-03-08"),
                    R("c7", 0, "block", DT, "2014-03-02", "2014-03-08"),
                    R("c1", 0, "block", DT, "2014-03-02", "2014-03-08"),
                ],
            ),
            (
                "block_c_witness_2014-04.txt",
                "ar",
                "إفادة شاهد عن مركز الكتلة ج",
                "2014-04-10",
                "witness_statement",
                "sensitive",
                True,
                [
                    R("c9", 0, "block", SA, "2014-03-03", "2014-03-10"),
                    R("c10", 0, "block", SA, "2014-03-03", "2014-03-10"),
                    R("c1", 0, "block", SA, "2014-03-03", "2014-03-10"),
                ],
            ),
        ],
        "photos": {
            "homs_bab_dreeb_3.jpg": "Destruction in Homs (1).jpg",
            "homs_bab_dreeb_4.jpg": "Destruction in Homs (4).jpg",
            "homs_bab_dreeb_5.jpg": "Destruction in Homs (5).jpg",
            "homs_bab_dreeb_6.jpg": "Destruction in Homs (6).jpg",
            "homs_bab_dreeb_7.jpg": "Destruction in Homs (7).jpg",
            "homs_building_burning_2012.jpg": "Building burning in Homs city.jpg",
        },
        "reject": [
            (
                "branch_418_intake_2012-03.txt",
                5,
                "that row is an arrest in Homs, not Branch 418",
            )
        ],
        "decide": {
            "accept": (
                "candidate_incident",
                "2014-03",
                "Men held at Block C, March 2014",
            ),
            "reject": None,
        },
        "assign": [
            (
                1,
                [
                    "raid_log_ar_rastan_2013-06.txt",
                    "missing_appeal_post_2013-05.html",
                    "military_hospital_note_2014-02.txt",
                ],
                7,
                "normal",
            ),
            (2, ["released_detainee_statement_2013-01.txt"], 1, "high"),
            (4, ["homs_bab_dreeb_3.jpg", "homs_bab_dreeb_4.jpg"], 12, "low"),
            (0, ["block_c_witness_2014-04.txt"], 3, "high"),
        ],
    },
    "case-demo-06": {
        "title": "Market shelling, Aleppo, November 2016",
        "people": {
            "d1": (
                "فارس إدريس الكيالي",
                "Fares Idris al-Kayyali",
                "Faris Idriss Kayali",
            ),
            "d2": ("سناء توفيق المرعي", "Sanaa Tawfiq al-Marai", "Sana Tawfik Maraei"),
            "d3": (
                "عادل منصور الخياط",
                "Adel Mansour al-Khayyat",
                "Adil Mansur Khayat",
            ),
            "d4": ("رولا بشير قنوت", "Rula Bashir Qannout", "Roula Bashir Kanout"),
            "d5": ("زياد هاني النعسان", "Ziad Hani al-Nassan", "Ziyad Hany Nasan"),
            "d6": ("وفاء رشيد البني", "Wafaa Rashid al-Banni", "Wafa Rasheed Bani"),
            "d7": ("مروان صبحي الأسدي", "Marwan Subhi al-Asadi", "Marwan Sobhi Asadi"),
            "d8": ("عبير نزار طحان", "Abeer Nizar Tahhan", "Abir Nizar Tahan"),
            "d9": ("جورج ميشال سابا", "George Michel Saba", "Georges Michel Sabbagh"),
        },
        "docs": [
            (
                "eyewitness_report_2016-11-17.txt",
                "en",
                "Eyewitness report: shelling of the market",
                "2016-11-18",
                "witness_statement",
                "sensitive",
                True,
                [
                    R("d1", 1, "aleppo", SA, "2016-11-17", None, "wound"),
                    R("d2", 1, "aleppo", SA, "2016-11-17", None, "wound"),
                    R("d3", 1, "aleppo", SA, "2016-11-17", None, "wound"),
                ],
            ),
            (
                "clinic_admissions_2016-11-17.txt",
                "ar",
                "قائمة قبول المصابين في العيادة",
                "2016-11-17",
                "register",
                "sensitive",
                True,
                [
                    R("d2", 0, "aleppo", SA, "2016-11-17", None, "wound"),
                    R("d3", 0, "aleppo", SA, "2016-11-17", None, "wound"),
                    R("d4", 0, "aleppo", SA, "2016-11-17", None, "wound"),
                    R("d5", 0, "aleppo", SA, "2016-11-17", None, "wound"),
                ],
            ),
            (
                "morgue_register_2016-11-18.txt",
                "ar",
                "سجل الوفيات",
                "2016-11-18",
                "register",
                "sensitive",
                True,
                [
                    R("d6", 0, "aleppo", SA, "2016-11-17", None, "kill"),
                    R("d7", 0, "aleppo", SA, "2016-11-17", None, "kill"),
                ],
            ),
            (
                "shopkeeper_statement_2016-11-20.txt",
                "en",
                "Statement of a shopkeeper",
                "2016-11-20",
                "witness_statement",
                "sensitive",
                True,
                [
                    R("d1", 2, "aleppo", SA, "2016-11-17"),
                    R("d8", 2, "aleppo", SA, "2016-11-17"),
                    R("d9", 2, "aleppo", SA, "2016-11-17"),
                ],
            ),
            (
                "missing_post_2016-11-19.html",
                "ar",
                "مفقود: جورج ميشال سابا",
                "2016-11-19",
                "web_post",
                "ordinary",
                False,
                [
                    R("d9", 0, "aleppo", LS, "2016-11-17", None, "miss"),
                ],
            ),
            (
                "ngo_incident_note_2016-11-25.txt",
                "en",
                "Incident note from a monitoring group",
                "2016-11-25",
                "report",
                "ordinary",
                False,
                [
                    R("d1", 1, "aleppo", SA, "2016-11-17"),
                    R("d4", 1, "aleppo", SA, "2016-11-17"),
                    R("d6", 1, "aleppo", SA, "2016-11-17", None, "kill"),
                ],
            ),
            (
                "followup_visit_2016-12.txt",
                "ar",
                "زيارة متابعة للجرحى",
                "2016-12-05",
                "report",
                "sensitive",
                False,
                [
                    R("d4", 0, "aleppo", SA, "2016-12-02", "2016-12-05"),
                    R("d5", 0, "aleppo", SA, "2016-12-02", "2016-12-05"),
                ],
            ),
            (
                "casualty_summary_2016-11-30.txt",
                "en",
                "Casualty summary",
                "2016-11-30",
                "report",
                "ordinary",
                False,
                [
                    R("d6", 2, "aleppo", SA, "2016-11-17", None, "kill"),
                    R("d7", 2, "aleppo", SA, "2016-11-17", None, "kill"),
                    R("d2", 2, "aleppo", SA, "2016-11-17", None, "wound"),
                ],
            ),
        ],
        "photos": {"aleppo_souk.jpg": "Le souk d'Alep bombardé.jpg"},
        "reject": [
            (
                "clinic_admissions_2016-11-17.txt",
                4,
                "same man as row 1 of the witness report",
            )
        ],
        "decide": {
            "accept": (
                "candidate_incident",
                "2016-11",
                "Shelling of a market in Aleppo, 17 November 2016",
            ),
            "reject": None,
        },
        "assign": [
            (
                1,
                ["followup_visit_2016-12.txt", "casualty_summary_2016-11-30.txt"],
                3,
                "normal",
            ),
            (
                3,
                ["missing_post_2016-11-19.html", "ngo_incident_note_2016-11-25.txt"],
                8,
                "low",
            ),
            (4, ["aleppo_souk.jpg"], 14, "low"),
        ],
    },
}

LLM = {
    "route": "local",
    "endpoint": "http://127.0.0.1:8000/v1",
    "model_requested": "qwen3-vl-4b",
    "model_served": "qwen3-vl-4b-sy-demo",
    "provider_served": "local-vllm",
    "prompt_hash": "0" * 64,
}


def person(case: str, key: str) -> str:
    return f"person:{uuid.uuid5(uuid.NAMESPACE_URL, f'sealbook-demo/{case}/{key}')}"


def render(case: dict, doc: tuple) -> tuple[str, list[dict]]:
    """The document text and, per row, the span anchors of its claims: the text
    before the name, the place and the event, which is how `Seeder.say` finds each
    value once, even where one name contains another word."""
    file, lang, head, date, _, _, _, rows = doc
    ar = lang == "ar"
    html = file.endswith(".html")
    lines, anchors = [], []
    for n, (who, spelling, place, prop, d1, d2, event) in enumerate(rows, 1):
        name = case["people"][who][0 if ar else spelling]
        when = f"{d1} {'إلى' if ar else 'to'} {d2}" if d2 else d1
        found, out = {}, f"#{n} "
        if ar:
            out += "الاسم: "
            found["name"] = out
            out += name
            if place:
                out += f" | {LABEL_AR[prop]}: "
                found["place"] = out
                out += PLACE[place][0]
            out += f" | التاريخ: {when}"
            if event:
                out += " | الحدث: "
                found["event"] = out
                out += event[1]
        else:
            found["name"] = out
            out += f"{name}: "
            if place:
                out += f"{LABEL_EN[prop]} "
                found["place"] = out
                out += f"{PLACE[place][1]} on {when}"
            if event:
                out += ". Reported " if place else "reported "
                found["event"] = out
                out += event[2]
                out += "" if place else f" on {when}"
            out += "."
        lines.append(f"<p>{out}</p>" if html else out)
        anchors.append(found)
    body = f"<h1>{head}</h1>\n" if html else f"{head}\n"
    label = "التاريخ: " if ar else "Date: "
    body += f"<p>{label}{date}</p>\n" if html else f"{label}{date}\n\n"
    return body + "\n".join(lines) + "\n", anchors


def commons(title: str) -> tuple[str, dict]:
    """Origin and context for a Commons photo, copied from the saved API response."""
    pages = json.loads((ROOT / "public/commons.json").read_text())["query"]["pages"]
    page = next(p for p in pages.values() if p["title"] == f"File:{title}")
    meta = page["imageinfo"][0]["extmetadata"]

    def value(key):
        return meta.get(key, {}).get("value", "")

    author = re.sub(r"<[^>]+>", "", value("Artist")).strip()
    if "own-work" in value("Credit"):
        author += " (own work)"
    licence = value("LicenseShortName")
    if value("LicenseUrl"):
        licence += f", {value('LicenseUrl')}"
    return COMMONS + quote(title.replace(" ", "_"), safe="(),"), {
        "author": author,
        "licence": licence,
        "date_original": value("DateTimeOriginal")[:10],
        "caption": re.sub(r"<[^>]+>", "", value("ImageDescription")).strip(),
        "attribution": "acceptance/public/ATTRIBUTION.md",
        "note": "no claim in this case refers to this photograph",
    }


async def finding_of(s, kind, month):
    """The open finding of `kind` and month with the most people."""
    rows = (
        await s.s.execute(
            select(FindingKey, Payload.value)
            .join(Statement, Statement.id == FindingKey.statement_id)
            .join(Payload, Payload.commitment == Statement.commitment)
            .where(
                FindingKey.investigation == s.case,
                FindingKey.kind == kind,
                FindingKey.state == "open",
            )
        )
    ).all()
    mine = [(fk, v) for fk, v in rows if v["month"] == month]
    if not mine:
        raise click.ClickException(f"{s.case}: no open {kind} in {month}")
    return max(mine, key=lambda r: len(r[1]["people"]))


async def decide(s, reviewer, senior, plan: dict) -> None:
    """Accept one candidate incident with all its members, and optionally reject a
    shared presence with a reason, as the Discover checklist would."""
    kind, month, title = plan["accept"]
    fk, value = await finding_of(s, kind, month)
    roles = dict.fromkeys(value["people"], "victim")
    _, planned = await findings.plan(
        s.s, reviewer, fk.key, value["members"], roles, title, None, senior.id
    )
    await flows.gesture(s, reviewer, planned)
    if plan["reject"]:
        kind, month, reason = plan["reject"]
        fk, _ = await finding_of(s, kind, month)
        _, planned = await findings.reject(s.s, reviewer, fk.key, reason, senior.id)
        await flows.gesture(s, reviewer, planned)


async def seed_case(s, session, case_id, case, ctx) -> dict:
    s.case = case_id
    await ensure_case(session, case_id)
    owner, analysts, senior, reviewer, extractor, importer = ctx
    mine = [owner, *analysts]
    principals = [
        await principal_for_user(session, u.id, u.name or u.email) for u in mine
    ]
    batch = {"batch": str(uuid.uuid4())}
    docs = {d[0]: d for d in case["docs"]}
    for i, doc in enumerate(case["docs"]):
        file, lang, _, _, doc_type, sens, _, _ = doc
        text, _ = render(case, doc)
        mime = "text/html" if file.endswith(".html") else "text/plain"
        collector = principals[i % len(principals)]
        origin = (
            f"url:http://127.0.0.1/synthetic/{file}"
            if mime == "text/html"
            else f"channel:partner-{'abcd'[i % 4]}/{case_id}"
        )
        await intake(
            s,
            file,
            text.encode(),
            mime,
            origin,
            collector,
            doc_type,
            sens,
            batch if i < len(docs) // 2 else {},
        )
    for file, title in case["photos"].items():
        origin, ctx_photo = commons(title)
        await intake(
            s,
            file,
            (ROOT / "public" / file).read_bytes(),
            "image/jpeg",
            origin,
            importer,
            "photo",
            "ordinary",
            ctx_photo,
        )
    await session.commit()

    claims, accepted, positions, names = {}, [], [], {}
    rejects = {(f, n): why for f, n, why in case["reject"]}
    for doc in case["docs"]:
        file, lang, _, date, _, _, accept, rows = doc
        text, anchors = render(case, doc)
        run = await s.run(extractor, "extract", [file], **LLM)
        await s.say(
            run,
            file,
            f"blob:{s.blobs[file]}",
            Prop.DOCUMENT_DATE,
            {"date": date},
            date,
            after=text.split("\n")[0],
            valid=[date],
            evidence=[E.PRINTED_TEXT],
        )
        count, named = 1, {}
        for n, ((who, spelling, place, prop, d1, d2, event), found) in enumerate(
            zip(rows, anchors, strict=True), 1
        ):
            subject = person(case_id, who)
            ar = lang == "ar"
            name = case["people"][who][0 if ar else spelling]
            value = {"text": name, "lang": lang}
            row, at = [], None
            if who not in named:  # a person on two rows is named once per document
                st = named[who] = await s.say(
                    run,
                    file,
                    subject,
                    Prop.NAME,
                    value,
                    name,
                    after=found["name"],
                    evidence=[E.PRINTED_TEXT],
                )
                names.setdefault(file, []).append(st)
                row.append(st)
            valid = [d1, d2] if d2 else [d1]
            if place:
                label = PLACE[place][0 if ar else 1]
                at = await s.say(
                    run,
                    file,
                    subject,
                    prop,
                    {"text": label, "lang": lang},
                    label,
                    after=found["place"],
                    valid=valid,
                    evidence=[E.PRINTED_TEXT],
                )
                positions.append(at)
                row.append(at)
                claims[file, n] = at
            if event:
                kind, text_ar, text_en = event
                shown = text_ar if ar else text_en
                await s.say(
                    run,
                    file,
                    subject,
                    Prop.EVENT,
                    {"text": shown, "lang": lang, "kind": kind},
                    shown,
                    after=found["event"],
                    valid=valid,
                    evidence=[E.PRINTED_TEXT],
                )
                count += 1
            count += len(row)
            if accept:
                accepted += [
                    (r, f"{file}, row {n}")
                    for r in row
                    if (file, n) not in rejects or r is not at
                ]
        await s.done(run, count)
    await session.commit()

    for (file, n), why in rejects.items():
        await s.review(senior, claims[file, n], "reject", why)
    for st, why in accepted:
        await s.review(senior, st, "accept", why)
        await discovery.actor_changed(session, st)
    await session.commit()
    for st in positions:
        await flows.confirm_place(s, senior, st)
    await session.commit()

    # Two requests between colleagues on names nobody has decided yet.
    # (another document may already corroborate a name, which closes the proposal)
    open_names = [
        st
        for file, d in docs.items()
        if not d[6]
        for st in names[file]
        if (await session.get(StatementTier, st.id)).tier == Tier.UNCONFIRMED
    ]
    for k, st in enumerate(open_names[:2]):
        sender, to = analysts[k % len(analysts)], analysts[(k + 1) % len(analysts)]
        sent = await flows.request(
            s,
            st,
            sender,
            to,
            "Please check this spelling against the original before it is decided.",
        )
        await flows.reply(s, sent.id, to, "Checked: the spelling matches the document.")
    await session.commit()

    await flows.run_discover(s)
    if case["decide"]:
        await decide(s, reviewer, senior, case["decide"])
    for who, files, days, priority in case["assign"]:
        by = owner if who else analysts[0]
        to = mine[who]
        await flows.assign(
            s, by, to, [f"blob:{s.blobs[f]}" for f in files], days, priority
        )
    await session.commit()
    return {
        "documents": len(case["docs"]) + len(case["photos"]),
        "claims": len(positions),
    }


async def seed_more(session, app) -> dict:
    """Additive: refuses when any of the four cases exists, never deletes."""
    for case_id in CASES:
        if await seeded(session, case_id):
            raise click.ClickException(f"{case_id} is already seeded")
    s = Seeder(session)
    owner, *analysts = await users(session, app, ANALYSTS)
    senior = await s.analyst("senior-analyst", "demo-more")
    reviewer = await session.get(type(owner), senior.user_id)
    extractor = await system_agent(session, "extractor")
    importer = await s.principal("importer")
    # The fixture plus Raqqa and the invented facilities, imported together so
    # every place finds its governorate.
    with tempfile.TemporaryDirectory() as tmp:
        merged = Path(tmp) / "places.tsv"
        merged.write_text(
            places.FIXTURE.read_text()
            + (Path(__file__).parent / "seed_places.tsv").read_text()
        )
        await places.import_file(session, merged)
    await session.commit()
    ctx = (owner, analysts, senior, reviewer, extractor, importer)
    out = {}
    for case_id, case in CASES.items():
        out[case_id] = await seed_case(s, session, case_id, case, ctx)
        await session.commit()
    return {"cases": out, "started": datetime.now(UTC).isoformat()}
