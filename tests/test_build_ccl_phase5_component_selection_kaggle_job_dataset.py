"""Tests for navigation-gated Phase 5 Kaggle dataset packaging."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from finance_query.certified_canonical.phase5_component_selection import PHASE5_COMPONENT_SELECTION_PROTOCOL
from finance_query.certified_canonical.phase5_component_selection_policy import (
    POLICY_DECISION_NAME,
    POLICY_DECISION_PROTOCOL,
)
from finance_query.certified_canonical.phase5_component_selection_smoke import PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_ccl_phase5_component_selection_kaggle_job_dataset.py"
SPEC = importlib.util.spec_from_file_location("component_selection_kaggle_job_dataset", SCRIPT)
assert SPEC and SPEC.loader
dataset = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dataset)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _job_dir(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    job_dir = root / "job"
    job_dir.mkdir()
    packets = job_dir / "component_selection_smoke_packets_v1.jsonl"
    packets.write_text('{"component_selection_packet_id":"packet-1"}\n', encoding="utf-8")
    requests = job_dir / "component_selection_smoke_requests_v1.jsonl"
    requests.write_text('{"component_selection_request_id":"request-1"}\n', encoding="utf-8")
    selected_manifest = job_dir / "component_selection_smoke_packet_manifest.json"
    _write_json(
        selected_manifest,
        {
            "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
            "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
            "navigation_overlay_required": True,
            "training_eligible": False,
            "certification_allowed": False,
            "outputs": {packets.name: {"sha256": _sha(packets)}},
        },
    )
    job_manifest = job_dir / "component_selection_smoke_job_manifest.json"
    _write_json(
        job_manifest,
        {
            "protocol": PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL,
            "run_status": "prepared_component_selection_smoke_not_executed",
            "model_execution_allowed": True,
            "navigation_overlay_required": True,
            "training_eligible": False,
            "certification_allowed": False,
            "outputs": {
                packets.name: {"sha256": _sha(packets)},
                requests.name: {"sha256": _sha(requests)},
                selected_manifest.name: {"sha256": _sha(selected_manifest)},
            },
        },
    )
    decision = root / POLICY_DECISION_NAME
    _write_json(
        decision,
        {
            "protocol": POLICY_DECISION_PROTOCOL,
            "run_status": "bounded_smoke_explicitly_authorized_not_dispatched",
            "kaggle_dataset_packaging_allowed": True,
            "model_execution_allowed": True,
            "candidate_job_manifest_sha256": _sha(job_manifest),
            "training_eligible": False,
            "certification_allowed": False,
        },
    )
    decision_manifest = root / "component_selection_policy_decision_manifest.json"
    _write_json(
        decision_manifest,
        {
            "protocol": POLICY_DECISION_PROTOCOL,
            "run_status": "bounded_smoke_explicitly_authorized_not_dispatched",
            "kaggle_dataset_packaging_allowed": True,
            "model_execution_allowed": True,
            "training_eligible": False,
            "certification_allowed": False,
            "outputs": {decision.name: {"sha256": _sha(decision)}},
        },
    )
    return job_dir, job_manifest, selected_manifest, decision, decision_manifest


def _refresh_manifest_hash(job_manifest: Path, selected_manifest: Path) -> None:
    payload = json.loads(job_manifest.read_text(encoding="utf-8"))
    payload["outputs"][selected_manifest.name]["sha256"] = _sha(selected_manifest)
    _write_json(job_manifest, payload)


def test_copies_only_navigation_gated_job_files(tmp_path: Path) -> None:
    job_dir, _, _, decision, decision_manifest = _job_dir(tmp_path)
    output_dir = tmp_path / "dataset"

    result = dataset.build_dataset(
        job_dir=job_dir,
        policy_decision=decision,
        policy_decision_manifest=decision_manifest,
        output_dir=output_dir,
    )

    assert result["source_job_manifest_sha256"] == _sha(job_dir / "component_selection_smoke_job_manifest.json")
    assert result["training_eligible"] is False
    assert result["promotion_allowed"] is False
    assert sorted(path.name for path in output_dir.iterdir()) == [
        "DATASET_CONTENTS.json",
        "component_selection_smoke_job_manifest.json",
        "component_selection_smoke_packet_manifest.json",
        "component_selection_smoke_packets_v1.jsonl",
        "component_selection_smoke_requests_v1.jsonl",
    ]


def test_rejects_job_manifest_without_navigation_provenance(tmp_path: Path) -> None:
    job_dir, job_manifest, _, decision, decision_manifest = _job_dir(tmp_path)
    payload = json.loads(job_manifest.read_text(encoding="utf-8"))
    payload.pop("navigation_overlay_required")
    _write_json(job_manifest, payload)

    with pytest.raises(ValueError, match="proposal-only prepared"):
        dataset.build_dataset(
            job_dir=job_dir,
            policy_decision=decision,
            policy_decision_manifest=decision_manifest,
            output_dir=tmp_path / "dataset",
        )


def test_rejects_selected_manifest_without_navigation_provenance(tmp_path: Path) -> None:
    job_dir, job_manifest, selected_manifest, decision, decision_manifest = _job_dir(tmp_path)
    payload = json.loads(selected_manifest.read_text(encoding="utf-8"))
    payload.pop("navigation_overlay_required")
    _write_json(selected_manifest, payload)
    _refresh_manifest_hash(job_manifest, selected_manifest)

    with pytest.raises(ValueError, match="required navigation overlay"):
        dataset.build_dataset(
            job_dir=job_dir,
            policy_decision=decision,
            policy_decision_manifest=decision_manifest,
            output_dir=tmp_path / "dataset",
        )


def test_rejects_missing_policy_decision(tmp_path: Path) -> None:
    job_dir, _, _, decision, decision_manifest = _job_dir(tmp_path)
    decision.unlink()

    with pytest.raises(FileNotFoundError, match="policy decision"):
        dataset.build_dataset(
            job_dir=job_dir,
            policy_decision=decision,
            policy_decision_manifest=decision_manifest,
            output_dir=tmp_path / "dataset",
        )
