from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from finance_query.schemas import DirectBinding
from finance_query.submission import (
    SubmissionValidationError,
    validate_submission_directory,
    validate_submission_zip,
    write_direct_lookup_record,
    write_execution_record,
)


QUESTION = {"id": 1, "question": "Giá trị kiểm thử là bao nhiêu?"}


def _record(answer: float = 12.5) -> dict:
    return {
        "id": 1,
        "question": QUESTION["question"],
        "answer": answer,
        "relevant_docs": ["ABC_financial_statements_2023_separate"],
        "relevant_tables": ["ABC_financial_statements_2023_separate|12"],
        "evidence": [{"variable": "df1", "csv_path": "data/table.csv"}],
        "pandas_query": "float(df1.loc[df1['item'] == 'metric', '2023'].iloc[0])",
    }


class SubmissionContractTests(unittest.TestCase):
    def _package(self, root: Path, record: dict | None = None) -> None:
        (root / "data").mkdir()
        (root / "data/table.csv").write_text("item,2023\nmetric,12.5\n", encoding="utf-8")
        (root / "submission.json").write_text(json.dumps([record or _record()]), encoding="utf-8")

    def test_valid_directory_executes_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._package(root)
            self.assertEqual(validate_submission_directory(root, [QUESTION]).to_dict(), {
                "record_count": 1,
                "csv_count": 1,
                "executed_queries": 1,
            })

    def test_answer_must_equal_query_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._package(root, _record(9.0))
            with self.assertRaisesRegex(SubmissionValidationError, "differs"):
                validate_submission_directory(root, [QUESTION])

    def test_zip_must_keep_submission_at_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._package(root)
            archive = root / "submission.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.write(root / "submission.json", "nested/submission.json")
                bundle.write(root / "data/table.csv", "nested/data/table.csv")
            with self.assertRaisesRegex(SubmissionValidationError, "root submission.json"):
                validate_submission_zip(archive, [QUESTION], root / "unpacked")

    def test_direct_compiler_emits_grounded_csv_and_runnable_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = {
                "document_id": "ABC_financial_statements_2023_separate",
                "local_ordinal": 12,
                "rows_json": json.dumps(
                    [["", "Năm 2023"], ["Lãi tiền gửi", "12.500.000"]],
                    ensure_ascii=False,
                ),
            }
            binding = DirectBinding(
                internal_table_uid="u1",
                document_id=asset["document_id"],
                row_index=1,
                column_index=1,
                row_text="Lãi tiền gửi",
                column_text="Năm 2023",
                raw_value="12.500.000",
                parsed_value="12500000",
                source_unit="vnd",
                target_unit="million_vnd",
                converted_value="12.5",
                binding_score=0.9,
            )
            record = write_direct_lookup_record(
                question_id=1,
                question=QUESTION["question"],
                binding=binding,
                asset=asset,
                package_root=root,
            )
            (root / "submission.json").write_text(json.dumps([record], ensure_ascii=False), encoding="utf-8")
            result = validate_submission_directory(root, [QUESTION])
            self.assertEqual(result.executed_queries, 1)
            self.assertEqual(record["answer"], 12.5)
            self.assertIn("source_row_index", record["pandas_query"])

    def test_execution_compiler_reuses_exact_table_for_multiple_operands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = {
                "internal_table_uid": "income-u1",
                "document_id": "ABC_financial_statements_2023_separate",
                "local_ordinal": 9,
                "rows": [
                    ["", "Năm 2023"],
                    ["Lợi nhuận gộp", "30.000.000"],
                    ["Doanh thu thuần", "120.000.000"],
                ],
            }
            profit = DirectBinding(
                internal_table_uid="income-u1",
                document_id=asset["document_id"],
                row_index=1,
                column_index=1,
                row_text="Lợi nhuận gộp",
                column_text="Năm 2023",
                raw_value="30.000.000",
                parsed_value="30000000",
                source_unit="vnd",
                target_unit=None,
                converted_value="30000000",
                binding_score=0.95,
            )
            revenue = DirectBinding(
                internal_table_uid="income-u1",
                document_id=asset["document_id"],
                row_index=2,
                column_index=1,
                row_text="Doanh thu thuần",
                # Legacy tables can yield a distinct derived header string
                # for a later data row. The compiler must preserve both
                # explicit bindings rather than overwrite the first one.
                column_text="Năm 2023 > 30.000.000",
                raw_value="120.000.000",
                parsed_value="120000000",
                source_unit="vnd",
                target_unit=None,
                converted_value="120000000",
                binding_score=0.95,
            )
            record = write_execution_record(
                question_id=1,
                question=QUESTION["question"],
                operand_bindings={"profit": (profit, asset), "revenue": (revenue, asset)},
                operation_ast={
                    "op": "multiply",
                    "args": [{"op": "divide", "args": ["profit", "revenue"]}, 100],
                },
                package_root=root,
                normalize_operands_to_vnd=True,
            )
            self.assertEqual(record["answer"], 25.0)
            self.assertEqual(len(record["evidence"]), 1)
            self.assertEqual(len(record["relevant_tables"]), 1)
            (root / "submission.json").write_text(
                json.dumps([record], ensure_ascii=False), encoding="utf-8"
            )
            result = validate_submission_directory(root, [QUESTION])
            self.assertEqual(result.executed_queries, 1)

    def test_compiler_rejects_binding_that_disagrees_with_exact_source_cell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = {
                "document_id": "ABC_financial_statements_2023_separate",
                "local_ordinal": 12,
                "rows": [["", "Năm 2023"], ["Lãi tiền gửi", "12.500.000"]],
            }
            binding = DirectBinding(
                internal_table_uid="u1",
                document_id=asset["document_id"],
                row_index=1,
                column_index=1,
                row_text="Lãi tiền gửi",
                column_text="Năm 2023",
                raw_value="99.000.000",
                parsed_value="99000000",
                source_unit="vnd",
                target_unit="million_vnd",
                converted_value="99",
                binding_score=0.9,
            )
            with self.assertRaisesRegex(SubmissionValidationError, "raw value differs"):
                write_direct_lookup_record(
                    question_id=1,
                    question=QUESTION["question"],
                    binding=binding,
                    asset=asset,
                    package_root=root,
                )


if __name__ == "__main__":
    unittest.main()
