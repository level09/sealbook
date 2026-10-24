import unittest

from stk.ledger.span import validate


class SpanTest(unittest.TestCase):
    def test_spans(self):
        meta = {
            "mime": "application/pdf",
            "pages": [[100, 200]],
            "text_len": 10,
            "duration_ms": 1000,
        }
        cases = [
            ({"page": 1, "bbox": [0, 0, 100, 200]}, meta, ""),
            ({"start": 0, "end": 10}, meta, ""),
            ({"ms": [0, 1000]}, meta, ""),
            ({}, meta, "invalid_span_keys"),
            (None, meta, "invalid_span"),
            ({"start": 0, "end": 1, "extra": 0}, meta, "invalid_span_keys"),
            ({"page": 0, "bbox": [0, 0, 1, 1]}, meta, "page_out_of_range"),
            ({"page": 2, "bbox": [0, 0, 1, 1]}, meta, "page_out_of_range"),
            ({"page": True, "bbox": [0, 0, 1, 1]}, meta, "invalid_page"),
            ({"page": 1, "bbox": [0, 0, 1.0, 1]}, meta, "invalid_bbox"),
            ({"page": 1, "bbox": [0, 0, True, 1]}, meta, "invalid_bbox"),
            ({"page": 1, "bbox": [0, 0, 1]}, meta, "invalid_bbox"),
            ({"page": 1, "bbox": [1, 0, 1, 1]}, meta, "bbox_empty_or_reversed"),
            ({"page": 1, "bbox": [-1, 0, 1, 1]}, meta, "bbox_out_of_bounds"),
            ({"page": 1, "bbox": [0, 0, 101, 1]}, meta, "bbox_out_of_bounds"),
            (
                {"page": 1, "bbox": [0, 0, 1, 1]},
                meta | {"pages": None},
                "pages_required",
            ),
            ({"start": 0, "end": 1}, meta | {"text_len": None}, "text_len_required"),
            ({"start": False, "end": 1}, meta, "invalid_char_range"),
            ({"start": 1, "end": 1}, meta, "char_range_empty_or_reversed"),
            ({"start": -1, "end": 1}, meta, "char_range_out_of_bounds"),
            ({"start": 0, "end": 11}, meta, "char_range_out_of_bounds"),
            (
                {"start": 0, "end": 1},
                meta | {"text_len": 0},
                "char_range_out_of_bounds",
            ),
            ({"ms": [0, 1]}, meta | {"duration_ms": None}, "duration_ms_required"),
            ({"ms": [0, True]}, meta, "invalid_ms_range"),
            ({"ms": [0]}, meta, "invalid_ms_range"),
            ({"ms": [2, 1]}, meta, "ms_range_empty_or_reversed"),
            ({"ms": [0, 1001]}, meta, "ms_range_out_of_bounds"),
        ]
        for span, metadata, reason in cases:
            with self.subTest(span=span, reason=reason):
                self.assertEqual(validate(span, metadata), (not bool(reason), reason))


if __name__ == "__main__":
    unittest.main()
