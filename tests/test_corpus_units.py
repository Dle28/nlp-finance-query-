from __future__ import annotations

import unittest

from finance_query.corpus import infer_unit


class CorpusUnitTests(unittest.TestCase):
    def test_detects_standalone_vnd_in_period_header(self) -> None:
        self.assertEqual(
            infer_unit("Bảng cân đối kế toán", "Chỉ tiêu | 31/12/2023 VND | 1/1/2023 VND"),
            "vnd",
        )

    def test_prefers_scaled_vnd_over_standalone_marker(self) -> None:
        self.assertEqual(
            infer_unit("ĐVT: triệu VND", "Năm 2023 VND"),
            "million_vnd",
        )

    def test_does_not_invent_unit_from_bare_dong_word(self) -> None:
        self.assertIsNone(
            infer_unit("Giá trị thanh toán bằng đồng ý của các bên", "Chỉ tiêu | 2023"),
        )


if __name__ == "__main__":
    unittest.main()
