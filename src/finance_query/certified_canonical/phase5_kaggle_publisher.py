"""Fail-closed publisher for the policy-authorized Phase 5 Kaggle smoke."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

from finance_query.certified_canonical.phase5_component_selection_policy import (
    POLICY_DECISION_NAME,
    POLICY_DECISION_PROTOCOL,
)
from finance_query.certified_canonical.phase5_component_selection_smoke import PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL
from finance_query.certified_canonical.pipeline import CertifiedCanonicalError


SOURCE_ARCHIVE_NAME = "ai_guru_ccl_phase3_source_v1.bundle"
SOURCE_MANIFEST_NAME = "ai_guru_ccl_phase3_source_v1.manifest.json"
JOB_CONTENTS_NAME = "DATASET_CONTENTS.json"
JOB_MANIFEST_NAME = "component_selection_smoke_job_manifest.json"
KERNEL_PACKAGE_NAME = "KERNEL_PACKAGE.json"
KERNEL_METADATA_NAME = "kernel-metadata.json"
_PRIVATE_CONTRACT_KEYS = (
    "contains_raw_reports",
    "contains_labels",
    "contains_research_artifacts",
    "contains_credentials",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CertifiedCanonicalError(f"invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise CertifiedCanonicalError(f"expected JSON object: {path}")
    return value


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or expected != sha256_file(path):
        raise CertifiedCanonicalError(f"SHA-256 mismatch for {label}")


def _require_false(value: Mapping[str, Any], keys: tuple[str, ...], *, label: str) -> None:
    if any(value.get(key) is not False for key in keys):
        raise CertifiedCanonicalError(f"{label} weakens the private non-promotable contract")


def _require_slug(value: object, *, label: str) -> str:
    normalized = str(value or "").strip()
    owner, separator, slug = normalized.partition("/")
    if not separator or not owner or not slug or "/" in slug or any(character.isspace() for character in normalized):
        raise CertifiedCanonicalError(f"{label} must be exactly <owner>/<slug>")
    return normalized


def _dataset_metadata(*, dataset_id: str, title: str) -> dict[str, Any]:
    return {"title": title, "id": dataset_id, "licenses": [{"name": "other"}]}


def validate_phase5_kaggle_handoff(
    *,
    source_bundle_dir: Path,
    job_dataset_dir: Path,
    kernel_package_dir: Path,
    policy_decision: Path,
    policy_decision_manifest: Path,
) -> dict[str, Any]:
    """Validate every local hash and private contract before any Kaggle write."""
    source_bundle_dir = source_bundle_dir.resolve()
    job_dataset_dir = job_dataset_dir.resolve()
    kernel_package_dir = kernel_package_dir.resolve()
    policy_decision = policy_decision.resolve()
    policy_decision_manifest = policy_decision_manifest.resolve()
    source_archive = source_bundle_dir / SOURCE_ARCHIVE_NAME
    source_manifest_path = source_bundle_dir / SOURCE_MANIFEST_NAME
    job_contents_path = job_dataset_dir / JOB_CONTENTS_NAME
    job_manifest_path = job_dataset_dir / JOB_MANIFEST_NAME
    kernel_package_path = kernel_package_dir / KERNEL_PACKAGE_NAME
    kernel_metadata_path = kernel_package_dir / KERNEL_METADATA_NAME
    required = (
        source_archive,
        source_manifest_path,
        job_contents_path,
        job_manifest_path,
        kernel_package_path,
        kernel_metadata_path,
        policy_decision,
        policy_decision_manifest,
    )
    if any(not path.is_file() for path in required):
        raise FileNotFoundError("Kaggle handoff is missing a required local artifact")

    source_manifest = _json(source_manifest_path)
    source_output = (source_manifest.get("outputs") or {}).get("archive") or {}
    _require_hash(source_archive, source_output.get("sha256"), label="source bundle archive")
    source_contract = source_manifest.get("source_contract") or {}
    if not isinstance(source_contract, Mapping):
        raise CertifiedCanonicalError("source bundle contract is malformed")
    _require_false(source_contract, _PRIVATE_CONTRACT_KEYS, label="source bundle")
    if any(
        source_contract.get(key) is not False
        for key in ("eligible_for_training", "eligible_for_submission", "eligible_for_promotion")
    ):
        raise CertifiedCanonicalError("source bundle is unexpectedly eligible for promotion")

    job_contents = _json(job_contents_path)
    if job_contents.get("protocol") != "kaggle_ccl_phase5_component_selection_job_dataset_v1":
        raise CertifiedCanonicalError("prepared job dataset protocol is unsupported")
    _require_false(job_contents, ("contains_model_output", "contains_raw_reports", "contains_labels", "contains_credentials"), label="prepared job dataset")
    if job_contents.get("training_eligible") is not False or job_contents.get("promotion_allowed") is not False:
        raise CertifiedCanonicalError("prepared job dataset is unexpectedly promotable")
    _require_hash(job_manifest_path, job_contents.get("source_job_manifest_sha256"), label="prepared job manifest")
    files = job_contents.get("files") or {}
    if not isinstance(files, Mapping) or not files:
        raise CertifiedCanonicalError("prepared job dataset file inventory is malformed")
    for name, contract in files.items():
        if not isinstance(name, str) or not isinstance(contract, Mapping):
            raise CertifiedCanonicalError("prepared job dataset file inventory is malformed")
        path = job_dataset_dir / name
        if not path.is_file():
            raise FileNotFoundError(path)
        _require_hash(path, contract.get("sha256"), label=f"prepared job file {name}")

    decision = _json(policy_decision)
    decision_manifest = _json(policy_decision_manifest)
    if (
        decision.get("protocol") != POLICY_DECISION_PROTOCOL
        or decision.get("run_status") != "bounded_smoke_explicitly_authorized_not_dispatched"
        or decision.get("kaggle_dataset_packaging_allowed") is not True
        or decision.get("model_execution_allowed") is not True
        or decision.get("candidate_job_manifest_sha256") != sha256_file(job_manifest_path)
    ):
        raise CertifiedCanonicalError("policy decision does not authorize this exact smoke job")
    _require_false(decision, ("training_eligible", "certification_allowed"), label="policy decision")
    if (
        decision_manifest.get("protocol") != POLICY_DECISION_PROTOCOL
        or decision_manifest.get("run_status") != "bounded_smoke_explicitly_authorized_not_dispatched"
        or ((decision_manifest.get("outputs") or {}).get(POLICY_DECISION_NAME) or {}).get("sha256")
        != sha256_file(policy_decision)
    ):
        raise CertifiedCanonicalError("policy decision manifest is unsupported or changed")
    _require_false(decision_manifest, ("training_eligible", "certification_allowed"), label="policy decision manifest")
    if (
        job_contents.get("policy_decision_sha256") != sha256_file(policy_decision)
        or job_contents.get("policy_decision_manifest_sha256") != sha256_file(policy_decision_manifest)
    ):
        raise CertifiedCanonicalError("prepared job dataset is not bound to the policy decision")

    job_manifest = _json(job_manifest_path)
    if (
        job_manifest.get("protocol") != PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL
        or job_manifest.get("run_status") != "prepared_component_selection_smoke_not_executed"
        or job_manifest.get("navigation_overlay_required") is not True
        or job_manifest.get("model_execution_allowed") is not True
    ):
        raise CertifiedCanonicalError("prepared job manifest is not an authorized navigation-gated smoke")
    _require_false(job_manifest, ("training_eligible", "certification_allowed"), label="prepared job manifest")

    kernel_package = _json(kernel_package_path)
    kernel_metadata = _json(kernel_metadata_path)
    _require_hash(kernel_metadata_path, (kernel_package.get("kernel_metadata") or {}).get("sha256"), label="kernel metadata")
    notebook = kernel_package.get("notebook") or {}
    notebook_name = notebook.get("name") if isinstance(notebook, Mapping) else None
    if not isinstance(notebook_name, str) or Path(notebook_name).name != notebook_name:
        raise CertifiedCanonicalError("kernel package notebook identity is malformed")
    _require_hash(kernel_package_dir / notebook_name, notebook.get("sha256"), label="kernel notebook")
    kernel_id = _require_slug(kernel_package.get("kernel_id"), label="kernel ID")
    dataset_sources = kernel_metadata.get("dataset_sources")
    if (
        kernel_metadata.get("id") != kernel_id
        or kernel_metadata.get("is_private") != "true"
        or kernel_metadata.get("enable_gpu") != "true"
        or not isinstance(dataset_sources, list)
        or len(dataset_sources) != 2
        or kernel_package.get("dataset_sources") != dataset_sources
    ):
        raise CertifiedCanonicalError("kernel metadata is not a private two-dataset GPU kernel")
    source_dataset_id, job_dataset_id = tuple(_require_slug(value, label="kernel dataset source") for value in dataset_sources)
    owner = kernel_id.partition("/")[0]
    if any(dataset_id.partition("/")[0] != owner for dataset_id in (source_dataset_id, job_dataset_id)):
        raise CertifiedCanonicalError("kernel and dataset sources must use one Kaggle owner namespace")
    kernel_contract = kernel_package.get("source_contract") or {}
    if not isinstance(kernel_contract, Mapping):
        raise CertifiedCanonicalError("kernel source contract is malformed")
    _require_false(kernel_contract, ("training_eligible", "certification_allowed", "contains_credentials"), label="kernel package")
    return {
        "kaggle_owner": owner,
        "source_dataset_id": source_dataset_id,
        "job_dataset_id": job_dataset_id,
        "kernel_id": kernel_id,
        "source_archive_sha256": sha256_file(source_archive),
        "job_manifest_sha256": sha256_file(job_manifest_path),
        "policy_decision_sha256": sha256_file(policy_decision),
        "request_count": 5,
        "training_eligible": False,
        "certification_allowed": False,
    }


def _copy_upload_files(source_dir: Path, upload_dir: Path) -> None:
    for source in sorted(source_dir.iterdir()):
        if source.is_file() and source.name != "dataset-metadata.json":
            shutil.copyfile(source, upload_dir / source.name)


def publish_phase5_kaggle_handoff(
    *,
    source_bundle_dir: Path,
    job_dataset_dir: Path,
    kernel_package_dir: Path,
    policy_decision: Path,
    policy_decision_manifest: Path,
    execute: bool = False,
) -> dict[str, Any]:
    """Return an upload plan, or create two private datasets then push one kernel when explicitly enabled."""
    handoff = validate_phase5_kaggle_handoff(
        source_bundle_dir=source_bundle_dir,
        job_dataset_dir=job_dataset_dir,
        kernel_package_dir=kernel_package_dir,
        policy_decision=policy_decision,
        policy_decision_manifest=policy_decision_manifest,
    )
    commands = {
        "source_dataset": ["kaggle", "datasets", "create", "--dir-mode", "zip", "-p", "<source-upload-dir>"],
        "job_dataset": ["kaggle", "datasets", "create", "--dir-mode", "zip", "-p", "<job-upload-dir>"],
        "kernel": ["kaggle", "kernels", "push", "-p", str(kernel_package_dir.resolve())],
    }
    plan = {
        "schema_version": 1,
        "protocol": "kaggle_ccl_phase5_component_selection_publish_plan_v1",
        "run_status": "kaggle_publish_plan_ready_not_executed" if not execute else "kaggle_publish_commands_completed_unverified",
        "handoff": handoff,
        "commands": commands,
        "will_create_private_datasets": [handoff["source_dataset_id"], handoff["job_dataset_id"]],
        "will_push_private_kernel": handoff["kernel_id"],
        "training_eligible": False,
        "certification_allowed": False,
    }
    if not execute:
        return plan
    try:
        subprocess.run(["kaggle", "--version"], check=True)
    except FileNotFoundError as error:
        raise CertifiedCanonicalError("Kaggle CLI is unavailable; install it and configure a local API token before publishing") from error
    source_bundle_dir = source_bundle_dir.resolve()
    job_dataset_dir = job_dataset_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="ccl-phase5-kaggle-") as temporary:
        temporary_root = Path(temporary)
        source_upload = temporary_root / "source"
        source_upload.mkdir()
        _copy_upload_files(source_bundle_dir, source_upload)
        (source_upload / "dataset-metadata.json").write_text(
            json.dumps(_dataset_metadata(dataset_id=handoff["source_dataset_id"], title="ViFinQA CCL Phase 5 V36 Source"), indent=2)
            + "\n",
            encoding="utf-8",
        )
        job_upload = temporary_root / "job"
        job_upload.mkdir()
        _copy_upload_files(job_dataset_dir, job_upload)
        (job_upload / "dataset-metadata.json").write_text(
            json.dumps(_dataset_metadata(dataset_id=handoff["job_dataset_id"], title="ViFinQA CCL Phase 5 V36 Smoke"), indent=2)
            + "\n",
            encoding="utf-8",
        )
        subprocess.run(["kaggle", "datasets", "create", "--dir-mode", "zip", "-p", str(source_upload)], check=True)
        subprocess.run(["kaggle", "datasets", "create", "--dir-mode", "zip", "-p", str(job_upload)], check=True)
        subprocess.run(["kaggle", "kernels", "push", "-p", str(kernel_package_dir.resolve())], check=True)
    return plan
