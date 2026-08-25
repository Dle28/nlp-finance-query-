from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "build_direct_lookup_candidate_queue",
    Path(__file__).parents[2] / "scripts" / "e2e" / "build_direct_lookup_candidate_queue.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _plain_lookup_plan(question_id: int, *, status: str = "complete") -> dict:
    return {
        "question_id": question_id,
        "decomposition_status": status,
        "effective_family": "direct_lookup",
        "operation_ast": {"op": "lookup", "args": ["x0"]},
        "operands": [
            {
                "operand_id": "x0",
                "metric_hints": ["doanh thu thuần"],
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
        ],
        "plan_fingerprint": f"plan-{question_id}",
    }


def _blocker(question_id: int, *, first_blocker: str = "operation_graph_required") -> dict:
    return {
        "question_id": question_id,
        "question": f"Q{question_id}",
        "first_blocker": first_blocker,
        "route_status": "composed_execution_required",
        "remediation_track": "controlled_direct_lookup_template",
        "missing_operations": ["reported_value"],
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    blocker_dir = tmp_path / "blockers"
    blocker_dir.mkdir()
    operation_path = blocker_dir / "operation_graph_queue.jsonl"
    route_path = blocker_dir / "route_definition_queue.jsonl"
    _write_rows(operation_path, [_blocker(1), _blocker(2)])
    _write_rows(route_path, [_blocker(3, first_blocker="route_definition_incomplete")])
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
            _plain_lookup_plan(1),
            {
                **_plain_lookup_plan(2),
                "effective_family": "ratio_or_derived",
                "operation_ast": {"op": "divide", "args": ["x0", "x1"]},
            },
            _plain_lookup_plan(3, status="abstain"),
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


def test_build_direct_lookup_candidate_queue_selects_only_plain_complete_lookup(tmp_path: Path) -> None:
    blocker_manifest, plans, plans_manifest = _inputs(tmp_path)
    result = module.build_direct_lookup_candidate_queue(
        blocker_manifest=blocker_manifest,
        typed_operand_plans=plans,
        typed_operand_plans_manifest=plans_manifest,
        output_dir=tmp_path / "out",
    )
    assert result["status"] == "candidates_materialized_no_route_or_answer_promoted"
    assert result["counts"] == {
        "direct_lookup_track_count": 3,
        "candidate_count": 1,
        "excluded_count": 2,
        "candidate_first_blocker_counts": {"operation_graph_required": 1},
        "exclusion_reason_counts": {
            "plan_not_complete": 1,
            "plan_not_direct_lookup": 1,
            "plan_not_lookup_ast": 1,
        },
    }
    candidate_path = Path(result["outputs"]["candidates"]["path"])
    candidate = json.loads(candidate_path.read_text().strip())
    assert candidate["question_id"] == 1
    assert candidate["planned_operation_ast"] == {"op": "lookup", "args": ["x0"]}
    assert candidate["source_contract"]["may_materialize_answer"] is False
    assert candidate["source_contract"]["promotion_allowed"] is False
    assert candidate["required_rebuilds"][-1] == "deterministic_replay"


def test_build_direct_lookup_candidate_queue_rejects_tampered_plan_sidecar(tmp_path: Path) -> None:
    blocker_manifest, plans, plans_manifest = _inputs(tmp_path)
    plans.write_text(plans.read_text() + "\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch for typed operand plans"):
        module.build_direct_lookup_candidate_queue(
            blocker_manifest=blocker_manifest,
            typed_operand_plans=plans,
            typed_operand_plans_manifest=plans_manifest,
            output_dir=tmp_path / "out",
        )
