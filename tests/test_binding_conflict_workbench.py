from __future__ import annotations

from finance_query.binding_conflict_workbench import context_unit_anchor_candidate


def test_context_unit_candidate_requires_matching_hash_bound_source_title() -> None:
    provenance = {"source_sha256": "a" * 64, "table_sha256": "b" * 64}
    table = {
        "document_id": "DTK-2017",
        "internal_table_uid": "table-1",
        "source_provenance": provenance,
    }
    context = {
        "document_id": "DTK-2017",
        "internal_table_uid": "table-1",
        "source_provenance": provenance,
        "context_trace": {
            "source_title": "Báo cáo kết quả hoạt động kinh doanh. Đơn vị: VND",
            "unit_labels": ["VND"],
        },
    }
    candidate = context_unit_anchor_candidate(table, context)
    assert candidate is not None
    assert candidate["source_unit"] == "vnd"
    assert candidate["source_to_vnd_multiplier"] == "1"
    assert candidate["source_contract"]["eligible_for_materialization"] is False


def test_context_unit_candidate_rejects_conflict_or_lineage_drift() -> None:
    provenance = {"source_sha256": "a" * 64, "table_sha256": "b" * 64}
    table = {"document_id": "doc", "internal_table_uid": "table", "source_provenance": provenance}
    context = {
        "document_id": "doc",
        "internal_table_uid": "table",
        "source_provenance": provenance,
        "context_trace": {"source_title": "Đơn vị: VND và triệu đồng", "unit_labels": ["VND", "triệu đồng"]},
    }
    assert context_unit_anchor_candidate(table, context) is None
    context["context_trace"] = {"source_title": "Đơn vị: VND", "unit_labels": ["VND"]}
    context["source_provenance"] = {**provenance, "source_sha256": "c" * 64}
    assert context_unit_anchor_candidate(table, context) is None
