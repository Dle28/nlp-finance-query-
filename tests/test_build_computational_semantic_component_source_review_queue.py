from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts" / "kaggle_runs" / "notebook5554bd790d_v10_20260811" / "vifinqa_review_bundle"
SEMANTIC = ROOT / "artifacts" / "research" / "computational_semantics_v2"
SPEC = importlib.util.spec_from_file_location(
    "computational_semantic_component_source_review_queue",
    ROOT / "scripts" / "build_computational_semantic_component_source_review_queue_v1.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def kwargs(tmp_path: Path) -> dict[str, Path]:
    return {
        "questions": BUNDLE / "review_items.jsonl",
        "semantic_plans": SEMANTIC / "computational_semantic_plans_v2.jsonl",
        "semantic_manifest": SEMANTIC / "computational_semantic_plans_v2.manifest.json",
        "table_catalog": BUNDLE / "table_routing_catalog_v1.jsonl",
        "table_catalog_manifest": BUNDLE / "table_routing_catalog_v1.manifest.json",
        "output_dir": tmp_path / "component-source-review",
    }


def test_component_source_queue_is_blank_hash_bound_and_keeps_composition_unresolved(tmp_path: Path) -> None:
    result = MODULE.build(**kwargs(tmp_path))
    queue_path = Path(result["outputs"]["queue"]["path"])
    rows = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines()]
    assert result["queue_status"] == "blank_component_source_coordinate_review"
    assert result["question_ids"] == [422, 426, 750]
    assert hashlib.sha256(queue_path.read_bytes()).hexdigest() == result["outputs"]["queue"]["sha256"]
    assert all(row["review_decision_contract"]["decision"] is None for row in rows)
    assert all(row["review_decision_contract"]["composition_accepted"] is False for row in rows)
    assert all(row["materialization_allowed"] is False for row in rows)
    assert all(
        candidate["candidate_tables"]
        for row in rows
        for candidate in row["review_context"]["source_candidates"]
    )
    assert all(
        row["review_context"]["semantic_plan"]["semantic_intent"] == "known_multiple_components_only"
        for row in rows
    )


def test_component_source_queue_rejects_tampered_plans_and_overwrite(tmp_path: Path) -> None:
    args = kwargs(tmp_path)
    tampered = tmp_path / "semantic.jsonl"
    tampered.write_text(args["semantic_plans"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    args["semantic_plans"] = tampered
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        MODULE.build(**args)
    args = kwargs(tmp_path / "overwrite")
    args["output_dir"].mkdir(parents=True)
    (args["output_dir"] / "computational_semantic_component_source_review_queue_v1.jsonl").write_text(
        "exists", encoding="utf-8"
    )
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        MODULE.build(**args)
