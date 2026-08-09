from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from finance_query.evidence_context import AUTONOMOUS_REVIEW_PROTOCOL


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "build_execution_ledger", ROOT / "scripts" / "build_execution_ledger.py"
)
ledger = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ledger)


UID = "a" * 64


def _item() -> dict:
    return {
        "id": 1,
        "weak_family": "direct_lookup",
        "question_plan": {
            "family": "direct_lookup",
            "requested_unit": "billion_vnd",
            "operands": [{"operand_id": "x0"}],
            "operation_ast": {"op": "lookup", "args": ["x0"]},
        },
    }


def _table() -> dict:
    return {
        "internal_table_uid": UID,
        "document_id": "ABC_financial_statements_2023_separate",
        "local_ordinal": 4,
        "unit_hint": "vnd",
        "rows": [["Chỉ tiêu", "Số cuối năm VND"], ["Tiền", "1.880.000.000"]],
        "column_labels": ["Nhãn dòng", "Số cuối năm VND"],
        "structure_quality": {"status": "reconstructed_from_raw_html"},
    }


def _review(value: str = "1.880.000.000") -> dict:
    return {
        "id": 1,
        "consensus_status": "machine_calibrated",
        "machine_candidate_uid": UID,
        "machine_confidence": 0.91,
        "agreement": 0.86,
        "machine_self_review": {
            "protocol": AUTONOMOUS_REVIEW_PROTOCOL,
            "training_eligible": True,
            "critic_accepts": True,
            "selected_value_binding": {
                "status": "cell_bound",
                "row_index": 1,
                "column_index": 1,
                "column_label": "Số cuối năm VND",
                "value": value,
            },
        },
    }


def _context(label: str = "Số cuối năm VND") -> dict:
    return {
        "internal_table_uid": UID,
        "canonical_headers": {
            "columns": [
                {"column_index": 0, "source_label": "Nhãn dòng"},
                {"column_index": 1, "source_label": label},
            ]
        },
    }


class ExecutionLedgerTests(unittest.TestCase):
    def test_machine_calibrated_direct_cell_becomes_exact_execution_record(self) -> None:
        row = ledger.direct_execution_row(_item(), _review(), {UID: _table()})
        self.assertEqual(row["execution_status"], "grounded")
        self.assertEqual(row["grounding_status"], "exact_rows_validated")
        binding = row["operand_bindings"][0]["binding"]
        self.assertEqual(binding["raw_value"], "1.880.000.000")
        self.assertEqual(binding["converted_value"], "1.88")

    def test_value_that_differs_from_v2_source_is_not_executable(self) -> None:
        row = ledger.direct_execution_row(_item(), _review("1.990.000.000"), {UID: _table()})
        self.assertEqual(row["execution_status"], "not_executable")
        self.assertEqual(row["reason"], "selected_value_differs_from_v2_source")

    def test_historic_context_v1_review_cannot_enter_v2_execution(self) -> None:
        review = _review()
        review["machine_self_review"]["protocol"] = "raw_v2_canonical_context_v1"
        row = ledger.direct_execution_row(_item(), review, {UID: _table()})
        self.assertEqual(row["execution_status"], "not_executable")
        self.assertEqual(row["reason"], "review_protocol_not_numeric_safe_v2")

    def test_canonical_header_path_is_the_production_binding_contract(self) -> None:
        review = _review()
        review["machine_self_review"]["selected_value_binding"]["column_label"] = (
            "Đơn vị: VND Tổng cộng · Tại ngày 31 tháng 12 năm 2023"
        )
        row = ledger.direct_execution_row(
            _item(),
            review,
            {UID: _table()},
            {UID: _context("Đơn vị: VND Tổng cộng · Tại ngày 31 tháng 12 năm 2023")},
        )
        self.assertEqual(row["execution_status"], "grounded")

    def test_canonical_header_mismatch_is_not_executable(self) -> None:
        row = ledger.direct_execution_row(
            _item(), _review(), {UID: _table()}, {UID: _context("Năm 2022")}
        )
        self.assertEqual(row["execution_status"], "not_executable")
        self.assertEqual(row["reason"], "selected_column_label_differs_from_canonical_source")


if __name__ == "__main__":
    unittest.main()
