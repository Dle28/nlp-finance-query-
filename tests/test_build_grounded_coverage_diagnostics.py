from __future__ import annotations

import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "build_grounded_coverage_diagnostics",
    Path(__file__).parents[1] / "scripts" / "build_grounded_coverage_diagnostics.py",
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


def test_build_diagnostics_freezes_route_and_binding_queues(tmp_path: Path) -> None:
    execution, execution_manifest = _artifact(tmp_path, "execution", [
        {"question_id": 1, "execution_status": "route_incomplete"},
        {"question_id": 2, "execution_status": "binding_conflict"},
    ], "execution")
    bindings, bindings_manifest = _artifact(tmp_path, "bindings", [
        {"question_id": 1, "binding_packet_status": "route_incomplete", "stages": []},
        {"question_id": 2, "binding_packet_status": "binding_blocked", "stages": []},
    ], "bindings")
    routes, routes_manifest = _artifact(tmp_path, "routes", [
        {"question_id": 1, "route_status": "route_incomplete", "reason_codes": ["WHOLE_QUESTION_OPERATION_UNCOVERED"]},
        {"question_id": 2, "route_status": "route_complete", "reason_codes": []},
    ], "overlay")
    periods, periods_manifest = _artifact(tmp_path, "periods", [
        {"question_id": 1, "packet_status": "packet_blocked"},
        {"question_id": 2, "packet_status": "ambiguous_period_columns"},
    ], "period_packets")
    result = module.build_diagnostics(
        execution=execution, execution_manifest=execution_manifest,
        bindings=bindings, bindings_manifest=bindings_manifest,
        route_overlay=routes, route_overlay_manifest=routes_manifest,
        period_packets=periods, period_manifest=periods_manifest,
        output_dir=tmp_path / "out",
    )
    assert result["counts"]["route_remediation_count"] == 1
    assert result["counts"]["binding_conflict_remediation_count"] == 1
    assert result["diagnosis"]["automatic_gate_lowering_allowed"] is False


def test_build_diagnostics_rejects_mismatched_question_sets(tmp_path: Path) -> None:
    execution, execution_manifest = _artifact(tmp_path, "execution", [{"question_id": 1}], "execution")
    bindings, bindings_manifest = _artifact(tmp_path, "bindings", [{"question_id": 2}], "bindings")
    routes, routes_manifest = _artifact(tmp_path, "routes", [{"question_id": 1}], "overlay")
    periods, periods_manifest = _artifact(tmp_path, "periods", [{"question_id": 1}], "period_packets")
    try:
        module.build_diagnostics(
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
