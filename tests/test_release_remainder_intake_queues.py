from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts" / "kaggle_runs" / "notebook5554bd790d_v10_20260811" / "vifinqa_review_bundle"
V5 = ROOT / "artifacts" / "research" / "production_coverage_iteration_v5"

spec = importlib.util.spec_from_file_location(
    "release_remainder_intake_queues",
    ROOT / "scripts" / "build_release_remainder_intake_queues.py",
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


def test_remainder_intakes_cover_each_nonoverlapping_lane_blindly(tmp_path: Path) -> None:
    result = mod.build(**_kwargs(tmp_path))

    assert result["queue_status"] == "blank_source_review_intakes"
    assert result["lane_counts"] == {
        "deterministic_executor_compile": 51,
        "exact_source_conflict_adjudication": 40,
        "query_program_shadow_completion": 2,
    }
    assert result["lane_family_counts"] == {
        "deterministic_executor_compile": {
            "conditional_analytical": 44,
            "temporal_change": 7,
        },
        "exact_source_conflict_adjudication": {"direct_lookup": 40},
        "query_program_shadow_completion": {"ratio_or_derived": 2},
    }
    all_ids: set[int] = set()
    for lane, artifact in result["outputs"].items():
        rows = [json.loads(line) for line in Path(artifact["path"]).read_text().splitlines()]
        assert len(rows) == result["lane_counts"][lane]
        ids = {row["question_id"] for row in rows}
        assert not ids.intersection(all_ids)
        all_ids.update(ids)
        assert all(row["review_decision_contract"]["decision"] is None for row in rows)
        assert all(row["materialization_allowed"] is False for row in rows)
        assert all(row["source_contract"] == mod.SOURCE_CONTRACT for row in rows)
        serialized = "\n".join(json.dumps(row, sort_keys=True) for row in rows)
        for forbidden in ('"internal_table_uid":', '"raw_value":', '"parsed_value":', '"column_index":', '"row_index":', '"answer"'):
            assert forbidden not in serialized
    assert len(all_ids) == 93


def test_remainder_intakes_reject_tampered_remediation_queue(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    tampered = tmp_path / "queue.jsonl"
    tampered.write_text(kwargs["remediation_queue"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    kwargs["remediation_queue"] = tampered
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        mod.build(**kwargs)


def test_remainder_intakes_refuse_to_overwrite(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    kwargs["output_dir"].mkdir()
    (kwargs["output_dir"] / "production_release_remainder_intakes_v1.manifest.json").write_text("exists", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        mod.build(**kwargs)
