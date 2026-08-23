from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts" / "kaggle_runs" / "notebook5554bd790d_v10_20260811" / "vifinqa_review_bundle"
V5 = ROOT / "artifacts" / "research" / "production_coverage_iteration_v5"

spec = importlib.util.spec_from_file_location(
    "production_release_remediation_queue",
    ROOT / "scripts" / "build_production_release_remediation_queue.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _kwargs(tmp_path: Path) -> dict[str, Path]:
    return {
        "release_gate": V5 / "production_release_gate_v1.json",
        "production_audit": BUNDLE / "production_independent_audit_candidate_v1.jsonl",
        "typed_plans": BUNDLE / "typed_operand_plans_v1.jsonl",
        "formula_evidence": BUNDLE / "formula_evidence_sets_typed_v1.jsonl",
        "query_program": BUNDLE / "query_program_ast_shadow_v1.jsonl",
        "output": tmp_path / "remediation.jsonl",
    }


def test_queue_assigns_each_blocked_question_once_without_materializing(tmp_path: Path) -> None:
    rows, manifest = mod.build(**_kwargs(tmp_path))

    assert len(rows) == 957
    assert manifest["queue_status"] == "non_materializable"
    assert manifest["lane_counts"] == {
        "deterministic_executor_compile": 51,
        "exact_source_conflict_adjudication": 40,
        "formula_evidence_completion": 73,
        "formula_evidence_materialization": 204,
        "independent_source_replay": 251,
        "query_program_shadow_completion": 2,
        "typed_plan_source_adjudication": 336,
    }
    assert len({row["question_id"] for row in rows}) == 957
    assert all(row["materialization_allowed"] is False for row in rows)
    assert all(row["source_contract"] == mod.NON_PROMOTABLE_CONTRACT for row in rows)
    assert not any("answer" in row or "selected" in row for row in rows)


def test_queue_rejects_audit_tamper_not_bound_to_release_gate(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    forged = tmp_path / "audit.jsonl"
    forged.write_text(
        kwargs["production_audit"].read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    forged.with_suffix(".manifest.json").write_text(
        json.dumps(json.loads(kwargs["production_audit"].with_suffix(".manifest.json").read_text())),
        encoding="utf-8",
    )
    kwargs["production_audit"] = forged
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        mod.build(**kwargs)


def test_queue_refuses_to_overwrite(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    kwargs["output"].write_text("already here", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        mod.build(**kwargs)
