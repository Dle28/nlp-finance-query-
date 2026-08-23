from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts" / "kaggle_runs" / "notebook5554bd790d_v10_20260811" / "vifinqa_review_bundle"
V5 = ROOT / "artifacts" / "research" / "production_coverage_iteration_v5"

spec = importlib.util.spec_from_file_location(
    "typed_plan_abstain_review_queue",
    ROOT / "scripts" / "build_typed_plan_abstain_review_queue.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _kwargs(tmp_path: Path) -> dict[str, Path]:
    return {
        "bundle_dir": BUNDLE,
        "typed_plans": BUNDLE / "typed_operand_plans_v1.jsonl",
        "release_gate": V5 / "production_release_gate_v1.json",
        "remediation_queue": V5 / "production_release_remediation_queue_v1.jsonl",
        "remediation_manifest": V5 / "production_release_remediation_queue_v1.manifest.json",
        "output_dir": tmp_path / "intake",
    }


def test_typed_plan_abstain_intake_is_full_hash_bound_and_blank(tmp_path: Path) -> None:
    result = mod.build(**_kwargs(tmp_path))
    queue = Path(result["outputs"]["queue"]["path"])
    rows = [json.loads(line) for line in queue.read_text().splitlines()]

    assert result["queue_status"] == "blank_source_review_intake"
    assert result["question_count"] == 336
    assert result["pattern_count"] == 21
    assert result["family_counts"] == {
        "conditional_analytical": 66,
        "cross_entity_comparison": 40,
        "direct_lookup": 12,
        "multi_entity_or_period_aggregation": 174,
        "ratio_or_derived": 35,
        "temporal_change": 9,
    }
    assert len({row["question_id"] for row in rows}) == 336
    assert all(row["review_decision_contract"]["decision"] is None for row in rows)
    assert all(row["materialization_allowed"] is False for row in rows)
    assert all(row["source_contract"] == mod.SOURCE_CONTRACT for row in rows)
    assert not any("answer" in row or "proposed_typed_plan" in row for row in rows)


def test_typed_plan_abstain_intake_rejects_tampered_remediation_queue(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    tampered = tmp_path / "remediation.jsonl"
    tampered.write_text(kwargs["remediation_queue"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    kwargs["remediation_queue"] = tampered
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        mod.build(**kwargs)


def test_typed_plan_abstain_intake_refuses_to_overwrite(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    kwargs["output_dir"].mkdir()
    (kwargs["output_dir"] / "typed_plan_abstain_review_queue_v1.jsonl").write_text("exists", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        mod.build(**kwargs)
