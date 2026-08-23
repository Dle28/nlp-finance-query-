"""Tests for fail-closed publishing of the policy-authorized Phase 5 smoke."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

from finance_query.certified_canonical.phase5_component_selection_policy import (
    POLICY_DECISION_MANIFEST_NAME,
    POLICY_DECISION_NAME,
    POLICY_DECISION_PROTOCOL,
)
from finance_query.certified_canonical.phase5_component_selection_smoke import PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL
from finance_query.certified_canonical.phase5_kaggle_publisher import (
    JOB_CONTENTS_NAME,
    JOB_MANIFEST_NAME,
    KERNEL_METADATA_NAME,
    KERNEL_PACKAGE_NAME,
    SOURCE_ARCHIVE_NAME,
    SOURCE_MANIFEST_NAME,
    CertifiedCanonicalError,
    publish_phase5_kaggle_handoff,
    sha256_file,
    validate_phase5_kaggle_handoff,
)


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(root: Path) -> dict[str, Path]:
    source_dir = root / "source"
    source_dir.mkdir()
    archive = source_dir / SOURCE_ARCHIVE_NAME
    archive.write_bytes(b"source bundle")
    _json(
        source_dir / SOURCE_MANIFEST_NAME,
        {
            "outputs": {"archive": {"sha256": sha256_file(archive)}},
            "source_contract": {
                "contains_raw_reports": False,
                "contains_labels": False,
                "contains_research_artifacts": False,
                "contains_credentials": False,
                "eligible_for_training": False,
                "eligible_for_submission": False,
                "eligible_for_promotion": False,
            },
        },
    )
    job_dir = root / "job"
    job_dir.mkdir()
    packets = job_dir / "component_selection_smoke_packets_v1.jsonl"
    packets.write_text('{"component_selection_packet_id":"packet-1"}\n', encoding="utf-8")
    requests = job_dir / "component_selection_smoke_requests_v1.jsonl"
    requests.write_text("".join(json.dumps({"component_selection_request_id": f"request-{index}"}) + "\n" for index in range(5)), encoding="utf-8")
    packet_manifest = job_dir / "component_selection_smoke_packet_manifest.json"
    _json(packet_manifest, {"navigation_overlay_required": True})
    job_manifest = job_dir / JOB_MANIFEST_NAME
    _json(
        job_manifest,
        {
            "protocol": PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL,
            "run_status": "prepared_component_selection_smoke_not_executed",
            "navigation_overlay_required": True,
            "model_execution_allowed": True,
            "training_eligible": False,
            "certification_allowed": False,
        },
    )
    decision_dir = root / "decision"
    decision_dir.mkdir()
    decision = decision_dir / POLICY_DECISION_NAME
    _json(
        decision,
        {
            "protocol": POLICY_DECISION_PROTOCOL,
            "run_status": "bounded_smoke_explicitly_authorized_not_dispatched",
            "kaggle_dataset_packaging_allowed": True,
            "model_execution_allowed": True,
            "candidate_job_manifest_sha256": sha256_file(job_manifest),
            "training_eligible": False,
            "certification_allowed": False,
        },
    )
    decision_manifest = decision_dir / POLICY_DECISION_MANIFEST_NAME
    _json(
        decision_manifest,
        {
            "protocol": POLICY_DECISION_PROTOCOL,
            "run_status": "bounded_smoke_explicitly_authorized_not_dispatched",
            "outputs": {POLICY_DECISION_NAME: {"sha256": sha256_file(decision)}},
            "training_eligible": False,
            "certification_allowed": False,
        },
    )
    dataset_files = {
        path.name: {"sha256": sha256_file(path)}
        for path in (job_manifest, packet_manifest, packets, requests)
    }
    _json(
        job_dir / JOB_CONTENTS_NAME,
        {
            "protocol": "kaggle_ccl_phase5_component_selection_job_dataset_v1",
            "source_job_manifest_sha256": sha256_file(job_manifest),
            "policy_decision_sha256": sha256_file(decision),
            "policy_decision_manifest_sha256": sha256_file(decision_manifest),
            "files": dataset_files,
            "contains_model_output": False,
            "contains_raw_reports": False,
            "contains_labels": False,
            "contains_credentials": False,
            "training_eligible": False,
            "promotion_allowed": False,
        },
    )
    kernel_dir = root / "kernel"
    kernel_dir.mkdir()
    notebook = kernel_dir / "smoke.ipynb"
    notebook.write_text("{}\n", encoding="utf-8")
    metadata = kernel_dir / KERNEL_METADATA_NAME
    _json(
        metadata,
        {
            "id": "owner/vifinqa-smoke-run",
            "is_private": "true",
            "enable_gpu": "true",
            "dataset_sources": ["owner/vifinqa-source", "owner/vifinqa-smoke"],
        },
    )
    _json(
        kernel_dir / KERNEL_PACKAGE_NAME,
        {
            "kernel_id": "owner/vifinqa-smoke-run",
            "notebook": {"name": notebook.name, "sha256": sha256_file(notebook)},
            "kernel_metadata": {"sha256": sha256_file(metadata)},
            "dataset_sources": ["owner/vifinqa-source", "owner/vifinqa-smoke"],
            "source_contract": {"training_eligible": False, "certification_allowed": False, "contains_credentials": False},
        },
    )
    return {
        "source_bundle_dir": source_dir,
        "job_dataset_dir": job_dir,
        "kernel_package_dir": kernel_dir,
        "policy_decision": decision,
        "policy_decision_manifest": decision_manifest,
    }


def test_builds_dry_run_plan_without_kaggle_cli(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)

    handoff = validate_phase5_kaggle_handoff(**paths)
    plan = publish_phase5_kaggle_handoff(**paths)

    assert handoff["request_count"] == 5
    assert handoff["source_dataset_id"] == "owner/vifinqa-source"
    assert plan["run_status"] == "kaggle_publish_plan_ready_not_executed"
    assert plan["will_push_private_kernel"] == "owner/vifinqa-smoke-run"


def test_execute_runs_exactly_two_dataset_creates_then_one_kernel_push(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    with mock.patch("finance_query.certified_canonical.phase5_kaggle_publisher.subprocess.run") as run:
        plan = publish_phase5_kaggle_handoff(**paths, execute=True)

    assert plan["run_status"] == "kaggle_publish_commands_completed_unverified"
    commands = [call.args[0] for call in run.call_args_list]
    assert commands[0] == ["kaggle", "--version"]
    assert [command[:3] for command in commands[1:3]] == [["kaggle", "datasets", "create"], ["kaggle", "datasets", "create"]]
    assert commands[3][:3] == ["kaggle", "kernels", "push"]


def test_rejects_policy_decision_for_a_different_job(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    payload = json.loads(paths["policy_decision"].read_text(encoding="utf-8"))
    payload["candidate_job_manifest_sha256"] = "0" * 64
    _json(paths["policy_decision"], payload)

    with pytest.raises(CertifiedCanonicalError, match="does not authorize this exact smoke job"):
        validate_phase5_kaggle_handoff(**paths)
