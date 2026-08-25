from __future__ import annotations

import pytest

from scripts.research.proof_policy.build_public_pipeline_snapshot_v13 import _public_counts


def _counts() -> dict[str, object]:
    return {
        "question_count": 1012,
        "first_blocker_partition_counts": {
            "composed": 595,
            "route": 353,
            "temporal": 38,
            "v12_complete_shadowed": 26,
        },
        "composed_primary_blocker_counts": {
            "FORMULA_DEFINITION_INCOMPLETE": 501,
            "OPERAND_SET_INCOMPLETE": 94,
        },
        "route_primary_blocker_counts": {
            "TABLE_OR_METRIC_BINDING_UNRESOLVED": 250,
            "FORMULA_OR_OPERATOR_DEFINITION_UNRESOLVED": 37,
            "ROUTE_CAUSE_UNESTABLISHED": 66,
        },
        "temporal_claim_kind_counts": {"instant": 27, "duration": 11},
        "internal_coverage_status_counts": {"INTERNAL_COVERAGE_INCOMPLETE": 1012},
        "claim_completeness_status_counts": {"CLAIM_COMPLETENESS_UNESTABLISHED": 1012},
    }


def test_public_counts_preserve_all_manifest_taxonomy_counts() -> None:
    result = _public_counts(_counts())
    assert result["temporalInstant"] == 27
    assert result["temporalDuration"] == 11
    assert result["composedFormulaDefinition"] == 501
    assert result["routeCauseUnestablished"] == 66


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("composed_primary_blocker_counts", "OPERAND_SET_INCOMPLETE"),
        ("route_primary_blocker_counts", "ROUTE_CAUSE_UNESTABLISHED"),
        ("temporal_claim_kind_counts", "duration"),
        ("first_blocker_partition_counts", "v12_complete_shadowed"),
    ],
)
def test_public_counts_reject_non_reconciling_taxonomy(section: str, field: str) -> None:
    counts = _counts()
    nested = dict(counts[section])
    nested[field] = int(nested[field]) + 1
    counts[section] = nested
    with pytest.raises(ValueError, match="reconcile"):
        _public_counts(counts)
