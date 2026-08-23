from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts" / "kaggle_runs" / "notebook5554bd790d_v10_20260811" / "vifinqa_review_bundle"
V5 = ROOT / "artifacts" / "research" / "production_coverage_iteration_v5"

spec = importlib.util.spec_from_file_location(
    "independent_source_replay_intake_queue",
    ROOT / "scripts" / "build_independent_source_replay_intake_queue.py",
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


def test_source_replay_intake_is_full_blind_and_hash_bound(tmp_path: Path) -> None:
    result = mod.build(**_kwargs(tmp_path))
    queue = Path(result["outputs"]["queue"]["path"])
    rows = [json.loads(line) for line in queue.read_text().splitlines()]

    assert result["queue_status"] == "blind_source_review_intake"
    assert result["question_count"] == 251
    assert result["family_counts"] == {"direct_lookup": 251}
    assert len({row["question_id"] for row in rows}) == 251
    assert all(row["review_decision_contract"]["decision"] is None for row in rows)
    assert all(row["materialization_allowed"] is False for row in rows)
    assert all(row["source_contract"] == mod.SOURCE_CONTRACT for row in rows)
    serialized = "\n".join(json.dumps(row, sort_keys=True) for row in rows)
    for forbidden in (
        "independent_critic", "retrieval_candidate", '"internal_table_uid":',
        '"raw_value":', '"parsed_value":', '"column_index":', '"row_index":', '"answer"',
    ):
        assert forbidden not in serialized


def test_source_replay_intake_rejects_tampered_typed_plans(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    tampered = tmp_path / "typed.jsonl"
    tampered.write_text(kwargs["typed_plans"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    tampered.with_suffix(".manifest.json").write_text(
        kwargs["typed_plans"].with_suffix(".manifest.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    kwargs["typed_plans"] = tampered
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        mod.build(**kwargs)


def test_source_replay_intake_refuses_to_overwrite(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    kwargs["output_dir"].mkdir()
    (kwargs["output_dir"] / "independent_source_replay_intake_queue_v1.jsonl").write_text("exists", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        mod.build(**kwargs)
