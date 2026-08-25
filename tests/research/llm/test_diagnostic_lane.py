"""Hermetic tests for the bounded proposer-only LLM diagnostic lane."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.research.proof_policy.active_learning_models import (
    MODEL_JOB_PROTOCOL,
    VALIDATED_RESPONSE_PROTOCOL,
)
from finance_query.research.proof_policy.evidence_closure import canonical_sha256, load_json, load_jsonl, sha256_file
from finance_query.research.llm.diagnostic_lane import (
    LLM_DIAGNOSTIC_BATCH_PROTOCOL,
    LLM_DIAGNOSTIC_REVIEW_PROTOCOL,
    build_diagnostic_batch,
    evaluate_diagnostic_batch,
    verify_diagnostic_batch,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def _source_job(root: Path, *, count: int = 24) -> tuple[Path, Path]:
    requests = root / "source" / "proposer_requests.jsonl"
    queues = ("formula_definition", "operand_ambiguity", "temporal")
    rows = [
        {
            "request_id": f"request-{qid}",
            "review_item_id": f"review-{qid}",
            "question_id": qid,
            "model_role": "open_source_model_proposer",
            "model_id": "Qwen/Qwen3-8B",
            "model_revision": "a" * 40,
            "generation_contract": {
                "do_sample": False,
                "max_new_tokens": 384,
                "max_seconds_per_request": 90,
                "progress_every": 4,
                "qwen3_thinking_enabled": False,
                "temperature": 0,
            },
            "packet": {
                "queue": queues[(qid - 1) % len(queues)],
                "cluster_id": f"cluster-{(qid - 1) % 6}",
            },
        }
        for qid in range(1, count + 1)
    ]
    _write_jsonl(requests, rows)
    manifest = root / "source" / "active_learning_model_job.manifest.json"
    route = {
        "role": "open_source_model_proposer",
        "model_id": "Qwen/Qwen3-8B",
        "revision": "a" * 40,
        "parameter_count_billions": 8.2,
        "weight_shards": [],
    }
    _write_json(
        manifest,
        {
            "protocol": MODEL_JOB_PROTOCOL,
            "status": "PREPARED_GPU_EXECUTION_NOT_RUN",
            "release_status": "blocked",
            "model_routes": {"open_source_model_proposer": route},
            "outputs": {
                "proposer_requests": {
                    "path": str(requests),
                    "sha256": sha256_file(requests),
                }
            },
        },
    )
    control = root / "control.json"
    _write_json(
        control,
        {
            "protocol": "vifinqa_llm_lane_control_v1",
            "mode": "paused_except_bounded_diagnostic",
            "default_operational_llm_enabled": False,
            "diagnostic": {
                "proposer_only": True,
                "selection_policy": "queue_cluster_round_robin",
                "review_protocol": "independent_counterbalanced_v1",
                "max_packets": 20,
                "min_human_reviewed": 20,
                "min_human_reviewed_proposals": 5,
                "min_human_reviewed_for_keep": 125,
                "min_human_reviewed_proposals_for_keep": 25,
            },
            "decision_thresholds": {
                "min_schema_valid_rate": 0.9,
                "min_valid_proposal_rate": 0.1,
                "min_proposal_precision": 0.8,
                "min_source_reference_exact_rate": 0.9,
                "min_reviewer_time_reduction": 0.25,
                "max_missed_blocker_rate": 0.0,
                "max_missed_blocker_wilson_upper_95": 0.03,
            },
            "training_eligible": False,
            "release_authorized": False,
        },
    )
    return manifest, control


def _build(root: Path) -> Path:
    source, control = _source_job(root)
    result = build_diagnostic_batch(
        source_model_job_manifest_path=source,
        control_policy_path=control,
        output_dir=root / "diagnostic",
    )
    return result.manifest_path


def _validated_rows(manifest_path: Path) -> list[dict[str, object]]:
    manifest = load_json(manifest_path)
    requests = load_jsonl(Path(manifest["outputs"]["proposer_requests"]["path"]))
    rows = []
    for request in requests:
        payload = {
            "schema_version": 1,
            "protocol": VALIDATED_RESPONSE_PROTOCOL,
            "request_id": request["request_id"],
            "review_item_id": request["review_item_id"],
            "question_id": request["question_id"],
            "model_role": "open_source_model_proposer",
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "validation_status": "VALID_PROPOSAL",
            "verdict": "PROPOSE_RULE",
            "policy": {"rule_type": "test"},
            "policy_sha256": canonical_sha256({"rule_type": "test"}),
            "cited_source_ref_sha256": ["b" * 64],
            "reason": None,
            "training_eligible": False,
            "certification_allowed": False,
            "submission_eligible": False,
        }
        rows.append({**payload, "validated_response_id": canonical_sha256(payload)})
    return rows


def _completed_reviews(manifest_path: Path) -> list[dict[str, object]]:
    manifest = load_json(manifest_path)
    templates = load_jsonl(Path(manifest["outputs"]["human_review_template"]["path"]))
    return [
        {
            **row,
            "protocol": LLM_DIAGNOSTIC_REVIEW_PROTOCOL,
            "human_review_status": "COMPLETE",
            "comparison_design": "independent_reviewers",
            "assignment_id": f"assignment-{row['review_item_id']}",
            "baseline_reviewer_id": "reviewer-baseline",
            "assisted_reviewer_id": "reviewer-assisted",
            "comparison_order": "baseline_then_assisted",
            "response_correct": True,
            "source_reference_exact": True,
            "missed_blocker": False,
            "baseline_review_seconds": 100,
            "assisted_review_seconds": 50,
            "notes": "independent diagnostic review",
        }
        for row in templates
    ]


def test_build_is_queue_stratified_proposer_only_and_hash_verified(tmp_path: Path) -> None:
    manifest_path = _build(tmp_path)
    receipt = verify_diagnostic_batch(manifest_path)
    manifest = load_json(manifest_path)
    requests = load_jsonl(Path(manifest["outputs"]["proposer_requests"]["path"]))
    assert receipt["packet_count"] == 20
    assert set(row["packet"]["queue"] for row in requests) == {
        "formula_definition",
        "operand_ambiguity",
        "temporal",
    }
    assert all(row["model_role"] == "open_source_model_proposer" for row in requests)
    assert manifest["training_eligible"] is False
    assert manifest["release_status"] == "blocked"


def test_small_complete_diagnostic_cannot_recommend_keeping_a_proposer(tmp_path: Path) -> None:
    manifest_path = _build(tmp_path)
    validated = tmp_path / "validated.jsonl"
    reviews = tmp_path / "reviews.jsonl"
    report = tmp_path / "keep.json"
    _write_jsonl(validated, _validated_rows(manifest_path))
    _write_jsonl(reviews, _completed_reviews(manifest_path))
    result = evaluate_diagnostic_batch(
        diagnostic_manifest_path=manifest_path,
        validated_responses_path=validated,
        human_reviews_path=reviews,
        output_path=report,
    )
    assert result.decision == "MORE_DIAGNOSTIC_REVIEW_REQUIRED"
    value = json.loads(report.read_text(encoding="utf-8"))
    assert value["minimum_review_requirements"]["human_reviews_for_keep"] == 125
    assert value["metrics"]["comparison_design_counts"] == {"independent_reviewers": 20}
    assert value["promotion_effect"] == "none_explicit_policy_change_required"
    assert value["release_authorized"] is False


def test_one_missed_blocker_recommends_removal(tmp_path: Path) -> None:
    manifest_path = _build(tmp_path)
    validated = tmp_path / "validated.jsonl"
    reviews = tmp_path / "reviews.jsonl"
    report = tmp_path / "remove.json"
    rows = _completed_reviews(manifest_path)
    rows[0]["missed_blocker"] = True
    _write_jsonl(validated, _validated_rows(manifest_path))
    _write_jsonl(reviews, rows)
    result = evaluate_diagnostic_batch(
        diagnostic_manifest_path=manifest_path,
        validated_responses_path=validated,
        human_reviews_path=reviews,
        output_path=report,
    )
    assert result.decision == "REMOVE_FROM_OPERATIONAL_PIPELINE"
    assert load_json(report)["gates"]["missed_blocker_rate"] is False


def test_incomplete_review_cannot_make_keep_or_remove_decision(tmp_path: Path) -> None:
    manifest_path = _build(tmp_path)
    validated = tmp_path / "validated.jsonl"
    reviews = tmp_path / "reviews.jsonl"
    _write_jsonl(validated, _validated_rows(manifest_path))
    _write_jsonl(reviews, _completed_reviews(manifest_path)[:5])
    result = evaluate_diagnostic_batch(
        diagnostic_manifest_path=manifest_path,
        validated_responses_path=validated,
        human_reviews_path=reviews,
        output_path=tmp_path / "more.json",
    )
    assert result.decision == "MORE_DIAGNOSTIC_REVIEW_REQUIRED"

