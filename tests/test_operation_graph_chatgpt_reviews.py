from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.operation_graph_chatgpt_reviews import (
    OperationGraphChatGPTReviewError,
    build_decisions,
)
from finance_query.operation_graphs import canonical_sha256, sha256_file, source_contract


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixtures(tmp_path: Path) -> dict[str, Path]:
    queue_rows = []
    for question_id in (1, 2):
        payload = {
            "question_id": question_id,
            "question": "A trừ B" if question_id == 1 else "Câu hỏi chưa đủ stage",
            "queue_status": "review_required_operation_graph",
            "required_operations": ["subtract_or_difference"],
            "missing_operations": [],
            "missing_context": [],
            "route_packet_status": "complete",
            "route_contract": {"question_id": question_id, "required_operations": [], "stages": []},
            "stage_nodes": [],
            "final_operator_candidates": ["subtract"],
            "source_route_sha256": "a" * 64,
            "source_overlay_row_sha256": "b" * 64,
            "source_packet_sha256": "c" * 64,
        }
        queue_rows.append(
            {
                "schema_version": 1,
                "protocol": "vifinqa_operation_graph_review_queue_v1",
                **payload,
                "queue_item_sha256": canonical_sha256(payload),
                "source_contract": source_contract(),
            }
        )
    queue = tmp_path / "queue.jsonl"
    _jsonl(queue, queue_rows)
    queue_manifest = tmp_path / "queue.manifest.json"
    queue_manifest.write_text(
        json.dumps(
            {
                "protocol": "vifinqa_operation_graph_review_queue_v1",
                "outputs": {"queue": {"sha256": sha256_file(queue)}},
            }
        ),
        encoding="utf-8",
    )

    candidate_rows = []
    for question_id, status, reasons in (
        (1, "typed_candidate_validated_not_authorized", []),
        (2, "blocked_graph_candidate", ["BINARY_OPERATOR_STAGE_ARITY_MISMATCH"]),
    ):
        payload = {
            "question_id": question_id,
            "candidate_status": status,
            "candidate_graph": {"final_node_id": "op:subtract"} if question_id == 1 else None,
            "reason_codes": reasons,
            "source_queue_item_sha256": queue_rows[question_id - 1]["queue_item_sha256"],
            "source_route_sha256": "a" * 64,
        }
        candidate_rows.append(
            {
                "schema_version": 1,
                "protocol": "vifinqa_operation_graph_candidates_v1",
                **payload,
                "candidate_item_sha256": canonical_sha256(payload),
                "source_contract": source_contract(),
            }
        )
    candidates = tmp_path / "candidates.jsonl"
    _jsonl(candidates, candidate_rows)
    candidate_manifest = tmp_path / "candidates.manifest.json"
    candidate_manifest.write_text(
        json.dumps(
            {
                "protocol": "vifinqa_operation_graph_candidates_v1",
                "outputs": {"candidates": {"sha256": sha256_file(candidates)}},
            }
        ),
        encoding="utf-8",
    )
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "blocked_candidate_policy": "confirm_existing_blocker_fail_closed",
                "typed_candidate_reviews": [
                    {
                        "question_id": 1,
                        "decision": "approve_graph_semantics",
                        "reason_codes": [],
                        "rationale": "Phép trừ và thứ tự đúng câu hỏi.",
                        "semantic_checks": {
                            "operator_matches_question": True,
                            "stage_population_complete": True,
                            "noncommutative_order_correct": True,
                            "final_output_type_matches_question": True,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return {
        "review_queue": queue,
        "review_manifest": queue_manifest,
        "candidates": candidates,
        "candidate_manifest": candidate_manifest,
        "review_spec": spec,
    }


def test_reviews_typed_candidate_and_confirms_blocker_without_execution_authority(tmp_path: Path) -> None:
    inputs = _fixtures(tmp_path)
    result = build_decisions(**inputs, output_dir=tmp_path / "out")
    assert result["counts"] == {
        "decision_count": 2,
        "approve_graph_semantics": 1,
        "confirmed_blocker": 1,
    }
    rows = [json.loads(line) for line in Path(result["outputs"]["decisions"]["path"]).read_text().splitlines()]
    assert rows[0]["decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert rows[0]["source_contract"]["graph_review_gate_authorized"] is True
    assert rows[0]["source_contract"]["may_execute_formula"] is False
    assert rows[1]["decision"] == "confirmed_blocker"
    assert rows[1]["reviewed_graph"] is None


def test_rejects_approval_with_failed_semantic_check(tmp_path: Path) -> None:
    inputs = _fixtures(tmp_path)
    spec = json.loads(inputs["review_spec"].read_text())
    spec["typed_candidate_reviews"][0]["semantic_checks"]["stage_population_complete"] = False
    inputs["review_spec"].write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(OperationGraphChatGPTReviewError, match="approval has a failed"):
        build_decisions(**inputs, output_dir=tmp_path / "out")
