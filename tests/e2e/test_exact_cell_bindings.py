from __future__ import annotations

import pytest

from finance_query.e2e.core.exact_cell_bindings import _candidate_source_cell


def test_candidate_only_coordinate_rehydrates_from_v2_with_explicit_contract() -> None:
    raw, provenance = _candidate_source_cell(
        candidate={
            "source_contract": {
                "candidate_only": True,
                "may_select_value": False,
            }
        },
        rows=[["Nhãn", "2023 VND"], ["Doanh thu", "123"]],
        provenance=[[{}, {}], [{}, {"source_row": 1, "source_cell": 1}]],
        row_index=1,
        column_index=1,
    )
    assert raw == "123"
    assert provenance == {"source_row": 1, "source_cell": 1}


def test_value_free_coordinate_without_candidate_contract_is_rejected() -> None:
    with pytest.raises(ValueError, match="candidate-only contract"):
        _candidate_source_cell(
            candidate={},
            rows=[["Nhãn", "2023 VND"], ["Doanh thu", "123"]],
            provenance=[[{}, {}], [{}, {"source_row": 1, "source_cell": 1}]],
            row_index=1,
            column_index=1,
        )


def test_legacy_raw_candidate_still_requires_exact_v2_match() -> None:
    with pytest.raises(ValueError, match="provenance mismatch"):
        _candidate_source_cell(
            candidate={
                "raw_source_cell": "wrong",
                "cell_provenance": {"source_row": 1, "source_cell": 1},
            },
            rows=[["Nhãn", "2023 VND"], ["Doanh thu", "123"]],
            provenance=[[{}, {}], [{}, {"source_row": 1, "source_cell": 1}]],
            row_index=1,
            column_index=1,
        )
