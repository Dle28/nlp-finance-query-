"""Fail-closed ChatGPT critique for hash-bound operation-graph candidates."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

from .operation_graphs import canonical_sha256, sha256_file


PROTOCOL = "vifinqa_operation_graph_chatgpt_decision_v1"
QUEUE_PROTOCOL = "vifinqa_operation_graph_review_queue_v1"
CANDIDATE_PROTOCOL = "vifinqa_operation_graph_candidates_v1"
TYPED_STATUS = "typed_candidate_validated_not_authorized"
BLOCKED_STATUS = "blocked_graph_candidate"
EXPLICIT_DECISIONS = {"approve_graph_semantics", "reject_graph_semantics"}


class OperationGraphChatGPTReviewError(ValueError):
    """Raised when a graph review is incomplete, stale, or over-authorizing."""


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise OperationGraphChatGPTReviewError(f"{path}:{line_number} must be an object")
        values.append(value)
    return values


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise OperationGraphChatGPTReviewError(f"{path} must be an object")
    return value


def _require_manifest_output(
    *, manifest_path: Path, artifact_path: Path, output_name: str, protocol: str
) -> Mapping[str, Any]:
    manifest = _json(manifest_path)
    if manifest.get("protocol") != protocol:
        raise OperationGraphChatGPTReviewError(f"unexpected {output_name} manifest protocol")
    expected = (((manifest.get("outputs") or {}).get(output_name) or {}).get("sha256"))
    if expected != sha256_file(artifact_path):
        raise OperationGraphChatGPTReviewError(f"{output_name} SHA-256 mismatch")
    return manifest


def _valid_queue_item(row: Mapping[str, Any]) -> bool:
    payload = {
        key: value
        for key, value in row.items()
        if key not in {"schema_version", "protocol", "queue_item_sha256", "source_contract"}
    }
    return (
        row.get("protocol") == QUEUE_PROTOCOL
        and row.get("queue_item_sha256") == canonical_sha256(payload)
    )


def _valid_candidate_item(row: Mapping[str, Any]) -> bool:
    payload = {
        key: value
        for key, value in row.items()
        if key not in {"schema_version", "protocol", "candidate_item_sha256", "source_contract"}
    }
    return (
        row.get("protocol") == CANDIDATE_PROTOCOL
        and row.get("candidate_item_sha256") == canonical_sha256(payload)
    )


def _reviewer_provenance() -> dict[str, Any]:
    return {
        "reviewer_type": "chatgpt_verified",
        "reviewer_id": "chatgpt-gpt5-operation-graph-reviewer-v1",
        "model_family": "GPT-5",
        "review_policy": "fail_closed_question_graph_semantics_v1",
        "authority_grant": {
            "granted_by": "campaign_owner",
            "grant_scope": "operation_graph_review_gate_equivalence",
            "grant_basis": "explicit_user_instruction",
        },
    }


def build_decisions(
    *,
    review_queue: Path,
    review_manifest: Path,
    candidates: Path,
    candidate_manifest: Path,
    review_spec: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Review every graph-ready item without authorizing formula execution."""

    _require_manifest_output(
        manifest_path=review_manifest,
        artifact_path=review_queue,
        output_name="queue",
        protocol=QUEUE_PROTOCOL,
    )
    _require_manifest_output(
        manifest_path=candidate_manifest,
        artifact_path=candidates,
        output_name="candidates",
        protocol=CANDIDATE_PROTOCOL,
    )
    queue_rows = [row for row in _rows(review_queue) if row.get("queue_status") == "review_required_operation_graph"]
    candidate_rows = _rows(candidates)
    if any(not _valid_queue_item(row) for row in queue_rows):
        raise OperationGraphChatGPTReviewError("operation graph queue item SHA-256 mismatch")
    if any(not _valid_candidate_item(row) for row in candidate_rows):
        raise OperationGraphChatGPTReviewError("operation graph candidate SHA-256 mismatch")
    queue_by_question = {int(row["question_id"]): row for row in queue_rows}
    candidate_by_question = {int(row["question_id"]): row for row in candidate_rows}
    if len(queue_by_question) != len(queue_rows) or len(candidate_by_question) != len(candidate_rows):
        raise OperationGraphChatGPTReviewError("duplicate graph question IDs")
    if set(queue_by_question) != set(candidate_by_question):
        raise OperationGraphChatGPTReviewError("queue and candidates cover different questions")
    for question_id, candidate in candidate_by_question.items():
        if candidate.get("source_queue_item_sha256") != queue_by_question[question_id].get("queue_item_sha256"):
            raise OperationGraphChatGPTReviewError("candidate is stale for review queue")

    spec = _json(review_spec)
    explicit_reviews = spec.get("typed_candidate_reviews") or []
    if not isinstance(explicit_reviews, list):
        raise OperationGraphChatGPTReviewError("typed_candidate_reviews must be a list")
    review_by_question = {
        int(row["question_id"]): row
        for row in explicit_reviews
        if isinstance(row, Mapping) and row.get("question_id") is not None
    }
    typed_questions = {
        question_id
        for question_id, row in candidate_by_question.items()
        if row.get("candidate_status") == TYPED_STATUS
    }
    if set(review_by_question) != typed_questions or len(review_by_question) != len(explicit_reviews):
        raise OperationGraphChatGPTReviewError("typed candidate review coverage mismatch")
    if spec.get("blocked_candidate_policy") != "confirm_existing_blocker_fail_closed":
        raise OperationGraphChatGPTReviewError("blocked candidate policy is not fail-closed")

    decisions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    candidate_sha = sha256_file(candidates)
    queue_sha = sha256_file(review_queue)
    for question_id in sorted(candidate_by_question):
        item = queue_by_question[question_id]
        candidate = candidate_by_question[question_id]
        status = candidate.get("candidate_status")
        if status == BLOCKED_STATUS:
            reasons = [str(value) for value in candidate.get("reason_codes") or []]
            if not reasons:
                raise OperationGraphChatGPTReviewError(f"Q{question_id} blocked candidate lacks reasons")
            decision = "confirmed_blocker"
            rationale = "Candidate chưa tạo được completed graph; giữ fail-closed theo blocker đã hash-bind."
            semantic_checks = {
                "operator_matches_question": "not_reviewable",
                "stage_population_complete": "not_reviewable",
                "noncommutative_order_correct": "not_reviewable",
                "final_output_type_matches_question": "not_reviewable",
            }
            graph = None
            counts[decision] += 1
        elif status == TYPED_STATUS:
            review = review_by_question[question_id]
            decision = str(review.get("decision") or "")
            if decision not in EXPLICIT_DECISIONS:
                raise OperationGraphChatGPTReviewError(f"Q{question_id} decision is invalid")
            rationale = str(review.get("rationale") or "").strip()
            semantic_checks = review.get("semantic_checks")
            if not rationale or not isinstance(semantic_checks, Mapping):
                raise OperationGraphChatGPTReviewError(f"Q{question_id} review is incomplete")
            expected_checks = {
                "operator_matches_question",
                "stage_population_complete",
                "noncommutative_order_correct",
                "final_output_type_matches_question",
            }
            if set(semantic_checks) != expected_checks or any(
                value not in {True, False, "not_applicable"} for value in semantic_checks.values()
            ):
                raise OperationGraphChatGPTReviewError(f"Q{question_id} semantic checks are invalid")
            if decision == "approve_graph_semantics" and any(value is False for value in semantic_checks.values()):
                raise OperationGraphChatGPTReviewError(f"Q{question_id} approval has a failed semantic check")
            if decision == "reject_graph_semantics" and not any(value is False for value in semantic_checks.values()):
                raise OperationGraphChatGPTReviewError(f"Q{question_id} rejection lacks a failed semantic check")
            reasons = [str(value) for value in review.get("reason_codes") or []]
            if decision == "reject_graph_semantics" and not reasons:
                raise OperationGraphChatGPTReviewError(f"Q{question_id} rejection lacks reason codes")
            graph = candidate.get("candidate_graph")
            if not isinstance(graph, Mapping):
                raise OperationGraphChatGPTReviewError(f"Q{question_id} typed candidate lacks graph")
            counts[decision] += 1
        else:
            raise OperationGraphChatGPTReviewError(f"Q{question_id} candidate status is invalid")

        payload = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "question": item.get("question"),
            "queue_item_sha256": item.get("queue_item_sha256"),
            "candidate_item_sha256": candidate.get("candidate_item_sha256"),
            "source_review_queue_sha256": queue_sha,
            "source_candidates_sha256": candidate_sha,
            "candidate_status": status,
            "decision": decision,
            "reason_codes": reasons,
            "rationale": rationale,
            "semantic_checks": dict(semantic_checks),
            "reviewed_graph": dict(graph) if isinstance(graph, Mapping) else None,
            "decision_provenance": _reviewer_provenance(),
            "source_contract": {
                "graph_review_gate_authorized": decision == "approve_graph_semantics",
                "eligible_for_materialization": False,
                "may_execute_formula": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        decisions.append({**payload, "decision_sha256": canonical_sha256(payload)})

    output_dir.mkdir(parents=True, exist_ok=False)
    decisions_path = output_dir / "operation_graph_chatgpt_decisions_v1.jsonl"
    decisions_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in decisions
        ),
        encoding="utf-8",
    )
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "reviewed_not_materialized",
        "inputs": {
            "review_queue": {"path": str(review_queue), "sha256": queue_sha},
            "review_manifest": {"path": str(review_manifest), "sha256": sha256_file(review_manifest)},
            "candidates": {"path": str(candidates), "sha256": candidate_sha},
            "candidate_manifest": {"path": str(candidate_manifest), "sha256": sha256_file(candidate_manifest)},
            "review_spec": {"path": str(review_spec), "sha256": sha256_file(review_spec)},
        },
        "outputs": {"decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)}},
        "counts": {"decision_count": len(decisions), **dict(sorted(counts.items()))},
        "source_contract": {
            "chatgpt_authority_grant_present": True,
            "eligible_for_materialization": False,
            "may_execute_formula": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "operation_graph_chatgpt_decisions_v1.manifest.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "manifest_path": str(manifest_path)}
