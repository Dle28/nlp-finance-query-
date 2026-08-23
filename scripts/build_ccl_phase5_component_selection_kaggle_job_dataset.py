#!/usr/bin/env python3
"""Copy one non-promotable CCL component-selection job into a Kaggle dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from finance_query.certified_canonical.phase5_component_selection import PHASE5_COMPONENT_SELECTION_PROTOCOL
from finance_query.certified_canonical.phase5_component_selection_policy import (
    POLICY_DECISION_NAME,
    POLICY_DECISION_PROTOCOL,
)
from finance_query.certified_canonical.phase5_component_selection_smoke import PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL


PROTOCOL = "kaggle_ccl_phase5_component_selection_job_dataset_v1"
_PILOT_PROTOCOL = "vifinqa_ccl_phase5_component_selection_pilot_v1"
_JOB_CONTRACTS = {
    PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL: "prepared_component_selection_smoke_not_executed",
    _PILOT_PROTOCOL: "prepared_component_selection_pilot_not_executed",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_policy_decision(*, decision_path: Path, decision_manifest_path: Path, job_manifest_path: Path) -> None:
    if not decision_path.is_file() or not decision_manifest_path.is_file():
        raise FileNotFoundError("Kaggle packaging requires a policy decision and its manifest")
    try:
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        decision_manifest = json.loads(decision_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("policy decision input is not valid JSON") from error
    if not isinstance(decision, dict) or not isinstance(decision_manifest, dict):
        raise ValueError("policy decision input must contain JSON objects")
    if (
        decision.get("protocol") != POLICY_DECISION_PROTOCOL
        or decision.get("run_status") != "bounded_smoke_explicitly_authorized_not_dispatched"
        or decision.get("kaggle_dataset_packaging_allowed") is not True
        or decision.get("model_execution_allowed") is not True
        or decision.get("candidate_job_manifest_sha256") != sha256_file(job_manifest_path)
        or decision.get("training_eligible") is not False
        or decision.get("certification_allowed") is not False
    ):
        raise ValueError("policy decision does not authorize this exact bounded smoke job")
    if (
        decision_manifest.get("protocol") != POLICY_DECISION_PROTOCOL
        or decision_manifest.get("run_status") != "bounded_smoke_explicitly_authorized_not_dispatched"
        or decision_manifest.get("kaggle_dataset_packaging_allowed") is not True
        or decision_manifest.get("model_execution_allowed") is not True
        or decision_manifest.get("training_eligible") is not False
        or decision_manifest.get("certification_allowed") is not False
        or ((decision_manifest.get("outputs") or {}).get(POLICY_DECISION_NAME) or {}).get("sha256")
        != sha256_file(decision_path)
    ):
        raise ValueError("policy decision manifest is unsupported or changed")


def build_dataset(
    *, job_dir: Path, policy_decision: Path, policy_decision_manifest: Path, output_dir: Path
) -> dict[str, Any]:
    job_dir, output_dir = job_dir.resolve(), output_dir.resolve()
    manifest_candidates = sorted(job_dir.glob("component_selection_*_job_manifest.json"))
    if len(manifest_candidates) != 1:
        raise ValueError("job directory must contain exactly one component-selection job manifest")
    manifest_path = manifest_candidates[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("protocol") not in _JOB_CONTRACTS
        or manifest.get("run_status") != _JOB_CONTRACTS[manifest.get("protocol")]
        or manifest.get("model_execution_allowed") is not True
        or manifest.get("navigation_overlay_required") is not True
        or manifest.get("training_eligible") is not False
        or manifest.get("certification_allowed") is not False
    ):
        raise ValueError("job manifest is not a proposal-only prepared component-selection job")
    if output_dir.exists():
        raise FileExistsError("Kaggle dataset output directory must be new")
    outputs = manifest.get("outputs") or {}
    job_files = sorted(
        name
        for name in outputs
        if name.endswith(".jsonl") or name.endswith("_packet_manifest.json")
    )
    if len(job_files) != 3 or sum(name.endswith("_requests_v1.jsonl") for name in job_files) != 1 or sum(name.endswith("_packets_v1.jsonl") for name in job_files) != 1 or sum(name.endswith("_packet_manifest.json") for name in job_files) != 1:
        raise ValueError("job manifest does not declare exactly one requests, packets and packet manifest file")
    packet_manifest_name = next(name for name in job_files if name.endswith("_packet_manifest.json"))
    packet_manifest_path = job_dir / packet_manifest_name
    if (outputs.get(packet_manifest_name) or {}).get("sha256") != sha256_file(packet_manifest_path):
        raise ValueError("job manifest checksum mismatch: selected packet manifest")
    try:
        packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("selected packet manifest is not valid JSON") from error
    if not isinstance(packet_manifest, dict):
        raise ValueError("selected packet manifest must be a JSON object")
    if (
        packet_manifest.get("protocol") != PHASE5_COMPONENT_SELECTION_PROTOCOL
        or packet_manifest.get("run_status") != "phase_5_component_selection_packets_complete_not_dispatched"
        or packet_manifest.get("navigation_overlay_required") is not True
        or packet_manifest.get("training_eligible") is not False
        or packet_manifest.get("certification_allowed") is not False
    ):
        raise ValueError("selected packet manifest is missing the required navigation overlay")
    _require_policy_decision(
        decision_path=policy_decision.resolve(),
        decision_manifest_path=policy_decision_manifest.resolve(),
        job_manifest_path=manifest_path,
    )
    copied: dict[str, dict[str, Any]] = {}
    output_dir.mkdir(parents=True)
    for name in [manifest_path.name, *job_files]:
        source = job_dir / name
        if not source.is_file():
            raise FileNotFoundError(source)
        if name != manifest_path.name and (outputs.get(name) or {}).get("sha256") != sha256_file(source):
            raise ValueError(f"job manifest checksum mismatch: {name}")
        target = output_dir / name
        shutil.copyfile(source, target)
        copied[name] = {"sha256": sha256_file(target), "bytes": target.stat().st_size}
    contract = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "source_job_manifest_sha256": sha256_file(manifest_path),
        "policy_decision_sha256": sha256_file(policy_decision),
        "policy_decision_manifest_sha256": sha256_file(policy_decision_manifest),
        "files": copied,
        "contains_model_output": False,
        "contains_raw_reports": False,
        "contains_labels": False,
        "contains_credentials": False,
        "training_eligible": False,
        "promotion_allowed": False,
    }
    (output_dir / "DATASET_CONTENTS.json").write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return contract


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--policy-decision", type=Path, required=True)
    parser.add_argument("--policy-decision-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_dataset(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
