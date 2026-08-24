"""Hermetic tests for open-source active-learning model packets and gates."""
from __future__ import annotations

import json
from pathlib import Path

from finance_query.active_learning_models import (
    MODEL_MAX_NEW_TOKENS,
    MODEL_MAX_SECONDS_PER_REQUEST,
    MODEL_PROGRESS_EVERY,
    RAW_RESPONSE_PROTOCOL,
    VALIDATED_RESPONSE_PROTOCOL,
    build_model_job,
    reconcile_validated_responses,
    render_prompt,
    validate_raw_responses,
)
from finance_query.evidence_closure import canonical_sha256, load_jsonl, sha256_file


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _fixture(root: Path) -> tuple[Path, Path]:
    clusters = root / "data" / "clusters.jsonl"
    batch = root / "data" / "batch.jsonl"
    cluster_rows = [
        {"cluster_id": "cluster-formula", "features": {"queue": "formula_definition", "family": "ratio_or_derived"}},
        {"cluster_id": "cluster-temporal", "features": {"queue": "temporal", "kind": "instant"}},
    ]
    batch_rows = []
    closure_outputs: dict[str, dict[str, str]] = {}
    queue_rows: dict[str, list[dict[str, object]]] = {
        "formula_definition": [],
        "temporal": [],
    }
    for qid, queue, cluster in ((1, "formula_definition", "cluster-formula"), (2, "temporal", "cluster-temporal")):
        source_projection = {
            "question_id": qid,
            "receipt_status": "PENDING_TYPED_RECEIPT",
            "required_receipt_fields": ["definition_id", "evidence_refs"],
        }
        source_packet_id = canonical_sha256(source_projection)
        queue_rows[queue].append({**source_projection, "packet_id": source_packet_id})
        packet_payload = canonical_sha256({"packet": qid})
        batch_rows.append({
            "question_id": qid,
            "queue": queue,
            "cluster_id": cluster,
            "assignment_role": "learn_seed",
            "raw_claim": f"Claim Q{qid} năm 2022",
            "evaluation_role": "active_learning",
            "review_item_id": canonical_sha256({"review": qid}),
            "packet_payload_sha256": packet_payload,
            "source_packet_id": source_packet_id,
            "allowed_source_ref_sha256": [packet_payload, source_packet_id],
        })
    output_names = {
        "formula_definition": "formula_definition_receipt_intake",
        "temporal": "temporal_receipt_intake",
    }
    for queue, rows in queue_rows.items():
        path = root / "data" / f"{queue}.jsonl"
        _write_jsonl(path, rows)
        closure_outputs[output_names[queue]] = {"path": str(path), "sha256": sha256_file(path)}
    closure = root / "closure.manifest.json"
    _write_json(closure, {
        "protocol": "vifinqa_v13_evidence_closure_workbench_v1",
        "outputs": closure_outputs,
    })
    _write_jsonl(clusters, cluster_rows)
    _write_jsonl(batch, batch_rows)
    cycle = root / "cycle.manifest.json"
    _write_json(cycle, {
        "protocol": "vifinqa_active_learning_cycle_v1",
        "release_decision": {"status": "blocked"},
        "counts": {"active_learning_review_count": 2},
        "inputs": {"closure_manifest": {"path": str(closure), "sha256": sha256_file(closure)}},
        "outputs": {
            "cluster_inventory": {"path": str(clusters), "sha256": sha256_file(clusters)},
            "review_batch": {"path": str(batch), "sha256": sha256_file(batch)},
        },
    })
    policy = root / "model-policy.json"
    _write_json(policy, {
        "protocol": "vifinqa_open_source_model_policy_v1",
        "strict_parameter_cap_billions": 14.7,
        "routes": [
            {"route_id": "qwen", "role": "open_source_model_proposer", "model_id": "Qwen/Qwen3-8B", "revision": "a" * 40, "parameter_count_billions": 8.2, "open_weights": True, "license": "Apache-2.0", "weight_shards": [{"filename": "qwen.safetensors", "sha256": "a" * 64}]},
            {"route_id": "mistral", "role": "open_source_model_critic", "model_id": "mistralai/Mistral-Nemo-Instruct-2407", "revision": "b" * 40, "parameter_count_billions": 12.0, "open_weights": True, "license": "Apache-2.0", "weight_shards": [{"filename": "mistral.safetensors", "sha256": "b" * 64}]},
        ],
    })
    return cycle, policy


