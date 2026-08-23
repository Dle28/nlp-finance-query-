from __future__ import annotations

import json
from pathlib import Path

from finance_query.v2_reviewability import build_v2_reviewability_audit


def test_queue_without_source_identity_is_blocked(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({"question_id": 1, "stage_id": "s", "role": "r", "concept_id": "c"}) + "\n", encoding="utf-8")
    result = build_v2_reviewability_audit(queue_path=queue, output_dir=tmp_path / "out")
    row = json.loads(Path(result["outputs"]["audit"]["path"]).read_text(encoding="utf-8"))
    assert row["reviewability_status"] == "BLOCKED_MISSING_REVIEW_PROVENANCE"
    assert row["eligible_for_materialization"] is False


def test_complete_queue_is_only_reviewable_not_approved(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({"question_id": 1, "stage_id": "s", "role": "r", "concept_id": "c", "immutable_source_identity": {"audit_sha256": "a", "audit_item_sha256": "b", "question_id": 1, "stage_id": "s", "role": "r", "concept_id": "c"}, "target_document_candidates": [{"document_id": "d", "company": "ACME", "report_year": 2024, "report_scope": "separate", "available_period_years": [2024]}], "nearby_exact_concept_candidates": [{"internal_table_uid": "u", "row_index": 0, "document_id": "d", "gate_vector": {"entity": {}}}]}) + "\n", encoding="utf-8")
    result = build_v2_reviewability_audit(queue_path=queue, output_dir=tmp_path / "out")
    row = json.loads(Path(result["outputs"]["audit"]["path"]).read_text(encoding="utf-8"))
    assert row["reviewability_status"] == "REVIEWABLE"
    assert row["eligible_for_materialization"] is False


def test_missing_nested_lineage_is_blocked(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({"question_id": 1, "stage_id": "s", "role": "r", "concept_id": "c", "immutable_source_identity": {"audit_sha256": "a"}, "target_document_candidates": [{"document_id": "d"}], "nearby_exact_concept_candidates": [{"internal_table_uid": "u", "row_index": 0, "document_id": "d", "gate_vector": {"entity": {}}}]}) + "\n", encoding="utf-8")
    result = build_v2_reviewability_audit(queue_path=queue, output_dir=tmp_path / "out")
    row = json.loads(Path(result["outputs"]["audit"]["path"]).read_text(encoding="utf-8"))
    assert row["reviewability_status"] == "BLOCKED_MISSING_REVIEW_PROVENANCE"
    assert "immutable_source_identity.audit_item_sha256" in row["missing_fields"]
