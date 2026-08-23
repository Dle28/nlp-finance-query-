from __future__ import annotations

import json
from pathlib import Path

from finance_query.binding_conflict_workbench import canonical_sha256, sha256_file, source_contract
from finance_query.navigation_review_evidence import build_evidence_packets


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_builds_hash_bound_packets_without_numeric_values(tmp_path: Path) -> None:
    candidate = {
        "document_id": "MSR_financial_statements_2025_separate",
        "internal_table_uid": "u" * 64,
        "row_index": 0,
        "failure_codes": ["navigation_gate", "sector"],
        "gate_vector": {
            field: {"pass": True}
            for field in ("entity", "scope", "year", "table_type")
        },
    }
    payload = {
        "question_id": 1,
        "remediation_status": "needs_human_no_eligible_automatic_repair",
        "concept_id": "interest_expense",
        "exclusive_primary_cause": "SECTOR",
        "exclusive_primary_cause_blocker_set": ["sector"],
        "minimal_blocker_sets": [["navigation_gate", "sector"]],
        "nearby_exact_concept_candidates": [candidate],
        "recommended_review_queue": "document_metadata",
        "automatic_materialization_eligible": False,
        "source_conflict_item_sha256": "a" * 64,
        "source_no_candidate_audit_sha256": "b" * 64,
    }
    queue_row = {
        "schema_version": 1,
        "protocol": "vifinqa_navigation_remediation_queue_v1",
        **payload,
        "remediation_item_sha256": canonical_sha256(payload),
        "source_contract": source_contract(),
    }
    queue = tmp_path / "queue.jsonl"
    _jsonl(queue, [queue_row])
    queue_manifest = tmp_path / "queue.manifest.json"
    queue_manifest.write_text(json.dumps({
        "protocol": "vifinqa_navigation_remediation_queue_v1",
        "outputs": {"queue": {"sha256": sha256_file(queue)}},
    }), encoding="utf-8")

    routes = tmp_path / "routes.jsonl"
    _jsonl(routes, [
        {"question_id": question_id, "question": "Chi phí lãi vay MSR 2025", "question_context": {}}
        for question_id in range(1, 1013)
    ])
    route_manifest = tmp_path / "routes.manifest.json"
    route_manifest.write_text(json.dumps({"outputs": {"packets": {"sha256": sha256_file(routes)}}}), encoding="utf-8")

    tables = tmp_path / "tables.jsonl"
    _jsonl(tables, [{
        "internal_table_uid": "u" * 64,
        "rows": [["Trong đó: Chi phí lãi vay", "23", "154.674.553"]],
        "column_labels": ["Chỉ tiêu", "Mã số", "2025 Nghìn VND"],
        "source_provenance": {"source_sha256": "c" * 64, "table_sha256": "d" * 64},
        "context_trace": {"source_title": "Báo cáo kết quả hoạt động kinh doanh riêng năm 2025"},
    }])
    table_manifest = tmp_path / "tables.manifest.json"
    table_manifest.write_text(json.dumps({"sidecar_sha256": sha256_file(tables)}), encoding="utf-8")

    result = build_evidence_packets(
        queue=queue, queue_manifest=queue_manifest,
        route_packets=routes, route_manifest=route_manifest,
        structured_tables=tables, table_manifest=table_manifest,
        output_dir=tmp_path / "out",
    )
    assert result["counts"]["candidate_count"] == 1
    assert result["counts"]["numeric_value_exposure_count"] == 0
    packet = json.loads(Path(result["outputs"]["packets"]["path"]).read_text())
    assert packet["candidate_evidence"][0]["row_text_cells"] == [
        {"column_index": 0, "text": "Trong đó: Chi phí lãi vay"}
    ]
    assert "154.674.553" not in json.dumps(packet, ensure_ascii=False)
    assert packet["source_contract"]["may_select_value"] is False
