from __future__ import annotations

import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "build_replay_blocker_queues",
    Path(__file__).parents[2] / "scripts" / "e2e" / "build_replay_blocker_queues.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def _artifact(tmp_path: Path, name: str, rows: list[dict], output_name: str) -> tuple[Path, Path]:
    path = tmp_path / f"{name}.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest = tmp_path / f"{name}.manifest.json"
    manifest.write_text(json.dumps({"outputs": {output_name: {"sha256": module.sha256_file(path)}}}))
    return path, manifest


def test_build_replay_blocker_queues_preserves_distinct_first_blockers(tmp_path: Path) -> None:
    execution, execution_manifest = _artifact(tmp_path, "execution", [
        {"question_id": 1, "execution_status": "route_incomplete"},
        {"question_id": 2, "execution_status": "route_incomplete"},
        {"question_id": 3, "execution_status": "binding_conflict"},
        {"question_id": 4, "execution_status": "execution_replay_ready"},
    ], "execution")
    bindings, bindings_manifest = _artifact(tmp_path, "bindings", [
        {"question_id": 1, "binding_packet_status": "route_incomplete", "stages": []},
        {"question_id": 2, "binding_packet_status": "binding_blocked", "stages": []},
        {"question_id": 3, "binding_packet_status": "binding_blocked", "stages": []},
        {"question_id": 4, "binding_packet_status": "binding_ready", "stages": []},
    ], "bindings")
    routes, routes_manifest = _artifact(tmp_path, "routes", [
        {"question_id": 1, "route_status": "composed_execution_required", "missing_operations": ["reported_value"], "reason_codes": ["WHOLE_QUESTION_OPERATION_UNCOVERED"]},
        {"question_id": 2, "route_status": "route_incomplete", "missing_operations": ["ratio_or_percent"], "reason_codes": ["WHOLE_QUESTION_OPERATION_UNCOVERED"]},
        {"question_id": 3, "route_status": "route_complete", "reason_codes": []},
        {"question_id": 4, "route_status": "route_complete", "reason_codes": []},
    ], "overlay")
    periods, periods_manifest = _artifact(tmp_path, "periods", [
        {"question_id": 1, "packet_status": "packet_blocked"},
        {"question_id": 2, "packet_status": "ambiguous_period_columns"},
        {"question_id": 3, "packet_status": "ambiguous_period_columns"},
        {"question_id": 4, "packet_status": "complete"},
    ], "period_packets")
    result = module.build_replay_blocker_queues(
        execution=execution, execution_manifest=execution_manifest,
        bindings=bindings, bindings_manifest=bindings_manifest,
        route_overlay=routes, route_overlay_manifest=routes_manifest,
        period_packets=periods, period_manifest=periods_manifest,
        output_dir=tmp_path / "out",
    )
    assert result["counts"]["first_blocker_counts"] == {
        "exact_binding_conflict": 1,
        "operation_graph_required": 1,
        "ready_for_semantic_review": 1,
        "route_definition_incomplete": 1,
    }
    queue = Path(result["outputs"]["operation_graph"]["path"])
    assert json.loads(queue.read_text().splitlines()[0])["remediation_track"] == "controlled_direct_lookup_template"
    assert result["diagnosis"]["automatic_gate_lowering_allowed"] is False


def test_build_replay_blocker_queues_rejects_mismatched_question_sets(tmp_path: Path) -> None:
    execution, execution_manifest = _artifact(tmp_path, "execution", [{"question_id": 1}], "execution")
    bindings, bindings_manifest = _artifact(tmp_path, "bindings", [{"question_id": 2}], "bindings")
    routes, routes_manifest = _artifact(tmp_path, "routes", [{"question_id": 1}], "overlay")
    periods, periods_manifest = _artifact(tmp_path, "periods", [{"question_id": 1}], "period_packets")
    try:
        module.build_replay_blocker_queues(
            execution=execution, execution_manifest=execution_manifest,
            bindings=bindings, bindings_manifest=bindings_manifest,
            route_overlay=routes, route_overlay_manifest=routes_manifest,
            period_packets=periods, period_manifest=periods_manifest,
            output_dir=tmp_path / "out",
        )
    except ValueError as exc:
        assert "same question IDs" in str(exc)
    else:
        raise AssertionError("mismatched inputs must fail closed")
