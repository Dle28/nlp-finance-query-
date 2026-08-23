from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.grounding_adjudication import (
    _source_contract,
    _sector_diagnosis,
    build_grounding_adjudication_queues,
)


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle"
PERIOD = ROOT / "artifacts/research/period_column_candidates_v1"


def _paths() -> dict[str, Path]:
    return {
        "period_packets_path": PERIOD / "period_column_candidate_packets_v1.jsonl",
        "period_manifest_path": PERIOD / "period_column_candidate_packets_v1.manifest.json",
        "no_candidate_audit_path": PERIOD / "route_packet_no_candidate_audit_v1.jsonl",
        "document_metadata_path": RUN / "document_metadata_v1.jsonl",
        "routing_catalog_path": RUN / "table_routing_catalog_v1.jsonl",
        "structured_tables_path": RUN / "tables_structured_v2.jsonl",
        "evidence_context_path": RUN / "tables_evidence_context_v3.jsonl",
    }


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_unknown_candidate_sector_is_not_called_confirmed_mismatch() -> None:
    diagnosis = _sector_diagnosis(
        {},
        [{"gate_vector": {"sector": {"observed": "unknown", "expected": ["banking"]}}}],
        [],
    )
    assert diagnosis == "CANDIDATE_SECTOR_UNKNOWN"


def test_queue_materialization_is_hash_bound_blank_and_nonpromotable(tmp_path: Path) -> None:
    result = build_grounding_adjudication_queues(**_paths(), output_dir=tmp_path)
    assert result["counts"]["missing_operand_audits"] == 91
    assert result["counts"]["metadata_queue"] == 54
    assert result["counts"]["routing_queue"] == 37
    assert result["counts"]["period_queue"] == 16
    for output in result["outputs"].values():
        assert Path(output["path"]).exists()
    for name in ("metadata_queue", "routing_queue", "period_queue"):
        for item in _read(Path(result["outputs"][name]["path"])):
            assert item["decision_contract"]["decision"] is None
            assert item["decision_contract"]["eligible_for_materialization"] is False
            assert item["source_contract"] == _source_contract()


def test_hash_mismatch_fails_before_queue_output(tmp_path: Path) -> None:
    paths = _paths()
    tampered = tmp_path / "period-tampered.jsonl"
    tampered.write_text(paths["period_packets_path"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    paths["period_packets_path"] = tampered
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        build_grounding_adjudication_queues(**paths, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()
