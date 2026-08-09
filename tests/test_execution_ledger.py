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
ALT_UID = "b" * 64


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


def _equivalence_context(uid: str, label: str = "Số cuối năm VND") -> dict:
    return {
        "internal_table_uid": uid,
        "canonical_headers": {
            "columns": [
                {"column_index": 0, "source_label": "Chỉ tiêu"},
                {"column_index": 1, "source_label": label},
            ]
        },
        "row_profiles": [
            {"row_index": 0, "role": "header", "numeric_columns": []},
            {"row_index": 1, "role": "data", "numeric_columns": [1]},
        ],
    }


def _equivalence_table(uid: str, label: str) -> dict:
    return {
        "internal_table_uid": uid,
        "document_id": f"ABC_{uid[:1]}",
        "unit_hint": "vnd",
        "rows": [["Chỉ tiêu", "Số cuối năm VND"], [label, "1.880.000.000"]],
        "cell_provenance": [
            [
                {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                for column in range(2)
            ]
            for row in range(2)
        ],
    }


def _formula_table(uid: str, year: int, value: str) -> dict:
    return {
        "internal_table_uid": uid,
        "document_id": f"ABC_financial_statements_{year}_separate",
        "local_ordinal": 1,
        "unit_hint": "vnd",
        "rows": [["Chỉ tiêu", f"Năm {year}"], ["Doanh thu xây dựng", value]],
        "cell_provenance": [
            [
                {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                for column in range(2)
            ]
            for row in range(2)
        ],
    }


def _formula_context(year: int) -> dict:
    return {
        "canonical_headers": {
            "columns": [
                {"column_index": 0, "source_label": "Chỉ tiêu"},
                {"column_index": 1, "source_label": f"Năm {year}"},
            ]
        },
        "row_profiles": [
            {"row_index": 0, "role": "header", "numeric_columns": []},
            {"row_index": 1, "role": "data", "numeric_columns": [1]},
        ],
    }


def _formula_match(uid: str, year: int, value: str) -> dict:
    source_cell = {"anchor_row": 1, "anchor_column": 1, "covered_by_span": False}
    return {
        "internal_table_uid": uid,
        "source_row": ["Doanh thu xây dựng", value],
        "binding": {
            "status": "cell_bound",
            "row_index": 1,
            "column_index": 1,
            "column_label": f"Năm {year}",
            "raw_value": value,
            "parsed_value": value,
            "source_cell": source_cell,
        },
    }


def _cfo_formula_table(uid: str, year: int, value: str) -> dict:
    table = _formula_table(uid, year, value)
    table.update(
        {
            "document_id": f"QNS_financial_statements_{year}_separate",
            "ticker": "QNS",
            "report_year": year,
            "scope": "separate",
            "rows": [["Chỉ tiêu", f"Năm {year} VND"], ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", value]],
        }
    )
    return table


def _cfo_formula_context(year: int) -> dict:
    context = _formula_context(year)
    context["canonical_headers"]["columns"][1]["source_label"] = f"Năm {year} VND"
    context["table_function"] = {"kind": "cash_flow_statement"}
    return context


def _cfo_formula_match(uid: str, year: int, value: str) -> dict:
    match = _formula_match(uid, year, value)
    match["source_row"] = ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", value]
    match["binding"]["column_label"] = f"Năm {year} VND"
    match["scope"] = "separate"
    return match


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

    def test_equivalent_critic_policy_revalidates_every_alternate_v2_cell(self) -> None:
        selected = _equivalence_table(UID, "Tiền")
        alternate = _equivalence_table(ALT_UID, "Tiền gửi")
        review = _review()
        self_review = review["machine_self_review"]
        self_review.update(
            {
                "critic_accepts": False,
                "selection_policy": "strict_equivalent_critic_answer",
                "selected_assessment": {
                    "uid": UID,
                    "semantic_score": 1.0,
                    "evidence_score": 0.95,
                    "source_score": 1.0,
                    "metadata_score": 1.0,
                    "raw_metric_identity": {"exact": True},
                    "value_binding": self_review["selected_value_binding"],
                },
                "candidate_assessments": [
                    {
                        "uid": UID,
                        "semantic_score": 1.0,
                        "evidence_score": 0.95,
                        "source_score": 1.0,
                        "metadata_score": 1.0,
                    },
                    {
                        "uid": ALT_UID,
                        "semantic_score": 1.0,
                        "evidence_score": 0.95,
                        "source_score": 1.0,
                        "metadata_score": 1.0,
                    },
                ],
                "equivalent_critic_alternatives": [
                    {
                        "internal_table_uid": ALT_UID,
                        "raw_row_label": "Tiền gửi",
                        "row_index": 1,
                        "column_index": 1,
                        "column_label": "Số cuối năm VND",
                        "raw_value": "1.880.000.000",
                        "parsed_value": "1880000000",
                        "source_unit": "vnd",
                        "source_cell": {
                            "anchor_row": 1,
                            "anchor_column": 1,
                            "covered_by_span": False,
                        },
                    }
                ],
            }
        )
        review["agent_votes"] = {
            agent: UID
            for agent in ("semantic_agent", "evidence_agent", "challenger_agent")
        }
        contexts = {UID: _equivalence_context(UID), ALT_UID: _equivalence_context(ALT_UID)}
        row = ledger.direct_execution_row(
            _item(), review, {UID: selected, ALT_UID: alternate}, contexts
        )
        self.assertEqual(row["execution_status"], "grounded")
        self.assertFalse(row["review_provenance"]["critic_accepts"])
        self.assertTrue(row["review_provenance"]["critic_equivalence_revalidated"])

        review["machine_self_review"]["equivalent_critic_alternatives"][0]["raw_value"] = "9"
        row = ledger.direct_execution_row(
            _item(), review, {UID: selected, ALT_UID: alternate}, contexts
        )
        self.assertEqual(row["execution_status"], "not_executable")
        self.assertEqual(row["reason"], "critic_equivalence_not_revalidated")

    def test_complete_percentage_evidence_becomes_exact_formula_execution(self) -> None:
        old_uid, new_uid = "old", "new"
        evidence = {
            "id": 1,
            "evidence_completeness": "complete",
            "reason_codes": [],
            "formula": {
                "formula_id": "percentage_change",
                "definition_status": "defined",
                "confidence": 0.96,
            },
            "selected_operand_matches": {
                "x_old": _formula_match(old_uid, 2022, "100"),
                "x_new": _formula_match(new_uid, 2023, "80"),
            },
            "source_discovery": {"enabled": True},
        }
        row = ledger.exact_formula_execution_row(
            {"id": 1},
            evidence,
            {
                old_uid: _formula_table(old_uid, 2022, "100"),
                new_uid: _formula_table(new_uid, 2023, "80"),
            },
            {old_uid: _formula_context(2022), new_uid: _formula_context(2023)},
            manifest={
                "sidecar_sha256": "formula-sha",
                "evidence_context_file": "tables_evidence_context_v2.jsonl",
            },
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["execution_mode"], "exact_formula")
        self.assertEqual(row["computed_answer"], "-20.0")
        self.assertEqual(row["operation_ast"], {"op": "percentage_change", "args": ["x_new", "x_old"]})
        self.assertFalse(row["formula_provenance"]["review_status_promoted"])

    def test_complete_single_entity_cfo_argmax_returns_year_without_promoting_review(self) -> None:
        evidence = {
            "id": 1,
            "evidence_completeness": "complete",
            "reason_codes": [],
            "formula": {
                "formula_id": "operating_cash_flow_argmax_period",
                "definition_status": "defined",
                "confidence": 0.995,
                "entity": "QNS",
                "operands": [
                    {"operand_id": "cfo_2021", "entity": "QNS", "years": [2021], "role": "period_argmax_value", "allowed_table_functions": ["cash_flow_statement"]},
                    {"operand_id": "cfo_2023", "entity": "QNS", "years": [2023], "role": "period_argmax_value", "allowed_table_functions": ["cash_flow_statement"]},
                ],
            },
            "selected_operand_matches": {
                "cfo_2021": _cfo_formula_match("cfo-old", 2021, "100"),
                "cfo_2023": _cfo_formula_match("cfo-new", 2023, "120"),
            },
        }
        row = ledger.exact_formula_execution_row(
            {"id": 1},
            evidence,
            {
                "cfo-old": _cfo_formula_table("cfo-old", 2021, "100"),
                "cfo-new": _cfo_formula_table("cfo-new", 2023, "120"),
            },
            {"cfo-old": _cfo_formula_context(2021), "cfo-new": _cfo_formula_context(2023)},
            manifest={"sidecar_sha256": "formula-sha", "evidence_context_file": "tables_evidence_context_v3.jsonl"},
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["computed_answer"], "2023")
        self.assertEqual(row["execution_mode"], "exact_formula_period_argmax")
        self.assertFalse(row["submission_eligible"])
        self.assertFalse(row["formula_provenance"]["review_status_promoted"])

        evidence["selected_operand_matches"]["cfo_2021"]["binding"]["raw_value"] = "120"
        self.assertIsNone(
            ledger.exact_formula_execution_row(
                {"id": 1}, evidence,
                {"cfo-old": _cfo_formula_table("cfo-old", 2021, "100"), "cfo-new": _cfo_formula_table("cfo-new", 2023, "120")},
                {"cfo-old": _cfo_formula_context(2021), "cfo-new": _cfo_formula_context(2023)},
                manifest={"sidecar_sha256": "formula-sha", "evidence_context_file": "tables_evidence_context_v3.jsonl"},
            )
        )

    def test_formula_execution_requires_high_controlled_formula_confidence(self) -> None:
        evidence = {
            "id": 1,
            "evidence_completeness": "complete",
            "reason_codes": [],
            "formula": {
                "formula_id": "percentage_change",
                "definition_status": "defined",
                "confidence": 0.94,
            },
            "selected_operand_matches": {
                "x_old": _formula_match("old", 2022, "100"),
                "x_new": _formula_match("new", 2023, "80"),
            },
        }
        self.assertIsNone(
            ledger.exact_formula_execution_row(
                {"id": 1},
                evidence,
                {
                    "old": _formula_table("old", 2022, "100"),
                    "new": _formula_table("new", 2023, "80"),
                },
                {"old": _formula_context(2022), "new": _formula_context(2023)},
                manifest={
                    "sidecar_sha256": "formula-sha",
                    "evidence_context_file": "tables_evidence_context_v2.jsonl",
                },
            )
        )

    def test_formula_execution_rejects_a_cell_that_differs_from_v2(self) -> None:
        evidence = {
            "id": 1,
            "evidence_completeness": "complete",
            "reason_codes": [],
            "formula": {
                "formula_id": "percentage_change",
                "definition_status": "defined",
                "confidence": 0.96,
            },
            "selected_operand_matches": {
                "x_old": _formula_match("old", 2022, "999"),
                "x_new": _formula_match("new", 2023, "80"),
            },
        }
        self.assertIsNone(
            ledger.exact_formula_execution_row(
                {"id": 1},
                evidence,
                {
                    "old": _formula_table("old", 2022, "100"),
                    "new": _formula_table("new", 2023, "80"),
                },
                {"old": _formula_context(2022), "new": _formula_context(2023)},
                manifest={
                    "sidecar_sha256": "formula-sha",
                    "evidence_context_file": "tables_evidence_context_v2.jsonl",
                },
            )
        )


if __name__ == "__main__":
    unittest.main()
