from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.binding_conflict_workbench import canonical_sha256, sha256_file
from finance_query.navigation_chatgpt_reviews import NavigationChatGPTReviewError, build_decisions


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Path]:
    packets = []
    selected_sha = ""
    for question_id, exact in ((1, True), (2, False)):
        candidate_payload = {
            "candidate_index": 1,
            "document_id": "MSR_financial_statements_2025_separate",
            "internal_table_uid": str(question_id) * 64,
            "row_index": 3,
            "failure_codes": ["sector"] if exact else ["entity"],
            "gate_vector": {
                "entity": {"pass": exact}, "scope": {"pass": True},
                "year": {"pass": True}, "table_type": {"pass": True},
            },
            "exact_identity_scope_year_table": exact,
            "row_text_cells": [{"column_index": 0, "text": "Trong đó: Chi phí lãi vay"}],
            "raw_row_sha256": "a" * 64,
            "column_labels": ["Chỉ tiêu", "2025"],
            "column_labels_sha256": "b" * 64,
            "source_title": "Báo cáo riêng",
            "source_title_sha256": "c" * 64,
            "source_document_sha256": "d" * 64,
            "source_table_sha256": "e" * 64,
        }
        candidate = {**candidate_payload, "candidate_evidence_sha256": canonical_sha256(candidate_payload)}
        if exact:
            selected_sha = candidate["candidate_evidence_sha256"]
        payload = {
            "question_id": question_id,
            "question": "Chi phí lãi vay",
            "question_context": {},
            "concept_id": "interest_expense",
            "exclusive_primary_cause": "SECTOR",
            "exclusive_primary_cause_blocker_set": ["sector"],
            "minimal_blocker_sets": [["sector"]],
            "recommended_review_queue": "document_metadata",
            "candidate_evidence": [candidate],
            "source_remediation_item_sha256": "f" * 64,
            "source_route_packet_sha256": "0" * 64,
        }
        packets.append({
            "schema_version": 1,
            "protocol": "vifinqa_navigation_review_evidence_v1",
            **payload,
            "packet_sha256": canonical_sha256(payload),
            "source_contract": {"may_change_route": False},
        })
    evidence = tmp_path / "evidence.jsonl"
    _jsonl(evidence, packets)
    manifest = tmp_path / "evidence.manifest.json"
    manifest.write_text(json.dumps({
        "protocol": "vifinqa_navigation_review_evidence_v1",
        "outputs": {"packets": {"sha256": sha256_file(evidence)}},
    }), encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "no_exact_context_policy": "confirm_upstream_blocker_fail_closed",
        "exact_context_reviews": [{
            "question_id": 1,
            "decision": "approve_navigation_candidate_semantics",
            "selected_candidate_evidence_sha256": selected_sha,
            "reason_codes": [],
            "rationale": "Exact raw row khớp câu hỏi.",
        }],
    }), encoding="utf-8")
    return {"evidence_packets": evidence, "evidence_manifest": manifest, "review_spec": spec}


def test_approves_one_semantic_candidate_without_route_or_value_authority(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    # The production queue has 38 items; mirror coverage with 36 fail-closed packets.
    rows = [json.loads(line) for line in inputs["evidence_packets"].read_text().splitlines()]
    template = rows[1]
    for question_id in range(3, 39):
        payload = {key: value for key, value in template.items() if key not in {"schema_version", "protocol", "packet_sha256", "source_contract"}}
        payload["question_id"] = question_id
        rows.append({**template, **payload, "packet_sha256": canonical_sha256(payload)})
    _jsonl(inputs["evidence_packets"], rows)
    inputs["evidence_manifest"].write_text(json.dumps({
        "protocol": "vifinqa_navigation_review_evidence_v1",
        "outputs": {"packets": {"sha256": sha256_file(inputs["evidence_packets"])}},
    }), encoding="utf-8")
    result = build_decisions(**inputs, output_dir=tmp_path / "out")
    assert result["counts"]["approve_navigation_candidate_semantics"] == 1
    assert result["counts"]["confirmed_upstream_blocker"] == 37
    first = json.loads(Path(result["outputs"]["decisions"]["path"]).read_text().splitlines()[0])
    assert first["decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert first["source_contract"]["navigation_review_gate_authorized"] is True
    assert first["source_contract"]["may_change_route"] is False
    assert first["source_contract"]["may_select_value"] is False


def test_rejects_stale_selected_candidate(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    spec = json.loads(inputs["review_spec"].read_text())
    spec["exact_context_reviews"][0]["selected_candidate_evidence_sha256"] = "0" * 64
    inputs["review_spec"].write_text(json.dumps(spec), encoding="utf-8")
    # Coverage fails first in the minimal fixture, so add the required 36 blockers.
    rows = [json.loads(line) for line in inputs["evidence_packets"].read_text().splitlines()]
    template = rows[1]
    for question_id in range(3, 39):
        payload = {key: value for key, value in template.items() if key not in {"schema_version", "protocol", "packet_sha256", "source_contract"}}
        payload["question_id"] = question_id
        rows.append({**template, **payload, "packet_sha256": canonical_sha256(payload)})
    _jsonl(inputs["evidence_packets"], rows)
    inputs["evidence_manifest"].write_text(json.dumps({
        "protocol": "vifinqa_navigation_review_evidence_v1",
        "outputs": {"packets": {"sha256": sha256_file(inputs["evidence_packets"])}},
    }), encoding="utf-8")
    with pytest.raises(NavigationChatGPTReviewError, match="selected candidate is stale"):
        build_decisions(**inputs, output_dir=tmp_path / "out")
