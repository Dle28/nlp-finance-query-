from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.research.experiment_split import freeze_experiment_split, validate_experiment_split


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_repository_temporal_split_is_frozen_and_validated() -> None:
    artifact = Path("artifacts/research/temporal_point_semantics_v1_split_20260825_r2")
    if not artifact.is_dir():
        pytest.skip("local immutable split artifact is not present")
    result = validate_experiment_split(artifact)
    assert result["stage_counts"] == {"discovery": 10, "development": 30, "untouched_evaluation": 30}
    assert result["company_overlap_count"] == 0
    assert result["model_change_allowed"] is False


def test_freezer_refuses_to_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        freeze_experiment_split(
            hypothesis=Path("configs/research/experiments/temporal_point_semantics_v1.json"),
            taxonomy=tmp_path / "missing.jsonl",
            output_dir=output,
        )
