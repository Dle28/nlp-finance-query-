from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "artifacts" / "research" / "production_coverage_iteration_v5"
spec = importlib.util.spec_from_file_location(
    "production_release_review_assignment_registry",
    ROOT / "scripts" / "build_production_release_review_assignment_registry.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def test_assignment_registry_covers_all_remediation_lanes_without_materializing(tmp_path: Path) -> None:
    result = mod.build(root=V5, output=tmp_path / "registry.json")
    assert result["question_count"] == 957
    assert result["assignment_status"] == "blank_independent_human_review"
    assert result["labels_prepopulated"] is False
    assert result["materialization_allowed"] is False
    assert [entry["question_count"] for entry in result["lanes"]] == [336, 73, 251, 204, 51, 40, 2]
    assert all(set(entry["reviewer_assignments"]) == {"reviewer_a", "reviewer_b"} for entry in result["lanes"])


def test_assignment_registry_refuses_stale_assignment_hash(tmp_path: Path) -> None:
    assignment_manifest = V5 / "typed_plan_abstain_review_assignments_v1" / "production_release_intake_independent_review_assignments_v1.manifest.json"
    original = assignment_manifest.read_text(encoding="utf-8")
    payload = json.loads(original)
    payload["outputs"]["reviewer_a"]["assignment"]["sha256"] = "0" * 64
    assignment_manifest.write_text(json.dumps(payload), encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            mod.build(root=V5, output=tmp_path / "registry.json")
    finally:
        assignment_manifest.write_text(original, encoding="utf-8")
