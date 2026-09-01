from __future__ import annotations

import pytest

from finance_query.research.full_dataset_compare import compare_full_dataset


def _certificate(question_id: int, status: str = "ABSTAIN") -> dict:
    return {"question_id": question_id, "answer_certificate": {"status": status}}


def _taxonomy(question_id: int) -> dict:
    return {
        "question_id": question_id,
        "question_sha256": f"hash-{question_id}",
        "taxonomy": {
            "question_type": "reported_value_lookup",
            "operation_families": ["lookup"],
            "temporal_families": ["single_fiscal_year"],
            "expected_source_topology": "single_value_source_unresolved",
            "entity_family": "single_entity",
            "entities_resolved": ["AAA"],
        },
    }


def test_identical_population_is_unchanged_without_false_claims() -> None:
    rows, report = compare_full_dataset(
        baseline_rows=[_certificate(1)],
        candidate_rows=[_certificate(1)],
        taxonomy_rows=[_taxonomy(1)],
    )
    assert rows[0]["outcome"] == "UNCHANGED"
    assert report["overall_outcome_counts"] == {"UNCHANGED": 1}
    assert report["regression_count"] == 0
    assert report["semantic_improvement_status"] == "NOT_MEASURABLE_NO_VIFINQA_GOLD"
    assert report["by_dimension"]["company_group"]["single_company:AAA"] == {"UNCHANGED": 1}


def test_changed_candidate_is_not_labeled_improvement_or_regression_without_gold() -> None:
    _rows, report = compare_full_dataset(
        baseline_rows=[_certificate(1)],
        candidate_rows=[_certificate(1, "PASS")],
        taxonomy_rows=[_taxonomy(1)],
    )
    assert report["overall_outcome_counts"] == {"CHANGED_UNSCORABLE_WITHOUT_GOLD": 1}
    assert report["regression_count"] is None


def test_population_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="same non-empty IDs"):
        compare_full_dataset(
            baseline_rows=[_certificate(1)],
            candidate_rows=[],
            taxonomy_rows=[_taxonomy(1)],
        )
