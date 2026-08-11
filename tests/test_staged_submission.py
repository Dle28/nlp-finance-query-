from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_query.schemas import DirectBinding
from finance_query.staged_submission import (
    Q368_FAMILY,
    Q369_FAMILY,
    write_staged_execution_record,
)
from finance_query.submission import (
    SubmissionValidationError,
    validate_submission_directory,
    write_direct_lookup_record,
)


def source_bindings(
    values: dict[str, str], *, document_id: str = "SYNTH_financial_statements_2023_consolidated"
) -> dict[str, tuple[DirectBinding, dict]]:
    rows = [["Chỉ tiêu", "Nguồn"]] + [
        [operand_id, raw_value] for operand_id, raw_value in values.items()
    ]
    asset = {
        "internal_table_uid": "synthetic-table-v2",
        "document_id": document_id,
        "local_ordinal": 1,
        "rows": rows,
    }
    result = {}
    for row_index, (operand_id, raw_value) in enumerate(values.items(), start=1):
        result[operand_id] = (
            DirectBinding(
                internal_table_uid=asset["internal_table_uid"],
                document_id=document_id,
                row_index=row_index,
                column_index=1,
                row_text=operand_id,
                column_text="Nguồn",
                raw_value=raw_value,
                parsed_value=raw_value,
                source_unit="vnd",
                target_unit=None,
                converted_value=raw_value,
                binding_score=1.0,
            ),
            asset,
        )
    return result


QUICK = {
    "quick_ratio_screen.HPG.current_assets": "140",
    "quick_ratio_screen.HPG.inventory": "20",
    "quick_ratio_screen.HPG.current_liabilities": "40",
    "quick_ratio_screen.HSG.current_assets": "60",
    "quick_ratio_screen.HSG.inventory": "20",
    "quick_ratio_screen.HSG.current_liabilities": "40",
    "quick_ratio_screen.MSR.current_assets": "40",
    "quick_ratio_screen.MSR.inventory": "20",
    "quick_ratio_screen.MSR.current_liabilities": "40",
    "quick_ratio_screen.NKG.current_assets": "100",
    "quick_ratio_screen.NKG.inventory": "20",
    "quick_ratio_screen.NKG.current_liabilities": "40",
}


class StagedSubmissionTests(unittest.TestCase):
    def test_q368_replays_median_screen_and_margin_average(self) -> None:
        values = {
            **QUICK,
            "net_profit_margin_after_screen.HSG.net_income": "10",
            "net_profit_margin_after_screen.HSG.net_revenue": "100",
            "net_profit_margin_after_screen.MSR.net_income": "20",
            "net_profit_margin_after_screen.MSR.net_revenue": "100",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = "Q368 synthetic"
            record = write_staged_execution_record(
                question_id=368,
                question=question,
                route_family=Q368_FAMILY,
                operand_bindings=source_bindings(values),
                expected_result_value="15",
                expected_result_unit="percent",
                package_root=root,
            )
            self.assertEqual(record["answer"], 15.0)
            self.assertIn("sorted", record["pandas_query"])
            (root / "submission.json").write_text(json.dumps([record]), encoding="utf-8")
            result = validate_submission_directory(root, [{"id": 368, "question": question}])
            self.assertEqual(result.executed_queries, 1)

    def test_q369_replays_screen_rank_and_interest_coverage(self) -> None:
        values = {
            **QUICK,
            "gross_profit_margin_old.HSG.gross_profit": "20",
            "gross_profit_margin_old.HSG.net_revenue": "100",
            "gross_profit_margin_old.MSR.gross_profit": "30",
            "gross_profit_margin_old.MSR.net_revenue": "100",
            "gross_profit_margin_new.HSG.gross_profit": "30",
            "gross_profit_margin_new.HSG.net_revenue": "100",
            "gross_profit_margin_new.MSR.gross_profit": "31",
            "gross_profit_margin_new.MSR.net_revenue": "100",
            "interest_coverage_lookup.HSG.profit_before_tax": "10",
            "interest_coverage_lookup.HSG.interest_expense": "-2",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = "Q369 synthetic"
            record = write_staged_execution_record(
                question_id=369,
                question=question,
                route_family=Q369_FAMILY,
                operand_bindings=source_bindings(values),
                expected_result_value="6",
                expected_result_unit="times",
                package_root=root,
            )
            self.assertEqual(record["answer"], 6.0)
            (root / "submission.json").write_text(json.dumps([record]), encoding="utf-8")
            result = validate_submission_directory(root, [{"id": 369, "question": question}])
            self.assertEqual(result.executed_queries, 1)

    def test_audited_result_mismatch_fails_closed(self) -> None:
        values = {
            **QUICK,
            "net_profit_margin_after_screen.HSG.net_income": "10",
            "net_profit_margin_after_screen.HSG.net_revenue": "100",
            "net_profit_margin_after_screen.MSR.net_income": "20",
            "net_profit_margin_after_screen.MSR.net_revenue": "100",
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SubmissionValidationError, "differs from audited"):
                write_staged_execution_record(
                    question_id=368,
                    question="Q368 synthetic",
                    route_family=Q368_FAMILY,
                    operand_bindings=source_bindings(values),
                    expected_result_value="14",
                    expected_result_unit="percent",
                    package_root=Path(directory),
                )

    def test_shared_source_csv_preserves_bindings_across_questions(self) -> None:
        values = {"first": "10", "second": "20"}
        bindings = source_bindings(values)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_binding, asset = bindings["first"]
            second_binding, _ = bindings["second"]
            first_binding.column_text = "Canonical first"
            second_binding.column_text = "Canonical second"
            first = write_direct_lookup_record(
                question_id=1,
                question="First?",
                binding=first_binding,
                asset=asset,
                package_root=root,
            )
            second = write_direct_lookup_record(
                question_id=2,
                question="Second?",
                binding=second_binding,
                asset=asset,
                package_root=root,
            )
            (root / "submission.json").write_text(
                json.dumps([first, second]), encoding="utf-8"
            )
            result = validate_submission_directory(
                root,
                [
                    {"id": 1, "question": "First?"},
                    {"id": 2, "question": "Second?"},
                ],
            )
            self.assertEqual(result.executed_queries, 2)


if __name__ == "__main__":
    unittest.main()
