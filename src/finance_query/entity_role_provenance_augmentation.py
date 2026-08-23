"""Attach approved document-line role provenance to existing semantic decisions."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

from .entity_role_provenance import PROTOCOL as QUEUE_PROTOCOL
from .entity_role_provenance_reviews import DECISION_PROTOCOL
from .semantic_approvals import SEMANTIC_DECISION_PROTOCOL, canonical_sha256, sha256_file


PROTOCOL = "vifinqa_semantic_entity_role_provenance_augmentation_v1"


class EntityRoleProvenanceAugmentationError(ValueError):
    """Raised when role provenance cannot be bound to semantic decisions."""


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise EntityRoleProvenanceAugmentationError(f"{path}:{line_number} must be an object")
        values.append(value)
    return values


def _valid_hash(row: Mapping[str, Any], field: str) -> bool:
    expected = str(row.get(field) or "")
    payload = {key: value for key, value in row.items() if key != field}
    return len(expected) == 64 and expected == canonical_sha256(payload)


def augment(
    *,
    semantic_queue: Path,
    prior_semantic_decisions: Path,
    provenance_queue: Path,
    provenance_decisions: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Add only approved role fields; preserve all human row/cell fields."""

    semantic_queue_rows = _rows(semantic_queue)
    semantic_queue_sha = sha256_file(semantic_queue)
    semantic_by_sha: dict[str, Mapping[str, Any]] = {}
    for item in semantic_queue_rows:
        if not _valid_hash(item, "queue_item_sha256"):
            raise EntityRoleProvenanceAugmentationError("semantic queue item SHA-256 mismatch")
        semantic_by_sha[str(item["queue_item_sha256"])] = item

    role_queue_rows = _rows(provenance_queue)
    role_queue_sha = sha256_file(provenance_queue)
    role_queue_by_sha: dict[str, Mapping[str, Any]] = {}
    for item in role_queue_rows:
        if item.get("protocol") != QUEUE_PROTOCOL or not _valid_hash(item, "queue_item_sha256"):
            raise EntityRoleProvenanceAugmentationError("role provenance queue item is invalid")
        role_queue_by_sha[str(item["queue_item_sha256"])] = item

    role_reviews_by_question: dict[int, Mapping[str, Any]] = {}
    for review in _rows(provenance_decisions):
        if review.get("protocol") != DECISION_PROTOCOL or not _valid_hash(review, "decision_sha256"):
            raise EntityRoleProvenanceAugmentationError("role provenance decision is invalid")
        if review.get("source_candidate_queue_sha256") != role_queue_sha:
            raise EntityRoleProvenanceAugmentationError("role provenance decision is stale")
        queue_item = role_queue_by_sha.get(str(review.get("queue_item_sha256") or ""))
        if queue_item is None or int(queue_item["question_id"]) != int(review["question_id"]):
            raise EntityRoleProvenanceAugmentationError("role decision references a missing queue item")
        question_id = int(review["question_id"])
        if question_id in role_reviews_by_question:
            raise EntityRoleProvenanceAugmentationError("duplicate role decision")
        role_reviews_by_question[question_id] = review
    if set(role_reviews_by_question) != {int(row["question_id"]) for row in role_queue_rows}:
        raise EntityRoleProvenanceAugmentationError("role decision coverage is incomplete")

    output: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    seen_semantic_items: set[str] = set()
    for prior in _rows(prior_semantic_decisions):
        if prior.get("protocol") != SEMANTIC_DECISION_PROTOCOL:
            raise EntityRoleProvenanceAugmentationError("semantic decision protocol mismatch")
        item_sha = str(prior.get("queue_item_sha256") or "")
        item = semantic_by_sha.get(item_sha)
        if item is None or item_sha in seen_semantic_items:
            raise EntityRoleProvenanceAugmentationError("semantic decision references missing or duplicate item")
        seen_semantic_items.add(item_sha)
        if prior.get("source_review_queue_sha256") != semantic_queue_sha:
            raise EntityRoleProvenanceAugmentationError("semantic decision is stale")
        decision = dict(prior)
        question_id = int(item["question_id"])
        role_review = role_reviews_by_question.get(question_id)
        if role_review is not None and role_review.get("decision") == "approve_role_provenance":
            anchor = role_review.get("selected_source_anchor")
            if not isinstance(anchor, Mapping):
                raise EntityRoleProvenanceAugmentationError("approved role review lacks source anchor")
            if (
                anchor.get("document_uid") != item.get("document_uid")
                or role_review.get("claim_entity_identity") != item.get("requested_entity")
                or role_review.get("claim_entity_role") != item.get("requested_entity_role")
            ):
                raise EntityRoleProvenanceAugmentationError("role review does not match semantic claim")
            decision.update(
                {
                    "approved_entity_role": role_review["claim_entity_role"],
                    "entity_role_evidence_checked": True,
                    "entity_role_source_title_sha256": None,
                    "entity_role_source_anchor": dict(anchor),
                    "entity_role_provenance_decision_sha256": role_review["decision_sha256"],
                    "entity_role_provenance_decisions_file_sha256": sha256_file(provenance_decisions),
                    "entity_role_decision_provenance": dict(role_review["decision_provenance"]),
                }
            )
            counts["document_line_role_augmented"] += 1
        elif role_review is not None:
            counts["role_review_rejected"] += 1
        elif decision.get("approved_entity_role"):
            counts["existing_title_role_preserved"] += 1
        else:
            counts["role_not_applicable"] += 1
        output.append(decision)

    output_dir.mkdir(parents=True, exist_ok=False)
    output_path = output_dir / "semantic_binding_human_decisions_with_document_role_v1.jsonl"
    output_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in output
        ),
        encoding="utf-8",
    )
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "inputs": {
            "semantic_queue": {"path": str(semantic_queue), "sha256": semantic_queue_sha},
            "prior_semantic_decisions": {
                "path": str(prior_semantic_decisions),
                "sha256": sha256_file(prior_semantic_decisions),
            },
            "provenance_queue": {"path": str(provenance_queue), "sha256": role_queue_sha},
            "provenance_decisions": {
                "path": str(provenance_decisions),
                "sha256": sha256_file(provenance_decisions),
            },
        },
        "outputs": {"decisions": {"path": str(output_path), "sha256": sha256_file(output_path)}},
        "counts": {"decision_count": len(output), **dict(sorted(counts.items()))},
        "source_contract": {
            "original_human_cell_provenance_preserved": True,
            "chatgpt_role_review_separate": True,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "semantic_entity_role_provenance_augmentation_v1.manifest.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "manifest_path": str(manifest_path)}
