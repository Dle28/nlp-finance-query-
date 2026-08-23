from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.grounding_repairs import materialize_grounding_repairs


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/research/grounding_adjudication_v1"


def _kwargs(output_dir: Path) -> dict:
    return {
        "metadata_queue_path": BASE / "metadata_adjudication_queue_v1.jsonl",
        "routing_queue_path": BASE / "routing_gate_adjudication_queue_v1.jsonl",
        "period_queue_path": BASE / "period_adjudication_queue_v1.jsonl",
        "adjudication_manifest_path": BASE / "grounding_adjudication_v1.manifest.json",
        "output_dir": output_dir,
    }


def test_blank_adjudications_make_no_repairs_and_keep_all_unresolved(tmp_path: Path) -> None:
    result = materialize_grounding_repairs(**_kwargs(tmp_path))
    assert result["counts"] == {"approved": 0, "rejected_or_unresolved": 107}
    assert Path(result["outputs"]["approved"]["path"]).read_text(encoding="utf-8") == ""


def test_queue_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    paths = _kwargs(tmp_path / "out")
    tampered = tmp_path / "metadata.jsonl"
    tampered.write_text(paths["metadata_queue_path"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    paths["metadata_queue_path"] = tampered
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        materialize_grounding_repairs(**paths)
