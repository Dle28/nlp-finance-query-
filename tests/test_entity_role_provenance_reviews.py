from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.entity_role_provenance_reviews import (
    DECISION_PROTOCOL,
    EntityRoleProvenanceReviewError,
    build_decisions,
)
from finance_query.semantic_approvals import canonical_sha256, sha256_file


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _candidate(line_number: int = 7) -> dict:
    payload = {
        "document_uid": "ABC_2024_separate",
        "source_file_sha256": "a" * 64,
        "source_path": "source.txt",
        "line_number": line_number,
        "raw_text": "Công ty là công ty mẹ có các công ty con.",
        "raw_text_sha256": "b" * 64,
        "context_before": "before",
        "context_after": "after",
    }
    return {**payload, "candidate_sha256": canonical_sha256(payload)}


def _queue(question_id: int = 1) -> dict:
    payload = {
        "schema_version": 1,
        "protocol": "vifinqa_entity_role_provenance_candidate_queue_v1",
        "question_id": question_id,
        "claim_entity_identity": "ABC",
        "claim_entity_role": "parent",
        "document_uid": "ABC_2024_separate",
        "source_file_sha256": "a" * 64,
        "issue_brief_sha256": "c" * 64,
        "candidates": [_candidate()],
        "review_prompts": [],
        "decision_options": [],
        "source_contract": {"research_only": True},
    }
    return {**payload, "queue_item_sha256": canonical_sha256(payload)}


def test_builds_complete_authorized_chatgpt_decision(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.jsonl"
    _jsonl(queue_path, [_queue()])
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "reviews": [
                    {
                        "question_id": 1,
                        "decision": "approve_role_provenance",
                        "selected_line_number": 7,
                        "reason_code": "DIRECT_ASSERTION",
                        "rationale": "Issuer is explicitly the parent.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = build_decisions(
        candidate_queue=queue_path,
        review_spec=spec_path,
        output_dir=tmp_path / "out",
    )

    assert result["counts"] == {"decision_count": 1, "approved_role_provenance": 1}
    row = json.loads(Path(result["outputs"]["decisions"]["path"]).read_text())
    assert row["protocol"] == DECISION_PROTOCOL
    assert row["source_candidate_queue_sha256"] == sha256_file(queue_path)
    assert row["selected_source_anchor"]["candidate_sha256"] == _candidate()["candidate_sha256"]
    assert row["decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert row["decision_provenance"]["authority_grant"]["grant_basis"] == "explicit_user_instruction"


def test_rejects_incomplete_review_coverage(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.jsonl"
    _jsonl(queue_path, [_queue(1), _queue(2)])
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "reviews": [
                    {
                        "question_id": 1,
                        "decision": "reject_candidate_set",
                        "reason_code": "WRONG_ENTITY",
                        "rationale": "Literal refers to another entity.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EntityRoleProvenanceReviewError, match="coverage mismatch"):
        build_decisions(
            candidate_queue=queue_path,
            review_spec=spec_path,
            output_dir=tmp_path / "out",
        )
