import unittest

from finance_query.evidence_context import build_evidence_context, recover_continuation_headers


class EvidenceContextTests(unittest.TestCase):
    def test_span_parent_header_is_recovered_without_changing_grid_cells(self):
        table = {
            "internal_table_uid": "u1",
            "rows": [
                ["Chỉ tiêu", "Số cuối năm", "", "Số đầu năm", ""],
                ["", "Giá trị VND", "Dự phòng", "Giá trị VND", "Dự phòng"],
                ["Tiền", "100", "-", "80", "-"],
            ],
            "header_row_indices": [0, 1],
            "cell_provenance": [
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": True},
                    {"anchor_row": 0, "anchor_column": 3, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 3, "covered_by_span": True},
                ],
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": True},
                    {"anchor_row": 1, "anchor_column": 1, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 2, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 3, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 4, "covered_by_span": False},
                ],
                [
                    {"anchor_row": 2, "anchor_column": column, "covered_by_span": False}
                    for column in range(5)
                ],
            ],
            "source_provenance": {},
        }

        context = build_evidence_context(table)

        headers = context["canonical_headers"]["columns"]
        self.assertEqual(table["rows"][0][2], "")
        self.assertEqual(headers[2]["source_label"], "Số cuối năm · Dự phòng")
        self.assertEqual(headers[3]["source_label"], "Số đầu năm · Giá trị VND")
        self.assertEqual(context["quality"]["status"], "review_ready")

    def test_invalid_provenance_is_blocked_not_repaired(self):
        table = {
            "internal_table_uid": "u2",
            "rows": [["Chỉ tiêu", "2024"], ["Tiền", "100"]],
            "header_row_indices": [0],
            "cell_provenance": [],
        }

        context = build_evidence_context(table)

        self.assertEqual(context["quality"]["status"], "blocked")
        self.assertIn("provenance_shape_mismatch", context["quality"]["reason_codes"])

    def test_numeric_rows_after_first_data_are_not_canonical_headers(self):
        table = {
            "internal_table_uid": "u_header_drift",
            "rows": [
                ["", "Năm 2025", "Năm 2024"],
                ["Lợi nhuận", "23.989.930", "15.778.795"],
                ["Số cổ phiếu bình quân", "7.933.923.601", "7.933.923.601"],
                ["EPS", "3.024", "1.989"],
            ],
            "header_row_indices": [0, 2, 3],
            "cell_provenance": [
                [
                    {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                    for column in range(3)
                ]
                for row in range(4)
            ],
            "source_provenance": {},
        }
        context = build_evidence_context(table)
        headers = context["canonical_headers"]
        self.assertEqual(headers["header_row_indices"], [0])
        self.assertEqual(headers["excluded_header_row_indices"], [2, 3])
        self.assertEqual(headers["columns"][1]["source_label"], "Năm 2025")
        self.assertEqual(
            [row["role"] for row in context["row_profiles"]],
            ["header", "data", "data", "data"],
        )
        self.assertIn(
            "nonleading_header_rows_excluded_from_canonical_path",
            context["quality"]["reason_codes"],
        )

    def test_adjacent_continuation_recovers_only_compatible_raw_header(self):
        previous = {
            "internal_table_uid": "previous",
            "document_id": "D",
            "local_ordinal": 4,
            "table_function": {"kind": "cash_flow_statement"},
            "rows": [["Chỉ tiêu", "Số cuối năm"], ["Tiền", "100"]],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                ],
                [
                    {"anchor_row": 1, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 1, "covered_by_span": False},
                ],
            ],
        }
        continuation = {
            "internal_table_uid": "continuation",
            "document_id": "D",
            "local_ordinal": 5,
            "table_function": {"kind": "cash_flow_statement"},
            "rows": [["Chi đầu tư", "80"]],
            "header_row_indices": [],
            "cell_provenance": [
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                ]
            ],
        }
        contexts = [build_evidence_context(previous), build_evidence_context(continuation)]

        result = recover_continuation_headers(contexts)

        self.assertEqual(result["recovered_count"], 1)
        recovered = contexts[1]
        self.assertEqual(recovered["quality"]["status"], "review_ready")
        self.assertEqual(
            recovered["canonical_headers"]["recovered_from"]["internal_table_uid"],
            "previous",
        )
        self.assertEqual(
            recovered["canonical_headers"]["columns"][1]["source_label"],
            "Số cuối năm",
        )


if __name__ == "__main__":
    unittest.main()