def _raw_rows(requests: list[dict], *, rule_value: str = "difference", forbidden: bool = False) -> list[dict[str, object]]:
    rows = []
    for request in requests:
        packet = request["packet"]
        response: dict[str, object] = {
            "schema_version": 1,
            "protocol": VALIDATED_RESPONSE_PROTOCOL,
            "review_item_id": request["review_item_id"],
            "model_role": request["model_role"],
            "verdict": "PROPOSE_RULE",
            "policy": {
                "rule_type": packet["policy_contract"]["expected_rule_type"],
                "rule_value": rule_value,
                "applicability_conditions": ["claim pattern matches"],
                "required_checks": ["exact-cell replay"],
            },
            "reason_codes": ["SUPPORTED_BY_PACKET"],
            "cited_source_ref_sha256": [packet["source_packet_ref_sha256"]],
        }
        if forbidden:
            response["answer_decimal"] = "123"
        rows.append({
            "protocol": RAW_RESPONSE_PROTOCOL,
            "request_id": request["request_id"],
            "review_item_id": request["review_item_id"],
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "model_role": request["model_role"],
            "prompt_sha256": canonical_sha256(render_prompt(request)),
            "raw_response": json.dumps(response, ensure_ascii=False),
        })
    return rows


def _abstention_rows(requests: list[dict], *, extra_key: bool = False) -> list[dict[str, object]]:
    rows = []
    for request in requests:
        response: dict[str, object] = {
            "verdict": "ABSTAIN",
            "reason_codes": ["INSUFFICIENT_PACKET_EVIDENCE"],
        }
        if extra_key:
            response["policy"] = None
        rows.append({
            "protocol": RAW_RESPONSE_PROTOCOL,
            "request_id": request["request_id"],
            "review_item_id": request["review_item_id"],
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "model_role": request["model_role"],
            "prompt_sha256": canonical_sha256(render_prompt(request)),
            "raw_response": json.dumps(response, ensure_ascii=False),
        })
    return rows


def test_model_job_is_blind_numeric_free_and_chatgpt_free(tmp_path: Path) -> None:
    cycle, policy = _fixture(tmp_path)
    result = build_model_job(active_cycle_manifest_path=cycle, model_policy_path=policy, output_dir=tmp_path / "job")
    assert result.packet_count == 2
    packets = load_jsonl(tmp_path / "job" / "open_source_model_review_packets_v1.jsonl")
    assert all(row["prior_model_output_visible"] is False for row in packets)
    assert all(row["numeric_answer_visible"] is False for row in packets)
    assert all(row["source_packet_projection"]["receipt_status"] == "PENDING_TYPED_RECEIPT" for row in packets)
    text = (tmp_path / "job" / "active_learning_model_job.manifest.json").read_text(encoding="utf-8").casefold()
    assert '"chatgpt_in_model_graph": false' in text
    assert "answer_decimal" not in text
    requests = load_jsonl(tmp_path / "job" / "qwen3_8b_proposer_requests_v1.jsonl")
    assert all(row["generation_contract"]["max_new_tokens"] == MODEL_MAX_NEW_TOKENS for row in requests)
    assert all(row["generation_contract"]["max_seconds_per_request"] == MODEL_MAX_SECONDS_PER_REQUEST for row in requests)
    assert all(row["generation_contract"]["progress_every"] == MODEL_PROGRESS_EVERY for row in requests)


def test_matching_blind_policies_reconcile_to_non_materializable_candidate(tmp_path: Path) -> None:
    cycle, policy = _fixture(tmp_path)
    build_model_job(active_cycle_manifest_path=cycle, model_policy_path=policy, output_dir=tmp_path / "job")
    proposer_requests = load_jsonl(tmp_path / "job" / "qwen3_8b_proposer_requests_v1.jsonl")
    critic_requests = load_jsonl(tmp_path / "job" / "mistral_nemo_12b_critic_requests_v1.jsonl")
    proposer_raw = tmp_path / "proposer-raw.jsonl"; critic_raw = tmp_path / "critic-raw.jsonl"
    _write_jsonl(proposer_raw, _raw_rows(proposer_requests)); _write_jsonl(critic_raw, _raw_rows(critic_requests))
    proposer_valid = tmp_path / "proposer-valid.jsonl"; critic_valid = tmp_path / "critic-valid.jsonl"
    validate_raw_responses(requests_path=tmp_path / "job" / "qwen3_8b_proposer_requests_v1.jsonl", raw_responses_path=proposer_raw, output_path=proposer_valid)
    validate_raw_responses(requests_path=tmp_path / "job" / "mistral_nemo_12b_critic_requests_v1.jsonl", raw_responses_path=critic_raw, output_path=critic_valid)
    result = reconcile_validated_responses(packets_path=tmp_path / "job" / "open_source_model_review_packets_v1.jsonl", proposer_validated_path=proposer_valid, critic_validated_path=critic_valid, output_dir=tmp_path / "reconciled")
    assert result.agreement_count == 2
    assert result.escalation_count == 0
    candidates = load_jsonl(tmp_path / "reconciled" / "machine_provisional_policy_candidates_v1.jsonl")
    assert all(row["materialization_eligible"] is False and row["training_eligible"] is False for row in candidates)


