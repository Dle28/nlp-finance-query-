"""Rebind verified cell decisions and add evidence-bound ChatGPT role reviews."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .semantic_approvals import SEMANTIC_DECISION_PROTOCOL, canonical_sha256, sha256_file


PROTOCOL = "vifinqa_semantic_entity_role_chatgpt_augmentation_v1"
STABLE_QUEUE_FIELDS = (
    "question_id",
    "stage_id",
    "role",
    "candidate_variable_id",
    "requested_entity",
    "requested_scope",
    "document_uid",
    "internal_table_uid",
    "value_cell",
    "row_label_candidates",
    "source_title",
    "source_provenance",
)


class EntityRoleChatGPTReviewError(ValueError):
    """Raised when a role augmentation loses immutable lineage."""


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise EntityRoleChatGPTReviewError(f"{path}:{line_number} must be an object")
        values.append(value)
    return values


def _identity(row: Mapping[str, Any]) -> tuple[int, str, str]:
    return (int(row["question_id"]), str(row.get("stage_id") or ""), str(row.get("role") or ""))


def _valid_queue_item(row: Mapping[str, Any]) -> bool:
    item_sha = str(row.get("queue_item_sha256") or "")
    payload = {key: value for key, value in row.items() if key != "queue_item_sha256"}
    return bool(item_sha) and item_sha == canonical_sha256(payload)


def _chatgpt_role_provenance() -> dict[str, Any]:
    return {
        "reviewer_type": "chatgpt_verified",
        "reviewer_id": "chatgpt-gpt5-role-reviewer-v1",
        "model_family": "GPT-5",
        "review_policy": "fail_closed_evidence_bound_v1",
        "authority_grant": {
            "granted_by": "campaign_owner",
            "grant_scope": "entity_role_review_gate_equivalence",
            "grant_basis": "explicit_user_instruction",
        },
    }


def augment(
    *,
    prior_queue: Path,
    prior_decisions: Path,
    current_queue: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Migrate unchanged human decisions and add only explicit role literals."""

    old_queue_rows = _rows(prior_queue)
    new_queue_rows = _rows(current_queue)
    old_decisions = _rows(prior_decisions)
    if not old_queue_rows or not new_queue_rows or not old_decisions:
        raise EntityRoleChatGPTReviewError("augmentation requires non-empty queues and decisions")
    if any(not _valid_queue_item(row) for row in old_queue_rows + new_queue_rows):
        raise EntityRoleChatGPTReviewError("semantic queue item identity is invalid")
    old_queue_sha = sha256_file(prior_queue)
    new_queue_sha = sha256_file(current_queue)
    old_by_identity = {_identity(row): row for row in old_queue_rows}
    if len(old_by_identity) != len(old_queue_rows):
        raise EntityRoleChatGPTReviewError("prior queue has duplicate identities")
    old_decision_by_item = {str(row.get("queue_item_sha256") or ""): row for row in old_decisions}
    if len(old_decision_by_item) != len(old_decisions):
        raise EntityRoleChatGPTReviewError("prior decisions have duplicate queue items")

    migrated: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for current in new_queue_rows:
        prior = old_by_identity.get(_identity(current))
        if prior is None or any(prior.get(field) != current.get(field) for field in STABLE_QUEUE_FIELDS):
            raise EntityRoleChatGPTReviewError("current semantic queue changed beyond entity-role context")
        prior_decision = old_decision_by_item.get(str(prior["queue_item_sha256"]))
        if prior_decision is None:
            raise EntityRoleChatGPTReviewError("current queue lacks a prior reviewed decision")
        if (
            prior_decision.get("protocol") != SEMANTIC_DECISION_PROTOCOL
            or prior_decision.get("source_review_queue_sha256") != old_queue_sha
            or (prior_decision.get("decision_provenance") or {}).get("reviewer_type") != "human_verified"
        ):
            raise EntityRoleChatGPTReviewError("prior decision is not a hash-bound human decision")
        decision = dict(prior_decision)
        decision["queue_item_sha256"] = current["queue_item_sha256"]
        decision["source_review_queue_sha256"] = new_queue_sha
        decision["migration_lineage"] = {
            "protocol": PROTOCOL,
            "prior_queue_item_sha256": prior["queue_item_sha256"],
            "prior_decision_sha256": canonical_sha256(prior_decision),
        }
        requested_role = str(current.get("requested_entity_role") or "").strip()
        source_title = str(current.get("source_title") or "")
        explicit_parent = bool(re.search(r"\bcông ty m(?:ẹ|ệ)\b", source_title, re.IGNORECASE))
        if decision.get("decision") == "approve" and requested_role == "parent" and explicit_parent:
            decision.update(
                {
                    "approved_entity_role": "parent",
                    "entity_role_evidence_checked": True,
                    "entity_role_source_title_sha256": hashlib.sha256(source_title.encode("utf-8")).hexdigest(),
                    "entity_role_decision_provenance": _chatgpt_role_provenance(),
                }
            )
            status_counts["chatgpt_role_verified"] += 1
        elif requested_role:
            decision.update(
                {
                    "approved_entity_role": None,
                    "entity_role_evidence_checked": False,
                    "entity_role_source_title_sha256": None,
                    "entity_role_decision_provenance": None,
                }
            )
            status_counts["role_unresolved"] += 1
        else:
            status_counts["role_not_applicable"] += 1
        migrated.append(decision)

    output_dir.mkdir(parents=True, exist_ok=False)
    decisions_path = output_dir / "semantic_binding_human_decisions_with_chatgpt_role_v1.jsonl"
    decisions_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in migrated),
        encoding="utf-8",
    )
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "inputs": {
            "prior_queue": {"path": str(prior_queue), "sha256": old_queue_sha},
            "prior_decisions": {"path": str(prior_decisions), "sha256": sha256_file(prior_decisions)},
            "current_queue": {"path": str(current_queue), "sha256": new_queue_sha},
        },
        "outputs": {"decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)}},
        "counts": {"decision_count": len(migrated), **dict(sorted(status_counts.items()))},
        "source_contract": {
            "original_human_cell_provenance_preserved": True,
            "chatgpt_role_review_separate": True,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "semantic_entity_role_chatgpt_augmentation_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
