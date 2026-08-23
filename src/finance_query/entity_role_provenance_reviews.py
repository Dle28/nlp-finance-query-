"""Validate ChatGPT decisions over hash-bound entity-role provenance candidates."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

from .semantic_approvals import canonical_sha256, sha256_file


QUEUE_PROTOCOL = "vifinqa_entity_role_provenance_candidate_queue_v1"
DECISION_PROTOCOL = "vifinqa_entity_role_provenance_chatgpt_decision_v1"
ALLOWED_DECISIONS = {"approve_role_provenance", "reject_candidate_set"}


class EntityRoleProvenanceReviewError(ValueError):
    """Raised when a role-provenance review is incomplete or loses lineage."""


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise EntityRoleProvenanceReviewError(f"{path}:{line_number} must be an object")
        values.append(value)
    return values


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise EntityRoleProvenanceReviewError(f"{path} must be an object")
    return value


def _valid_hash(row: Mapping[str, Any], field: str) -> bool:
    expected = str(row.get(field) or "")
    payload = {key: value for key, value in row.items() if key != field}
    return len(expected) == 64 and expected == canonical_sha256(payload)


def _reviewer_provenance() -> dict[str, Any]:
    return {
        "reviewer_type": "chatgpt_verified",
        "reviewer_id": "chatgpt-gpt5-role-provenance-reviewer-v1",
        "model_family": "GPT-5",
        "review_policy": "fail_closed_evidence_bound_v1",
        "authority_grant": {
            "granted_by": "campaign_owner",
            "grant_scope": "entity_role_review_gate_equivalence",
            "grant_basis": "explicit_user_instruction",
        },
    }


def build_decisions(
    *,
    candidate_queue: Path,
    review_spec: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Bind one complete ChatGPT review to every candidate-queue item."""

    queue_rows = _rows(candidate_queue)
    if not queue_rows:
        raise EntityRoleProvenanceReviewError("candidate queue must be non-empty")
    if any(row.get("protocol") != QUEUE_PROTOCOL for row in queue_rows):
        raise EntityRoleProvenanceReviewError("candidate queue protocol mismatch")
    if any(not _valid_hash(row, "queue_item_sha256") for row in queue_rows):
        raise EntityRoleProvenanceReviewError("candidate queue item SHA-256 mismatch")
    queue_by_question = {int(row["question_id"]): row for row in queue_rows}
    if len(queue_by_question) != len(queue_rows):
        raise EntityRoleProvenanceReviewError("candidate queue has duplicate question IDs")

    spec = _json(review_spec)
    reviews = spec.get("reviews") or []
    if not isinstance(reviews, list):
        raise EntityRoleProvenanceReviewError("review spec reviews must be a list")
    review_by_question = {
        int(review["question_id"]): review
        for review in reviews
        if isinstance(review, Mapping) and review.get("question_id") is not None
    }
    if len(review_by_question) != len(reviews):
        raise EntityRoleProvenanceReviewError("review spec has duplicate or invalid question IDs")
    missing = sorted(set(queue_by_question) - set(review_by_question))
    extra = sorted(set(review_by_question) - set(queue_by_question))
    if missing or extra:
        raise EntityRoleProvenanceReviewError(
            f"review coverage mismatch: missing={missing}, extra={extra}"
        )

    queue_sha = sha256_file(candidate_queue)
    decisions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for question_id in sorted(queue_by_question):
        queue_item = queue_by_question[question_id]
        review = review_by_question[question_id]
        decision = str(review.get("decision") or "")
        if decision not in ALLOWED_DECISIONS:
            raise EntityRoleProvenanceReviewError(f"Q{question_id} has invalid decision")
        rationale = str(review.get("rationale") or "").strip()
        reason_code = str(review.get("reason_code") or "").strip()
        if not rationale or not reason_code:
            raise EntityRoleProvenanceReviewError(f"Q{question_id} lacks rationale or reason code")

        candidates = queue_item.get("candidates") or []
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or not _valid_hash(candidate, "candidate_sha256"):
                raise EntityRoleProvenanceReviewError(f"Q{question_id} candidate SHA-256 mismatch")
        selected: Mapping[str, Any] | None = None
        selected_line = review.get("selected_line_number")
        if decision == "approve_role_provenance":
            if not isinstance(selected_line, int):
                raise EntityRoleProvenanceReviewError(f"Q{question_id} approval lacks selected line")
            matches = [candidate for candidate in candidates if candidate.get("line_number") == selected_line]
            if len(matches) != 1:
                raise EntityRoleProvenanceReviewError(f"Q{question_id} selected line is not unique")
            selected = matches[0]
            answers = {
                "refers_to_issuer": True,
                "asserts_parent_role": True,
                "same_document": True,
                "sufficient_for_certificate": True,
            }
            counts["approved_role_provenance"] += 1
        else:
            if selected_line is not None:
                raise EntityRoleProvenanceReviewError(f"Q{question_id} rejection selects a line")
            answers = {
                "refers_to_issuer": False,
                "asserts_parent_role": False,
                "same_document": True,
                "sufficient_for_certificate": False,
            }
            counts["rejected_candidate_set"] += 1

        payload = {
            "schema_version": 1,
            "protocol": DECISION_PROTOCOL,
            "question_id": question_id,
            "claim_entity_identity": queue_item.get("claim_entity_identity"),
            "claim_entity_role": queue_item.get("claim_entity_role"),
            "document_uid": queue_item.get("document_uid"),
            "queue_item_sha256": queue_item["queue_item_sha256"],
            "source_candidate_queue_sha256": queue_sha,
            "decision": decision,
            "reason_code": reason_code,
            "rationale": rationale,
            "review_answers": answers,
            "selected_source_anchor": dict(selected) if selected is not None else None,
            "rejected_candidate_sha256s": (
                [] if selected is not None else [candidate["candidate_sha256"] for candidate in candidates]
            ),
            "decision_provenance": _reviewer_provenance(),
            "source_contract": {
                "role_gate_authorized": decision == "approve_role_provenance",
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        decisions.append({**payload, "decision_sha256": canonical_sha256(payload)})

    output_dir.mkdir(parents=True, exist_ok=False)
    decisions_path = output_dir / "entity_role_provenance_chatgpt_decisions_v1.jsonl"
    decisions_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in decisions
        ),
        encoding="utf-8",
    )
    result = {
        "schema_version": 1,
        "protocol": DECISION_PROTOCOL,
        "inputs": {
            "candidate_queue": {"path": str(candidate_queue), "sha256": queue_sha},
            "review_spec": {"path": str(review_spec), "sha256": sha256_file(review_spec)},
        },
        "outputs": {"decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)}},
        "counts": {"decision_count": len(decisions), **dict(sorted(counts.items()))},
        "source_contract": {
            "chatgpt_authority_grant_present": True,
            "review_complete": True,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "entity_role_provenance_chatgpt_decisions_v1.manifest.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "manifest_path": str(manifest_path)}
