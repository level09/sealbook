import unittest
from datetime import date

from stk.discovery.intervals import interval, overlap
from stk.ledger.contracts import Prop


class IntervalTest(unittest.TestCase):
    def test_year_interval_weak_overlap(self):
        y = interval(date(2013, 1, 1), date(2013, 12, 31), Prop.DETAINED_AT)
        d = interval(date(2013, 7, 4), None, Prop.SEEN_AT)
        self.assertEqual(overlap(y, d), 1 / 365)

    def test_month_overlap_strong(self):
        m = interval(date(2013, 7, 1), date(2013, 7, 31), Prop.DETAINED_AT)
        self.assertEqual(overlap(m, m), 1.0)

    def test_disjoint(self):
        a = (date(2013, 1, 1), date(2013, 1, 5))
        b = (date(2014, 1, 1), date(2014, 1, 5))
        self.assertEqual(overlap(a, b), 0.0)

    def test_undated_claims_never_overlap(self):
        self.assertIsNone(interval(None, None, Prop.DETAINED_AT))

    def test_last_seen_counts_its_day(self):
        self.assertEqual(
            interval(date(2013, 7, 4), date(2014, 1, 1), Prop.LAST_SEEN),
            (date(2013, 7, 4), date(2013, 7, 4)),
        )
