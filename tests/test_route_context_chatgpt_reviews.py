from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.route_context_chatgpt_reviews import (
    RouteContextChatGPTReviewError,
    build_decisions,
)
from finance_query.route_context_repairs import canonical_sha256, sha256_file, source_contract


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Path]:
    question = "Chênh lệch năm 2022 so với năm 2018"
    candidate = {
        "value": "year_over_year_growth",
        "literal_text": "so với năm",
        "char_start": 20,
        "char_end": 30,
        "question_sha256": "a" * 64,
    }
    payload = {
        "question_id": 1,
        "question": question,
        "repair_status": "machine_provisional_requires_human",
        "missing_context": ["controlled_operation_contract"],
        "literal_repair_candidates": {"controlled_operation_contract": [candidate]},
        "unresolved_fields": [],
        "source_packet_sha256": "b" * 64,
        "source_operation_queue_item_sha256": "c" * 64,
    }
    row = {
        "schema_version": 1,
        "protocol": "vifinqa_route_context_repair_candidates_v1",
        **payload,
        "repair_item_sha256": canonical_sha256(payload),
        "source_contract": source_contract(),
    }
    queue = tmp_path / "queue.jsonl"
    _write_jsonl(queue, [row])
    manifest = tmp_path / "queue.manifest.json"
    manifest.write_text(json.dumps({
        "protocol": "vifinqa_route_context_repair_candidates_v1",
        "outputs": {"queue": {"sha256": sha256_file(queue)}},
    }), encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "default_candidate_policy": "approve_literal_label_after_question_semantic_review",
        "blocked_item_policy": "confirm_incomplete_literal_blocker_fail_closed",
        "candidate_overrides": [{
            "question_id": 1,
            "field": "controlled_operation_contract",
            "char_start": 20,
            "char_end": 30,
            "value": "year_over_year_growth",
            "decision": "reject_literal_label",
            "reason_codes": ["NOT_GROWTH"],
            "rationale": "Đây là chênh lệch tuyệt đối, không phải tăng trưởng.",
        }],
    }), encoding="utf-8")
    return {"queue": queue, "queue_manifest": manifest, "review_spec": spec}


def test_rejects_regex_collision_without_route_authority(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    result = build_decisions(**inputs, output_dir=tmp_path / "out")
    assert result["counts"] == {
        "decision_count": 1,
        "reject_all_literal_candidates": 1,
        "candidate_decision_counts": {"reject_literal_label": 1},
    }
    row = json.loads(Path(result["outputs"]["decisions"]["path"]).read_text())
    assert row["decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert row["approved_context_repairs"] == {}
    assert row["source_contract"]["may_change_route"] is False
    assert row["source_contract"]["may_execute_formula"] is False


def test_rejects_override_that_does_not_match_frozen_queue(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    spec = json.loads(inputs["review_spec"].read_text())
    spec["candidate_overrides"][0]["char_start"] = 19
    inputs["review_spec"].write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(RouteContextChatGPTReviewError, match="does not match"):
        build_decisions(**inputs, output_dir=tmp_path / "out")
