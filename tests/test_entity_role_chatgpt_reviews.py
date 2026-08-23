from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.entity_role_chatgpt_reviews import EntityRoleChatGPTReviewError, augment
from finance_query.semantic_approvals import SEMANTIC_DECISION_PROTOCOL, canonical_sha256, sha256_file


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _queue_item(*, requested_role: str | None, source_title: str) -> dict:
    payload = {
        "schema_version": 1,
        "protocol": "vifinqa_semantic_binding_review_queue_v1",
        "question_id": 730,
        "stage_id": "stage-1",
        "role": "gross_profit",
        "candidate_variable_id": "gross_profit",
        "requested_entity": "DTK",
        "requested_entity_role": requested_role,
        "requested_scope": "separate",
        "document_uid": "DTK_2017_separate",
        "internal_table_uid": "table-1",
        "value_cell": {"row_index": 5, "column_index": 3, "raw_text": "1290", "raw_text_sha256": "a" * 64},
        "row_label_candidates": [{"row_index": 5, "column_index": 0, "raw_text": "Lợi nhuận gộp", "raw_text_sha256": "b" * 64}],
        "source_title": source_title,
        "source_provenance": {"source_sha256": "c" * 64},
        "reviewer_action": "approve | reject | uncertain",
        "human_verified": False,
        "eligible_for_authorization": False,
        "source_contract": {"candidate_only": True},
    }
    return {**payload, "queue_item_sha256": canonical_sha256(payload)}


def test_augments_only_explicit_role_literal_and_preserves_human_cell_provenance(tmp_path: Path) -> None:
    title = "CÔNG TY MỆ - TỔNG CÔNG TY ĐIỆN LỰC TKV - BÁO CÁO RIÊNG"
    prior_item = _queue_item(requested_role=None, source_title=title)
    current_item = _queue_item(requested_role="parent", source_title=title)
    prior_queue = tmp_path / "prior-queue.jsonl"
    current_queue = tmp_path / "current-queue.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    _jsonl(prior_queue, [prior_item])
    _jsonl(current_queue, [current_item])
    _jsonl(
        decisions,
        [
            {
                "protocol": SEMANTIC_DECISION_PROTOCOL,
                "queue_item_sha256": prior_item["queue_item_sha256"],
                "source_review_queue_sha256": sha256_file(prior_queue),
                "decision": "approve",
                "decision_provenance": {"reviewer_type": "human_verified", "reviewer_id": "dungle01"},
                "reviewed_at": "2026-08-23T00:00:00Z",
                "notes": "Verified exact row and cell.",
                "source_coordinates_checked": True,
                "approved_variable_id": "gross_profit",
                "approved_entity": "DTK",
                "approved_scope": "separate",
                "selected_row_label": {"row_index": 5, "column_index": 0, "raw_text_sha256": "b" * 64},
            }
        ],
    )
    result = augment(
        prior_queue=prior_queue,
        prior_decisions=decisions,
        current_queue=current_queue,
        output_dir=tmp_path / "augmented",
    )
    assert result["counts"] == {
        "decision_count": 1,
        "chatgpt_role_verified": 1,
    }
    output = json.loads(Path(result["outputs"]["decisions"]["path"]).read_text(encoding="utf-8"))
    assert output["decision_provenance"] == {"reviewer_type": "human_verified", "reviewer_id": "dungle01"}
    assert output["approved_entity_role"] == "parent"
    assert output["entity_role_decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert output["entity_role_source_title_sha256"] == hashlib.sha256(title.encode()).hexdigest()
    assert output["queue_item_sha256"] == current_item["queue_item_sha256"]


def test_rejects_semantic_changes_hidden_inside_queue_migration(tmp_path: Path) -> None:
    prior_item = _queue_item(requested_role=None, source_title="CÔNG TY MẸ DTK")
    current_item = _queue_item(requested_role="parent", source_title="CÔNG TY MẸ DTK")
    current_item["requested_entity"] = "OTHER"
    payload = {key: value for key, value in current_item.items() if key != "queue_item_sha256"}
    current_item["queue_item_sha256"] = canonical_sha256(payload)
    prior_queue = tmp_path / "prior.jsonl"
    current_queue = tmp_path / "current.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    _jsonl(prior_queue, [prior_item])
    _jsonl(current_queue, [current_item])
    _jsonl(
        decisions,
        [{
            "protocol": SEMANTIC_DECISION_PROTOCOL,
            "queue_item_sha256": prior_item["queue_item_sha256"],
            "source_review_queue_sha256": sha256_file(prior_queue),
            "decision": "approve",
            "decision_provenance": {"reviewer_type": "human_verified", "reviewer_id": "human"},
        }],
    )
    with pytest.raises(EntityRoleChatGPTReviewError, match="changed beyond entity-role"):
        augment(
            prior_queue=prior_queue,
            prior_decisions=decisions,
            current_queue=current_queue,
            output_dir=tmp_path / "out",
        )
