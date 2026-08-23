from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.entity_role_provenance_augmentation import (
    EntityRoleProvenanceAugmentationError,
    augment,
)
from finance_query.entity_role_provenance_reviews import DECISION_PROTOCOL
from finance_query.semantic_approvals import (
    SEMANTIC_DECISION_PROTOCOL,
    canonical_sha256,
    sha256_file,
)


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixtures(tmp_path: Path) -> dict[str, Path]:
    semantic_payload = {
        "question_id": 12,
        "requested_entity": "VGT",
        "requested_entity_role": "parent",
        "document_uid": "VGT_2024_separate",
    }
    semantic_item = {
        **semantic_payload,
        "queue_item_sha256": canonical_sha256(semantic_payload),
    }
    semantic_queue = tmp_path / "semantic-queue.jsonl"
    _jsonl(semantic_queue, [semantic_item])
    prior_decisions = tmp_path / "prior-decisions.jsonl"
    _jsonl(
        prior_decisions,
        [
            {
                "protocol": SEMANTIC_DECISION_PROTOCOL,
                "queue_item_sha256": semantic_item["queue_item_sha256"],
                "source_review_queue_sha256": sha256_file(semantic_queue),
                "decision": "approve",
                "decision_provenance": {
                    "reviewer_type": "human_verified",
                    "reviewer_id": "human-1",
                },
                "approved_entity": "VGT",
            }
        ],
    )
    candidate_payload = {
        "document_uid": "VGT_2024_separate",
        "line_number": 7,
        "raw_text": "Công ty mẹ - Tập đoàn Dệt May Việt Nam",
        "raw_text_sha256": "a" * 64,
        "source_file_sha256": "b" * 64,
    }
    candidate = {**candidate_payload, "candidate_sha256": canonical_sha256(candidate_payload)}
    role_queue_payload = {
        "protocol": "vifinqa_entity_role_provenance_candidate_queue_v1",
        "question_id": 12,
        "candidates": [candidate],
    }
    role_queue_item = {
        **role_queue_payload,
        "queue_item_sha256": canonical_sha256(role_queue_payload),
    }
    role_queue = tmp_path / "role-queue.jsonl"
    _jsonl(role_queue, [role_queue_item])
    role_decision_payload = {
        "protocol": DECISION_PROTOCOL,
        "question_id": 12,
        "claim_entity_identity": "VGT",
        "claim_entity_role": "parent",
        "document_uid": "VGT_2024_separate",
        "queue_item_sha256": role_queue_item["queue_item_sha256"],
        "source_candidate_queue_sha256": sha256_file(role_queue),
        "decision": "approve_role_provenance",
        "selected_source_anchor": candidate,
        "decision_provenance": {
            "reviewer_type": "chatgpt_verified",
            "reviewer_id": "chatgpt-role-1",
        },
    }
    role_decision = {
        **role_decision_payload,
        "decision_sha256": canonical_sha256(role_decision_payload),
    }
    role_decisions = tmp_path / "role-decisions.jsonl"
    _jsonl(role_decisions, [role_decision])
    return {
        "semantic_queue": semantic_queue,
        "prior_semantic_decisions": prior_decisions,
        "provenance_queue": role_queue,
        "provenance_decisions": role_decisions,
    }


def test_augments_only_role_and_preserves_human_cell_provenance(tmp_path: Path) -> None:
    inputs = _fixtures(tmp_path)
    result = augment(**inputs, output_dir=tmp_path / "out")
    assert result["counts"] == {"decision_count": 1, "document_line_role_augmented": 1}
    row = json.loads(Path(result["outputs"]["decisions"]["path"]).read_text())
    assert row["decision_provenance"] == {
        "reviewer_type": "human_verified",
        "reviewer_id": "human-1",
    }
    assert row["approved_entity_role"] == "parent"
    assert row["entity_role_decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert row["entity_role_source_anchor"]["line_number"] == 7


def test_rejects_tampered_role_decision(tmp_path: Path) -> None:
    inputs = _fixtures(tmp_path)
    row = json.loads(inputs["provenance_decisions"].read_text())
    row["claim_entity_identity"] = "OTHER"
    _jsonl(inputs["provenance_decisions"], [row])
    with pytest.raises(EntityRoleProvenanceAugmentationError, match="decision is invalid"):
        augment(**inputs, output_dir=tmp_path / "out")
