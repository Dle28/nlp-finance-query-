import unittest

from finance_query.evaluation_dashboard import build_evaluation_dashboard


class EvaluationDashboardTests(unittest.TestCase):
    def test_dashboard_cross_tabs_existing_metadata_without_mutating_status(self):
        dashboard = build_evaluation_dashboard(
            [
                {"id": 1, "family": "direct_lookup", "consensus_status": "machine_calibrated", "machine_candidate_uid": "u1", "machine_candidate_source": "raw_v2"},
                {"id": 2, "family": "ratio_or_derived", "consensus_status": "needs_human"},
            ],
            [
                {"id": 1, "formula": {}, "evidence_completeness": "not_applicable"},
                {"id": 2, "formula": {"formula_id": "ratio"}, "evidence_completeness": "partial"},
            ],
            [{"internal_table_uid": "u1", "document_role": "primary_financial_statement"}],
            [{"internal_table_uid": "u1", "triage": {"action": "normal"}}],
        )
        self.assertEqual(dashboard["status_counts"], {"machine_calibrated": 1, "needs_human": 1})
        self.assertEqual(dashboard["cross_tabs"]["family_by_document_role"]["direct_lookup"], {"primary_financial_statement": 1})
        self.assertEqual(dashboard["cross_tabs"]["family_by_ocr_triage"]["ratio_or_derived"], {"no_candidate": 1})
        self.assertEqual(dashboard["formula_evidence_counts"], {"not_formula": 1, "partial": 1})
        self.assertFalse(dashboard["source_contract"]["may_change_review_status"])

    def test_dashboard_rejects_unknown_status(self):
        with self.assertRaisesRegex(ValueError, "unsupported review status"):
            build_evaluation_dashboard(
                [{"id": 1, "consensus_status": "approved"}], [], [], []
            )

