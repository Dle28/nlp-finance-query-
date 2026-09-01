from __future__ import annotations

import pytest

from finance_query.research.research_conclusion import (
    EXPERIMENT_SECTIONS,
    build_research_conclusion,
    validate_research_conclusion,
)


def _inputs() -> dict:
    candidate = "B_A_plus_named_constant_or_percent_scalar"
    baseline_arm = {"eligible_count": 0, "false_confident_count": 0}
    candidate_arm = {"eligible_count": 30, "accuracy_on_eligible": 1.0, "false_confident_count": 0}
    dataset_map = {
        "question_count": 1012,
        "baseline_overall": {"status_counts": {"ABSTAIN": 1012}, "accuracy": "NOT_MEASURABLE"},
        "question_family_counts": {
            "reported_value_lookup": 414, "filter_then_rank_or_select": 125,
            "rank_or_select_extreme": 107, "comparison": 83, "aggregate": 77,
            "count_matching_items": 4, "net_value_composition": 4,
            "counterfactual_composition": 7, "filter_then_compute": 21, "filter_then_count": 21,
        },
        "operation_family_counts": {"lookup": 414},
        "temporal_family_counts": {"opening_instant": 23, "time_unspecified": 1},
        "linguistic_diversity": {
            "unique_questions": 1012, "unique_wording_templates": 1003,
            "unique_semantic_signatures": 772,
        },
    }
    split = {
        "company_overlap_counts": {"a": 0},
        "wording_overlap_development_untouched": 0,
        "metric_holdout": {"family": "return_and_future_value", "untouched_count": 8},
        "composition_holdout": {"signature": "multiply", "untouched_count": 7},
        "contaminated_test_excluded_count": 23,
    }
    discovery = {"candidate_outcome_counts": {"IMPROVED": 10}}
    development = {
        "arms": {candidate: {**candidate_arm, "eligible_count": 29},
                 "C_B_without_percent_literal_scalars": {},
                 "D_B_without_named_constant_scalars": {}},
        "candidate_outcome_counts": {"IMPROVED": 29, "UNCHANGED": 1},
    }
    untouched = {
        "arms": {"A_current_locked_kernel_contract": baseline_arm, candidate: candidate_arm},
        "candidate_outcome_counts": {"IMPROVED": 30},
        "metric_outcome_counts": {"return_and_future_value": {"IMPROVED": 8}},
        "composition_outcome_counts": {"multiply": {"IMPROVED": 7}},
    }
    full_external = {
        "record_count": 424,
        "arms": {candidate: {"eligible_count": 413, "false_confident_count": 0}},
        "candidate_outcome_counts": {"IMPROVED": 413, "UNCHANGED": 11},
    }
    full_vifinqa = {
        "overall_outcome_counts": {"UNCHANGED": 1012},
        "abstention": {"baseline_count": 1012, "candidate_count": 1012, "change": 0},
        "false_confidence_change": "NOT_ESTIMABLE_NO_PREDICTIONS",
        "regression_count": 0,
    }
    replay = {"reproducibility": {"a": True, "b": True}}
    return {
        "dataset_map": dataset_map, "split_summary": split, "discovery": discovery,
        "development": development, "untouched": untouched, "full_external": full_external,
        "full_vifinqa": full_vifinqa, "candidate_replay": replay,
    }


def test_conclusion_has_exact_sections_questions_and_retained_rule() -> None:
    report = build_research_conclusion(**_inputs())
    validate_research_conclusion(report)
    assert list(report["experiment_report_sections"]) == EXPERIMENT_SECTIONS
    assert len(report["final_research_questions"]) == 17
    assert report["retained_rules"][0]["unseen_evidence"]["false_confident"] == 0
    assert report["submission_readiness"]["submission_zip_created"] is False


def test_conclusion_rejects_unseen_false_confidence() -> None:
    inputs = _inputs()
    inputs["untouched"]["arms"]["B_A_plus_named_constant_or_percent_scalar"]["false_confident_count"] = 1
    with pytest.raises(ValueError, match="does not support"):
        build_research_conclusion(**inputs)
