from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "build_ratio_formula_candidate_queue",
    Path(__file__).parents[2] / "scripts" / "e2e" / "build_ratio_formula_candidate_queue.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _operand(operand_id: str, metric: str) -> dict:
    return {
        "operand_id": operand_id,
        "metric_hints": [metric],
        "entity": "HPG",
        "years": [2023],
        "unit_contract": {"source_unit_required": True},
        "grounding_contract": {
            "exact_internal_table_uid": True,
            "exact_row_index": True,
            "exact_column_index": True,
            "exact_raw_cell": True,
            "canonical_header_required": True,
        },
    }


def _ratio_plan(question_id: int, *, status: str = "complete") -> dict:
    return {
        "question_id": question_id,
        "decomposition_status": status,
        "effective_family": "ratio_or_derived",
        "route": "controlled_formula_template",
        "formula_id": "explicit_stated_fraction",
        "operation_ast": {"op": "divide", "args": ["numerator", "denominator"]},
        "operands": [_operand("numerator", "tiền"), _operand("denominator", "tổng tài sản")],
        "plan_fingerprint": f"formula-plan-{question_id}",
    }


def _blocker(question_id: int) -> dict:
    return {
        "question_id": question_id,
        "question": f"Q{question_id}",
        "first_blocker": "route_definition_incomplete",
        "route_status": "route_incomplete",
        "remediation_track": "controlled_ratio_template",
        "missing_operations": ["ratio_or_percent"],
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    blocker_dir = tmp_path / "blockers"
    blocker_dir.mkdir()
    operation_path = blocker_dir / "operation_graph_queue.jsonl"
    route_path = blocker_dir / "route_definition_queue.jsonl"
    _write_rows(operation_path, [])
    _write_rows(route_path, [_blocker(1), _blocker(2), _blocker(3)])
    blocker_manifest = blocker_dir / "replay_blocker_queues.manifest.json"
    blocker_manifest.write_text(
        json.dumps(
            {
                "protocol": module.BLOCKER_PROTOCOL,
                "outputs": {
                    "operation_graph": {"sha256": module.sha256_file(operation_path)},
                    "route_definition": {"sha256": module.sha256_file(route_path)},
                },
            }
        )
    )
    plans = tmp_path / "typed_operand_plans.jsonl"
    _write_rows(
        plans,
        [
            _ratio_plan(1),
            {
                **_ratio_plan(2),
                "effective_family": "direct_lookup",
                "route": "existing_typed_plan",
                "formula_id": None,
                "operation_ast": {"op": "lookup", "args": ["numerator"]},
            },
            _ratio_plan(3, status="abstain"),
        ],
    )
    plans_manifest = tmp_path / "typed_operand_plans.manifest.json"
    plans_manifest.write_text(
        json.dumps(
            {
                "protocol": module.PLAN_PROTOCOL,
                "sidecar_sha256": module.sha256_file(plans),
            }
        )
    )
    return blocker_manifest, plans, plans_manifest


def test_ratio_formula_queue_admits_only_complete_two_operand_divide_plan(tmp_path: Path) -> None:
    blocker_manifest, plans, plans_manifest = _inputs(tmp_path)
    result = module.build_ratio_formula_candidate_queue(
        blocker_manifest=blocker_manifest,
        typed_operand_plans=plans,
        typed_operand_plans_manifest=plans_manifest,
        output_dir=tmp_path / "out",
    )
    assert result["status"] == "candidates_materialized_no_formula_or_answer_executed"
    assert result["counts"]["controlled_ratio_track_count"] == 3
    assert result["counts"]["candidate_count"] == 1
    assert result["counts"]["excluded_count"] == 2
    assert result["counts"]["exclusion_reason_counts"]["plan_not_ratio_or_derived"] == 1
    assert result["counts"]["exclusion_reason_counts"]["plan_not_complete"] == 1
    candidate = json.loads(Path(result["outputs"]["candidates"]["path"]).read_text().strip())
    assert candidate["formula_id"] == "explicit_stated_fraction"
    assert candidate["planned_operation_ast"]["op"] == "divide"
    assert len(candidate["planned_operands"]) == 2
    assert candidate["source_contract"]["may_execute_formula"] is False
    assert candidate["source_contract"]["may_materialize_answer"] is False


def test_ratio_formula_queue_rejects_tampered_blocker_input(tmp_path: Path) -> None:
    blocker_manifest, plans, plans_manifest = _inputs(tmp_path)
    route_path = blocker_manifest.parent / "route_definition_queue.jsonl"
    route_path.write_text(route_path.read_text() + "\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch for blocker queue output route_definition"):
        module.build_ratio_formula_candidate_queue(
            blocker_manifest=blocker_manifest,
            typed_operand_plans=plans,
            typed_operand_plans_manifest=plans_manifest,
            output_dir=tmp_path / "out",
        )
