from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_V1 = ROOT / "artifacts" / "research" / "computational_semantics_v1"
SEMANTIC_V2 = ROOT / "artifacts" / "research" / "computational_semantics_v2"
SPEC = importlib.util.spec_from_file_location(
    "computational_semantic_review_campaign",
    ROOT / "scripts" / "build_computational_semantic_review_campaign_v1.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def kwargs(tmp_path: Path) -> dict[str, Path]:
    return {
        "source_queue": SEMANTIC_V1 / "source_review_v2" / "computational_semantic_source_review_queue_v1.jsonl",
        "source_manifest": SEMANTIC_V1 / "source_review_v2" / "computational_semantic_source_review_queue_v1.manifest.json",
        "scope_queue": SEMANTIC_V1 / "scope_review_v2" / "computational_semantic_scope_review_queue_v1.jsonl",
        "scope_manifest": SEMANTIC_V1 / "scope_review_v2" / "computational_semantic_scope_review_queue_v1.manifest.json",
        "component_queue": SEMANTIC_V2 / "component_source_review_v1" / "computational_semantic_component_source_review_queue_v1.jsonl",
        "component_manifest": SEMANTIC_V2 / "component_source_review_v1" / "computational_semantic_component_source_review_queue_v1.manifest.json",
        "dimension_queue": SEMANTIC_V1 / "dimension_review_v1" / "computational_semantic_dimension_review_queue_v1.jsonl",
        "dimension_manifest": SEMANTIC_V1 / "dimension_review_v1" / "computational_semantic_dimension_review_queue_v1.manifest.json",
        "output_dir": tmp_path / "campaign",
    }


def test_campaign_is_hash_bound_blank_and_prioritized_for_one_reviewer(tmp_path: Path) -> None:
    result = MODULE.build(**kwargs(tmp_path))
    worklist_path = Path(result["outputs"]["worklist"]["path"])
    rows = [json.loads(line) for line in worklist_path.read_text(encoding="utf-8").splitlines()]
    assert result["campaign_status"] == "blank_single_reviewer_worklist"
    assert result["work_item_count"] == 49
    assert result["phase_counts"] == {
        "p1_direct_source_coordinates": 8,
        "p2_scope_resolution": 7,
        "p3_component_source_coordinates": 3,
        "p4_dimension_contracts": 31,
    }
    assert hashlib.sha256(worklist_path.read_bytes()).hexdigest() == result["outputs"]["worklist"]["sha256"]
    assert [row["queue_question_id"] for row in rows[:8]] == [6, 27, 176, 181, 256, 310, 325, 332]
    assert all(row["review_decision"] is None for row in rows)
    assert all(row["materialization_allowed"] is False for row in rows)
    assert all(row["source_contract"] == MODULE.SOURCE_CONTRACT for row in rows)


def test_campaign_rejects_tampered_queue_and_overwrite(tmp_path: Path) -> None:
    args = kwargs(tmp_path)
    tampered = tmp_path / "source.jsonl"
    tampered.write_text(args["source_queue"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    args["source_queue"] = tampered
    with pytest.raises(ValueError, match="queue manifest is invalid"):
        MODULE.build(**args)
    args = kwargs(tmp_path / "overwrite")
    args["output_dir"].mkdir(parents=True)
    (args["output_dir"] / "computational_semantic_single_reviewer_campaign_v1.jsonl").write_text(
        "exists", encoding="utf-8"
    )
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        MODULE.build(**args)
