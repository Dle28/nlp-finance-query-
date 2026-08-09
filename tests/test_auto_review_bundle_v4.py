import importlib.util
import sys
import unittest
from pathlib import Path

from finance_query.evidence_context import AUTONOMOUS_REVIEW_PROTOCOL, build_evidence_context


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "auto_review_bundle_v4",
    ROOT / "scripts" / "auto_review_bundle_v4.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class AutonomousReviewV4Tests(unittest.TestCase):
    def test_no_eligible_candidate_records_the_current_context_protocol(self):
        review, _quarantine = mod.autonomous_review_item(
            {
                "id": 1,
                "question": "Tiền cuối năm là bao nhiêu?",
                "weak_family": "direct_lookup",
                "candidates": [],
            },
            {},
            {},
            0.85,
            0.45,
            0.67,
            0.84,
        )
        self.assertEqual(
            review["machine_self_review"]["protocol"], AUTONOMOUS_REVIEW_PROTOCOL
        )

    def test_explicit_year_binds_only_matching_raw_header(self):
        table = {
            "internal_table_uid": "u1",
            "rows": [["", "Năm 2023", "Năm 2022"], ["Tiền", "100", "80"]],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 2, "covered_by_span": False},
                ],
                [
                    {"anchor_row": 1, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 1, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 2, "covered_by_span": False},
                ],
            ],
            "source_provenance": {},
        }
        context = build_evidence_context(table)
        binding = mod.bind_value_row(
            {"question": "Tiền năm 2023 là bao nhiêu?"},
            {"report_year": 2023, "structure_validation": {"row_index": 1}},
            table,
            context,
        )
        self.assertEqual(binding["status"], "cell_bound")
        self.assertEqual(binding["column_index"], 1)
        self.assertEqual(binding["value"], "100")
        self.assertEqual(binding["binding_reason"], "explicit_year_header")

    def test_year_without_matching_raw_header_remains_unbound(self):
        table = {
            "internal_table_uid": "u1",
            "rows": [["", "Năm 2023", "Năm 2022"], ["Tiền", "100", "80"]],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 2, "covered_by_span": False},
                ],
                [
                    {"anchor_row": 1, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 1, "covered_by_span": False},
                    {"anchor_row": 1, "anchor_column": 2, "covered_by_span": False},
                ],
            ],
            "source_provenance": {},
        }
        context = build_evidence_context(table)
        binding = mod.bind_value_row(
            {"question": "Tiền năm 2021 là bao nhiêu?"},
            {"report_year": 2023, "structure_validation": {"row_index": 1}},
            table,
            context,
        )
        self.assertEqual(binding["status"], "ambiguous_period_column")

    def test_unreliable_ocr_number_cannot_be_cell_bound(self):
        table = {
            "internal_table_uid": "u_unsafe",
            "rows": [
                ["", "Năm 2023"],
                ["Chi phí tài chính", "(72.193.585.614)(27.471.160.925)"],
            ],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                    for column in range(2)
                ]
                for row in range(2)
            ],
            "source_provenance": {},
        }
        context = build_evidence_context(table)
        binding = mod.bind_value_row(
            {"question": "Chi phí tài chính năm 2023 là bao nhiêu?"},
            {"report_year": 2023, "structure_validation": {"row_index": 1}},
            table,
            context,
        )
        self.assertNotEqual(binding["status"], "cell_bound")
        self.assertEqual(binding["reason"], "projected evidence row is not a canonical data row")

    def test_direct_lookup_requires_exact_row_and_period_cell_binding(self):
        table = {
            "internal_table_uid": "u1",
            "rows": [["", "Số cuối năm"], ["Tiền", "100"]],
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
            "source_provenance": {},
        }
        context = build_evidence_context(table)
        item = {
            "id": 1,
            "question": "Tiền vào cuối năm là bao nhiêu?",
            "weak_family": "direct_lookup",
            "effective_metric": "Tiền",
            "question_plan": {"family": "direct_lookup", "operands": [{"metric": "Tiền"}]},
            "candidates": [
                {
                    "internal_table_uid": "u1",
                    "rank": 1,
                    "candidate_source": "retrieved",
                    "metadata_score": 1.0,
                    "ticker_match": True,
                    "scope_match": True,
                    "year_match": True,
                    "best_row_index": 1,
                    "direct_evidence": "VALUE: Tiền | 100",
                    "evidence_features": {
                        "row_score": 1.0,
                        "metric_overlap": 1.0,
                        "question_overlap": 1.0,
                        "numeric": True,
                    },
                    "structure_validation": {"validated": True, "row_index": 1},
                }
            ],
        }

        review, _quarantine = mod.autonomous_review_item(
            item,
            {"u1": table},
            {"u1": context},
            token_gate=0.85,
            bigram_gate=0.45,
            min_agreement=0.67,
            silver_threshold=0.84,
        )

        binding = review["machine_self_review"]["selected_value_binding"]
        self.assertEqual(binding["status"], "cell_bound")
        self.assertEqual(binding["column_index"], 1)
        self.assertEqual(review["consensus_status"], "machine_calibrated")
        self.assertTrue(review["machine_self_review"]["training_eligible"])
        self.assertEqual(
            review["machine_self_review"]["selection_policy"],
            "ordinary_multi_view_consensus",
        )

    def test_related_raw_row_cannot_become_machine_calibrated_silver(self):
        table = {
            "internal_table_uid": "u_related",
            "rows": [["", "Năm 2023"], ["Tổng tài sản", "100"]],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                    for column in range(2)
                ]
                for row in range(2)
            ],
            "source_provenance": {},
        }
        item = {
            "id": 98,
            "question": "Tài sản năm 2023 là bao nhiêu?",
            "weak_family": "direct_lookup",
            "effective_metric": "Tài sản",
            "question_plan": {"family": "direct_lookup", "operands": [{"metric": "Tài sản"}]},
            "candidates": [
                {
                    "internal_table_uid": "u_related",
                    "rank": 1,
                    "candidate_source": "retrieved",
                    "metadata_score": 1.0,
                    "ticker_match": True,
                    "scope_match": True,
                    "year_match": True,
                    "report_year": 2023,
                    "best_row_index": 1,
                    "direct_evidence": "VALUE: Tổng tài sản | 100",
                    "evidence_features": {
                        "row_score": 1.0,
                        "metric_overlap": 1.0,
                        "question_overlap": 1.0,
                        "numeric": True,
                    },
                    "structure_validation": {"validated": True, "row_index": 1},
                }
            ],
        }
        context = build_evidence_context(table)

        review, _quarantine = mod.autonomous_review_item(
            item,
            {"u_related": table},
            {"u_related": context},
            token_gate=0.85,
            bigram_gate=0.45,
            min_agreement=0.67,
            silver_threshold=0.84,
        )

        selected = review["machine_self_review"]["selected_assessment"]
        self.assertFalse(selected["raw_metric_identity"]["exact"])
        self.assertEqual(review["consensus_status"], "machine_provisional")
        self.assertFalse(review["machine_self_review"]["training_eligible"])

    def test_strict_identity_tiebreak_needs_exact_raw_value_label(self):
        def table(uid: str, label: str, value: str) -> dict:
            return {
                "internal_table_uid": uid,
                "rows": [["", "Năm 2023"], [label, value]],
                "header_row_indices": [0],
                "cell_provenance": [
                    [
                        {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                        for column in range(2)
                    ]
                    for row in range(2)
                ],
                "source_provenance": {},
            }

        good = table("good", "Tiền", "100")
        distractor = table("distractor", "Chi phí", "90")
        good_context = build_evidence_context(good)
        distractor_context = build_evidence_context(distractor)

        def candidate(uid: str, row: str, *, rank: int, row_score: float) -> dict:
            return {
                "internal_table_uid": uid,
                "rank": rank,
                "lexical_rank": rank,
                "candidate_source": "retrieved",
                "metadata_score": 1.0,
                "ticker_match": True,
                "scope_match": True,
                "year_match": True,
                "best_row_index": 1,
                "direct_evidence": f"VALUE: {row}",
                "evidence_features": {
                    "row_score": row_score,
                    "metric_overlap": row_score,
                    "question_overlap": row_score,
                    "numeric": True,
                },
                "structure_validation": {"validated": True, "row_index": 1},
            }

        item = {
            "id": 2,
            "question": "Tiền năm 2023 là bao nhiêu?",
            "weak_family": "direct_lookup",
            "effective_metric": "Tiền",
            "question_plan": {"family": "direct_lookup", "operands": [{"metric": "Tiền"}]},
            # The nondiscriminative source/metadata selectors select this
            # earlier candidate by legacy order; semantic/evidence/challenger
            # select the raw-label-identical candidate instead.
            "candidates": [
                candidate("distractor", "Chi phí | 90", rank=1, row_score=0.40),
                candidate("good", "Tiền | 100", rank=2, row_score=1.0),
            ],
        }
        review, _quarantine = mod.autonomous_review_item(
            item,
            {"good": good, "distractor": distractor},
            {"good": good_context, "distractor": distractor_context},
            token_gate=0.85,
            bigram_gate=0.45,
            min_agreement=0.67,
            silver_threshold=0.84,
        )
        selected = review["machine_self_review"]["selected_assessment"]
        self.assertEqual(review["machine_candidate_uid"], "good")
        self.assertEqual(review["consensus_status"], "machine_calibrated")
        self.assertEqual(
            review["machine_self_review"]["selection_policy"],
            "strict_raw_metric_identity_tiebreak",
        )
        self.assertTrue(selected["raw_metric_identity"]["exact"])

        noise = mod.candidate_assessment(
            item,
            item["candidates"][0],
            distractor,
            distractor_context,
            token_gate=0.85,
            bigram_gate=0.45,
        )
        self.assertFalse(noise["raw_metric_identity"]["exact"])

    def test_raw_metric_identity_ignores_only_standalone_note_reference(self):
        table = {
            "internal_table_uid": "u_note",
            "rows": [
                ["Mã số", "Chỉ tiêu", "Thuyết minh", "Năm 2025"],
                ["26", "9. Chi phí quản lý doanh nghiệp", "VI.06", "100"],
            ],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                    for column in range(4)
                ]
                for row in range(2)
            ],
            "source_provenance": {},
        }
        context = build_evidence_context(table)
        item = {
            "question": "Chi phí quản lý doanh nghiệp năm 2025 là bao nhiêu?",
            "effective_metric": "Chi phí quản lý doanh nghiệp năm 2025",
            "question_plan": {"operands": [{"metric": "Chi phí quản lý doanh nghiệp"}]},
        }
        candidate = {
            "internal_table_uid": "u_note",
            "report_year": 2025,
            "structure_validation": {"validated": True, "row_index": 1},
            "evidence_features": {"row_score": 1.0, "numeric": True},
        }
        assessment = mod.candidate_assessment(
            item, candidate, table, context, token_gate=0.85, bigram_gate=0.45
        )
        identity = assessment["raw_metric_identity"]
        self.assertTrue(identity["exact"])
        self.assertEqual(identity["ignored_note_references"], ["VI.06"])
        self.assertIn(
            "structural_row_code_or_expanding_financial_acronym", identity["reason"]
        )

        wrong_item = {**item, "effective_metric": "Chi phí tài chính"}
        wrong_identity = mod.candidate_assessment(
            wrong_item, candidate, table, context, token_gate=0.85, bigram_gate=0.45
        )["raw_metric_identity"]
        self.assertFalse(wrong_identity["exact"])

    def test_raw_metric_identity_normalizes_only_structural_code_and_tndn(self):
        table = {
            "internal_table_uid": "u_identity",
            "rows": [
                ["Mã số", "Chỉ tiêu", "Năm 2023"],
                ["XIII", "Lợi nhuận sau thuế", "100"],
                ["16.", "Chi phí thuế TNDN theo thuế suất hiện hành", "50"],
            ],
            "header_row_indices": [0],
            "cell_provenance": [
                [
                    {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                    for column in range(3)
                ]
                for row in range(3)
            ],
        }
        candidate = {"effective_metric": "Lợi nhuận sau thuế"}
        first = mod.raw_metric_identity(
            {"effective_metric": "Lợi nhuận sau thuế"},
            candidate,
            table,
            {"row_index": 1},
        )
        second = mod.raw_metric_identity(
            {
                "effective_metric": (
                    "Chi phí thuế thu nhập doanh nghiệp theo thuế suất hiện hành"
                )
            },
            {
                "effective_metric": (
                    "Chi phí thuế thu nhập doanh nghiệp theo thuế suất hiện hành"
                )
            },
            table,
            {"row_index": 2},
        )
        self.assertTrue(first["exact"])
        self.assertEqual(len(first["ignored_structural_row_code"]), 1)
        self.assertIn("XIII", first["ignored_structural_row_code"][0])
        self.assertTrue(second["exact"])
        self.assertIn("structural_row_code_or_expanding_financial_acronym", second["reason"])

    def test_equivalent_critic_answer_requires_same_declared_source_unit(self):
        def table(uid: str, label: str, *, unit: str) -> dict:
            return {
                "internal_table_uid": uid,
                "context_before": f"Đơn vị tính: {unit}",
                "rows": [["", "Năm 2023"], [label, "100"]],
                "header_row_indices": [0],
                "cell_provenance": [
                    [
                        {"anchor_row": row, "anchor_column": column, "covered_by_span": False}
                        for column in range(2)
                    ]
                    for row in range(2)
                ],
                "source_provenance": {},
            }

        selected_table = table("selected", "Tổng doanh thu", unit="VND")
        equivalent_table = table("equivalent", "Tổng doanh thu", unit="VND")

        def candidate(uid: str, rank: int) -> dict:
            return {
                "internal_table_uid": uid,
                "rank": rank,
                "lexical_rank": rank,
                "candidate_source": "retrieved",
                "metadata_score": 1.0,
                "ticker_match": True,
                "scope_match": True,
                "year_match": True,
                "best_row_index": 1,
                "direct_evidence": "VALUE: Tổng doanh thu | 100",
                "evidence_features": {
                    "row_score": 1.0,
                    "metric_overlap": 1.0,
                    "question_overlap": 1.0,
                    "numeric": True,
                },
                "structure_validation": {"validated": True, "row_index": 1},
            }

        item = {
            "id": 3,
            "question": "Tổng doanh thu năm 2023 là bao nhiêu?",
            "weak_family": "direct_lookup",
            "effective_metric": "Tổng doanh thu",
            "question_plan": {"family": "direct_lookup", "operands": [{"metric": "Tổng doanh thu"}]},
            "candidates": [candidate("selected", 1), candidate("equivalent", 2)],
        }
        contexts = {
            "selected": build_evidence_context(selected_table),
            "equivalent": build_evidence_context(equivalent_table),
        }
        review, _quarantine = mod.autonomous_review_item(
            item,
            {"selected": selected_table, "equivalent": equivalent_table},
            contexts,
            token_gate=0.85,
            bigram_gate=0.45,
            min_agreement=0.67,
            silver_threshold=0.84,
        )
        self.assertEqual(review["consensus_status"], "machine_calibrated")
        self.assertEqual(
            review["machine_self_review"]["selection_policy"],
            "strict_equivalent_critic_answer",
        )
        self.assertEqual(
            review["machine_self_review"]["equivalent_critic_alternatives"][0]["source_unit"],
            "vnd",
        )

        mismatched_table = table("equivalent", "Tổng doanh thu", unit="Triệu đồng")
        review, _quarantine = mod.autonomous_review_item(
            item,
            {"selected": selected_table, "equivalent": mismatched_table},
            {
                "selected": contexts["selected"],
                "equivalent": build_evidence_context(mismatched_table),
            },
            token_gate=0.85,
            bigram_gate=0.45,
            min_agreement=0.67,
            silver_threshold=0.84,
        )
        self.assertNotEqual(review["consensus_status"], "machine_calibrated")

    def test_direct_source_discovery_replaces_only_row_evidence_and_keeps_rank(self):
        plan = {"family": "direct_lookup", "operands": [{"metric": "Tiền"}]}
        item = {
            "id": 99,
            "question": "Tiền năm 2023 là bao nhiêu?",
            "weak_family": "direct_lookup",
            "question_plan": plan,
            "candidates": [
                {
                    "internal_table_uid": "u1",
                    "rank": 4,
                    "lexical_rank": 3,
                    "dense_rank": 2,
                    "direct_evidence": "VALUE: wrong row",
                }
            ],
        }
        discovered = {
            "schema_version": 1,
            "id": 99,
            "family": "direct_lookup",
            "effective_question_plan_sha256": mod.canonical_sha256(plan),
            "candidates": [
                {
                    "internal_table_uid": "u1",
                    "rank": 1_000_000,
                    "lexical_rank": 1_000_000,
                    "dense_rank": 1_000_000,
                    "candidate_source": "raw_v2_direct_source_discovery",
                    "best_row_index": 7,
                    "one_line_summary": "Nguồn raw V2: TEST | 2023 | separate. Hàng exact: Tiền | 100",
                    "direct_evidence": "VALUE: Tiền | 100",
                    "source_discovery": {
                        "policy": "exact_raw_v2_metric_token_sequence_v1",
                        "row_index": 7,
                    },
                }
            ],
            "ambiguous_same_table_rows": [],
        }
        count, ambiguous = mod.apply_direct_source_discovery([item], [discovered])
        self.assertEqual((count, ambiguous), (1, 0))
        candidate = item["candidates"][0]
        self.assertEqual(candidate["rank"], 4)
        self.assertEqual(candidate["lexical_rank"], 3)
        self.assertEqual(candidate["dense_rank"], 2)
        self.assertEqual(candidate["best_row_index"], 7)
        self.assertEqual(
            candidate["one_line_summary"],
            "Nguồn raw V2: TEST | 2023 | separate. Hàng exact: Tiền | 100",
        )
        self.assertEqual(candidate["direct_evidence"], "VALUE: Tiền | 100")

        discovered["effective_question_plan_sha256"] = "mismatch"
        with self.assertRaisesRegex(ValueError, "plan hash"):
            mod.apply_direct_source_discovery([item], [discovered])

    def test_missing_period_header_cannot_be_autonomous_silver(self):
        table = {
            "internal_table_uid": "u1",
            "rows": [["Tiền", "100"]],
            "header_row_indices": [],
            "cell_provenance": [
                [
                    {"anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                    {"anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                ]
            ],
        }
        context = build_evidence_context(table)
        item = {
            "id": 1,
            "question": "Tiền cuối năm là bao nhiêu?",
            "weak_family": "direct_lookup",
            "effective_metric": "Tiền",
            "question_plan": {"family": "direct_lookup", "operands": [{"metric": "Tiền"}]},
            "candidates": [
                {
                    "internal_table_uid": "u1",
                    "rank": 1,
                    "candidate_source": "retrieved",
                    "metadata_score": 1.0,
                    "best_row_index": 0,
                    "direct_evidence": "VALUE: Tiền | 100",
                    "evidence_features": {"row_score": 1.0, "numeric": True},
                    "structure_validation": {"validated": True, "row_index": 0},
                }
            ],
        }

        review, _quarantine = mod.autonomous_review_item(
            item, {"u1": table}, {"u1": context}, 0.85, 0.45, 0.67, 0.84
        )

        self.assertNotEqual(review["consensus_status"], "machine_calibrated")


if __name__ == "__main__":
    unittest.main()
