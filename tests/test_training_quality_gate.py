from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "train_dense_retriever_quality_gate",
    Path(__file__).parents[1] / "scripts" / "train_dense_retriever.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

producer_spec = importlib.util.spec_from_file_location(
    "build_retriever_training_quality_gate",
    Path(__file__).parents[1] / "scripts" / "build_retriever_training_quality_gate.py",
)
producer = importlib.util.module_from_spec(producer_spec)
assert producer_spec.loader is not None
producer_spec.loader.exec_module(producer)


def _gate() -> dict:
    return {
        "protocol": mod.TRAINING_QUALITY_GATE_PROTOCOL,
        "status": "READY",
        "training_eligible": True,
        "answer_eligible": False,
        "submission_eligible": False,
        "provenance_promotion_allowed": False,
        "labels_sha256": "a" * 64,
        "machine_silver_pair_count": 200,
        "provenance_leakage_check": "PASS",
        "independent_audit_sha256": "b" * 64,
        "fingerprint_census_sha256": "c" * 64,
        "metrics": {
            "independent_audit_precision": 0.99,
            "audit_ci95_lower": 0.97,
            "major_fingerprint_coverage": 0.90,
        },
    }


def test_machine_silver_quality_gate_requires_all_quality_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "quality_gate.json"
    path.write_text(json.dumps(_gate()), encoding="utf-8")

    mod.validate_machine_silver_quality_gate(
        path,
        labels_sha256="a" * 64,
        pair_count=200,
    )

    invalid = _gate()
    invalid["metrics"]["audit_ci95_lower"] = 0.96
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError, match="audit_ci95_lower"):
        mod.validate_machine_silver_quality_gate(
            path,
            labels_sha256="a" * 64,
            pair_count=200,
        )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_ready_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    labels = tmp_path / "labels.jsonl"
    audit = tmp_path / "audit.jsonl"
    census = tmp_path / "census.jsonl"
    calibration = tmp_path / "calibration.json"
    label_rows = []
    audit_rows = []
    census_rows = []
    for question_id in range(1, 201):
        label_rows.append(
            {
                "id": question_id,
                "question": f"Question {question_id}",
                "positive_table_uids": [f"table-{question_id}"],
                "annotation_status": "machine_calibrated",
                "label_source": "machine",
                "structure_validation": {"validated": True},
                "machine_self_review": {"training_eligible": True},
                "direct_replay_gate": {"training_gate_only": True},
                "independent_critic_gate": {"training_gate_only": True},
                "answer_eligible": False,
                "submission_eligible": False,
                "provenance_promotion_allowed": False,
            }
        )
        audit_rows.append(
            {
                "question_id": question_id,
                "independent_audit_status": "passed",
                "answer_eligible": False,
                "training_eligible": False,
                "provenance_promotion_allowed": False,
            }
        )
        census_rows.append(
            {
                "question_id": question_id,
                "schema_version": 2,
                "structural_fingerprint": f"fingerprint-{(question_id - 1) // 20}",
            }
        )
    _write_jsonl(labels, label_rows)
    _write_jsonl(audit, audit_rows)
    _write_jsonl(census, census_rows)
    audit.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "protocol": producer.AUDIT_PROTOCOL,
                "sidecar_sha256": producer.sha256_file(audit),
                "reviewer_inputs_used": [],
                "answer_eligible": False,
                "training_eligible": False,
                "provenance_promotion_allowed": False,
            }
        ),
        encoding="utf-8",
    )
    census.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "protocol": producer.FINGERPRINT_PROTOCOL,
                "schema_version": 2,
                "sidecar_sha256": producer.sha256_file(census),
                "answer_eligible": False,
                "training_eligible": False,
                "provenance_promotion_allowed": False,
            }
        ),
        encoding="utf-8",
    )
    calibration.write_text(
        json.dumps(
            {
                "source_contract": {
                    "evidence_eligible": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "promotion_allowed": False,
                },
                "training_quality_metrics": {
                    "metric_protocol": producer.CALIBRATION_METRIC_PROTOCOL,
                    "independent_audit_precision": 0.99,
                    "audit_ci95_lower": 0.97,
                    "independent_audit_sample_size": 200,
                },
            }
        ),
        encoding="utf-8",
    )
    return labels, audit, census, calibration


def test_quality_gate_producer_binds_ready_inputs_and_trainer_accepts_it(tmp_path: Path) -> None:
    labels, audit, census, calibration = _write_ready_inputs(tmp_path)
    output = tmp_path / "quality_gate.json"

    gate = producer.build_quality_gate(labels, audit, census, calibration, output)

    assert gate["status"] == "READY"
    assert gate["training_eligible"] is True
    assert gate["metrics"]["major_fingerprint_coverage"] == 1.0
    mod.validate_machine_silver_quality_gate(
        output,
        labels_sha256=producer.sha256_file(labels),
        pair_count=200,
    )


def test_quality_gate_producer_blocks_missing_calibration_bound(tmp_path: Path) -> None:
    labels, audit, census, calibration = _write_ready_inputs(tmp_path)
    payload = json.loads(calibration.read_text(encoding="utf-8"))
    payload["training_quality_metrics"]["audit_ci95_lower"] = None
    calibration.write_text(json.dumps(payload), encoding="utf-8")

    gate = producer.build_quality_gate(
        labels,
        audit,
        census,
        calibration,
        tmp_path / "quality_gate.json",
    )

    assert gate["status"] == "BLOCKED"
    assert gate["training_eligible"] is False
    assert "CALIBRATION_AUDIT_CI95_LOWER_MISSING" in gate["reason_codes"]
