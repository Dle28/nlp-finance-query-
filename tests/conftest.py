"""Pytest collection policy for local, provenance-bound regression assets."""

from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
ARTIFACT_ROOT = ROOT / "artifacts"

# These modules validate frozen research runs that are intentionally excluded
# from Git. They run in the full local suite when the artifact tree is mounted;
# a clean public checkout reports them as skipped instead of failing on missing
# provenance inputs.
LOCAL_ARTIFACT_MODULES = frozenset(
    {
        "test_build_computational_semantic_component_source_review_queue.py",
        "test_build_computational_semantic_dimension_review_queue.py",
        "test_build_computational_semantic_review_campaign.py",
        "test_build_computational_semantic_review_campaign_status.py",
        "test_build_computational_semantic_review_response_template.py",
        "test_build_computational_semantic_scope_review_queue.py",
        "test_build_computational_semantic_source_review_queue.py",
        "test_build_grounded_campaign_review_handoff.py",
        "test_campaign_chatgpt_audits.py",
        "test_cross_entity_binding_promotions.py",
        "test_cross_entity_operand_reviews.py",
        "test_exact_cell_bindings.py",
        "test_exact_cell_bindings_v2.py",
        "test_formula_evidence_materialization_intake_queue.py",
        "test_formula_evidence_partial_review_queue.py",
        "test_grounded_critic_calibration.py",
        "test_grounded_execution_v2.py",
        "test_grounding_adjudication.py",
        "test_grounding_adjudication_v2.py",
        "test_grounding_repairs.py",
        "test_independent_source_replay_intake_queue.py",
        "test_navigation_binding_promotions.py",
        "test_period_column_candidates_hardening.py",
        "test_production_coverage_adjudication.py",
        "test_production_coverage_v5_readiness.py",
        "test_production_release_gate.py",
        "test_production_release_operations_status.py",
        "test_production_release_remediation_queue.py",
        "test_production_release_review_assignment_registry.py",
        "test_qwen_critic_kernel_notebook.py",
        "test_release_remainder_intake_queues.py",
        "test_route_coverage_review_reconciliation.py",
        "test_semantic_binding_corrections.py",
        "test_typed_plan_abstain_review_queue.py",
        "test_validate_grounded_campaign_human_decision.py",
        "test_validate_integration_quality_gate.py",
        "test_verify_computational_semantic_human_reviews.py",
        "test_verify_production_release_intake_reviews.py",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if ARTIFACT_ROOT.is_dir():
        return
    marker = pytest.mark.skip(
        reason="requires provenance-bound local artifacts excluded from Git"
    )
    for item in items:
        if Path(str(item.fspath)).name in LOCAL_ARTIFACT_MODULES:
            item.add_marker(marker)
