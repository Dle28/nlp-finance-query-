from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.grounding_adjudication_v2 import build, entity_diagnosis
from finance_query.v2_reviewability import build_v2_reviewability_audit


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def _metadata_audit(question_id: int = 1) -> dict:
    return {
        "question_id": question_id,
        "stage_id": "stage_1",
        "role": "metric",
        "concept_id": "metric",
        "exclusive_primary_cause": "ENTITY",
        "minimal_blocker_sets": [["entity"]],
        "nearby_exact_concept_candidates": [
            {
                "internal_table_uid": "table-a",
                "row_index": 7,
                "document_id": "acme-2024-separate",
                "failure_codes": ["entity"],
                "gate_vector": {"entity": {"expected": ["ACME"]}, "year": {"expected": [2024]}, "scope": {"expected": "separate"}},
                "routing_snapshot": {"routing_eligible": True},
            }
        ],
    }


def test_builder_retains_frozen_identity_and_exact_target_documents(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    document_path = tmp_path / "documents.jsonl"
    taxonomy_path = tmp_path / "taxonomy.jsonl"
    _write_jsonl(audit_path, [_metadata_audit() for _ in range(54)])
    _write_jsonl(document_path, [{"document_id": "acme-2024-separate", "company": "ACME", "report_year": 2024, "report_scope": "separate", "available_period_years": [2024]}, {"document_id": "acme-2024-consolidated", "company": "ACME", "report_year": 2024, "report_scope": "consolidated", "available_period_years": [2024]}])
    _write_jsonl(taxonomy_path, [{"document_id": "acme-2024-separate", "match_status": "exact_unique", "concept_candidates": [{"concept_id": "metric"}]}])
    manifest_path = tmp_path / "period.manifest.json"
    manifest_path.write_text(json.dumps({"outputs": {"no_candidate_audit": {"sha256": _sha(audit_path)}}, "inputs": {"document_metadata": {"sha256": _sha(document_path)}, "taxonomy_candidates": {"sha256": _sha(taxonomy_path)}}}))
    output_path = tmp_path / "metadata_v3.jsonl"
    result = build(audit_path=audit_path, period_manifest=manifest_path, document_metadata=document_path, taxonomy_candidates=taxonomy_path, output=output_path)
    row = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["immutable_source_identity"]["audit_sha256"] == _sha(audit_path)
    assert row["immutable_source_identity"]["audit_item_sha256"]
    assert row["target_document_candidates"] == [{"document_id": "acme-2024-separate", "company": "ACME", "report_year": 2024, "report_scope": "separate", "available_period_years": [2024]}]
    assert row["nearby_exact_concept_candidates"][0]["internal_table_uid"] == "table-a"
    assert row["decision_contract"]["decision"] is None
    assert row["decision_contract"]["eligible_for_materialization"] is False
    assert result["protocol"] == "grounding_adjudication_queues_v3"


def test_entity_diagnosis_filters_company_year_and_scope() -> None:
    audit = _metadata_audit()
    documents = [
        {"document_id": "wrong-company", "company": "OTHER", "report_year": 2024, "report_scope": "separate"},
        {"document_id": "wrong-year", "company": "ACME", "report_year": 2023, "report_scope": "separate"},
        {"document_id": "wrong-scope", "company": "ACME", "report_year": 2024, "report_scope": "consolidated"},
        {"document_id": "right", "company": "ACME", "report_year": 2024, "report_scope": "separate"},
    ]
    taxonomy = [{"document_id": "wrong-scope", "match_status": "exact_unique", "concept_candidates": [{"concept_id": "metric"}]}]
    assert entity_diagnosis(audit, documents, taxonomy) == "EXACT_CONCEPT_ABSENT_FOR_ENTITY"


def test_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    document_path = tmp_path / "documents.jsonl"
    taxonomy_path = tmp_path / "taxonomy.jsonl"
    _write_jsonl(audit_path, [_metadata_audit() for _ in range(54)])
    _write_jsonl(document_path, [])
    _write_jsonl(taxonomy_path, [])
    manifest_path = tmp_path / "period.manifest.json"
    manifest_path.write_text(json.dumps({"outputs": {"no_candidate_audit": {"sha256": "wrong"}}, "inputs": {"document_metadata": {"sha256": _sha(document_path)}, "taxonomy_candidates": {"sha256": _sha(taxonomy_path)}}}))
    with pytest.raises(ValueError, match="no-candidate audit"):
        build(audit_path=audit_path, period_manifest=manifest_path, document_metadata=document_path, taxonomy_candidates=taxonomy_path, output=tmp_path / "out.jsonl")


def test_regenerated_queue_is_reviewable_not_approved(tmp_path: Path) -> None:
    period_manifest = ROOT / "artifacts/research/period_column_candidates_v1/period_column_candidate_packets_v1.manifest.json"
    audit = ROOT / "artifacts/research/period_column_candidates_v1/route_packet_no_candidate_audit_v1.jsonl"
    documents = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/document_metadata_v1.jsonl"
    taxonomy = ROOT / "artifacts/research/financial_taxonomy_v1/financial_taxonomy_candidates_v1.jsonl"
    queue = tmp_path / "metadata_adjudication_queue_v3.jsonl"
    build(audit_path=audit, period_manifest=period_manifest, document_metadata=documents, taxonomy_candidates=taxonomy, output=queue)
    result = build_v2_reviewability_audit(queue_path=queue, output_dir=tmp_path)
    records = [json.loads(line) for line in Path(result["outputs"]["audit"]["path"]).read_text(encoding="utf-8").splitlines()]
    assert len(records) == 54
    assert {record["reviewability_status"] for record in records} == {"REVIEWABLE"}
    assert all(record["eligible_for_materialization"] is False for record in records)
