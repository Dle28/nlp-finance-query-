from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.e2e.operation_graph_review import build_operation_graph_queue, sha256_file


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    overlay = tmp_path / "overlay.jsonl"
    packets = tmp_path / "packets.jsonl"
    _write_rows(
        overlay,
        [
            {
                "question_id": 1,
                "question": "Chênh lệch doanh thu là bao nhiêu?",
                "route_status": "composed_execution_required",
                "required_operations": ["reported_value", "subtract_or_difference", "stage_output_dependency"],
                "missing_operations": ["subtract_or_difference", "stage_output_dependency"],
            },
            {
                "question_id": 2,
                "question": "Bình quân chi phí là bao nhiêu?",
                "route_status": "composed_execution_required",
                "required_operations": ["reported_value", "average_or_median", "stage_output_dependency"],
                "missing_operations": ["average_or_median", "stage_output_dependency"],
            },
            {
                "question_id": 3,
                "question": "Một giá trị được công bố?",
                "route_status": "route_incomplete",
                "required_operations": ["reported_value"],
                "missing_operations": ["reported_value"],
            },
        ],
    )
    _write_rows(
        packets,
        [
            {
                "question_id": 1,
                "question": "Chênh lệch doanh thu là bao nhiêu?",
                "packet_status": "bounded",
                "stages": [{"stage_id": "left"}, {"stage_id": "right"}],
            },
            {
                "question_id": 2,
                "question": "Bình quân chi phí là bao nhiêu?",
                "packet_status": "route_blocked",
                "stages": [],
                "feedback": {"missing_contract": ["literal_metric_or_concept_candidate"]},
            },
            {
                "question_id": 3,
                "question": "Một giá trị được công bố?",
                "packet_status": "route_blocked",
                "stages": [],
            },
        ],
    )
    overlay_manifest = tmp_path / "overlay.manifest.json"
    overlay_manifest.write_text(
        json.dumps({"outputs": {"overlay": {"sha256": sha256_file(overlay)}}})
    )
    packets_manifest = tmp_path / "packets.manifest.json"
    packets_manifest.write_text(
        json.dumps({"outputs": {"packets": {"sha256": sha256_file(packets)}}})
    )
    return overlay, overlay_manifest, packets, packets_manifest


def test_operation_graph_review_queue_keeps_missing_context_blocked(tmp_path: Path) -> None:
    overlay, overlay_manifest, packets, packets_manifest = _inputs(tmp_path)
    result = build_operation_graph_queue(
        route_overlay=overlay,
        route_overlay_manifest=overlay_manifest,
        route_packets=packets,
        route_packets_manifest=packets_manifest,
        output_dir=tmp_path / "out",
    )
    assert result["status"] == "operation_graph_queue_built_not_materialized"
    assert result["counts"] == {
        "queue_item_count": 2,
        "human_decision_count": 0,
        "queue_status_counts": {
            "blocked_missing_context": 1,
            "review_required_operation_graph": 1,
        },
    }
    queue = [
        json.loads(line)
        for line in Path(result["outputs"]["queue"]["path"]).read_text().splitlines()
    ]
    assert queue[0]["queue_status"] == "review_required_operation_graph"
    assert queue[0]["final_operator_candidates"] == ["subtract"]
    assert queue[0]["source_contract"]["may_execute_formula"] is False
    assert queue[1]["queue_status"] == "blocked_missing_context"
    decisions = [
        json.loads(line)
        for line in Path(result["outputs"]["blank_decisions"]["path"]).read_text().splitlines()
    ]
    assert all(decision["decision"] is None and decision["is_blank_template"] for decision in decisions)


def test_operation_graph_review_queue_rejects_tampered_overlay(tmp_path: Path) -> None:
    overlay, overlay_manifest, packets, packets_manifest = _inputs(tmp_path)
    overlay.write_text(overlay.read_text() + "\n")
    with pytest.raises(ValueError, match="route overlay SHA-256 mismatch"):
        build_operation_graph_queue(
            route_overlay=overlay,
            route_overlay_manifest=overlay_manifest,
            route_packets=packets,
            route_packets_manifest=packets_manifest,
            output_dir=tmp_path / "out",
        )
