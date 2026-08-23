from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "artifacts" / "research" / "production_coverage_iteration_v5"
spec = importlib.util.spec_from_file_location(
    "production_release_operations_status",
    ROOT / "scripts" / "build_production_release_operations_status.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _kwargs(output: Path) -> dict[str, Path]:
    return {
        "registry": V5 / "production_release_review_assignment_registry_v1.json",
        "release_gate": V5 / "production_release_gate_v1.json",
        "remediation_queue": V5 / "production_release_remediation_queue_v1.jsonl",
        "remediation_manifest": V5 / "production_release_remediation_queue_v1.manifest.json",
        "output": output,
    }


def test_operations_status_reports_readiness_not_unobserved_completion(tmp_path: Path) -> None:
    result = mod.build(**_kwargs(tmp_path / "status.json"))
    assert result["release_status"] == "blocked_awaiting_external_human_review_evidence"
    assert result["counts"] == {
        "remediation_question_count": 957,
        "ready_reviewer_assignment_count": 1914,
        "completed_human_labels_observed": 0,
        "completed_review_receipts_observed": 0,
        "lane_count": 7,
    }
    assert [lane["question_count"] for lane in result["lanes"]] == [336, 73, 251, 204, 51, 40, 2]
    assert all(lane["materialization_allowed"] is False for lane in result["lanes"])


def test_operations_status_rejects_stale_registry_hash(tmp_path: Path) -> None:
    registry = V5 / "production_release_review_assignment_registry_v1.json"
    original = registry.read_text(encoding="utf-8")
    payload = json.loads(original)
    payload["lanes"][0]["assignment_manifest"]["sha256"] = "0" * 64
    registry.write_text(json.dumps(payload), encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="assignment manifest hash mismatch"):
            mod.build(**_kwargs(tmp_path / "status.json"))
    finally:
        registry.write_text(original, encoding="utf-8")
