from __future__ import annotations

import finance_query.research.staged_formula_hydrated_source_probe as probe


def test_hydrated_probe_reports_only_safe_navigation_metadata(monkeypatch) -> None:
    candidate = {
        "question_id": 1,
        "document_id": "AAA_2024_consolidated",
        "internal_table_uid": "u1",
        "requested_year": 2024,
        "row_index": 2,
        "column_index": 1,
        "exact_table_locator": {"source_path": "/immutable/AAA.txt"},
        "exact_table_locator_sha256": "a" * 64,
        "scope_status": "SCOPE_NOT_REQUESTED",
        "source_unit_candidate": "VND",
        "header_sha256": "b" * 64,
    }
    monkeypatch.setattr(probe, "_candidate", lambda **_kwargs: (candidate, {"ok": True}))
    monkeypatch.setattr(probe, "_candidate_from_diagnostic", lambda **_kwargs: (("u1", 2, 1), {"ok": True}))
    rows = [{"internal_table_uid": "u1", "observed_scope": "consolidated", "row_index": 2, "row_rank": 1, "row_label": "Lợi nhuận", "numeric_cell_indices": [1]}]
    result = probe._strict_status(
        source_rows=rows,
        plan={"question_id": 1},
        tables={"u1": {}},
        contexts={"u1": {"table_function": {"kind": "income_statement"}}},
        minimum_row_jaccard=0.9,
        minimum_row_margin=0.2,
    )
    assert result["status"] == "UNIQUE_STRICT_SOURCE_ROW_RESOLVED"
    assert result["navigation_candidates"][0]["observed_scope"] == "consolidated"
    assert "Lợi nhuận" not in str(result)
    assert "row_index" not in str(result)
    assert "human_verified" not in str(result)
