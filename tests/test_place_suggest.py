"""Suggested places on unresolved names: the rule-based order of evidence against a
throwaway database, and the place finder's validation of a model answer."""

import unittest
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest import mock

from sealbook import blobs
from sealbook.agents import approximate, read
from sealbook.entities.data import _place_rows
from sealbook.ledger.contracts import Prop
from sealbook.ledger.write import record_acquisition
from sealbook.places import normalise, resolve, suggest
from sealbook.places.models import Place
from sealbook.settings import Config
from tests.test_places import POSTGRES, ResolveCase

NOTE = (
    "Synthetic note. He was stopped at the bakery roundabout in Harasta. "
    "وقال ابن عمه إنه نقل بعد أسبوع إلى الجديدة في حلب. "
    "Later he went home."
)


@unittest.skipUnless(POSTGRES, "places need Postgres (set SQLALCHEMY_DATABASE_URI)")
class RulesTest(ResolveCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.text = (
            await blobs.put(
                self.db, NOTE.encode(), "text/plain; charset=utf-8", Config.BLOB_DIR
            )
        ).sha256
        await record_acquisition(
            self.db,
            blob_sha256=self.text,
            origin="device:case-a",
            collector_id=self.human.id,
            at=datetime.now(UTC),
            context={"investigation": "case-a", "file": "note.txt"},
            sensitivity="ordinary",
            doc_type="register",
        )
        self.readable = {"case-a", "case-b"}

        async def can_read(db, user_id, slug):
            return slug in self.readable

        self.enterContext(mock.patch("sealbook.agents.tasks.can_read", can_read))

    async def quoted(self, name, inv="case-a"):
        i = NOTE.index(name)
        return await self.say(
            f"person:{uuid.uuid4()}",
            Prop.SEEN_AT,
            {"text": name, "lang": "ar"},
            span_blob=self.text,
            span={"start": i, "end": i + len(name)},
            valid_from=date(2013, 5, 2),
        )

    async def row(self, name, *claims):
        """An unresolved row as entities.data.unresolved builds it."""
        norm = normalise(name)
        found = (await resolve.candidates_many(self.db, [norm])).get(norm, [])
        rows = await _place_rows(self.db, [p.id for p in found])
        return {
            "norm": norm,
            "name": name,
            "count": len(claims),
            "candidates": [rows[p.id] for p in found],
            "claims": [
                {"id": str(c.id), "file": "note.txt", "href": None} for c in claims
            ],
        }

    async def suggested(self, *rows, inv="case-a"):
        await suggest.annotate(self.db, SimpleNamespace(id=1), list(rows), inv)
        return [r["suggestion"] for r in rows]

    async def test_order_of_evidence(self):
        harasta = await self.place("حرستا", "populated")
        quarter = next(
            p
            for p in await resolve.candidates(self.db, "الجديدة")
            if p.name_en == "Al Jadida Quarter"  # the one in Aleppo
        )
        one = await self.row("حرستا", await self.position("حرستا"))
        several = await self.row("الجديدة", await self.quoted("الجديدة"))
        none = await self.row("home", await self.quoted("home"))
        a, c, e = await self.suggested(one, several, none)
        assert a["kind"] == "gazetteer" and a["selectable"]
        assert [p["id"] for p in a["places"]] == [str(harasta.id)]
        # The passage names Aleppo, the governorate of one of the two candidates.
        assert c["kind"] == "several" and not c["selectable"]
        assert len(c["places"]) == 2 and c["favoured"] == str(quarter.id)
        assert "حلب" in c["quote"] and c["quote"] in NOTE
        assert e == {
            "kind": "none",
            "places": [],
            "why": "The gazetteer has no place with this spelling.",
            "selectable": False,
        }

    async def test_confirmed_elsewhere_only_from_cases_the_reader_opens(self):
        site = await self.place("حرستا", "populated")
        other = await self.position("مخفر الجسر", "case-b")
        await self.accept(other, site, "مخفر الجسر")
        claim = await self.position("مخفر الجسر")
        (b,) = await self.suggested(await self.row("مخفر الجسر", claim))
        assert b["kind"] == "elsewhere" and b["selectable"]
        assert b["places"][0]["id"] == str(site.id)
        assert b["why"] == "Confirmed for this spelling in case-b."
        self.readable = {"case-a"}
        (hidden,) = await self.suggested(await self.row("مخفر الجسر", claim))
        assert hidden["kind"] == "none" and "case-b" not in str(hidden)

    async def test_passages_and_named_places(self):
        claim = await self.quoted("the bakery roundabout")
        texts = await suggest.passages(self.db, [claim.id])
        assert "Harasta" in texts[claim.id]
        named = await suggest.named_in(self.db, texts[claim.id], normalise("x"))
        assert any(p.name_en == "Harasta" for _, p in named)


def claim(text, source="s1"):
    return read.Claim(
        id=uuid.uuid4(),
        subject="person:x",
        person=None,
        prop=Prop.SEEN_AT,
        text=text,
        lo=None,
        hi=None,
        source=source,
        span_blob=None,
        span=None,
        tier="unconfirmed",
        sensitive=False,
    )


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.c = claim("the bakery roundabout")
        self.place = Place(id=uuid.uuid4(), name_ar="حرستا", name_en="Harasta")
        self.far = Place(id=uuid.uuid4(), name_ar="حمص", name_en="Homs")
        self.cands = [
            {
                "norm": normalise(self.c.text),
                "name": self.c.text,
                "claims": [self.c],
                "passages": {self.c.id: NOTE},
                "places": [("Harasta", self.place), ("Homs", self.far)],
            }
        ]
        self.h = read.Handles()
        self.payload = approximate.payload(
            self.cands, self.h, {"s1": {"title": "note"}}
        )
        self.norms = {
            self.place.id: {normalise("Harasta"), normalise("حرستا")},
            self.far.id: {normalise("Homs")},
        }

    def item(self, **kw):
        return {
            "name": "n1",
            "claims": ["c1"],
            "place": "p1",
            "radius_km": 1.5,
            "quote": "the bakery roundabout in Harasta",
        } | kw

    def check(self, *items):
        return approximate.validate(
            {"items": list(items)}, self.cands, self.h, self.norms
        )

    def test_keeps_a_quoted_suggestion_near_an_offered_place(self):
        kept, dropped = self.check(self.item())
        assert kept == [
            {
                "norm": "the bakery roundabout",
                "name": "the bakery roundabout",
                "claims": [str(self.c.id)],
                "source": "s1",
                "place": str(self.place.id),
                "radius_km": 1.5,
                "quote": "the bakery roundabout in Harasta",
            }
        ]
        assert not any(dropped.values())

    def test_drops_what_the_passage_does_not_say(self):
        cases = {
            "quote": self.item(quote="the bakery near the Harasta mosque"),
            "unsupported": self.item(place="p2"),  # Homs is not in the quote
            "place": self.item(place="p9"),
            "radius": self.item(radius_km=80),
            "unknown": self.item(claims=["c7"]),
        }
        for reason, item in cases.items():
            kept, dropped = self.check(item)
            assert kept == [] and dropped[reason] == 1, reason
        kept, dropped = self.check(self.item(), self.item(radius_km=2))
        assert len(kept) == 1 and dropped["duplicate"] == 1

    def test_demo_answer_passes_its_own_checks(self):
        kept, _ = approximate.validate(
            approximate.fixed_answer(self.payload), self.cands, self.h, self.norms
        )
        assert [k["place"] for k in kept] == [str(self.place.id)]
        assert "Harasta" in kept[0]["quote"]
