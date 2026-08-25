"""Hermetic tests for the fail-closed active-learning control plane."""
from __future__ import annotations

import json
from pathlib import Path

from finance_query.research.proof_policy.active_learning import (
    DECISION_PROTOCOL,
    build_active_learning_cycle,
    wilson_lower_bound,
)
from finance_query.research.proof_policy.evidence_closure import canonical_sha256, load_jsonl, sha256_file


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _packet(question_id: int, **extra: object) -> dict[str, object]:
    return {"question_id": question_id, "packet_id": canonical_sha256({"packet": question_id, **extra}), **extra}


def _fixture(root: Path, decisions: Path | None = None) -> Path:
    data = root / "data"
    queues: dict[str, list[dict[str, object]]] = {
        "formula_definition_receipt_intake": [
            _packet(qid, required_operations=["subtract_or_difference"], operation_plan={"effective_family": "ratio_or_derived", "formula_id": "difference", "decomposition_status": "complete"}, formula_evidence_candidate={"status": "MISSING"})
            for qid in range(1, 502)
        ],
        "operand_compatibility_receipt_intake": [
            _packet(qid, operation_plan={"effective_family": "ratio_or_derived", "formula_id": "ratio", "decomposition_status": "complete", "operand_count": 2}, formula_evidence_candidate={"status": "MISSING"}, compatibility_required=True)
            for qid in range(502, 596)
        ],
        "route_binding_receipt_intake": [
            _packet(qid, primary_blocker="TABLE_OR_METRIC_BINDING_UNRESOLVED", missing_operations=["reported_value"], covered_operations=[], route_reason_codes=[])
            for qid in range(596, 846)
        ],
        "route_operator_receipt_intake": [
            _packet(qid, primary_blocker="FORMULA_OR_OPERATOR_DEFINITION_UNRESOLVED", missing_operations=["ratio_or_percent"], covered_operations=["reported_value"], route_reason_codes=[], operation_plan={"effective_family": "ratio_or_derived", "formula_id": "ratio"})
            for qid in range(846, 883)
        ],
        "route_cause_investigation_intake": [
            _packet(qid, primary_blocker="ROUTE_CAUSE_UNESTABLISHED", missing_operations=[], covered_operations=[], route_reason_codes=[])
            for qid in range(883, 949)
        ],
        "temporal_receipt_intake": [
            _packet(qid, claim_temporal_requirement={"kind": "duration", "role": "current_duration", "requested_years": [2022]}, source_temporal_observation={"period_types": ["fiscal_year"], "source_expressions": None})
            for qid in range(949, 987)
        ],
        "v12_candidate_recertification_intake": [
            _packet(qid, pending_v13_obligations=[{"dimension": "unit.scale", "status": "UNRESOLVED"}])
            for qid in range(987, 1013)
        ],
    }
    queues["independent_requirement_review_packets"] = [
        _packet(qid, raw_claim=f"Claim Q{qid}", raw_claim_sha256=canonical_sha256(f"Claim Q{qid}")) for qid in range(1, 1013)
    ]
    queues["production_execution_ledger_intake"] = [
        {"question_id": qid, "entry_status": "BLOCKED"} for qid in range(1, 1013)
    ]
    outputs = {}
    for name, rows in queues.items():
        path = data / f"{name}.jsonl"
        _write_jsonl(path, rows)
        outputs[name] = {"path": str(path), "sha256": sha256_file(path)}
    closure = data / "closure.manifest.json"
    _write_json(closure, {
        "protocol": "vifinqa_v13_evidence_closure_workbench_v1",
        "release_decision": {"status": "blocked"},
        "outputs": outputs,
    })
    authority = data / "reviewer-authority.json"
    model_policy = data / "model-policy.json"
    _write_json(model_policy, {
        "protocol": "vifinqa_open_source_model_policy_v1",
        "strict_parameter_cap_billions": 14.7,
        "routes": [
            {"route_id": "fixture-proposer", "role": "open_source_model_proposer", "model_id": "open/proposer-8b", "revision": "a" * 40, "weight_shards": [{"filename": "model.safetensors", "sha256": "a" * 64}], "parameter_count_billions": 8.0, "open_weights": True, "license": "Apache-2.0"},
        ],
    })
    _write_json(authority, {
        "protocol": "vifinqa_active_learning_reviewer_authority_registry_v1",
        "reviewers": [
            {"reviewer_id": "proposer", "reviewer_type": "open_source_model_proposer", "model_route_id": "fixture-proposer", "authority_receipt_sha256": "1" * 64, "authority_scopes": ["shadow_proposal"], "enabled_for_decisions": True},
            {"reviewer_id": "auditor", "reviewer_type": "independent_auditor", "authority_receipt_sha256": "3" * 64, "authority_scopes": ["population_audit"], "enabled_for_decisions": True},
            {"reviewer_id": "adjudicator", "reviewer_type": "human_adjudicator", "authority_receipt_sha256": "4" * 64, "authority_scopes": ["proposal_training_adjudication"], "enabled_for_decisions": True},
            {"reviewer_id": "chatgpt-reviewer", "reviewer_type": "chatgpt_human_equivalent_reviewer", "authority_receipt_sha256": "5" * 64, "authority_scopes": ["bounded_semantic_review"], "enabled_for_decisions": True, "review_only": True, "competition_model_eligible": False, "training_authority": False},
        ],
    })
    paths = {"closure_manifest": closure, "reviewer_authority_registry": authority, "open_source_model_policy": model_policy}
    if decisions is not None:
        paths["review_decisions"] = decisions
    config = root / "configs" / ("cycle-with-decisions.json" if decisions else "cycle.json")
    _write_json(config, {
        "schema_version": 1,
        "protocol": "vifinqa_active_learning_cycle_v1",
        "mode": "offline_shadow_active_learning",
        "input_paths": {
            name: str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
            for name, path in paths.items()
        },
        "locked_input_sha256": {name: sha256_file(path) for name, path in paths.items()},
        "review_budget_by_queue": {"formula_definition": 16, "operand_compatibility": 8, "route_binding": 16, "route_operator": 4, "route_cause": 4, "temporal": 8, "v12_recertification": 8},
        "sampling_policy": {"population_audit_budget": 32, "population_audit_seed": "fixture-audit-seed"},
        "policy_learning": {"minimum_seed_decisions_per_cluster": 2, "minimum_holdout_decisions_per_cluster": 1},
        "calibration_gate": {"minimum_independent_holdout_decisions": 125, "minimum_wilson_lower_bound": 0.97},
    })
    return config


