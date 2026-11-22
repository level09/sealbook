import itertools
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from stk.migrations import throwaway_database_url

from sealbook.search.arabic import (
    NORMALIZE_SQL,
    candidates,
    name_keys,
    natural,
    normalize,
    same_keys,
)
from sealbook.settings import Config


class NormalizeTests(unittest.TestCase):
    def test_sql_translation(self):
        self.assertEqual(normalize("أإآٱىة٠١٢٣٤٥٦٧٨٩ًٌٍَُِّْـ"), "اااايه0123456789")

    def test_newlines_only_are_collapsed(self):
        self.assertEqual(normalize("  يُـوسُف\r\n\nخالد\rعلي\t "), "  يوسف خالد علي\t ")

    def test_null_and_empty(self):
        self.assertIsNone(normalize(None))
        self.assertEqual(normalize(""), "")

    def test_sql_preserves_escape_sequences(self):
        self.assertIn(r"E'[\r\n]+'", NORMALIZE_SQL)
        self.assertTrue(NORMALIZE_SQL.endswith("$$ LANGUAGE plpgsql IMMUTABLE STRICT"))


class NameTests(unittest.TestCase):
    def test_required_variants_pairwise(self):
        names = (
            "يوسف خالد العلي",
            "Yousef Khaled Al-Ali",
            "Yusuf Khalid al Ali",
            "يوسف خالد علي",
        )
        for first, second in itertools.product(names, repeat=2):
            with self.subTest(first=first, second=second):
                self.assertEqual(name_keys(first), ("sf", "kld", "l"))
                self.assertTrue(candidates(first, second))

    def test_abd_compounds(self):
        for name in (
            "Mohammad Abdulrahman",
            "Muhammad abd al-Rahman",
            "محمد عبد الرحمن",
        ):
            with self.subTest(name=name):
                self.assertEqual(name_keys(name), ("mhmd", "bdrhmn"))
                self.assertTrue(candidates(name, "محمد عبد الرحمن"))

    def test_digraphs_and_doubled_letters(self):
        for arabic, latin in (
            ("خالد", "Khaled"),
            ("شريف", "Shareef"),
            ("ثابت", "Thabit"),
            ("ذهبي", "Dhahabi"),
            ("غسان", "Ghassan"),
        ):
            with self.subTest(latin=latin):
                self.assertEqual(name_keys(arabic), name_keys(latin))

    def test_articles_and_marks(self):
        self.assertEqual(
            name_keys("يُـوسُف خَالِد العَلِيّ"), name_keys("YUSUF Khalid el-Ali")
        )

    def test_order_and_multiplicity(self):
        self.assertTrue(candidates("يوسف علي", "يوسف خالد علي"))
        self.assertTrue(candidates("يوسف خالد علي", "يوسف علي"))
        self.assertFalse(candidates("علي يوسف", "يوسف خالد علي"))
        self.assertFalse(candidates("يوسف يوسف", "يوسف خالد"))

    def test_common_transliteration_variants(self):
        for a, b in [
            ("فاطمة", "Fatima"),
            ("فاطمة", "Fatimah"),
            ("طه", "Taha"),
            ("قاسم", "Kassem"),
            ("قاسم", "Qasim"),
            ("عبد الرحمن", "Abdel Rahman"),
        ]:
            with self.subTest(a=a, b=b):
                self.assertTrue(candidates(a, b))

    def test_given_names_that_begin_with_al(self):
        # A hamza on the alif (إلياس, إلهام) is never the article ال.
        self.assertEqual(name_keys("إلياس"), name_keys("Elias"))
        self.assertEqual(name_keys("إِلْهَام"), name_keys("Ilham"))
        self.assertEqual(name_keys("آلاء"), name_keys("Alaa"))
        # Without the hamza the two letters may be either, so both readings match.
        for a, b in [
            ("الياس", "Elias"),
            ("الياس", "إلياس"),
            ("الهام", "Ilham"),
            ("أدريان إلياس قصار", "Adrian Elias Kassar"),
            ("العلي", "Ali"),
            ("الحلبي", "Halabi"),
            ("الحلبي", "al-Halabi"),
        ]:
            with self.subTest(a=a, b=b):
                self.assertTrue(candidates(a, b))
                self.assertTrue(same_keys(a, b))
        # A real article is still stripped, and a hamza does not stop it elsewhere.
        self.assertEqual(name_keys("يوسف العلي"), ("sf", "l"))
        self.assertEqual(name_keys("نور الدين"), name_keys("Nour al-Din"))
        self.assertEqual(name_keys("عبد الله"), name_keys("Abdullah"))
        self.assertEqual(name_keys("آل سعود"), name_keys("Al Saud"))
        self.assertFalse(same_keys("إلياس", "ياس"))
        self.assertFalse(same_keys("الياس", "Kassar"))

    def test_surname_first_with_a_separator_is_read_in_natural_order(self):
        for name in ("KASSAR / ADRIAN", "Kassar, Adrian", "Kassar,Adrian"):
            with self.subTest(name=name):
                self.assertTrue(same_keys(name, "Adrian Kassar"))
                self.assertTrue(same_keys(name, "أدريان قصار"))
                self.assertTrue(candidates(name, "Adrian Elias Kassar"))
        self.assertEqual(natural("KASSAR / ADRIAN"), "ADRIAN KASSAR")
        self.assertTrue(same_keys("العلي، يوسف خالد", "Yusuf Khalid al-Ali"))
        # Without a separator the written order is the name: Ali Hasan is Ali son
        # of Hasan, not Hasan Ali.
        self.assertFalse(candidates("Kassar Adrian", "Adrian Kassar"))
        # A note or a list is left as written.
        for name in ("Faten (no surname given), nurse", "Ali, Hasan, Omar", "Ali /"):
            with self.subTest(name=name):
                self.assertEqual(natural(name), name)

    def test_different_person(self):
        self.assertFalse(candidates("يوسف خالد العلي", "يوسف محمد العلي"))

    def test_empty_keys_do_not_propose_candidates(self):
        for name in ("", "ال", "al el", "aeiou", "---"):
            with self.subTest(name=name):
                self.assertEqual(name_keys(name), ())
                self.assertFalse(candidates(name, "يوسف"))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(
    make_url(Config.SQLALCHEMY_DATABASE_URI).get_backend_name() == "postgresql",
    "needs Postgres",
)
class SqlParityTest(unittest.TestCase):
    def test_sql_function_matches_python(self):
        samples = [
            "أحمد إبراهيم آمنة ٱلله",
            "مُحَمَّد",
            "عـــلي",
            "مستشفى",
            "فاطمة",
            "١٩٩١",
            "a\r\nb\n\nc",
            "",
        ]
        with throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True) as url:
            engine = create_engine(url)
            try:
                with engine.begin() as connection:
                    connection.execute(text(NORMALIZE_SQL))
                    for sample in samples:
                        got = connection.execute(
                            text("select normalize_arabic_text(:s)"), {"s": sample}
                        ).scalar()
                        self.assertEqual(got, normalize(sample))
            finally:
                engine.dispose()