def test_disagreement_or_forbidden_answer_escalates(tmp_path: Path) -> None:
    cycle, policy = _fixture(tmp_path)
    build_model_job(active_cycle_manifest_path=cycle, model_policy_path=policy, output_dir=tmp_path / "job")
    proposer_requests = load_jsonl(tmp_path / "job" / "qwen3_8b_proposer_requests_v1.jsonl")
    critic_requests = load_jsonl(tmp_path / "job" / "mistral_nemo_12b_critic_requests_v1.jsonl")
    proposer_raw = tmp_path / "proposer-raw.jsonl"; critic_raw = tmp_path / "critic-raw.jsonl"
    _write_jsonl(proposer_raw, _raw_rows(proposer_requests, forbidden=True)); _write_jsonl(critic_raw, _raw_rows(critic_requests, rule_value="ratio"))
    proposer_valid = tmp_path / "proposer-valid.jsonl"; critic_valid = tmp_path / "critic-valid.jsonl"
    p = validate_raw_responses(requests_path=tmp_path / "job" / "qwen3_8b_proposer_requests_v1.jsonl", raw_responses_path=proposer_raw, output_path=proposer_valid)
    validate_raw_responses(requests_path=tmp_path / "job" / "mistral_nemo_12b_critic_requests_v1.jsonl", raw_responses_path=critic_raw, output_path=critic_valid)
    assert p.valid_count == 0
    result = reconcile_validated_responses(packets_path=tmp_path / "job" / "open_source_model_review_packets_v1.jsonl", proposer_validated_path=proposer_valid, critic_validated_path=critic_valid, output_dir=tmp_path / "reconciled")
    assert result.agreement_count == 0
    assert result.escalation_count == 2


def test_cuda_runner_requires_tokenizer_attention_mask() -> None:
    runner = Path("scripts/run_active_learning_open_source_model_v1.py").read_text(encoding="utf-8")
    assert '"return_dict": True' in runner
    assert 'if "attention_mask" not in model_inputs' in runner
    assert "model.generate(\n                            **model_inputs" in runner
    assert "max_time=max_seconds_per_request" in runner
    assert 'progress_path = staging / "model_execution_progress.json"' in runner
    assert "handle.flush()" in runner and "os.fsync(handle.fileno())" in runner


def test_minimal_abstention_is_valid_but_never_authorizing(tmp_path: Path) -> None:
    cycle, policy = _fixture(tmp_path)
    build_model_job(active_cycle_manifest_path=cycle, model_policy_path=policy, output_dir=tmp_path / "job")
    requests = load_jsonl(tmp_path / "job" / "mistral_nemo_12b_critic_requests_v1.jsonl")
    raw = tmp_path / "abstain-raw.jsonl"
    validated = tmp_path / "abstain-valid.jsonl"
    _write_jsonl(raw, _abstention_rows(requests))
    result = validate_raw_responses(
        requests_path=tmp_path / "job" / "mistral_nemo_12b_critic_requests_v1.jsonl",
        raw_responses_path=raw,
        output_path=validated,
    )
    assert result.valid_count == 0
    rows = load_jsonl(validated)
    assert all(row["validation_status"] == "VALID_ABSTENTION" for row in rows)
    assert all(row["training_eligible"] is False and row["certification_allowed"] is False for row in rows)


def test_abstention_with_extra_fields_remains_invalid(tmp_path: Path) -> None:
    cycle, policy = _fixture(tmp_path)
    build_model_job(active_cycle_manifest_path=cycle, model_policy_path=policy, output_dir=tmp_path / "job")
    requests = load_jsonl(tmp_path / "job" / "mistral_nemo_12b_critic_requests_v1.jsonl")
    raw = tmp_path / "abstain-raw.jsonl"
    validated = tmp_path / "abstain-valid.jsonl"
    _write_jsonl(raw, _abstention_rows(requests, extra_key=True))
    validate_raw_responses(
        requests_path=tmp_path / "job" / "mistral_nemo_12b_critic_requests_v1.jsonl",
        raw_responses_path=raw,
        output_path=validated,
    )
    assert all(row["validation_status"] == "INVALID_MODEL_RESPONSE" for row in load_jsonl(validated))


def test_template_echo_and_contradictory_proposal_reason_are_rejected(tmp_path: Path) -> None:
    cycle, policy = _fixture(tmp_path)
    build_model_job(active_cycle_manifest_path=cycle, model_policy_path=policy, output_dir=tmp_path / "job")
    requests = load_jsonl(tmp_path / "job" / "qwen3_8b_proposer_requests_v1.jsonl")
    raw_rows = _raw_rows(requests, rule_value="categorical string")
    first = json.loads(str(raw_rows[0]["raw_response"]))
    first["reason_codes"] = ["INSUFFICIENT_PACKET_EVIDENCE"]
    raw_rows[0]["raw_response"] = json.dumps(first, ensure_ascii=False)
    raw = tmp_path / "proposal-raw.jsonl"
    validated = tmp_path / "proposal-valid.jsonl"
    _write_jsonl(raw, raw_rows)
    validate_raw_responses(
        requests_path=tmp_path / "job" / "qwen3_8b_proposer_requests_v1.jsonl",
        raw_responses_path=raw,
        output_path=validated,
    )
    rows = load_jsonl(validated)
    assert all(row["validation_status"] == "INVALID_MODEL_RESPONSE" for row in rows)
