from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_grounded_critic_gpu_run", ROOT / "scripts" / "audit_grounded_critic_gpu_run.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    packets = tmp_path / "packets.jsonl"
    packets.write_text(
        json.dumps(
            {
                "question_id": 6,
                "execution_status": "execution_replay_ready",
                "allowed_packet_evidence_ids": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    packets_manifest = tmp_path / "packets.manifest.json"
    _write_json(packets_manifest, {"outputs": {"packets": {"sha256": MODULE.sha256_file(packets)}}})
    results = tmp_path / "results.jsonl"
    result_row = {
        "question_id": 6,
        "status": "accept",
        "stage_reviews": [],
        "binding_reviews": [],
        "execution_review": {},
        "missing_evidence": [],
        "conflicts": [],
        "feedback": {"reason_codes": []},
        "packet_evidence_ids_used": [],
        "provenance": "machine_provisional",
        "source_contract": {
            "candidate_only": True,
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
            "may_select_final_candidate": False,
            "may_select_value": False,
            "may_execute_formula": False,
        },
    }
    results.write_text(json.dumps(result_row) + "\n", encoding="utf-8")
    runtime = tmp_path / "runtime.json"
    _write_json(
        runtime,
        {
            "run_mode": "qwen14b_4bit",
            "runtime": "torch=test",
            "gpu": "test-gpu",
            "gpu_memory_gib": 16,
            "model": "test-model",
            "model_revision": "revision",
            "packets_sha256": MODULE.sha256_file(packets),
            "results_sha256": MODULE.sha256_file(results),
        },
    )
    result_manifest = tmp_path / "results.manifest.json"
    _write_json(
        result_manifest,
        {
            "schema_version": 2,
            "protocol": "grounded_critic_results_v2",
            "run_mode": "qwen14b_4bit",
            "runtime": "torch=test",
            "gpu": "test-gpu",
            "model_revision": "revision",
            "inputs": {
                "packets": {"sha256": MODULE.sha256_file(packets)},
                "packets_manifest": {"sha256": MODULE.sha256_file(packets_manifest)},
                "source_bundle": None,
            },
            "outputs": {"results": {"sha256": MODULE.sha256_file(results)}},
            "counts": {
                "packet_count": 1,
                "result_count": 1,
                "schema_valid_count": 1,
                "machine_provisional_count": 1,
                "status_counts": {"accept": 1},
                "invalid_schema_count": 0,
                "external_evidence_reference_count": 0,
                "numeric_invention_count": 0,
                "candidate_selection_count": 0,
            },
            "source_contract": result_row["source_contract"],
        },
    )
    return {
        "packets": packets,
        "packets_manifest": packets_manifest,
        "results": results,
        "results_manifest": result_manifest,
        "runtime": runtime,
    }


def test_audit_accepts_closed_world_hash_bound_gpu_cohort(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    output = tmp_path / "audit.json"
    result = MODULE.audit_gpu_run(**paths, output=output)
    assert result["audit_passed"] is True
    assert result["protocol"] == "grounded_critic_gpu_run_audit_v2"
    assert result["counts"]["packet_count"] == 1
    assert json.loads(output.read_text())["source_contract"]["promotion_allowed"] is False


def test_audit_rejects_runtime_mismatch_and_closed_world_violation(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    runtime = json.loads(paths["runtime"].read_text())
    runtime["model_revision"] = "different"
    _write_json(paths["runtime"], runtime)
    with pytest.raises(ValueError, match="model_revision"):
        MODULE.audit_gpu_run(**paths, output=tmp_path / "runtime-fail.json")

    paths = _fixture(tmp_path / "again")
    manifest = json.loads(paths["results_manifest"].read_text())
    manifest["counts"]["numeric_invention_count"] = 1
    _write_json(paths["results_manifest"], manifest)
    with pytest.raises(ValueError, match="closed-world violation"):
        MODULE.audit_gpu_run(**paths, output=tmp_path / "counter-fail.json")
