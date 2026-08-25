"""Contract tests for the single open-weight Qwen proposal lane."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.research.proof_policy.active_learning_models import (
    RAW_RESPONSE_PROTOCOL,
    VALIDATED_RESPONSE_PROTOCOL,
    build_model_job,
    reconcile_validated_responses,
    render_prompt,
    validate_raw_responses,
)
from finance_query.research.proof_policy.evidence_closure import canonical_sha256, load_jsonl, sha256_file


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _fixture(root: Path) -> tuple[Path, Path]:
    queue_rows: list[dict[str, object]] = []
    batch_rows: list[dict[str, object]] = []
    for question_id in (1, 2):
        source = {"question_id": question_id, "receipt_status": "PENDING_TYPED_RECEIPT"}
        source_id = canonical_sha256(source)
        queue_rows.append({**source, "packet_id": source_id})
        item = canonical_sha256({"review": question_id})
        batch_rows.append({
            "question_id": question_id, "queue": "formula_definition", "cluster_id": "formula-cluster",
            "assignment_role": "learn_seed", "evaluation_role": "active_learning", "raw_claim": f"Claim Q{question_id}",
            "review_item_id": item, "packet_payload_sha256": canonical_sha256({"packet": question_id}),
            "source_packet_id": source_id, "allowed_source_ref_sha256": [source_id],
        })
    queue_path, batch_path, clusters_path = root / "closure.jsonl", root / "batch.jsonl", root / "clusters.jsonl"
    _write_jsonl(queue_path, queue_rows)
    _write_jsonl(batch_path, batch_rows)
    _write_jsonl(clusters_path, [{"cluster_id": "formula-cluster", "features": {"queue": "formula_definition"}}])
    closure_path = root / "closure.manifest.json"
    _write_json(closure_path, {"protocol": "vifinqa_v13_evidence_closure_workbench_v1", "outputs": {"formula_definition_receipt_intake": {"path": str(queue_path), "sha256": sha256_file(queue_path)}}})
    cycle_path = root / "cycle.manifest.json"
    _write_json(cycle_path, {
        "protocol": "vifinqa_active_learning_cycle_v1", "release_decision": {"status": "blocked"},
        "counts": {"active_learning_review_count": 2},
        "inputs": {"closure_manifest": {"path": str(closure_path), "sha256": sha256_file(closure_path)}},
        "outputs": {"cluster_inventory": {"path": str(clusters_path), "sha256": sha256_file(clusters_path)}, "review_batch": {"path": str(batch_path), "sha256": sha256_file(batch_path)}},
    })
    policy_path = root / "model-policy.json"
    _write_json(policy_path, {
        "protocol": "vifinqa_open_source_model_policy_v1", "strict_parameter_cap_billions": 14.7,
        "routes": [{"route_id": "qwen3-8b", "role": "open_source_model_proposer", "model_id": "Qwen/Qwen3-8B", "revision": "a" * 40, "parameter_count_billions": 8.2, "open_weights": True, "license": "Apache-2.0", "weight_shards": [{"filename": "model.safetensors", "sha256": "a" * 64}]}],
    })
    return cycle_path, policy_path


def _raw_rows(requests: list[dict], *, forbidden: bool = False) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for request in requests:
        packet = request["packet"]
        response: dict[str, object] = {
            "schema_version": 1, "protocol": VALIDATED_RESPONSE_PROTOCOL,
            "review_item_id": request["review_item_id"], "model_role": "open_source_model_proposer",
            "verdict": "PROPOSE_RULE",
            "policy": {"rule_type": packet["policy_contract"]["expected_rule_type"], "rule_value": "difference", "applicability_conditions": ["claim pattern matches"], "required_checks": ["exact-cell replay"]},
            "reason_codes": ["SUPPORTED_BY_PACKET"], "cited_source_ref_sha256": [packet["source_packet_ref_sha256"]],
        }
        if forbidden:
            response["answer_decimal"] = "1"
        rows.append({"protocol": RAW_RESPONSE_PROTOCOL, "request_id": request["request_id"], "review_item_id": request["review_item_id"], "model_id": request["model_id"], "model_revision": request["model_revision"], "model_role": request["model_role"], "prompt_sha256": canonical_sha256(render_prompt(request)), "raw_response": json.dumps(response, ensure_ascii=False)})
    return rows


def test_job_has_one_qwen_route(tmp_path: Path) -> None:
    cycle, policy = _fixture(tmp_path)
    result = build_model_job(active_cycle_manifest_path=cycle, model_policy_path=policy, output_dir=tmp_path / "job")
    assert result.packet_count == 2
    manifest = json.loads((tmp_path / "job" / "active_learning_model_job.manifest.json").read_text())
    assert set(manifest["model_routes"]) == {"open_source_model_proposer"}
    assert set(manifest["outputs"]) == {"packets", "proposer_requests", "summary"}
    assert manifest["independent_human_review_required"] is True
    assert manifest["chatgpt_in_model_graph"] is False


def test_qwen_proposal_is_provisional_and_routes_to_human_review(tmp_path: Path) -> None:
    cycle, policy = _fixture(tmp_path)
    build_model_job(active_cycle_manifest_path=cycle, model_policy_path=policy, output_dir=tmp_path / "job")
    requests_path = tmp_path / "job" / "qwen3_8b_proposer_requests_v1.jsonl"
    raw_path, validated_path = tmp_path / "raw.jsonl", tmp_path / "validated.jsonl"
    _write_jsonl(raw_path, _raw_rows(load_jsonl(requests_path)))
    validate_raw_responses(requests_path=requests_path, raw_responses_path=raw_path, output_path=validated_path)
    result = reconcile_validated_responses(packets_path=tmp_path / "job" / "open_source_model_review_packets_v1.jsonl", proposer_validated_path=validated_path, output_dir=tmp_path / "triage")
    assert result.proposal_count == 2
    candidate = load_jsonl(tmp_path / "triage" / "machine_provisional_policy_candidates_v1.jsonl")[0]
    assert candidate["reconciliation_status"] == "QWEN_PROPOSAL_PENDING_INDEPENDENT_REVIEW"
    assert candidate["required_reviewer_types"] == ["human_adjudicator", "chatgpt_human_equivalent_reviewer"]
    assert candidate["training_eligible"] is False and candidate["materialization_eligible"] is False


def test_forbidden_answer_is_invalid_and_escalated(tmp_path: Path) -> None:
    cycle, policy = _fixture(tmp_path)
    build_model_job(active_cycle_manifest_path=cycle, model_policy_path=policy, output_dir=tmp_path / "job")
    requests_path = tmp_path / "job" / "qwen3_8b_proposer_requests_v1.jsonl"
    raw_path, validated_path = tmp_path / "raw.jsonl", tmp_path / "validated.jsonl"
    _write_jsonl(raw_path, _raw_rows(load_jsonl(requests_path), forbidden=True))
    validation = validate_raw_responses(requests_path=requests_path, raw_responses_path=raw_path, output_path=validated_path)
    triage = reconcile_validated_responses(packets_path=tmp_path / "job" / "open_source_model_review_packets_v1.jsonl", proposer_validated_path=validated_path, output_dir=tmp_path / "triage")
    assert validation.valid_count == 0 and triage.proposal_count == 0 and triage.escalation_count == 2


def test_second_model_route_is_rejected(tmp_path: Path) -> None:
    cycle, policy = _fixture(tmp_path)
    value = json.loads(policy.read_text())
    value["routes"].append({"route_id": "extra", "role": "unexpected_model_role", "model_id": "other/12b", "revision": "b" * 40, "parameter_count_billions": 12.0, "open_weights": True, "license": "Apache-2.0", "weight_shards": [{"filename": "x.safetensors", "sha256": "b" * 64}]})
    _write_json(policy, value)
    with pytest.raises(ValueError, match="exactly one proposer"):
        build_model_job(active_cycle_manifest_path=cycle, model_policy_path=policy, output_dir=tmp_path / "job")