def test_cycle_selects_disjoint_active_and_probability_audit_lanes(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    result = build_active_learning_cycle(config_path=config, output_dir=tmp_path / "cycle")
    assert result["counts"]["active_learning_review_count"] == 64
    assert result["counts"]["population_audit_review_count"] == 32
    assert result["counts"]["selected_review_count"] == 96
    batch = load_jsonl(tmp_path / "cycle" / "active_learning_review_batch_v1.jsonl")
    assert len({row["question_id"] for row in batch}) == 96
    audit = [row for row in batch if row["evaluation_role"] == "independent_population_audit"]
    assert len(audit) == 32
    assert len({row["inclusion_probability"] for row in audit}) == 1
    assert audit[0]["inclusion_probability"] == 32 / 1012
    assert result["calibration_gate"]["promotion_status"] == "BLOCKED"
    assert result["learning_contract"]["online_self_training"] is False
    assert result["learning_contract"]["competition_model_policy"] == "open_source_weights_strictly_below_14.7B_parameters"
    assert result["learning_contract"]["chatgpt_training_or_inference_allowed"] is False
    assert all("chatgpt_proposer" not in row["required_reviewer_types"] for row in batch)


def test_three_qwen_proposals_create_provisional_policy_only(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    initial = tmp_path / "initial"
    build_active_learning_cycle(config_path=config, output_dir=initial)
    batch = load_jsonl(initial / "active_learning_review_batch_v1.jsonl")
    cluster_counts: dict[str, list[dict]] = {}
    for row in batch:
        if row["evaluation_role"] == "active_learning":
            cluster_counts.setdefault(row["cluster_id"], []).append(row)
    chosen = next(rows for rows in cluster_counts.values() if len(rows) >= 3 and sum(row["assignment_role"] == "learn_validation" for row in rows) >= 1)
    chosen = sorted(chosen, key=lambda row: (row["assignment_role"] != "learn_seed", row["question_id"]))[:3]
    decisions = tmp_path / "data" / "decisions.jsonl"
    review_decisions = []
    for row in chosen:
        review_decisions.append({
            "protocol": DECISION_PROTOCOL,
            "review_item_id": row["review_item_id"],
            "packet_payload_sha256": row["packet_payload_sha256"],
            "question_id": row["question_id"],
            "reviewer_id": "proposer",
            "reviewer_type": "open_source_model_proposer",
            "reviewer_authority_receipt_sha256": "1" * 64,
            "verdict": "ACCEPT_PROPOSAL",
            "correctness_label": "UNESTABLISHED",
            "approved_policy": {"operation": "difference", "version": 1},
            "source_refs": [row["allowed_source_ref_sha256"][0]],
        })
    _write_jsonl(decisions, review_decisions)
    decision_config = _fixture(tmp_path / "scored", decisions=decisions)
    result = build_active_learning_cycle(config_path=decision_config, output_dir=tmp_path / "scored-cycle")
    assert result["counts"]["trusted_training_record_count"] == 0
    assert result["counts"]["machine_provisional_policy_count"] == 1
    assert result["calibration_gate"]["promotion_status"] == "BLOCKED"
    policies = load_jsonl(tmp_path / "scored-cycle" / "learned_policy_candidates_v1.jsonl")
    learned = [row for row in policies if row["policy_status"] == "MACHINE_PROVISIONAL"]
    assert len(learned) == 1
    assert learned[0]["promotion_allowed"] is False


def test_single_or_spoofed_review_cannot_enter_training(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    initial = tmp_path / "initial"
    build_active_learning_cycle(config_path=config, output_dir=initial)
    row = next(item for item in load_jsonl(initial / "active_learning_review_batch_v1.jsonl") if item["evaluation_role"] == "active_learning")
    decision = {
        "protocol": DECISION_PROTOCOL,
        "review_item_id": row["review_item_id"],
        "packet_payload_sha256": row["packet_payload_sha256"],
        "question_id": row["question_id"],
        "reviewer_id": "proposer",
        "reviewer_type": "open_source_model_proposer",
        "reviewer_authority_receipt_sha256": "1" * 64,
        "verdict": "ACCEPT_PROPOSAL",
        "correctness_label": "UNESTABLISHED",
        "approved_policy": {"operation": "difference", "version": 1},
        "source_refs": [row["allowed_source_ref_sha256"][0]],
    }
    decisions = tmp_path / "data" / "single.jsonl"
    _write_jsonl(decisions, [decision])
    scored_config = _fixture(tmp_path / "scored", decisions=decisions)
    result = build_active_learning_cycle(config_path=scored_config, output_dir=tmp_path / "single-cycle")
    assert result["counts"]["trusted_training_record_count"] == 0
    assert result["counts"]["machine_provisional_policy_count"] == 0

    decision["source_refs"] = ["f" * 64]
    spoofed = tmp_path / "data" / "spoofed.jsonl"
    _write_jsonl(spoofed, [decision])
    bad_config = _fixture(tmp_path / "bad", decisions=spoofed)
    try:
        build_active_learning_cycle(config_path=bad_config, output_dir=tmp_path / "bad-cycle")
    except ValueError as error:
        assert "packet-bound" in str(error)
    else:
        raise AssertionError("spoofed source reference was accepted")

    decision["source_refs"] = [row["allowed_source_ref_sha256"][0]]
    decision["source_group"] = {"issuer": "SPOOF", "document_uid": "SPOOF"}
    spoofed_group = tmp_path / "data" / "spoofed-group.jsonl"
    _write_jsonl(spoofed_group, [decision])
    group_config = _fixture(tmp_path / "bad-group", decisions=spoofed_group)
    try:
        build_active_learning_cycle(config_path=group_config, output_dir=tmp_path / "bad-group-cycle")
    except ValueError as error:
        assert "cannot be reviewer-supplied" in str(error)
    else:
        raise AssertionError("reviewer-supplied source group was accepted")


def test_wilson_gate_needs_about_125_independently_correct_audits() -> None:
    assert wilson_lower_bound(124, 124) < 0.97
    assert wilson_lower_bound(125, 125) >= 0.97


def test_model_at_14_7b_is_rejected_by_strict_competition_cap(tmp_path: Path) -> None:
    config_path = _fixture(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    policy_path = tmp_path / config["input_paths"]["open_source_model_policy"]
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["routes"][0]["parameter_count_billions"] = 14.7
    _write_json(policy_path, policy)
    config["locked_input_sha256"]["open_source_model_policy"] = sha256_file(policy_path)
    _write_json(config_path, config)
    try:
        build_active_learning_cycle(config_path=config_path, output_dir=tmp_path / "invalid-model-cycle")
    except ValueError as error:
        assert "strict <14.7B" in str(error)
    else:
        raise AssertionError("14.7B model was accepted despite strict competition cap")


def test_chatgpt_human_equivalent_review_stays_outside_training_and_model_consensus(tmp_path: Path) -> None:
    config = _fixture(tmp_path)
    initial = tmp_path / "initial"
    build_active_learning_cycle(config_path=config, output_dir=initial)
    row = next(item for item in load_jsonl(initial / "active_learning_review_batch_v1.jsonl") if item["evaluation_role"] == "active_learning")
    decisions = tmp_path / "data" / "chatgpt-review.jsonl"
    _write_jsonl(decisions, [{
        "protocol": DECISION_PROTOCOL,
        "review_item_id": row["review_item_id"],
        "packet_payload_sha256": row["packet_payload_sha256"],
        "question_id": row["question_id"],
        "reviewer_id": "chatgpt-reviewer",
        "reviewer_type": "chatgpt_human_equivalent_reviewer",
        "reviewer_authority_receipt_sha256": "5" * 64,
        "verdict": "ACCEPT_PROPOSAL",
        "correctness_label": "CORRECT",
        "approved_policy": {"operation": "difference", "version": 1},
        "source_refs": [row["allowed_source_ref_sha256"][0]],
    }])
    scored_config = _fixture(tmp_path / "scored", decisions=decisions)
    result = build_active_learning_cycle(config_path=scored_config, output_dir=tmp_path / "scored-cycle")
    assert result["counts"]["trusted_training_record_count"] == 0
    assert result["counts"]["machine_provisional_policy_count"] == 0
