"""Pre-review flag rules (review.assist) on synthetic text and claims, no database."""

import unittest
import uuid
from types import SimpleNamespace

from sealbook.agents.read import Claim
from sealbook.ledger.contracts import Prop, Tier
from sealbook.review import assist

TEXT = (
    "Field clinic log, Al Jadida Quarter\nDate: 2016-08-31\n"
    "#5 Najla Fares al-Omar: seen at Al-Jadida Quarter on 14 August 2016. "
    "Moved to Homs later.\n#6 Sami Adel: seen at Douma.\n"
)


def place(name, lon=None, lat=None, precision=None):
    return SimpleNamespace(
        id=uuid.uuid4(), name=name, lon=lon, lat=lat, precision_m=precision
    )


def claim(person, text, source="s1", prop=Prop.NAME):
    return Claim(
        uuid.uuid4(), person, person, prop, text, None, None, source, None, None,
        Tier.UNCONFIRMED, False,
    )  # fmt: skip


def row(prop, text, tier=Tier.ACCEPTED, day="2016-08-14", masked=False):
    return {
        "id": str(uuid.uuid4()),
        "prop": prop,
        "tier": tier,
        "value": {"text": None if masked else text, "masked": masked},
        "from": day,
        "to": day,
    }


class FlagRules(unittest.TestCase):
    def test_sentence_stops_at_line_and_full_stop(self):
        a = TEXT.index("Najla")
        lo, hi = assist.sentence(TEXT, a, a + 5)
        self.assertEqual(
            TEXT[lo:hi],
            "#5 Najla Fares al-Omar: seen at Al-Jadida Quarter on 14 August 2016.",
        )

    def test_unheld_place_is_flagged_and_held_one_is_not(self):
        jadida, homs, douma = place("Al Jadida"), place("Homs"), place("Douma")
        gazetteer = {"al jadida quarter": [jadida], "homs": [homs], "douma": [douma]}
        a = TEXT.index("#5")
        lo, hi = assist.sentence(TEXT, a, TEXT.index("later") + 5)
        found = assist.phrases(TEXT, lo, hi)
        # Nothing claimed from this source: both names in the passage are flagged.
        hits = assist.unheld_places(found, gazetteer, set(), set())
        self.assertEqual(
            [TEXT[h["start"] : h["end"]] for h in hits], ["Al-Jadida Quarter", "Homs"]
        )
        # A claim reading "Al Jadida Quarter, Homs" holds both, by its words.
        held = {assist.words("Al Jadida Quarter, Homs")}
        self.assertEqual(assist.unheld_places(found, gazetteer, held, set()), [])
        # A claim resolved to the place holds it whatever its spelling.
        self.assertEqual(
            len(assist.unheld_places(found, gazetteer, set(), {jadida.id})), 1
        )
        # Douma is on the next line, outside this passage.
        self.assertNotIn("douma", {n for n, _, _ in found})

    def test_dates_near(self):
        lo, hi = assist.sentence(TEXT, TEXT.index("Najla"), TEXT.index("Najla") + 5)
        self.assertEqual(
            [d for d, _, _ in assist.dates_near(TEXT, lo, hi)], ["14 August 2016"]
        )
        self.assertEqual(
            assist.dates_near("Date: 2016-08-31", 0, 16)[0][0], "2016-08-31"
        )

    def test_contradicted(self):
        mine = row(Prop.BIRTH_DATE, "1990", tier=Tier.UNCONFIRMED)
        same, other = row(Prop.BIRTH_DATE, "1990-03-02"), row(Prop.BIRTH_DATE, "1991")
        pending = row(Prop.BIRTH_DATE, "1992", tier=Tier.UNCONFIRMED)
        hidden = row(Prop.BIRTH_DATE, "1993", masked=True)
        got = assist.contradicted(mine, [same, other, pending, hidden], {}.get)
        self.assertEqual(got, [other])
        self.assertEqual(
            assist.contradicted({**mine, "value": {"text": None}}, [other], {}.get),
            [],
        )

    def test_positions_differ_only_on_one_day_at_places_apart(self):
        # A quarter inside its city's precision agrees with the city; Douma does not.
        city = place("Aleppo", 37.16, 36.20, 8000)
        quarter = place("Jdeideh", 37.155, 36.205, 300)
        douma = place("Douma", 36.40, 33.57, 3000)
        mine = row(Prop.SEEN_AT, "Aleppo", tier=Tier.UNCONFIRMED)
        inside, apart = row(Prop.SEEN_AT, "Jdeideh"), row(Prop.SEEN_AT, "Douma")
        later = row(Prop.SEEN_AT, "Douma", day="2016-08-15")
        places = {mine["id"]: city, inside["id"]: quarter, apart["id"]: douma}
        places[later["id"]] = douma
        self.assertEqual(
            assist.contradicted(mine, [inside, apart, later], places.get), [apart]
        )
        # Unresolved places never conflict on their spelling alone.
        self.assertEqual(assist.contradicted(mine, [apart], {}.get), [])

    def test_seen_in_other_places_at_other_times_is_a_timeline(self):
        # Seen in Aleppo on 3 Sep; in its quarter in July and mid September.
        city = place("Aleppo", 37.16, 36.20, 8000)
        quarter = place("Jdeideh", 37.155, 36.205, 300)
        mine = row(Prop.SEEN_AT, "Aleppo", tier=Tier.UNCONFIRMED, day="2016-09-03")
        july = {
            **row(Prop.SEEN_AT, "Jdeideh"),
            "from": "2016-07-20",
            "to": "2016-07-24",
        }
        sept = {
            **row(Prop.SEEN_AT, "Jdeideh"),
            "from": "2016-09-10",
            "to": "2016-09-15",
        }
        month = {
            **row(Prop.SEEN_AT, "Jdeideh"),
            "from": "2016-09-01",
            "to": "2016-09-30",
        }
        same = row(Prop.SEEN_AT, "Jdeideh", day="2016-09-03")
        places = {mine["id"]: city, same["id"]: quarter}
        got = [assist.relation(mine, r, places.get) for r in (july, sept, month, same)]
        self.assertEqual(got, ["earlier", "later", "same time", "agrees"])
        self.assertEqual(
            assist.contradicted(mine, [july, sept, month, same], places.get), []
        )
        born = row(Prop.BIRTH_DATE, "1990", tier=Tier.UNCONFIRMED)
        self.assertEqual(
            assist.relation(born, row(Prop.BIRTH_DATE, "1991"), {}.get), "differs"
        )

    def test_same_name(self):
        mine = [claim("person:a", "Najla Fares al-Omar", source="s1")]
        twin = claim("person:b", "Najla  Fares al-Omar", source="s1")
        elsewhere = claim("person:c", "najla fares al omar", source="s2")
        apart = claim("person:d", "Najla Fares al-Omar", source="s3")
        short = claim("person:e", "Najla", source="s4")
        found = assist.same_name(
            mine + [claim("person:a", "Najla", source="s5")],
            [twin, elsewhere, apart, short],
            lambda p: p == "person:d",
        )
        # One register naming two rows alike is left alone; a not_same pair too.
        self.assertEqual(found, {"person:c": elsewhere})


if __name__ == "__main__":
    unittest.main()
