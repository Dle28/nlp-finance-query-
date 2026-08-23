from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.operation_graphs import QUEUE_PROTOCOL, canonical_sha256, sha256_file, source_contract
from finance_query.route_context_chatgpt_reviews import PROTOCOL as DECISION_PROTOCOL
from finance_query.route_context_promotions import RouteContextPromotionError, build_promotion


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Path]:
    queue_rows = []
    for question_id, missing, status in (
        (1, ["controlled_operation_contract"], "blocked_missing_context"),
        (2, ["scope"], "blocked_missing_context"),
        (3, [], "review_required_operation_graph"),
    ):
        payload = {
            "question_id": question_id,
            "question": "Câu hỏi",
            "queue_status": status,
            "required_operations": ["reported_value"],
            "missing_operations": [],
            "missing_context": missing,
            "route_packet_status": "route_blocked" if missing else "bounded",
            "route_contract": {"question_id": question_id, "required_operations": [], "stages": [{"stage_id": "s1"}]},
            "stage_nodes": [{"node_id": "stage:s1", "op": "stage_ref", "stage_id": "s1", "inputs": []}],
            "final_operator_candidates": [],
            "source_route_sha256": "a" * 64,
            "source_overlay_row_sha256": "b" * 64,
            "source_packet_sha256": "c" * 64,
        }
        queue_rows.append({
            "schema_version": 1,
            "protocol": QUEUE_PROTOCOL,
            **payload,
            "queue_item_sha256": canonical_sha256(payload),
            "source_contract": source_contract(),
        })
    queue = tmp_path / "queue.jsonl"
    _jsonl(queue, queue_rows)
    queue_manifest = tmp_path / "queue.manifest.json"
    queue_manifest.write_text(json.dumps({
        "protocol": QUEUE_PROTOCOL,
        "outputs": {"queue": {"sha256": sha256_file(queue)}},
    }), encoding="utf-8")

    decisions_rows = []
    for question_id, field in ((1, "controlled_operation_contract"), (2, "scope")):
        payload = {
            "schema_version": 1,
            "protocol": DECISION_PROTOCOL,
            "question_id": question_id,
            "decision": "approve_all_literal_candidates",
            "approved_context_repairs": {field: [{"value": "x"}]},
            "decision_provenance": {
                "reviewer_type": "chatgpt_verified",
                "authority_grant": {"grant_scope": "route_context_literal_review_gate_equivalence"},
            },
            "source_contract": {
                "literal_candidate_review_completed": True,
                "may_change_question_plan": False,
                "may_change_route": False,
                "may_select_value": False,
                "may_execute_formula": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        decisions_rows.append({**payload, "decision_sha256": canonical_sha256(payload)})
    decisions = tmp_path / "decisions.jsonl"
    _jsonl(decisions, decisions_rows)
    decision_manifest = tmp_path / "decisions.manifest.json"
    decision_manifest.write_text(json.dumps({
        "protocol": DECISION_PROTOCOL,
        "outputs": {"decisions": {"sha256": sha256_file(decisions)}},
        "source_contract": {"chatgpt_authority_grant_present": True},
    }), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "promotion_policy": "complete_literal_repairs_to_graph_review_only",
        "allowed_fields": ["controlled_operation_contract"],
        "authority_grant": {
            "granted_by": "campaign_owner",
            "grant_basis": "explicit_user_instruction",
            "grant_scope": "route_context_literal_review_gate_equivalence",
        },
        "may_change_question_plan": False,
        "may_change_route": False,
        "may_select_value": False,
        "may_execute_formula": False,
        "promotion_allowed": False,
        "release_authorized": False,
    }), encoding="utf-8")
    return {
        "operation_queue": queue,
        "operation_manifest": queue_manifest,
        "decisions": decisions,
        "decision_manifest": decision_manifest,
        "promotion_config": config,
    }


def test_promotes_only_operation_contract_and_preserves_other_rows(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    result = build_promotion(**inputs, output_dir=tmp_path / "out")
    assert result["counts"]["promoted_question_count"] == 1
    rows = [json.loads(line) for line in Path(result["outputs"]["queue"]["path"]).read_text().splitlines()]
    assert rows[0]["queue_status"] == "review_required_operation_graph"
    assert rows[0]["missing_context"] == []
    assert rows[1]["queue_status"] == "blocked_missing_context"
    assert rows[1]["missing_context"] == ["scope"]
    assert rows[2]["queue_item_sha256"] == json.loads(inputs["operation_queue"].read_text().splitlines()[2])["queue_item_sha256"]
    assert result["source_contract"]["may_execute_formula"] is False


def test_rejects_authority_escalation(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    config = json.loads(inputs["promotion_config"].read_text())
    config["may_execute_formula"] = True
    inputs["promotion_config"].write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(RouteContextPromotionError, match="expand authority"):
        build_promotion(**inputs, output_dir=tmp_path / "out")
