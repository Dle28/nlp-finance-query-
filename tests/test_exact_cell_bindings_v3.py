from __future__ import annotations

from finance_query.binding_conflict_workbench import context_unit_anchor_candidate
from finance_query.grounded_authorization import _unit_binding


def test_v3_source_title_unit_anchor_passes_only_with_matching_context() -> None:
    provenance = {"source_sha256": "a" * 64, "table_sha256": "b" * 64}
    table = {
        "document_id": "DTK-2017",
        "internal_table_uid": "table-1",
        "source_provenance": provenance,
        "rows": [["Năm 2017"], ["Lợi nhuận gộp"]],
    }
    context = {
        "document_id": "DTK-2017",
        "internal_table_uid": "table-1",
        "source_provenance": provenance,
        "context_trace": {"source_title": "Báo cáo riêng. Đơn vị: VND", "unit_labels": ["VND"]},
    }
    candidate = context_unit_anchor_candidate(table, context)
    assert candidate is not None
    operand = {
        "binding_status": "binding_ready",
        "source_unit": "vnd",
        "source_to_vnd_multiplier": "1",
        "requested_output_unit": {"unit": "ty_dong"},
        "unit_resolution_method": "v3_exact_source_title_unit_v1",
        "source_unit_context_anchors": [candidate],
    }
    result = _unit_binding(table, operand, context)
    assert result["status"] == "PASS"
    assert result["resolution_level"] == "document"
    changed = {**context, "context_trace": {**context["context_trace"], "source_title": "Báo cáo riêng"}}
    assert _unit_binding(table, operand, changed)["status"] == "UNRESOLVED"
